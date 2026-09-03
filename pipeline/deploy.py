#!/usr/bin/env python3
"""sim2real deploy — orchestrate runs, collect results.

Subcommands:
  run      Submit PipelineRuns and orchestrate their execution
  status   Show progress of all (workload, package, iteration) triples
  collect  Pull results from cluster for completed phases
  stop     Stop the remote orchestrator Job
  reset    Reset all non-pending pairs to pending (with cluster cleanup)
  wipe     Delete local result files for pairs in scope
  pairs    List available pair keys, workloads, and packages
"""

import argparse
import datetime as _dt
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import yaml

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

# Ensure repo root is on sys.path when run as a script (python pipeline/deploy.py)
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

if TYPE_CHECKING:
    from pipeline.lib.health import RemediationTracker


# ── Repo layout ──────────────────────────────────────────────────────────────
# Reuse the value computed for the sys.path bootstrap above rather than
# recomputing it. The canonical helper is ``pipeline.lib.layout.repo_root()``,
# imported below once the package is importable; this alias preserves the
# module-level ``REPO_ROOT`` name used throughout deploy.py.
REPO_ROOT = _REPO_ROOT

# Overridden in main() when --experiment-root is specified.
EXPERIMENT_ROOT = REPO_ROOT


# ── Color helpers ────────────────────────────────────────────────────────────
_tty = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _tty else text


from pipeline.lib import cluster_ops, layout
from pipeline.lib import proc as _proc
from pipeline.lib.log import info, ok, warn, err
from pipeline.lib.pairkey import parse_iteration_spec, parse_pair_key
from pipeline.lib.redact import redact_yaml_file, redact_yaml_tree
from pipeline.lib.scope import expand_glob_values as _expand_glob_values
from pipeline.lib.scope import parse_name_list as _parse_list


def _is_pair_key(key: str) -> bool:
    """Return True if key is a real pair entry (not metadata)."""
    return not key.startswith("_")


def _key_iteration(key: str) -> int:
    """Return the iteration number encoded in a pair key.

    Canonical grammar (``wl-<workload>|<package>[|i<N>]``) yields the
    parsed ``iteration``; keys without an ``|iN`` suffix parse as ``1``.
    Legacy dash-shape keys that predate the step-5 grammar (e.g.
    ``wl-smoke-baseline`` from a pre-PR-2 run) do not parse; they map to
    ``1`` here to match the design's step-5 semantics — the
    single-replica shape reads as implicit ``i1``. ``--iteration 1``
    therefore includes legacy pairs and higher values exclude them,
    which is what the design table specifies for the mid-rollout state.
    """
    try:
        return parse_pair_key(key).iteration
    except ValueError:
        return 1


def _format_capacity(effective: int, probed: int, reserved: int,
                     allocatable: int, requested: int) -> str:
    """Return the unified orchestrator capacity log line (issue #272).

    All five numbers appear in a fixed order so a single log line is
    self-contained — the reader does not need surrounding context to know
    which view they are seeing. The five numbers split across three layers:

      - effective: derived (probed − reserved, clamped at 0)
      - probed, allocatable, requested: cluster probe (`probe_free_gpus`)
      - reserved: shadow ledger
    """
    return (
        f"Capacity: {effective} effective free GPUs "
        f"({probed} probed − {reserved} reserved; "
        f"cluster: {allocatable} allocatable − {requested} requested)"
    )


def step(n, title: str) -> None:
    print("\n" + _c("36", f"━━━ Step {n}: {title} ━━━"))


# ── Subprocess helper ────────────────────────────────────────────────────────

def run(cmd: list[str], *, check: bool = True, capture: bool = False,
        cwd: "Path | None" = None, timeout: int = 120) -> subprocess.CompletedProcess:
    """Delegates to :func:`pipeline.lib.proc.run` — the single process-exec seam.

    ``timeout`` defaults to 120 s so kubectl/helm calls against a hung or
    unreachable cluster do not block the deploy process indefinitely (#694).
    """
    return _proc.run(cmd, check=check, capture=capture, cwd=cwd, timeout=timeout)


# ── ConfigMap namespace resolution ──────────────────────────────────────────

def _configmap_namespace(cluster_config: dict | None,
                         namespaces: list[str] | None = None) -> str:
    """Return the namespace for the run-scoped progress ConfigMap.

    Uses ``namespaces[0]`` when the caller passes that list, else
    ``cluster_config["namespaces"][0]``. Returns "" when neither source
    yields a value.
    """
    ns_list = namespaces or (cluster_config or {}).get("namespaces") or []
    return ns_list[0] if ns_list else ""


# ── Phase discovery ─────────────────────────────────────────────────────────

def _discover_phases(cluster_dir: "Path") -> list[str]:
    """Discover package phases from pipelinerun-*.yaml filenames in cluster/."""
    phases: set[str] = set()
    for pr_file in cluster_dir.glob("pipelinerun-*.yaml"):
        # Filename pattern: pipelinerun-{workload}-{phase}.yaml
        stem = pr_file.stem
        parts = stem.split("-")
        if len(parts) >= 3:
            phases.add(parts[-1])
    return sorted(phases) if phases else ["baseline", "treatment"]

# ── Setup config ─────────────────────────────────────────────────────────────

# Set to "1" in build_orchestrator_job's env spec so downstream deploy.py
# error paths can distinguish "user is running this locally" from "running
# inside the orchestrator pod" and emit a hint the operator can act on
# (issue #562).
_ORCHESTRATOR_POD_ENV = "SIM2REAL_ORCHESTRATOR_POD"


def _in_orchestrator_pod() -> bool:
    return os.environ.get(_ORCHESTRATOR_POD_ENV) == "1"


def _no_namespaces_hint() -> str:
    """Return the operator-actionable "no namespaces" message for this context.

    In the pod: the cluster_config that was uploaded via the run-inputs
    ConfigMap didn't carry a ``namespaces`` list — an ``assemble``/pod-input
    problem, not a cluster-provisioning problem. Running ``cluster.py`` here
    is impossible; the pointer at ``sim2real assemble`` is what recovers.

    Locally: the historical hint at ``cluster.py provision --namespaces``
    is correct — this is where the operator adds namespaces to the
    cluster's ``cluster_config.json``.
    """
    if _in_orchestrator_pod():
        return (
            "No namespaces in the run-inputs cluster_config — the uploaded "
            "ConfigMap is missing them. Re-run 'sim2real assemble --run <N>' "
            "locally (ensure the target cluster has namespaces provisioned) "
            "and then 'deploy.py run --remote' again."
        )
    return "No namespaces configured. Run cluster.py provision with --namespaces."


def _init_experiment_root(args) -> Path:
    """Resolve EXPERIMENT_ROOT from CLI args and mirror it into ``layout``.

    Every subsequent path resolution — ``layout.workspace_dir()``,
    ``layout.cluster_config_path()``, ``cluster_ops.read_cluster_config()``
    — reads from ``layout._EXPERIMENT_ROOT`` via ``layout.experiment_root()``.
    That accessor falls back to ``Path.cwd()`` when the module global is
    unset, which is subtly wrong in any context where ``--experiment-root``
    differs from the current working directory — most visibly the
    orchestrator pod, whose Dockerfile pins ``WORKDIR /app`` while the
    entrypoint receives ``--experiment-root /data`` (issue #562).

    Wiring layout at startup means every downstream consumer sees the same
    experiment root the deploy.py module global does. ``set_experiment_root``
    is idempotent, so re-calling in main is safe.
    """
    global EXPERIMENT_ROOT
    EXPERIMENT_ROOT = (
        Path(args.experiment_root).resolve() if args.experiment_root else Path.cwd()
    )
    layout.set_experiment_root(EXPERIMENT_ROOT)
    return EXPERIMENT_ROOT


def _load_setup_config() -> dict:
    path = EXPERIMENT_ROOT / "workspace" / "setup_config.json"
    if not path.exists():
        path = REPO_ROOT / "workspace" / "setup_config.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _load_cluster_config() -> dict:
    """Load the single cluster_config.json from the workspace.

    Step 0 assumes one cluster per workspace. Returns ``{}`` when no
    ``clusters/`` entry exists yet (callers surface the same
    "no namespace configured" error path they used for an empty
    setup_config); hard-fails when more than one cluster is present.
    """
    layout.set_experiment_root(EXPERIMENT_ROOT)
    cluster_ids = layout.list_cluster_ids()
    if not cluster_ids:
        # Fall back to legacy workspace/ next to REPO_ROOT (matches
        # _load_setup_config's two-path resolution for in-repo workspaces).
        layout.set_experiment_root(REPO_ROOT)
        cluster_ids = layout.list_cluster_ids()
        if not cluster_ids:
            return {}
    if len(cluster_ids) > 1:
        err(f"Multiple clusters found in workspace ({len(cluster_ids)}); "
            f"Step 0 assumes a single cluster.")
        sys.exit(1)
    return cluster_ops.read_cluster_config(cluster_ids[0])


def _load_run_cluster_config(run_dir: Path) -> dict:
    """Resolve a run's cluster_config via ``run_metadata.json:cluster_id``.

    Per-run dispatch (issue #446): the run's cluster is recorded in
    ``runs/<R>/run_metadata.json`` by ``sim2real assemble``, and this helper
    reads that field then delegates to ``cluster_ops.read_cluster_config``.
    All error paths emit the exact acceptance-criterion strings from #446
    and exit — no auto-fix (that is step-6's job).
    """
    run_name = run_dir.name
    if not run_dir.exists() or not (run_dir / "cluster").exists():
        err(f"run 'sim2real assemble --run {run_name}' first")
        sys.exit(1)

    meta_path = run_dir / "run_metadata.json"
    if not meta_path.exists():
        err("run metadata corrupted; re-assemble")
        sys.exit(1)
    try:
        meta = json.loads(meta_path.read_text())
    except (json.JSONDecodeError, OSError):
        err("run metadata corrupted; re-assemble")
        sys.exit(1)

    cluster_id = meta.get("cluster_id") if isinstance(meta, dict) else None
    if not cluster_id:
        err("run metadata corrupted; re-assemble")
        sys.exit(1)

    return cluster_ops.read_cluster_config(cluster_id)


def _make_progress_store(namespace: str, run_dir: Path):
    """Construct a ConfigMapProgressStore scoped to this run's (scenario, run_name).

    Reads ``run_metadata.json:scenario`` — written by ``sim2real assemble``
    since issue #551. Exits with an operator-friendly error when the field
    is missing (a run assembled before #551 that hasn't been re-assembled)
    so operators aren't left staring at a NotFound after an unexpected name
    change. Callers get a store whose ConfigMap name is unique per
    (scenario, run) — no more cross-experiment-root bleed.
    """
    from pipeline.lib.progress import ConfigMapProgressStore
    meta_path = run_dir / "run_metadata.json"
    if not meta_path.exists():
        err(f"run_metadata.json not found at {meta_path} — "
            f"run 'sim2real assemble --run {run_dir.name}' first.")
        sys.exit(1)
    try:
        meta = json.loads(meta_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        err(f"run_metadata.json is not valid JSON: {exc}. "
            f"Re-run 'sim2real assemble --run {run_dir.name}'.")
        sys.exit(1)
    scenario = (
        (meta.get("scenario") or "").strip()
        if isinstance(meta, dict) else ""
    )
    if not scenario:
        # The remediation names --force because the assemble no-op path
        # (same manifest content + same replicas since last assemble)
        # would otherwise swallow the backfill silently. Prefill
        # --translation and --cluster from run_metadata.json when present
        # so the operator can copy-paste; fall back to placeholders when
        # either field is absent.
        translation_ref = ""
        cluster_ref = ""
        if isinstance(meta, dict):
            translation_ref = str(meta.get("translation_hash") or "").strip()
            cluster_ref = str(meta.get("cluster_id") or "").strip()
        translation_arg = translation_ref or "<translation-ref>"
        cluster_arg = cluster_ref or "<cluster-id>"
        err(
            f"scenario not recorded for run '{run_dir.name}' — this run "
            f"predates scenario tracking and cannot be resolved to a "
            f"ConfigMap. To backfill: sim2real assemble "
            f"--translation {translation_arg} --cluster {cluster_arg} "
            f"--run {run_dir.name} --force"
        )
        sys.exit(1)
    return ConfigMapProgressStore(
        namespace, run_name=run_dir.name, scenario=scenario
    )


# ── Progress store loading ───────────────────────────────────────────────────

class ProgressUnavailable(RuntimeError):
    """Progress store is reachable-failure (transient kubectl error).

    Distinct from the empty-result case (ConfigMap NotFound or empty data),
    which ``ConfigMapProgressStore.load`` returns as ``{}``. Only raised by
    ``_load_progress`` when ``allow_unreachable=True`` so callers can decide
    whether to abort or degrade. See issue #287.
    """


def _load_progress(store, *, allow_unreachable: bool = False,
                   run_name: str = "") -> dict:
    """Load progress data with consistent corrupt/unreachable handling.

    Single entry point for ``store.load()`` across deploy.py subcommands so
    every command surfaces corrupt-data errors with the same UX (issue #140).

    ``run_name`` is folded into the corrupt-data recovery hint when supplied
    so the user sees the concrete ``sim2real assemble --run <name>`` command
    to re-run; callers that do not have it in scope get a ``<run-name>``
    placeholder styled to match the other placeholders in the message.

    On ``ValueError`` (corrupt-data signal from ``ConfigMapProgressStore.load``):
    print a clear error pointing at the affected ConfigMap with recovery
    guidance, then ``sys.exit(1)``. Corrupt data is never recoverable by
    retry, so this applies regardless of ``allow_unreachable``.

    On ``RuntimeError`` (e.g. kubectl cannot reach the cluster): re-raise the
    original ``RuntimeError`` by default, or raise ``ProgressUnavailable`` when
    ``allow_unreachable=True`` so callers can distinguish unreachable from
    legitimate empty progress data (issue #287).
    """
    try:
        return store.load()
    except ValueError as exc:
        err(f"Corrupt progress data: {exc}")
        run_token = run_name if run_name else "<run-name>"
        err(f"Re-assemble the run (sim2real assemble --run {run_token}), or fix "
            f"the ConfigMap manually with `kubectl edit configmap <name> -n <namespace>`.")
        sys.exit(1)
    except RuntimeError as exc:
        if allow_unreachable:
            raise ProgressUnavailable(str(exc)) from exc
        raise


# ── PipelineRun helpers ──────────────────────────────────────────────────────

# Tekton PipelineRun `.status.conditions[0].reason` values that mean the run is
# fully done — no further state transitions, finally tasks (including
# `llmdbenchmark-teardown`) have completed.  `CancelledRunningFinally`,
# `PipelineRunStopping`, and `PipelineRunStoppingTimeout` are explicitly
# NOT terminal: they're transient states where finally is still executing,
# and deleting the PipelineRun while it's in one of those states force-kills
# the in-flight teardown and orphans its helm releases (issue #412).
_TERMINAL_PR_REASONS = frozenset({
    "Cancelled",
    "PipelineRunCancelled",        # older Tekton versions
    "Succeeded",
    "Completed",
    "Failed",
    "PipelineRunTimeout",
    "CreateRunFailed",
    "Unknown",                     # PR not found → also terminal for our wait
})


def _cancel_and_delete_pipelinerun(pr_name: str, namespace: str) -> bool:
    """If a PipelineRun with the given name exists, cancel it, wait for it to
    finish cancelling, then delete it so a fresh one can be submitted.

    Returns True if the PipelineRun was successfully deleted (or didn't exist).
    Returns False if it could not be removed — caller should NOT free the slot.
    The cancel-patch step is best-effort: if the patch fails the function still
    attempts the delete, and the return value reflects whether delete succeeded.
    """
    exists = run(
        ["kubectl", "get", "pipelinerun", pr_name, "-n", namespace],
        check=False, capture=True,
    )
    if exists.returncode != 0:
        stderr = exists.stderr.strip() if exists.stderr else ""
        if "NotFound" in stderr or "not found" in stderr:
            return True  # doesn't exist, nothing to cancel
        warn(f"Cannot reach PipelineRun {pr_name!r} in {namespace}"
             + (f": {stderr}" if stderr else "") + " — assuming still active")
        return False

    status = _check_pipelinerun_status(pr_name, namespace)
    info(f"Existing PipelineRun {pr_name!r} found (status: {status}); cancelling …")

    if status in ("Running", "Started"):
        patch_result = run(
            ["kubectl", "patch", "pipelinerun", pr_name,
             "--type=merge", "-p", '{"spec":{"status":"CancelledRunFinally"}}',
             "-n", namespace],
            check=False, capture=True,
        )
        if patch_result.returncode != 0:
            detail = patch_result.stderr.strip() if patch_result.stderr else ""
            warn(f"Failed to patch PipelineRun {pr_name!r} for cancellation"
                 + (f": {detail}" if detail else ""))
        else:
            # Wait for the PipelineRun to reach a TERMINAL reason — not just
            # "anything other than Running".  `CancelledRunningFinally` is the
            # transient state where the finally block (which runs helm uninstall
            # via llmdbenchmark-teardown) is still executing; breaking on it and
            # deleting the PR kills the teardown mid-flight.  See issue #412.
            for _ in range(40):  # wait up to 120 s
                time.sleep(3)
                current = _check_pipelinerun_status(pr_name, namespace)
                if current in _TERMINAL_PR_REASONS:
                    info(f"PipelineRun {pr_name!r} cancelled (now: {current})")
                    break
            else:
                warn(f"PipelineRun {pr_name!r} did not cancel within 120 s; deleting anyway")

    result = run(
        ["kubectl", "delete", "pipelinerun", pr_name, "-n", namespace,
         "--ignore-not-found"],
        check=False, capture=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() if result.stderr else ""
        warn(f"Failed to delete PipelineRun {pr_name!r} in {namespace}"
             + (f": {detail}" if detail else ""))
        return False
    return True


def _delete_pipelinerun(pr_name: str, namespace: str) -> None:
    """Delete a completed PipelineRun. Best-effort — warns on failure."""
    result = run(
        ["kubectl", "delete", "pipelinerun", pr_name, "-n", namespace,
         "--ignore-not-found"],
        check=False, capture=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() if result.stderr else ""
        warn(f"Failed to delete PipelineRun {pr_name!r} in {namespace}" +
             (f": {detail}" if detail else ""))


# ── Deploy command ───────────────────────────────────────────────────────────

# ── Status command ───────────────────────────────────────────────────────────

# ── Runtime tracking helpers ─────────────────────────────────────────────────
# Issue #378: progress entries carry `running_since` (ISO-8601 UTC, set on
# dispatch) and `last_duration` (float seconds, set on terminal transitions).
# The two are mutually exclusive: a row that's running has running_since set
# and last_duration None; a row that completed has last_duration set and
# running_since None. Entries that predate this feature are missing both —
# helpers and the status renderer treat that as "—".

def _mark_running(entry: dict) -> None:
    """Stamp ``running_since=now``, clear ``last_duration``. Call on dispatch.

    Pairs with :func:`_finalize_run` (terminal sites) and :func:`_clear_runtime`
    (reset / requeue sites) to maintain the running_since vs. last_duration
    invariant: a running entry has running_since set and last_duration None;
    a terminated entry has the inverse.
    """
    entry["running_since"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
    entry["last_duration"] = None


def _finalize_run(entry: dict) -> None:
    """Record the just-ended attempt's duration on terminal transitions.

    Call when status transitions to done / failed / timed-out / stalled. If
    ``running_since`` is set, computes ``now - running_since`` (seconds) and
    stores it as ``last_duration``; clears ``running_since`` either way. If
    ``running_since`` was not set (pair wasn't running, e.g. transitioning
    from pending → failed), this is a no-op other than ensuring the field
    stays None.
    """
    started = entry.get("running_since")
    if started:
        try:
            start_dt = _dt.datetime.fromisoformat(started)
            now = _dt.datetime.now(_dt.timezone.utc)
            entry["last_duration"] = (now - start_dt).total_seconds()
        except (ValueError, TypeError):
            # Malformed timestamp: just clear without recording.
            pass
    entry["running_since"] = None


def _clear_runtime(entry: dict) -> None:
    """Clear both runtime fields. Call on reset / requeue / pending transitions
    where the previous attempt's duration is being discarded (the next
    dispatch will start fresh)."""
    entry["running_since"] = None
    entry["last_duration"] = None


def _fmt_duration(seconds: "float | None") -> str:
    """Format a duration as a ≤7-char string: 42s / 5m12s / 1h08m / 2d04h.

    Returns "—" for None or negative values (defensive — clock skew, malformed
    data). Width budget keeps the RUNTIME column at 7 chars without re-flow.
    """
    if seconds is None or seconds < 0:
        return "—"
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s//60}m{s%60:02d}s"
    if s < 86400:
        return f"{s//3600}h{(s%3600)//60:02d}m"
    return f"{s//86400}d{(s%86400)//3600:02d}h"


def _runtime_str(entry: dict) -> str:
    """RUNTIME column value for the status table.

    - running: live ``now - running_since``, ticks per watch refresh
    - done / failed / timed-out / stalled: frozen ``last_duration``
    - pending or any state with missing fields: "—"
    """
    status = entry.get("status", "")
    if status == "running":
        started = entry.get("running_since")
        if not started:
            return "—"
        try:
            start_dt = _dt.datetime.fromisoformat(started)
        except (ValueError, TypeError):
            return "—"
        now = _dt.datetime.now(_dt.timezone.utc)
        return _fmt_duration((now - start_dt).total_seconds())
    if status in ("done", "failed", "timed-out", "stalled"):
        return _fmt_duration(entry.get("last_duration"))
    return "—"


def _cmd_status(args, run_dir: Path,
                cluster_config: dict | None = None) -> None:
    """Print a snapshot table of all (workload, package, iteration) statuses."""
    primary_ns = _configmap_namespace(cluster_config)
    if not primary_ns:
        err("No namespace configured. Run cluster.py provision with --namespaces.")
        sys.exit(1)
    store = _make_progress_store(primary_ns, run_dir)
    try:
        progress = _load_progress(store, allow_unreachable=True,
                                  run_name=run_dir.name)
    except ProgressUnavailable as exc:
        err(f"Cluster unreachable — cannot read progress ConfigMap: {exc}")
        err("Retry once kubectl can reach the cluster.")
        sys.exit(1)

    if not progress:
        suffix = " (no progress data)"
        filters_given = any([
            getattr(args, "only", None) is not None,
            getattr(args, "workload", None) is not None,
            getattr(args, "package", None) is not None,
            getattr(args, "status", None) is not None,
            getattr(args, "iteration", None) is not None,
        ])
        if filters_given:
            suffix += " — filters ignored"
        print(f"  0 pairs{suffix}")
        return

    pairs = {k: progress[k] for k in _resolve_scope(progress, args)}
    silent = getattr(args, "silent", False)

    if not pairs:
        print("  0 pairs")
        if not silent:
            print()
        return

    counts: dict[str, int] = {}

    if silent:
        for entry in pairs.values():
            status = entry.get("status", "unknown")
            counts[status] = counts.get(status, 0) + 1
    else:
        pair_w = max(len(k) for k in pairs) + 2
        col_status = 12
        col_slot = 14
        col_runtime = 7

        header = (f"{'PAIR':<{pair_w}} {'STATUS':<{col_status}} {'SLOT':<{col_slot}} {'RUNTIME':<{col_runtime}}")
        print()
        print(header)
        print("-" * len(header))

        for key, entry in sorted(pairs.items()):
            status = entry.get("status", "unknown")
            # `completed_namespace` is meaningful only while status == "done"
            # (set on completion in `_reconcile_on_resume`'s done branch and
            # in the orchestrator's per-cycle drain; cleared on every reset
            # path inside `_reset_pair`). Gate the fallback on status so any
            # leftover stale value on a non-done entry does not leak into
            # the display (issue #366).
            if status == "done":
                slot = entry.get("completed_namespace") or "—"
            else:
                slot = entry.get("namespace") or "—"
            runtime = _runtime_str(entry)
            counts[status] = counts.get(status, 0) + 1
            print(f"{key:<{pair_w}} {status:<{col_status}} {slot:<{col_slot}} {runtime}")

        print()

    summary_parts = [f"{v} {k}" for k, v in sorted(counts.items())]
    print(f"  {len(pairs)} pairs: " + "  ".join(summary_parts))

    if not silent:
        print()


# ── Pairs command ────────────────────────────────────────────────────────────

def _cmd_pairs(cluster_dir: Path, *, keys_only: bool = False,
               workloads_only: bool = False, packages_only: bool = False) -> None:
    """List available pair keys, workloads, and packages from cluster/ YAML files."""
    pairs = _load_pairs(cluster_dir)

    if not pairs:
        if keys_only or workloads_only or packages_only:
            return
        n = len(list(cluster_dir.glob("pipelinerun-*.yaml"))) if cluster_dir.exists() else 0
        if n == 0:
            print("  0 pairs (no pipelinerun-*.yaml files found)")
        else:
            print(f"  0 pairs ({n} files found but failed to parse — see warnings above)")
        return

    if keys_only:
        for key in sorted(pairs):
            print(key)
        return

    if workloads_only:
        workloads = sorted({v["workload"] for v in pairs.values() if v["workload"]})
        for w in workloads:
            print(w)
        return

    if packages_only:
        packages = sorted({v["package"] for v in pairs.values() if v["package"]})
        for p in packages:
            print(p)
        return

    # Default: human-readable table
    pair_w = max(len(k) for k in pairs) + 2
    col_wl = max(len(v["workload"]) for v in pairs.values()) + 2
    col_wl = max(col_wl, 10)

    header = f"{'PAIR':<{pair_w}} {'WORKLOAD':<{col_wl}} PACKAGE"
    print()
    print(header)
    print("-" * len(header))
    for key in sorted(pairs):
        entry = pairs[key]
        print(f"{key:<{pair_w}} {entry['workload']:<{col_wl}} {entry['package']}")
    print()
    print(f"  {len(pairs)} pairs")


# ── Collect command ──────────────────────────────────────────────────────────

def _check_pipelinerun_status(pr_name: str, namespace: str) -> str:
    """Return PipelineRun reason string, or 'Unknown' if not found."""
    result = run([
        "kubectl", "get", "pipelinerun", pr_name,
        "-n", namespace,
        "-o", "jsonpath={.status.conditions[0].reason}",
    ], check=False, capture=True)
    if result.returncode != 0:
        return "Unknown"
    return result.stdout.strip() or "Unknown"



def _handle_pending_pods(*, pr_name: str, namespace: str, entry: dict,
                         pending_threshold: int, max_pending_stalls: int) -> bool:
    """Check for pods stuck in Pending and take action.

    Returns True if the slot was reclaimed (caller should free slot).
    Returns False if no action taken (caller should proceed to timeout check).

    Side effects on *entry* (caller must persist):
      On True (non-recoverable):
        - status: "failed", pending_since: None
        - namespace retained so reset/cleanup can find releases (issue #277)
      On True (recoverable threshold exceeded):
        - status: "pending" (re-dispatch — namespace cleared) or
          "stalled" (terminal — namespace retained, issue #277)
        - pending_stalls: incremented, pending_since: None
      On False (no action):
        - pending_since: set on first recoverable detection, cleared when
          pods start running, reset on malformed timestamp
    """
    from pipeline.lib.pod_pending import parse_pod_conditions

    result = run(
        ["kubectl", "get", "pods", f"-n={namespace}",
         "-l", f"tekton.dev/pipelineRun={pr_name}",
         "-o", "json"],
        check=False, capture=True,
    )
    if result.returncode != 0:
        warn(f"[{entry.get('workload', '?')}] pod query failed: {(result.stdout or result.stderr or '')[:120]}")
        return False

    try:
        pods_json = json.loads(result.stdout)
    except json.JSONDecodeError:
        warn(f"[{entry.get('workload', '?')}] pod query returned invalid JSON: {result.stdout[:120]}")
        return False

    try:
        category, detail = parse_pod_conditions(pods_json)
    except (KeyError, TypeError, AttributeError) as exc:
        warn(f"[{entry.get('workload', '?')}] unexpected pod JSON shape: {exc}")
        return False

    if category is None:
        if entry.get("pending_since") is not None:
            entry["pending_since"] = None
        return False

    if category == "non_recoverable":
        warn(f"[{entry.get('workload', '?')}] non-recoverable pending: {detail}")
        if not _cancel_and_delete_pipelinerun(pr_name, namespace):
            warn(f"[{entry.get('workload', '?')}] could not remove PipelineRun {pr_name!r} in {namespace} — slot NOT freed")
            return False
        entry["status"] = "failed"
        # Retain namespace so reset/cleanup can find the helm releases (issue #277).
        entry["pending_since"] = None
        _finalize_run(entry)
        return True

    # category == "recoverable"
    now = _dt.datetime.now(_dt.timezone.utc)
    if entry.get("pending_since") is None:
        entry["pending_since"] = now.isoformat()
        info(f"[{entry.get('workload', '?')}] pending (recoverable): {detail}")
        return False

    try:
        pending_since = _dt.datetime.fromisoformat(entry["pending_since"])
    except (ValueError, TypeError):
        warn(f"[{entry.get('workload', '?')}] malformed pending_since — resetting timer")
        entry["pending_since"] = now.isoformat()
        return False
    elapsed = (now - pending_since).total_seconds()
    if elapsed <= pending_threshold:
        return False

    warn(f"[{entry.get('workload', '?')}] pending {int(elapsed)}s > {pending_threshold}s threshold → reclaim")
    if not _cancel_and_delete_pipelinerun(pr_name, namespace):
        warn(f"[{entry.get('workload', '?')}] could not remove PipelineRun {pr_name!r} in {namespace} — slot NOT freed")
        return False
    stalls = entry.get("pending_stalls", 0) + 1
    entry["pending_stalls"] = stalls
    entry["pending_since"] = None
    if stalls >= max_pending_stalls:
        entry["status"] = "stalled"
        # Terminal: retain namespace so reset/cleanup can find releases (issue #277).
        warn(f"[{entry.get('workload', '?')}] reached max pending stalls ({max_pending_stalls}) → stalled")
        _finalize_run(entry)
    else:
        entry["status"] = "pending"
        # Slot freed for re-dispatch — release the namespace.
        entry["namespace"] = None
        _clear_runtime(entry)
    return True


def _handle_timeout(*, pr_name: str, namespace: str, entry: dict,
                    timeout_hours: float, max_retries: int) -> bool | None:
    """Check if a PipelineRun has exceeded its timeout and handle accordingly.

    Returns True if the entry was timed out and cleaned up, False if timeout
    was detected but cancel failed (slot left busy), None if not timed out.
    """
    ts_result = run(
        ["kubectl", "get", "pipelinerun", pr_name, f"-n={namespace}",
         "-o", "jsonpath={.metadata.creationTimestamp}"],
        check=False, capture=True,
    )
    if ts_result.returncode != 0 or not ts_result.stdout.strip():
        return None
    try:
        created = _dt.datetime.fromisoformat(
            ts_result.stdout.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    age_h = (_dt.datetime.now(_dt.timezone.utc) - created).total_seconds() / 3600
    if age_h <= timeout_hours:
        return None

    retries = entry.get("retries", 0)
    if not _cancel_and_delete_pipelinerun(pr_name, namespace):
        warn(f"[{entry.get('workload', '?')}] timed out but could not remove "
             f"PipelineRun {pr_name!r} in {namespace} — slot NOT freed")
        return False
    if retries < max_retries:
        warn(f"[{entry.get('workload', '?')}] timed out → requeue "
             f"(attempt {retries + 1}/{max_retries})")
        entry["status"] = "pending"
        entry["retries"] = retries + 1
        # Slot freed for re-dispatch — release the namespace.
        entry["namespace"] = None
        _clear_runtime(entry)
    else:
        warn(f"[{entry.get('workload', '?')}] timed out, max retries → timed-out")
        entry["status"] = "timed-out"
        # Terminal: retain namespace so reset/cleanup can find releases (issue #277).
        _finalize_run(entry)
    entry["pending_since"] = None
    return True


def _check_pod_health(*, namespace: str, pair_key: str,
                      tracker: "RemediationTracker",
                      skip_teardown: bool) -> bool:
    """Check non-Tekton pods in namespace for health issues.

    Returns True if escalation is needed (tier-1 pod deletion failure, or
    tier-2 finding with skip_teardown=False), meaning caller should cancel
    the PipelineRun and reclaim the slot.
    """
    from pipeline.lib.health import (
        get_all_pods, get_events, triage_pod, delete_pod,
    )

    pods = get_all_pods(namespace)
    if not pods:
        return False
    events = get_events(namespace)
    needs_escalation = False

    for pod in pods:
        if pod.phase == "Running" and pod.ready:
            tracker.reset(pod.name)
            continue

        result = triage_pod(pod, events, tracker)
        if result is None:
            continue

        if result.tier == 1:
            success = delete_pod(namespace, pod.name)
            if success:
                tracker.record(pod.name)
                warn(f"[{pair_key}] {result.message}")
            else:
                warn(f"[{pair_key}] {result.message} — delete failed")
                needs_escalation = True
        elif result.tier == 2:
            warn(f"[{pair_key}] {result.message}")
            if result.suggestion:
                info(f"  Suggestion: {result.suggestion}")
            if not skip_teardown:
                needs_escalation = True
        elif result.tier == 3:
            warn(f"[{pair_key}] {result.message}")

    return needs_escalation


def _probe_phase_sizes(pod_name: str, run_name: str, phases: list[str],
                       namespace: str) -> dict[str, int]:
    """Return byte sizes for each phase directory on the PVC."""
    sizes: dict[str, int] = {}
    for phase in phases:
        result = run(
            ["kubectl", "exec", pod_name, f"-n={namespace}", "--",
             "du", "-sb", f"/data/{run_name}/{phase}"],
            check=False, capture=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            sizes[phase] = int(result.stdout.strip().split()[0])
        else:
            sizes[phase] = 0
    return sizes


def _fmt_size(b: int) -> str:
    if b >= 1 << 30:
        return f"{b / (1 << 30):.1f} GB"
    if b >= 1 << 20:
        return f"{b / (1 << 20):.0f} MB"
    return f"{b / (1 << 10):.0f} KB"


def _probe_remote_mtimes(pod_name: str, phase_path: str, namespace: str) -> dict[str, dict[str, float]]:
    """Return ``{workload_name: {iN: mtime_epoch}}`` for iterations that have
    ``trace_data.csv`` on the PVC.

    Uses a single kubectl exec to stat all ``trace_data.csv`` files in the
    phase directory. Traces live under
    ``<phase>/<workload>/i<N>/trace_data.csv`` — one per iteration. This
    function keeps mtimes at iteration granularity so the up-to-date check
    can decide per iteration rather than per workload. Per-workload keying
    was the root of issue #564: when two iterations of the same (phase,
    workload) pair dispatch to different slots, collapsing to a single
    per-workload mtime hid the missing iteration from the up-to-date gate.

    Returns empty dict on probe failure — callers should fall back to full
    copy in that case.
    """
    result = run(
        ["kubectl", "exec", pod_name, f"-n={namespace}", "--", "sh", "-c",
         f"find {phase_path} -name 'trace_data.csv'"
         " -exec stat -c '%Y %n' {} \\;"],
        check=False, capture=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        if result.returncode != 0:
            warn(f"mtime probe failed (rc={result.returncode}): "
                 f"{result.stderr.strip()} — falling back to full copy")
        else:
            info(f"mtime probe: no trace_data.csv found in {phase_path}")
        return {}
    if result.stderr.strip():
        warn(f"mtime probe had errors: {result.stderr.strip()}")
    mtimes: dict[str, dict[str, float]] = {}
    for line in result.stdout.strip().splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2:
            warn(f"mtime probe: unparseable line: {line!r}")
            continue
        try:
            mt = float(parts[0])
        except ValueError:
            warn(f"mtime probe: unparseable line: {line!r}")
            continue
        # Path shape: <phase_path>/<workload>/i<N>/trace_data.csv
        p = Path(parts[1])
        iN = p.parent.name
        wl = p.parent.parent.name
        mtimes.setdefault(wl, {})[iN] = mt
    return mtimes


def _is_up_to_date(local_path: Path, remote_mtime: "float | None") -> bool:
    """Return True if *local_path* exists and its mtime is at least as new as
    *remote_mtime*.

    Used by ``_extract_phase_plans`` to decide whether a specific YAML file
    needs re-downloading. For iteration-level trace-directory checks under
    the ``i<N>/`` layout, use ``_is_iteration_up_to_date`` instead.
    """
    if remote_mtime is None:
        return False
    try:
        return local_path.exists() and local_path.stat().st_mtime >= remote_mtime
    except OSError as exc:
        warn(f"stat failed for {local_path}: {exc} — will re-download")
        return False


def _is_iteration_up_to_date(iN_dir: Path, remote_mtime: "float | None",
                             exclude_subdirs: "frozenset[str]" = frozenset(),
                             ) -> bool:
    """Return True only if *iN_dir* carries a completeness marker that is not
    stale relative to *remote_mtime* and whose claim covers *exclude_subdirs*.

    Before #885 this trusted ``iN_dir/trace_data.csv``'s mtime. That file is
    copied early in the tar stream, so it survived a timeout that truncated the
    rest of the iteration — and the next collect then printed "up to date —
    skipping" over a partial directory, permanently retaining truncated logs.
    Only ``COLLECT_MARKER`` proves the whole inventory landed.

    Keyed per iteration so that in cross-slot collect (issue #564) each
    iteration is decided against its OWN remote mtime, not a per-workload max.
    ``remote_mtime is None`` still means "cannot skip": the iteration is either
    absent from the current slot's PVC or the probe failed. A marker whose own
    ``remote_mtime`` is None was written when the probe had failed; it still
    proves completion, so it is trusted.

    *exclude_subdirs* is the current request's scope. A marker only covers a
    request that leaves out at least as much as the marker did, so a
    ``--skip-logs`` marker never satisfies a later full collect — see
    ``_marker_scope_covers``.

    Iterations collected before #885 have no marker and are therefore not
    skipped. That costs one remote inventory each and zero file transfers,
    because the delta computed against a complete local tree is empty.
    """
    if remote_mtime is None:
        return False
    marker = _read_collect_marker(iN_dir)
    return (marker is not None
            and _marker_scope_covers(marker, exclude_subdirs)
            and _marker_covers(marker, remote_mtime))


def _list_pvc_iterations(
    pod_name: str, run_name: str, phase: str, wl_name: str, namespace: str,
) -> "tuple[list[str], str | None]":
    """List ``i<N>`` iteration subdirs on the current slot's PVC for one workload.

    Returns ``(iN_names, error)``. ``error`` is None on success. On kubectl
    failure or when no ``i<N>/`` subdirs exist, ``iN_names`` is empty and
    ``error`` describes the failure so the caller can surface it.
    """
    ls = run(
        ["kubectl", "exec", pod_name, f"-n={namespace}", "--",
         "sh", "-c", f"ls /data/{run_name}/{phase}/{wl_name}/"],
        check=False, capture=True,
    )
    if ls.returncode != 0:
        return [], f"{wl_name}: failed to list iterations: {ls.stderr.strip()}"
    iN_names = [
        n for n in ls.stdout.strip().split()
        if n.startswith("i") and n[1:].isdigit()
    ]
    if not iN_names:
        return [], f"{wl_name}: no i<N>/ iteration subdirs found on PVC"
    return iN_names, None


# ── Incremental iteration copy (issue #885) ──────────────────────────────────

COLLECT_MARKER = ".collect_complete"
"""Per-iteration completeness marker written by collect (issue #885).

Presence means every file in the remote inventory landed locally at the
matching size. ``trace_data.csv``'s mtime is not evidence of anything beyond
``trace_data.csv`` — it is copied early in the tar stream, so it survives a
timeout that truncates the rest of the iteration.
"""


def _remote_file_inventory(
    pod_name: str, namespace: str, remote_dir: str,
) -> "tuple[dict[str, int], str | None]":
    """Inventory every file under *remote_dir* on the extractor pod.

    Returns ``({relpath: size_bytes}, error)``. ``error`` is None on success;
    an empty dict with ``error=None`` means the directory exists but is empty.

    One ``kubectl exec`` per iteration. BusyBox (alpine:3.19) has no GNU
    ``find -printf``, so this shells out to ``stat -c``. The ``%s|%n`` format
    puts the size first and the path last, so a path containing the delimiter
    still parses via a single split from the left.
    """
    cmd = ["kubectl", "exec", pod_name, f"-n={namespace}", "--", "sh", "-c",
           f"cd {remote_dir} && find . -type f -exec stat -c '%s|%n' {{}} +"]
    try:
        result = run(cmd, check=False, capture=True)
    except subprocess.TimeoutExpired as exc:
        return {}, f"inventory of {remote_dir} timed out after {exc.timeout}s"
    except OSError as exc:
        # `check=False` rules out CalledProcessError, but not the OSError
        # family — an unresolvable kubectl, a fork failure, a transient
        # resource limit. Those propagate exactly like the TimeoutExpired
        # this change exists to contain, so they are contained too.
        return {}, f"inventory of {remote_dir} failed to run: {exc}"
    if result.returncode != 0:
        return {}, f"failed to inventory {remote_dir}: {(result.stderr or '').strip()}"
    inv: dict[str, int] = {}
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        size_str, sep, rel = line.partition("|")
        try:
            if not sep:
                raise ValueError("no size/path delimiter")
            size = int(size_str)
        except ValueError:
            # Fail the whole inventory rather than dropping the entry. A
            # dropped entry can never appear in ``missing``/``short``, so the
            # iteration could be marked complete and skipped forever while that
            # file was never fetched — the same silent data loss #885 was filed
            # about, moved one layer down into the parser.
            return {}, (f"failed to inventory {remote_dir}: unparseable "
                        f"stat line: {line!r}")
        inv[rel.removeprefix("./")] = size
    return inv, None


def _local_file_inventory(local_dir: Path) -> dict[str, int]:
    """Inventory every file under *local_dir*, excluding ``COLLECT_MARKER``.

    Keys are POSIX-style paths relative to *local_dir* so they compare
    directly against ``_remote_file_inventory`` keys.
    """
    inv: dict[str, int] = {}
    if not local_dir.is_dir():
        return inv
    for path in local_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(local_dir).as_posix()
        if rel == COLLECT_MARKER:
            continue
        try:
            inv[rel] = path.stat().st_size
        except OSError as exc:
            warn(f"stat failed for {path}: {exc} — treating as missing")
    return inv


def _inventory_sha256(inventory: dict[str, int]) -> str:
    """SHA-256 over canonically sorted ``<size> <relpath>`` lines."""
    h = hashlib.sha256()
    for rel in sorted(inventory):
        h.update(f"{inventory[rel]} {rel}\n".encode())
    return h.hexdigest()


def _write_collect_marker(iN_dir: Path, inventory: dict[str, int],
                          remote_mtime: "float | None",
                          excluded_subdirs: "frozenset[str]" = frozenset(),
                          ) -> "str | None":
    """Record that *iN_dir* holds every file in *inventory* at the right size.

    Written only after the local tree matches the remote inventory exactly —
    this is the sole evidence ``_is_iteration_up_to_date`` accepts (#885).
    ``remote_mtime`` is stored so a later collect can tell a complete-but-stale
    iteration from a complete-and-current one.

    ``excluded_subdirs`` records what the claim does NOT cover. Without it the
    marker asserts "this iteration is complete" when a ``--skip-logs`` collect
    only ever established "complete apart from ``server_logs``" — and a later
    full collect would honour that and skip the iteration, never fetching
    ``server_logs`` at all. That is the #885 failure class exactly (a proxy
    signal claiming completeness for data that was never transferred), moved
    off ``trace_data.csv`` and onto the logs.

    Returns an error string when the marker could not be written, else None.
    The caller records it rather than raising: the files are already on disk,
    so a failed marker write means "cannot prove complete", not "collect
    failed", and must not abort the rest of the slot.
    """
    payload = {
        "completed_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "file_count": len(inventory),
        "byte_count": sum(inventory.values()),
        "inventory_sha256": _inventory_sha256(inventory),
        "remote_mtime": remote_mtime,
        "excluded_subdirs": sorted(excluded_subdirs),
    }
    try:
        (iN_dir / COLLECT_MARKER).write_text(json.dumps(payload, indent=2) + "\n")
    except OSError as exc:
        return f"could not write {COLLECT_MARKER}: {exc}"
    return None


def _read_collect_marker(iN_dir: Path) -> "dict | None":
    """Return the parsed marker, or None when it is absent or unreadable."""
    try:
        data = json.loads((iN_dir / COLLECT_MARKER).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _marker_scope_covers(marker: dict,
                         exclude_subdirs: "frozenset[str]") -> bool:
    """True when *marker*'s claim is broad enough for this request.

    A marker covers a request only if everything the marker left out is also
    left out now: ``marker_excluded ⊆ requested_excluded``. So a marker written
    by a full collect satisfies a later ``--skip-logs`` one (the data is
    already there), but a ``--skip-logs`` marker never satisfies a later full
    collect — that would skip the iteration and never fetch ``server_logs``,
    which is the sequence the >1 GB prompt actively recommends.

    A marker with no ``excluded_subdirs`` field predates this check, so its
    scope is unknown and it is NOT honoured. That is safe and self-healing:
    the iteration is re-examined once, its delta against a complete local tree
    is empty, and the marker is rewritten with the field.
    """
    recorded = marker.get("excluded_subdirs")
    if not isinstance(recorded, list):
        return False
    return frozenset(recorded) <= frozenset(exclude_subdirs)


def _marker_covers(marker: dict, remote_mtime: float) -> bool:
    """True when *marker* was written against a remote at least as new.

    Shared by the up-to-date gate and the copy's refetch decision so the two
    cannot disagree about what "stale" means. A marker recording
    ``remote_mtime: null`` was written when the mtime probe had failed; it
    still proves the transfer completed, so it is trusted. A recorded value
    that is not a number is a corrupted or hand-edited marker — not trusted.
    """
    recorded = marker.get("remote_mtime")
    if recorded is None:
        return True
    try:
        return float(recorded) >= remote_mtime
    except (TypeError, ValueError):
        return False


@dataclass
class IterationCopy:
    """Outcome of copying one ``i<N>/`` iteration (issue #885).

    ``complete`` is the only thing the marker is written on. The remaining
    fields exist so a partial copy can be reported in terms an operator can
    act on: how much landed, what is missing or short, and whether the
    workload's primary artifact survived.
    """

    label: str = ""
    complete: bool = False
    files_total: int = 0
    bytes_total: int = 0
    files_present: int = 0
    bytes_present: int = 0
    missing: list = field(default_factory=list)
    short: list = field(default_factory=list)     # (relpath, local, remote)
    errors: list = field(default_factory=list)
    verified: bool = False

    @property
    def trace_status(self) -> str:
        """``"ok"``, ``"bad"``, or ``"unknown"`` for ``trace_data.csv``.

        Three states, not two: ``missing`` and ``short`` are populated only by
        the post-copy verification loop, so when that loop never ran — an
        inventory probe that failed outright — both are empty because nothing
        was examined, not because everything was fine. Reporting "safe" there
        would be the same class of false reassurance #885 was filed about, one
        step earlier in the pipeline.
        """
        if not self.verified:
            return "unknown"
        if ("trace_data.csv" in self.missing
                or any(rel == "trace_data.csv" for rel, _l, _r in self.short)):
            return "bad"
        return "ok"


def _human_bytes(n: int) -> str:
    """Format *n* bytes for an operator: ``512 B``, ``39.6 MB``, ``5.0 GB``."""
    if n < 1024:
        return f"{n} B"
    val = float(n)
    for unit in ("KB", "MB", "GB"):
        val /= 1024.0
        if val < 1024.0:
            return f"{val:.1f} {unit}"
    return f"{val / 1024.0:.1f} TB"


def _fmt_list(items: list, limit: int = 5) -> str:
    """Join the first *limit* items, noting how many were elided."""
    shown = ", ".join(str(i) for i in items[:limit])
    extra = len(items) - limit
    return shown + (f" (+{extra} more)" if extra > 0 else "")


def _report_partial(res: IterationCopy, run_name: str, phase: str,
                    wl_name: str) -> None:
    """Warn about an incomplete iteration in terms the operator can act on.

    Issue #885 defect 4: the old message was the raw ``TimeoutExpired`` argv.
    It did not say that data had partially landed, which files were short, that
    ``trace_data.csv`` had survived, or that re-running would resume rather
    than no-op — and all of that is known here. The three things the operator
    needs are: this is partial and not a total loss; the primary artifact is or
    is not safe; and re-running resumes.
    """
    warn(f"{res.label} — PARTIAL COPY")
    print(f"       transferred {res.files_present} of {res.files_total} files "
          f"({_human_bytes(res.bytes_present)} of "
          f"{_human_bytes(res.bytes_total)})")
    if res.short:
        print("       incomplete: " + _fmt_list(
            [f"{rel} (truncated at {local:,} B of {remote:,} B)"
             for rel, local, remote in res.short]))
    if res.missing:
        print(f"       missing:    {_fmt_list(res.missing)}")
    for cause in res.errors:
        print(f"       cause:      {cause}")
    status = res.trace_status
    if status == "ok":
        print("       trace_data.csv is present and complete — "
              "the workload's trace is safe")
    elif status == "bad":
        print("       trace_data.csv is MISSING OR TRUNCATED — "
              "this workload's trace is not usable")
    else:
        print("       trace_data.csv state is UNKNOWN — the iteration could "
              "not be inventoried, so nothing was checked")
    print("       ACTION: re-run to fetch only the missing files:")
    print(f"               python pipeline/deploy.py collect --run {run_name} "
          f"--package {phase} --workload {wl_name}")
    print("       This iteration is NOT marked complete, so the re-run will "
          "resume it rather than skip it.")


def _print_partial_summary(partials: list, run_name: str) -> None:
    """Roll up every partially-copied iteration at the end of a collect.

    A failure in cell 3 of 9 is otherwise lost in the scrollback (#885).
    """
    if not partials:
        return
    print(f"\n  Partial:   {len(partials)} iteration(s) copied incompletely "
          f"and NOT marked complete:")
    for res in partials:
        print(f"    - {res.label}  "
              f"({res.files_present}/{res.files_total} files, "
              f"{_human_bytes(res.bytes_present)} of "
              f"{_human_bytes(res.bytes_total)})")
    print(f"  Re-run `python pipeline/deploy.py collect --run {run_name}` "
          f"to transfer only the missing files.")


def _cp_one(remote: str, dest: Path, label: str, errors: list) -> None:
    """``kubectl cp`` one remote file-or-directory to *dest*.

    Contains :class:`subprocess.TimeoutExpired`, which ``check=False`` does NOT
    suppress — ``lib/proc.run`` passes ``timeout=`` straight to
    ``subprocess.run``, which raises regardless of ``check``. Before #885 that
    exception unwound out of the copy loop, past every remaining iteration and
    workload, to the slot-level handler, so one oversized file aborted an
    entire slot's collect.

    A "no such file" stderr is tolerated: the remote inventory is a snapshot,
    and a file can disappear between inventory and copy. The caller's post-copy
    verification catches anything that genuinely failed to land.

    A *remote* ending in ``/`` is a directory copy, and *dest* is then the
    directory to extract into — create it, as the pre-#885 per-subdirectory
    copies did, rather than relying on ``kubectl cp`` to create the target.
    """
    try:
        if remote.endswith("/"):
            dest.mkdir(parents=True, exist_ok=True)
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        errors.append(f"{label}: could not create destination: {exc}")
        return
    try:
        r = run(["kubectl", "cp", remote, str(dest), "--retries=3"],
                check=False, capture=True)
    except subprocess.TimeoutExpired as exc:
        errors.append(f"{label}: timed out after {exc.timeout}s")
        return
    except OSError as exc:
        # See _remote_file_inventory: `check=False` rules out
        # CalledProcessError but not the OSError family, and an uncaught one
        # here would unwind past every remaining iteration and workload to the
        # slot-level handler — the very propagation this function exists to stop.
        errors.append(f"{label}: failed to run kubectl cp: {exc}")
        return
    if r.returncode != 0:
        stderr = (r.stderr or "").strip()
        if "no such file" not in stderr.lower():
            errors.append(f"{label}: {stderr}")


def _delta(remote_inv: dict[str, int], local_inv: dict[str, int]) -> list[str]:
    """Relpaths present in *remote_inv* but missing or size-mismatched locally."""
    return sorted(rel for rel, size in remote_inv.items()
                  if local_inv.get(rel) != size)


def _prune_absent_locals(iN_dest: Path, local_inv: dict[str, int],
                         remote_all: dict[str, int]) -> list[str]:
    """Delete local files the remote iteration does not have, and return them.

    Before #885 each copy ``rmtree``d the destination, which cleared stale
    files from an earlier collect of the same iteration — and destroyed the
    bytes that had already landed, so a timed-out copy could never converge.
    Pruning gets the first effect without the second: a file the remote still
    holds is never touched, whether or not it has been fetched yet.

    Compared against the UNFILTERED remote inventory, so ``--skip-logs`` still
    clears a stale ``server_logs/`` the remote no longer has, while leaving one
    the remote does have in place rather than deleting data a previous full
    collect fetched.
    """
    removed: list[str] = []
    for rel in sorted(local_inv):
        if rel in remote_all:
            continue
        try:
            (iN_dest / rel).unlink()
        except OSError as exc:
            warn(f"could not remove stale {iN_dest / rel}: {exc}")
            continue
        removed.append(rel)
    # Drop directories the prune emptied, deepest first.
    for path in sorted(iN_dest.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            try:
                path.rmdir()
            except OSError:
                pass
    return removed


def _copy_iteration_incremental(
    pod_name: str, namespace: str, remote_iN: str, iN_dest: Path, *,
    exclude_subdirs: "frozenset[str]" = frozenset(),
    remote_mtime: "float | None" = None,
    label: str = "",
) -> IterationCopy:
    """Copy one ``i<N>/`` iteration to *iN_dest*, transferring only the delta.

    Issue #885: the previous implementation ``rmtree``d *iN_dest* and streamed
    the whole iteration in a single ``kubectl cp`` under ``deploy.run``'s 120 s
    control-plane guard (#694). Measured API-server throughput of ~3.5 MB/s
    capped an iteration at ~420 MB, and because each retry started from zero
    every attempt died at roughly the same place — the operation could not
    converge by repetition.

    This version never wipes the destination. It inventories the remote
    iteration once, diffs by name and size, and copies only what is missing or
    short: per remote top-level entry when nothing has landed yet (the fast
    path), then per individual file for anything still outstanding. A retry
    therefore costs only the delta. ``kubectl cp`` cannot resume mid-file, so a
    truncated file is re-copied whole.

    *exclude_subdirs* drops entries whose first path segment matches — used by
    ``--skip-logs`` to leave ``server_logs`` on the PVC. *label* names the cell
    in log lines; it defaults to the iteration directory's name, which is
    ambiguous across a multi-cell collect, so callers should pass
    ``<phase>/<workload>/<iN>``.

    Returns an :class:`IterationCopy`. Never raises: an inventory failure or a
    per-copy timeout is recorded in ``errors`` so the caller can keep going.
    """
    res = IterationCopy(label=label or iN_dest.name)
    remote_root = remote_iN.rstrip("/")

    remote_all, inv_err = _remote_file_inventory(pod_name, namespace, remote_root)
    if inv_err is not None:
        res.errors.append(inv_err)
        return res
    remote_inv = remote_all
    if exclude_subdirs:
        remote_inv = {rel: size for rel, size in remote_all.items()
                      if rel.split("/", 1)[0] not in exclude_subdirs}
    res.files_total = len(remote_inv)
    res.bytes_total = sum(remote_inv.values())

    try:
        iN_dest.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        res.errors.append(f"could not create {iN_dest}: {exc}")
        return res
    local_inv = _local_file_inventory(iN_dest)

    # Clear files a previous collect of this iteration left behind that the
    # remote no longer has. Never touches a file the remote still holds, so
    # already-landed bytes survive (that is what the old rmtree destroyed).
    pruned = _prune_absent_locals(iN_dest, local_inv, remote_all)
    if pruned:
        info(f"[{res.label}] removed {len(pruned)} stale file(s) absent from "
             f"the PVC: {_fmt_list(pruned, 3)}")
        local_inv = {rel: size for rel, size in local_inv.items()
                     if rel not in set(pruned)}

    # Refetch everything only on positive evidence that the remote moved on
    # past a marker we already hold: sizes can match while contents differ, so
    # the size diff alone would miss an in-place rewrite. Absence of a usable
    # remote mtime is NOT such evidence — a failed mtime probe blocks the skip
    # in ``_is_iteration_up_to_date`` and lands every iteration here, and
    # treating that as "remote moved on" would re-download whole complete
    # iterations in exactly the degradation path this change makes cheap.
    marker = _read_collect_marker(iN_dest)
    remote_advanced = (marker is not None and remote_mtime is not None
                       and not _marker_covers(marker, remote_mtime))
    delta = (sorted(remote_inv) if remote_advanced
             else _delta(remote_inv, local_inv))

    if delta:
        # Pass 1 (bulk): only when there is nothing local to preserve. One cp
        # per remote top-level entry bounds a single transfer to one subtree
        # instead of the whole iteration, while keeping the common case to a
        # handful of calls. Skipped once anything has landed, so a retry never
        # re-streams what it already has.
        if not local_inv:
            roots: list[str] = []
            for rel in delta:
                head = rel.split("/", 1)[0]
                if head not in roots:
                    roots.append(head)
            for head in roots:
                is_dir = any(rel.startswith(f"{head}/") for rel in delta)
                _cp_one(f"{namespace}/{pod_name}:{remote_root}/{head}"
                        + ("/" if is_dir else ""),
                        iN_dest / head, f"{res.label}/{head}", res.errors)
            delta = _delta(remote_inv, _local_file_inventory(iN_dest))

        # Pass 2 (per file): the granularity #885 specifies. Cost is
        # proportional to the delta, not to the tree.
        for rel in delta:
            _cp_one(f"{namespace}/{pod_name}:{remote_root}/{rel}",
                    iN_dest / rel, f"{res.label}/{rel}", res.errors)

    final_local = _local_file_inventory(iN_dest)
    for rel, size in sorted(remote_inv.items()):
        have = final_local.get(rel)
        if have is None:
            res.missing.append(rel)
        elif have != size:
            res.short.append((rel, have, size))
        else:
            res.files_present += 1
            res.bytes_present += size

    res.verified = True
    res.complete = not res.missing and not res.short
    if res.complete:
        # The marker records what this collect did NOT look at, so a later
        # broader collect cannot mistake a --skip-logs claim for a full one.
        marker_err = _write_collect_marker(
            iN_dest, remote_inv, remote_mtime, exclude_subdirs)
        if marker_err is not None:
            # Every file landed, so this is not a copy failure — but without
            # the marker completeness is unprovable, and the next collect must
            # re-examine rather than skip. Report it and drop the claim.
            res.errors.append(marker_err)
            res.complete = False
    return res


def _copy_workload_iterations_full(
    pod_name: str, run_name: str, phase: str, wl_name: str, namespace: str,
    wl_dest: Path, wl_remote_mtimes: dict[str, float],
    partials: "list | None" = None,
    exclude_subdirs: "frozenset[str]" = frozenset(),
    redact_resources: bool = False,
) -> list[str]:
    """Enumerate ``i<N>/`` on the current slot's PVC and copy each iteration
    incrementally to *wl_dest*, respecting per-iteration up-to-date skips.

    The workload directory itself is NEVER wiped — nor, since #885, is any
    ``i<N>/`` dir. Wiping destroyed the bytes that had already landed, which is
    why retrying a timed-out copy could not converge. Copies now transfer only
    the inventory delta (see ``_copy_iteration_incremental``).

    Preserves the issue #564 guarantee: iterations copied from other slots are
    not present in this slot's ``i<N>`` listing and are left untouched.

    *partials* — when a list is passed, one :class:`IterationCopy` per
    incomplete iteration is appended so the caller can print an end-of-collect
    summary. *exclude_subdirs* is forwarded to the copy helper; ``--skip-logs``
    passes ``{"server_logs"}``.

    *redact_resources* runs ``redact_yaml_tree`` over each copied iteration's
    ``resources/``. Only the ``--skip-logs`` caller sets it, which is where the
    call has always lived — the default full-copy path pulls ``resources/``
    unredacted. That asymmetry predates #885 and is tracked separately; this
    flag makes it an explicit argument rather than a difference buried in two
    divergent copy loops.

    Returns a list of error strings; empty on success.
    """
    wl_dest.mkdir(parents=True, exist_ok=True)
    iN_names, ls_error = _list_pvc_iterations(
        pod_name, run_name, phase, wl_name, namespace)
    if ls_error is not None:
        return [ls_error]
    wl_errors: list[str] = []
    for iN in iN_names:
        iN_dest = wl_dest / iN
        remote_mtime = wl_remote_mtimes.get(iN)
        if _is_iteration_up_to_date(iN_dest, remote_mtime, exclude_subdirs):
            info(f"[{phase}/{wl_name}/{iN}] up to date — skipping")
            continue
        res = _copy_iteration_incremental(
            pod_name, namespace,
            f"/data/{run_name}/{phase}/{wl_name}/{iN}", iN_dest,
            exclude_subdirs=exclude_subdirs, remote_mtime=remote_mtime,
            label=f"{phase}/{wl_name}/{iN}")
        if redact_resources and (iN_dest / "resources").is_dir():
            redact_yaml_tree(iN_dest / "resources")
        if res.complete:
            continue
        _report_partial(res, run_name, phase, wl_name)
        if partials is not None:
            partials.append(res)
        wl_errors.extend(f"{wl_name}/{iN}: {e}" for e in res.errors)
        if not res.errors:
            wl_errors.append(
                f"{wl_name}/{iN}: incomplete copy — "
                f"{len(res.missing)} missing, {len(res.short)} short")
    return wl_errors


def _extract_phases_from_pvc(phases: list[str], run_name: str, namespace: str,
                              run_dir: Path,
                              skip_logs: bool = False,
                              workload: "str | None" = None,
                              allowed_workloads: "dict[str, set[str]] | None" = None,
                              on_workload_done=None,
                              partials: "list | None" = None,
                              ) -> dict[str, "Exception | None"]:
    """Extract results for one or more phases from data-pvc using a single pod.

    Data layout on PVC (written by run-workload-blis-observe):
      /data/{runName}/{phase}/{workloadName}/trace_header.yaml
      /data/{runName}/{phase}/{workloadName}/trace_data.csv

    When *workload* is set, only that workload's subdirectory is copied for
    each phase (used by scoped ``collect --only/--workload``).
    When *workload* is None (default), workloads are discovered via ``ls`` and
    copied individually. In both cases, each workload's iterations are
    enumerated on the current slot's PVC and copied one ``i<N>/`` at a time,
    transferring only the inventory delta (issue #885). Iterations carrying a
    current ``.collect_complete`` marker are skipped, and iterations belonging
    to other slots (not present on this slot's PVC) are left untouched on local
    disk (issue #564).

    When *allowed_workloads* is set (a dict mapping phase name to a set of
    workload names), the ``ls``-discovered list for each phase is filtered to
    only include workloads in that phase's set. Used by the parallel/sequential
    callers to scope each slot to the exact (phase, workload) pairs that
    progress assigns to it.

    When *on_workload_done* is set, it is called after each workload completes
    (success or failure) with ``(phase, workload_name, namespace, error)``.
    *error* is None on success, or an Exception on failure. Used by callers to
    report per-workload progress in real time during extraction.

    When *skip_logs* is True, only trace files are copied (skipping vLLM and
    EPP log files which typically account for the bulk of the data).

    When *partials* is a list, one ``IterationCopy`` record per incomplete
    iteration is appended to it so the caller can print an end-of-collect
    summary of every cell left partial (issue #885).

    Returns a dict mapping phase -> None (success) or Exception (failure).
    """
    pod_name = "sim2real-extract"

    # Clean up any leftover pod from a prior failed attempt
    run(["kubectl", "delete", "pod", pod_name, "-n", namespace,
         "--ignore-not-found", "--force", "--grace-period=0"],
        check=False, capture=True)

    overrides = json.dumps({
        "spec": {
            "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": "data-pvc"}}],
            "containers": [{
                "name": "e", "image": "alpine:3.19",
                "command": ["sleep", "3600"],
                "volumeMounts": [{"name": "data", "mountPath": "/data"}],
            }],
            "restartPolicy": "Never",
        }
    })
    create_result = run(
        ["kubectl", "run", pod_name, "--image=alpine:3.19", "--restart=Never",
         "--overrides", overrides, "-n", namespace],
        check=False, capture=True,
    )
    if create_result.returncode != 0:
        run(["kubectl", "delete", "pod", pod_name, "-n", namespace,
             "--ignore-not-found", "--force", "--grace-period=0"],
            check=False, capture=True)
        raise RuntimeError(
            f"Extractor pod {pod_name} create failed: {create_result.stderr.strip()}")

    result = run(
        ["kubectl", "wait", f"pod/{pod_name}", "--for=condition=Ready",
         "--timeout=60s", f"-n={namespace}"],
        check=False, capture=True,
    )
    if result.returncode != 0:
        run(["kubectl", "delete", "pod", pod_name, "-n", namespace,
             "--ignore-not-found", "--force", "--grace-period=0"],
            check=False, capture=True)
        raise RuntimeError(f"Extractor pod {pod_name} not ready: {result.stderr.strip()}")

    errors: dict[str, "Exception | None"] = {}
    try:
        # ── Size probe ──────────────────────────────────────────────────
        sizes = _probe_phase_sizes(pod_name, run_name, phases, namespace)
        total = sum(sizes.values())

        if total > 1 << 30:  # > 1 GB
            breakdown = ", ".join(f"{p}: {_fmt_size(s)}" for p, s in sizes.items())
            warn(f"Total data size: {_fmt_size(total)} ({breakdown})")
            if not skip_logs:
                print("        Logs make up most of the size. "
                      "Re-run with --skip-logs to collect traces only.")
                answer = input("        Continue with full download? [y/N] ").strip().lower()
                if answer != "y":
                    info("Aborted. Re-run with --skip-logs to collect traces only.")
                    return errors
            else:
                info("--skip-logs: collecting traces only")

        # ── Copy ────────────────────────────────────────────────────────
        for phase in phases:
            dest_dir = run_dir / "results" / phase
            dest_dir.mkdir(parents=True, exist_ok=True)

            remote_mtimes = _probe_remote_mtimes(
                pod_name, f"/data/{run_name}/{phase}", namespace)

            if skip_logs:
                # Selective copy: trace files + epp_logs via kubectl cp per
                # iteration; skips vLLM server_logs which dominate data volume.
                # BusyBox tar doesn't handle large streaming well, so use cp.
                if workload:
                    wl_names = [workload]
                else:
                    list_result = run(
                        ["kubectl", "exec", pod_name, f"-n={namespace}", "--",
                         "sh", "-c",
                         f"ls /data/{run_name}/{phase}/"],
                        check=False, capture=True,
                    )
                    if list_result.returncode != 0:
                        ls_err = RuntimeError(
                            f"failed to list workloads: {list_result.stderr.strip()}")
                        errors[phase] = ls_err
                        if on_workload_done and allowed_workloads is not None:
                            for wl in allowed_workloads.get(phase, set()):
                                on_workload_done(phase, wl, namespace, ls_err)
                        continue
                    wl_names = list_result.stdout.strip().split() if list_result.stdout.strip() else []
                    if allowed_workloads is not None:
                        phase_allowed = allowed_workloads.get(phase, set())
                        wl_names = [w for w in wl_names if w in phase_allowed]
                phase_errors = []
                for wl_name in wl_names:
                    wl_remote_mtimes = remote_mtimes.get(wl_name, {})
                    wl_dest = dest_dir / wl_name
                    # --skip-logs leaves vLLM server_logs on the PVC; they
                    # dominate data volume. Everything else is collected.
                    # (Issue #883 tracks the stale docstring claim that EPP
                    # logs are skipped too — behavior here is unchanged.)
                    wl_errors = _copy_workload_iterations_full(
                        pod_name, run_name, phase, wl_name, namespace,
                        wl_dest, wl_remote_mtimes, partials=partials,
                        exclude_subdirs=frozenset({"server_logs"}),
                        redact_resources=True)
                    if wl_errors:
                        phase_errors.extend(wl_errors)
                    if on_workload_done:
                        wl_exc = RuntimeError("; ".join(wl_errors)) if wl_errors else None
                        on_workload_done(phase, wl_name, namespace, wl_exc)
                if phase_errors:
                    errors[phase] = RuntimeError("; ".join(phase_errors))
                else:
                    errors[phase] = None
            elif workload:
                wl_remote_mtimes = remote_mtimes.get(workload, {})
                wl_dest = dest_dir / workload
                wl_errors = _copy_workload_iterations_full(
                    pod_name, run_name, phase, workload, namespace,
                    wl_dest, wl_remote_mtimes, partials=partials)
                wl_exc = RuntimeError("; ".join(wl_errors)) if wl_errors else None
                errors[phase] = wl_exc
                if on_workload_done:
                    on_workload_done(phase, workload, namespace, wl_exc)
            else:
                # Unscoped full copy: discover workloads via ls, then copy
                # each iteration individually (issue #564 — never wipe the
                # whole workload dir; other slots may hold iterations under it).
                list_result = run(
                    ["kubectl", "exec", pod_name, f"-n={namespace}", "--",
                     "sh", "-c",
                     f"ls /data/{run_name}/{phase}/"],
                    check=False, capture=True,
                )
                if list_result.returncode != 0:
                    ls_err = RuntimeError(
                        f"failed to list workloads: {list_result.stderr.strip()}")
                    errors[phase] = ls_err
                    if on_workload_done and allowed_workloads is not None:
                        for wl in allowed_workloads.get(phase, set()):
                            on_workload_done(phase, wl, namespace, ls_err)
                    continue
                wl_names = list_result.stdout.strip().split() if list_result.stdout.strip() else []
                if allowed_workloads is not None:
                    phase_allowed = allowed_workloads.get(phase, set())
                    wl_names = [w for w in wl_names if w in phase_allowed]
                if not wl_names:
                    errors[phase] = None
                    continue
                phase_errors = []
                for wl_name in wl_names:
                    wl_remote_mtimes = remote_mtimes.get(wl_name, {})
                    wl_dest = dest_dir / wl_name
                    wl_errors = _copy_workload_iterations_full(
                        pod_name, run_name, phase, wl_name, namespace,
                        wl_dest, wl_remote_mtimes, partials=partials)
                    if wl_errors:
                        phase_errors.extend(wl_errors)
                    wl_exc = RuntimeError("; ".join(wl_errors)) if wl_errors else None
                    if on_workload_done:
                        on_workload_done(phase, wl_name, namespace, wl_exc)
                errors[phase] = RuntimeError("; ".join(phase_errors)) if phase_errors else None

            # Pull resolved llm-d-benchmark plan YAMLs alongside trace data.
            # Best-effort and non-fatal; multi-slot dedup via mtime skip.
            try:
                _extract_phase_plans(pod_name, run_name, phase, namespace, run_dir)
            except Exception as exc:
                warn(f"[{phase}/plans] extraction failed: {exc}")
    finally:
        run(["kubectl", "delete", "pod", pod_name, "-n", namespace,
             "--ignore-not-found", "--force", "--grace-period=0"],
            check=False, capture=True)

    return errors


def _extract_phase_plans(pod_name: str, run_name: str, phase: str,
                         namespace: str, run_dir: Path) -> None:
    """Copy resolved llm-d-benchmark plan YAMLs for one phase from data-pvc.

    PVC layout:
      /data/{run_name}/plans/{phase}/{workload}/root-<ts>/plan/{flow}/*.yaml

    Workload does not impact system config, so plans are phase-invariant
    across workloads — pick one workload (lex-first) to source the phase's
    plans. Multiple roots correspond to render passes (standup, smoketest,
    teardown) and are expected to be byte-identical; pick the latest by
    lex-sortable name (root-YYYYMMDD-HHMMSS-mmm).

    Destination: <run_dir>/results/{phase}/plans/{flow}/*.yaml

    Best-effort: errors are warned but never fatal — plan collection must
    not block trace collection.

    Multi-slot dedup: each namespace slot has its own data-pvc with its own
    copy of plans. mtime-based skip means the first slot copies, and
    subsequent slots see local files newer than remote and skip the cp.
    """
    plans_root = f"/data/{run_name}/plans/{phase}"

    # Discover workloads under plans/{phase}/
    ls_wl = run(
        ["kubectl", "exec", pod_name, f"-n={namespace}", "--",
         "sh", "-c", f"ls {plans_root} 2>/dev/null"],
        check=False, capture=True,
    )
    if ls_wl.returncode != 0 or not ls_wl.stdout.strip():
        info(f"[{phase}/plans] no plans directory on PVC — skipping")
        return
    workloads = sorted(ls_wl.stdout.strip().split())
    if not workloads:
        info(f"[{phase}/plans] no workloads under plans dir — skipping")
        return
    workload = workloads[0]
    wl_path = f"{plans_root}/{workload}"

    # Discover roots, pick latest by lex sort
    ls_roots = run(
        ["kubectl", "exec", pod_name, f"-n={namespace}", "--",
         "sh", "-c", f"ls {wl_path} 2>/dev/null"],
        check=False, capture=True,
    )
    if ls_roots.returncode != 0 or not ls_roots.stdout.strip():
        warn(f"[{phase}/plans] no contents under {wl_path} — skipping")
        return
    roots = sorted(
        r for r in ls_roots.stdout.strip().split() if r.startswith("root-")
    )
    if not roots:
        warn(f"[{phase}/plans] no root-* dirs under {wl_path} — skipping")
        return
    latest_root = roots[-1]
    plan_dir = f"{wl_path}/{latest_root}/plan"

    # Discover flow dirs (skip the metadata 'setup' dir)
    ls_flows = run(
        ["kubectl", "exec", pod_name, f"-n={namespace}", "--",
         "sh", "-c",
         f"find {plan_dir} -mindepth 1 -maxdepth 1 -type d 2>/dev/null"],
        check=False, capture=True,
    )
    if ls_flows.returncode != 0 or not ls_flows.stdout.strip():
        warn(f"[{phase}/plans] no flow dirs under {plan_dir} — skipping")
        return
    flows = sorted(
        Path(p.strip()).name
        for p in ls_flows.stdout.strip().splitlines()
        if p.strip() and Path(p.strip()).name != "setup"
    )
    if not flows:
        warn(f"[{phase}/plans] no flow dirs (after filtering) — skipping")
        return

    dest_root = run_dir / "results" / phase / "plans"
    for flow in flows:
        remote_flow = f"{plan_dir}/{flow}"
        stat_result = run(
            ["kubectl", "exec", pod_name, f"-n={namespace}", "--",
             "sh", "-c",
             f"find {remote_flow} -mindepth 1 -maxdepth 1 -name '*.yaml' "
             f"-exec stat -c '%Y %n' {{}} \\; 2>/dev/null"],
            check=False, capture=True,
        )
        if stat_result.returncode != 0 or not stat_result.stdout.strip():
            warn(f"[{phase}/plans/{flow}] no top-level YAMLs found — skipping")
            continue
        flow_dest = dest_root / flow
        flow_dest.mkdir(parents=True, exist_ok=True)
        copied = 0
        skipped = 0
        copy_errors: list[str] = []
        for line in stat_result.stdout.strip().splitlines():
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            try:
                remote_mtime = float(parts[0])
            except ValueError:
                continue
            remote_path = parts[1]
            yaml_name = Path(remote_path).name
            local_path = flow_dest / yaml_name
            if _is_up_to_date(local_path, remote_mtime):
                skipped += 1
                continue
            cp_result = run(
                ["kubectl", "cp",
                 f"{namespace}/{pod_name}:{remote_path}",
                 str(local_path), "--retries=3"],
                check=False, capture=True,
            )
            if cp_result.returncode != 0:
                copy_errors.append(f"{yaml_name}: {cp_result.stderr.strip()}")
            else:
                copied += 1
                redact_yaml_file(local_path)
        if copy_errors:
            warn(f"[{phase}/plans/{flow}] copy errors: {'; '.join(copy_errors)}")
        if copied or skipped:
            info(f"[{phase}/plans/{flow}] copied={copied} skipped={skipped}")


def _cmd_collect(args, run_dir: Path, cluster_config: dict):
    """Pull results from cluster for completed phases."""
    cluster_namespaces = cluster_config.get("namespaces") or []
    namespace = os.environ.get(
        "NAMESPACE", cluster_namespaces[0] if cluster_namespaces else ""
    )
    if not namespace:
        err("No namespace configured.")
        sys.exit(1)

    run_name = run_dir.name

    # Derive known phases from ConfigMap
    primary_ns = _configmap_namespace(cluster_config)
    if not primary_ns:
        err("No namespace configured. Run cluster.py provision with --namespaces.")
        sys.exit(1)
    store = _make_progress_store(primary_ns, run_dir)
    try:
        progress = _load_progress(store, allow_unreachable=True,
                                  run_name=run_dir.name) or None
    except ProgressUnavailable as exc:
        err(f"Cluster unreachable — cannot read progress ConfigMap: {exc}")
        err("Refusing to collect from filesystem-discovered phases without "
            "authoritative progress data. Retry once kubectl can reach the "
            "cluster.")
        sys.exit(1)

    # ── Pair-level scoping (--only / --workload / --package / --iteration) ─
    scope_only = getattr(args, "only", None)
    scope_workload = getattr(args, "workload", None)
    scope_package = _parse_list(getattr(args, "package", None))
    scope_iteration = getattr(args, "iteration", None)
    scoped = (scope_only is not None or scope_workload is not None
              or scope_iteration is not None)

    # One IterationCopy per incompletely-copied iteration, accumulated across
    # every slot so the end-of-collect summary can list them all (issue #885).
    # Appending from the slot threads is safe — list.append is atomic.
    partials: list = []

    if scoped and not progress:
        err("--only/--workload/--iteration require progress data to resolve pairs, but none was found.")
        sys.exit(1)

    if scoped and progress:
        # Build a lightweight args namespace for _resolve_scope. --package
        # acts as a pair-scope filter (cumulative-filter rule) — same as
        # every other deploy.py subcommand — unless the user passed the
        # synthetic 'experiment' value, which doesn't name a pair package
        # and so cannot narrow the pair set. In that case pass None and
        # let the phase-scope logic below expand 'experiment' to every
        # package directory of the scoped pairs.
        _pair_scope_pkg = None if scope_package == ["experiment"] else scope_package

        class _ScopeArgs:
            only = scope_only
            workload = scope_workload
            package = _pair_scope_pkg
            status = None
            iteration = scope_iteration

        in_scope = _resolve_scope(progress, _ScopeArgs())

        # Filter to collectible pairs (done) and warn about the rest
        collectible = {
            k for k in in_scope
            if isinstance(progress[k], dict) and progress[k].get("status") == "done"
        }
        for key in sorted(in_scope - collectible):
            entry = progress[key]
            st = entry.get("status", "") if isinstance(entry, dict) else str(entry)
            warn(f"Scoped pair {key} has status '{st}' — skipping")

        scoped_phases = sorted({
            progress[k].get("package", "") for k in collectible
        } - {""})

        if not scoped_phases:
            warn("No done phases for scoped pairs.")
            phases_to_collect: list[str] = []
        elif (pkg_filter := scope_package):
            valid = set(scoped_phases) | {"experiment"}
            expanded, unknown = _expand_glob_values(
                pkg_filter, valid, exclude_from_pattern={"experiment"})
            if unknown:
                err(f"Unknown packages: {sorted(unknown)}. Valid: {sorted(valid)}")
                sys.exit(1)
            phases_to_collect = []
            for p in expanded:
                if p == "experiment":
                    phases_to_collect.extend(scoped_phases)
                else:
                    phases_to_collect.append(p)
            seen: set[str] = set()
            phases_to_collect = [p for p in phases_to_collect
                                 if not (p in seen or seen.add(p))]  # type: ignore[func-returns-value]
        else:
            phases_to_collect = list(scoped_phases)

        # Group collectible pairs by completed_namespace (same model as unscoped path)
        ns_phase_map: dict[str, list[str]] = {}
        ns_pair_map: dict[str, set[tuple[str, str]]] = {}
        missing_ns_keys: list[str] = []
        for key in collectible:
            entry = progress[key]
            pkg = entry.get("package", "")
            if pkg not in phases_to_collect:
                continue
            ns = entry.get("completed_namespace")
            if not ns:
                missing_ns_keys.append(key)
                continue
            if pkg not in ns_phase_map.setdefault(ns, []):
                ns_phase_map[ns].append(pkg)
            wl = entry.get("workload", "")
            if wl:
                ns_pair_map.setdefault(ns, set()).add((pkg, wl))

        total_pairs = sum(len(pairs) for pairs in ns_pair_map.values())

        ns_items = sorted(ns_phase_map.items())
        if len(ns_items) > 1:
            step(1, f"Collecting Results ({len(ns_items)} slots in parallel)")
        else:
            step(1, "Collecting Results")

        for key in missing_ns_keys:
            warn(f"{key}: completed_namespace missing — skipping (re-run the workload with a newer orchestrator to collect results)")

        collected: list[str] = []
        failed: list[str] = []
        collected_pairs: list[str] = []
        failed_pairs: list[str] = []

        skip_logs = getattr(args, "skip_logs", False)

        def _on_workload_done(phase, wl_name, ns, error):
            if error is None:
                ok(f"{phase}/{wl_name}    ({ns})")
                collected_pairs.append(f"{phase}/{wl_name}")
                if phase not in collected:
                    collected.append(phase)
            else:
                warn(f"{phase}/{wl_name}    ({ns}): {error}")
                failed_pairs.append(f"{phase}/{wl_name}")
                if phase not in failed:
                    failed.append(phase)

        def _handle_slot_failure(ns, pairs_in_ns):
            ns_phases_local = ns_phase_map[ns]
            for p in ns_phases_local:
                if p not in failed:
                    failed.append(p)
                for pkg, wl in sorted(pairs_in_ns):
                    if pkg == p:
                        warn(f"{p}/{wl}    ({ns})")
                        failed_pairs.append(f"{p}/{wl}")

        if len(ns_items) > 1:
            import concurrent.futures

            def _extract_one_slot(ns, ns_phases):
                pairs_in_ns = ns_pair_map.get(ns, set())
                allowed = {}
                for pkg, wl in pairs_in_ns:
                    allowed.setdefault(pkg, set()).add(wl)
                try:
                    _extract_phases_from_pvc(
                        sorted(ns_phases), run_name, ns, run_dir,
                        skip_logs=skip_logs,
                        allowed_workloads=allowed,
                        on_workload_done=_on_workload_done,
                        partials=partials)
                except Exception as e:
                    return (ns, pairs_in_ns, e)
                return (ns, pairs_in_ns, None)

            with concurrent.futures.ThreadPoolExecutor(max_workers=len(ns_items)) as executor:
                futures = {
                    executor.submit(_extract_one_slot, ns, ns_phases): ns
                    for ns, ns_phases in ns_items
                }
                for future in concurrent.futures.as_completed(futures):
                    try:
                        ns, pairs_in_ns, result = future.result()
                    except Exception as e:
                        ns = futures[future]
                        pairs_in_ns = ns_pair_map.get(ns, set())
                        result = e
                    if isinstance(result, Exception):
                        warn(f"Extractor pod failed in {ns}: {result}")
                        _handle_slot_failure(ns, pairs_in_ns)
        else:
            for ns, ns_phases in ns_items:
                pairs_in_ns = ns_pair_map.get(ns, set())
                allowed = {}
                for pkg, wl in pairs_in_ns:
                    allowed.setdefault(pkg, set()).add(wl)
                try:
                    _extract_phases_from_pvc(
                        sorted(ns_phases), run_name, ns, run_dir,
                        skip_logs=skip_logs,
                        allowed_workloads=allowed,
                        on_workload_done=_on_workload_done,
                        partials=partials)
                except Exception as e:
                    warn(f"Extractor pod failed in {ns}: {e}")
                    _handle_slot_failure(ns, pairs_in_ns)

        collected = [p for p in collected if p not in failed]
    else:
        # ── Unscoped path (no --only/--workload) ─────────────────────────
        if progress:
            known_phases = sorted({
                entry.get("package", "")
                for entry in progress.values()
                if isinstance(entry, dict) and entry.get("status") == "done"
            } - {""})
        else:
            known_phases = []

        if not known_phases:
            cluster_dir = run_dir / "cluster"
            known_phases = _discover_phases(cluster_dir)
            if progress is None:
                warn(f"No progress data found — discovered phases from cluster/: {known_phases}")
            else:
                warn(f"No done phases in progress — discovered from cluster/: {known_phases}")

        pkg_filter = _parse_list(args.package)
        if pkg_filter:
            valid = set(known_phases) | {"experiment"}
            expanded, unknown = _expand_glob_values(
                pkg_filter, valid, exclude_from_pattern={"experiment"})
            if unknown:
                err(f"Unknown packages: {sorted(unknown)}. Valid: {sorted(valid)}")
                sys.exit(1)
            phases_to_collect = []
            for p in expanded:
                if p == "experiment":
                    phases_to_collect.extend(known_phases)
                else:
                    phases_to_collect.append(p)
            seen = set()
            phases_to_collect = [p for p in phases_to_collect
                                 if not (p in seen or seen.add(p))]  # type: ignore[func-returns-value]
        else:
            phases_to_collect = list(known_phases)

        collected = []
        failed = []
        collected_pairs: list[str] = []
        failed_pairs: list[str] = []

        if phases_to_collect:
            skip_logs = getattr(args, "skip_logs", False)
            if progress:
                # Group done entries by completed_namespace.
                # Entries without completed_namespace were written by an older
                # version of the orchestrator that did not record it.
                ns_phase_map: dict[str, list[str]] = {}
                ns_pair_map: dict[str, set[tuple[str, str]]] = {}
                missing_ns_keys: list[str] = []
                for key, pentry in progress.items():
                    if not isinstance(pentry, dict):
                        continue
                    if pentry.get("status") != "done":
                        continue
                    pkg = pentry.get("package", "")
                    if pkg not in phases_to_collect:
                        continue
                    ns = pentry.get("completed_namespace")
                    if not ns:
                        missing_ns_keys.append(key)
                        continue
                    if pkg not in ns_phase_map.setdefault(ns, []):
                        ns_phase_map[ns].append(pkg)
                    wl = pentry.get("workload", "")
                    if wl:
                        ns_pair_map.setdefault(ns, set()).add((pkg, wl))

                total_pairs = sum(
                    1 for pentry in progress.values()
                    if isinstance(pentry, dict)
                    and pentry.get("status") == "done"
                    and pentry.get("package", "") in phases_to_collect
                    and pentry.get("completed_namespace")
                )

                ns_items = sorted(ns_phase_map.items())
                if len(ns_items) > 1:
                    step(1, f"Collecting Results ({len(ns_items)} slots in parallel)")
                else:
                    step(1, "Collecting Results")

                for key in missing_ns_keys:
                    warn(f"{key}: completed_namespace missing — skipping (re-run the workload with a newer orchestrator to collect results)")

                def _on_workload_done(phase, wl_name, ns, error):
                    """Report per-workload progress as it happens."""
                    if error is None:
                        ok(f"{phase}/{wl_name}    ({ns})")
                        collected_pairs.append(f"{phase}/{wl_name}")
                        if phase not in collected:
                            collected.append(phase)
                    else:
                        warn(f"{phase}/{wl_name}    ({ns}): {error}")
                        failed_pairs.append(f"{phase}/{wl_name}")
                        if phase not in failed:
                            failed.append(phase)

                def _handle_slot_failure(ns, pairs_in_ns):
                    """Handle pod-level failure where callback never fired."""
                    ns_phases_local = ns_phase_map[ns]
                    for p in ns_phases_local:
                        if p not in failed:
                            failed.append(p)
                        for pkg, wl in sorted(pairs_in_ns):
                            if pkg == p:
                                warn(f"{p}/{wl}    ({ns})")
                                failed_pairs.append(f"{p}/{wl}")

                if len(ns_items) > 1:
                    import concurrent.futures

                    def _extract_one_slot(ns, ns_phases):
                        pairs_in_ns = ns_pair_map.get(ns, set())
                        allowed = {}
                        for pkg, wl in pairs_in_ns:
                            allowed.setdefault(pkg, set()).add(wl)
                        try:
                            _extract_phases_from_pvc(
                                sorted(ns_phases), run_name, ns, run_dir,
                                skip_logs=skip_logs,
                                allowed_workloads=allowed,
                                on_workload_done=_on_workload_done,
                                partials=partials)
                        except Exception as e:
                            return (ns, pairs_in_ns, e)
                        return (ns, pairs_in_ns, None)

                    with concurrent.futures.ThreadPoolExecutor(max_workers=len(ns_items)) as executor:
                        futures = {
                            executor.submit(_extract_one_slot, ns, ns_phases): ns
                            for ns, ns_phases in ns_items
                        }
                        for future in concurrent.futures.as_completed(futures):
                            try:
                                ns, pairs_in_ns, result = future.result()
                            except Exception as e:
                                ns = futures[future]
                                pairs_in_ns = ns_pair_map.get(ns, set())
                                result = e
                            if isinstance(result, Exception):
                                warn(f"Extractor pod failed in {ns}: {result}")
                                _handle_slot_failure(ns, pairs_in_ns)
                else:
                    for ns, ns_phases in ns_items:
                        pairs_in_ns = ns_pair_map.get(ns, set())
                        allowed = {}
                        for pkg, wl in pairs_in_ns:
                            allowed.setdefault(pkg, set()).add(wl)
                        try:
                            _extract_phases_from_pvc(
                                sorted(ns_phases), run_name, ns, run_dir,
                                skip_logs=skip_logs,
                                allowed_workloads=allowed,
                                on_workload_done=_on_workload_done,
                                partials=partials)
                        except Exception as e:
                            warn(f"Extractor pod failed in {ns}: {e}")
                            _handle_slot_failure(ns, pairs_in_ns)
            else:
                # No progress data — fallback to primary namespace.
                step(1, "Collecting Results")
                total_pairs = len(phases_to_collect)
                try:
                    errors = _extract_phases_from_pvc(
                        phases_to_collect, run_name, namespace, run_dir,
                        skip_logs=skip_logs, partials=partials)
                # Not just RuntimeError: anything surviving the copy layer must
                # degrade to a reported slot failure rather than crash the whole
                # collect with no summary and no per-iteration report.
                except Exception as e:
                    warn(f"Extractor pod failed: {e}")
                    failed.extend(phases_to_collect)
                    failed_pairs.extend(phases_to_collect)
                else:
                    for phase, exc in errors.items():
                        if exc is None:
                            ok(f"{phase}    ({namespace})")
                            collected.append(phase)
                            collected_pairs.append(phase)
                        else:
                            warn(f"{phase}    ({namespace}): {exc}")
                            failed.append(phase)
                            failed_pairs.append(phase)
        else:
            total_pairs = 0

    # Print summary
    print(f"\n  Collected: {len(collected_pairs)}/{total_pairs} pairs")
    if failed_pairs:
        print(f"  Failed:    {len(failed_pairs)} pairs")
    _print_partial_summary(partials, run_name)
    if collected_pairs:
        print(f"  Results:   {run_dir / 'results'}/")
        print("\n  Next:      /sim2real-analyze")
    print()


# ── Run helpers ─────────────────────────────────────────────────────────────

def _load_pairs(cluster_dir: Path) -> dict:
    """Discover all (workload, package, iteration) pairs from pipelinerun-*.yaml at cluster/ root.

    Returns dict keyed by "wl-" + filename stem (minus "pipelinerun-" prefix).
    Thin wrapper over ``_load_pairs_with_errors``; the malformed-key count is
    discarded here. Callers that need the count should call
    ``_load_pairs_with_errors`` directly.
    """
    pairs, _ = _load_pairs_with_errors(cluster_dir)
    return pairs


def _load_pairs_with_errors(cluster_dir: Path) -> tuple[dict, int]:
    """Discover pairs and report how many keys did not match the new grammar.

    Returns ``(pairs, malformed_count)``. ``pairs`` has the same dict shape as
    ``_load_pairs`` returns. ``malformed_count`` counts pair keys whose
    filename-derived string does not parse under the canonical grammar
    (see ``pipeline/lib/pairkey.py``).

    Entries whose key parses gain an ``iteration`` field (int, >= 1).
    Legacy keys without an ``|iN`` suffix parse as ``iteration=1``.

    Deviation from the design's literal loader-layer policy (drop with
    WARN): during the step-5 rollout, PR 2 (``assemble`` filename
    reshape) has not yet landed, so on-disk filenames still produce
    keys that fail the new grammar (e.g. ``wl-smoke-baseline``
    from ``pipelinerun-smoke-baseline.yaml``). Dropping them here would
    break every downstream command until PR 2 lands and existing runs
    are re-assembled. Instead this loader keeps the entry (without an
    ``iteration`` field) and reports the count so operators — and, in a
    later PR, ``_cmd_status`` — can surface it. Once PR 2 lands and
    runs are re-assembled, ``malformed_count`` should trend to zero and
    the WARN/drop semantics can be tightened without behavior change.
    """
    pairs: dict = {}
    malformed_count = 0
    if not cluster_dir.exists():
        return pairs, malformed_count
    for pr_path in sorted(cluster_dir.glob("pipelinerun-*.yaml")):
        try:
            pr_data = yaml.safe_load(pr_path.read_text())
            pr_name = pr_data.get("metadata", {}).get("name", pr_path.stem)
            params = {p["name"]: p["value"] for p in pr_data.get("spec", {}).get("params", [])}
            workload = params.get("workloadName", "")
            package = params.get("phase", "")
            key = "wl-" + pr_path.stem.removeprefix("pipelinerun-")
            entry: dict = {
                "workload": workload,
                "package": package,
                "pr_name": pr_name,
                "pr_path": str(pr_path),
                "namespace": pr_data.get("metadata", {}).get("namespace", ""),
                "scenario_content": params.get("scenarioContent"),
            }
            try:
                parts = parse_pair_key(key)
            except ValueError:
                malformed_count += 1
            else:
                entry["iteration"] = parts.iteration
            pairs[key] = entry
        except Exception as e:
            warn(f"Skipping {pr_path.name}: {e}")
            continue
    return pairs, malformed_count


def _uninstall_orphaned_helm(key: str, namespace: str) -> None:
    """Check a namespace for lingering helm releases and uninstall them."""
    result = run(["helm", "list", "-n", namespace, "-q"],
                 check=False, capture=True)
    if result.returncode != 0:
        warn(f"{key}: helm list failed in {namespace}")
    elif result.stdout.strip():
        for release in result.stdout.strip().splitlines():
            ur = run(["helm", "uninstall", release, "-n", namespace],
                     check=False, capture=True)
            if ur.returncode == 0:
                ok(f"Uninstalled: {release} (ns: {namespace})")
            else:
                warn(f"Failed to uninstall {release} in {namespace}")


def _sweep_orphaned_httproutes(key: str, namespace: str) -> None:
    """Delete HTTPRoutes whose backend InferencePool no longer exists (issue #603).

    Model HTTPRoutes are rendered by llm-d-benchmark's 08_httproute template and
    applied directly to the cluster — they carry no ``meta.helm.sh/release-name``
    annotation and no ownerReference to their InferencePool. So when a model is
    torn down, ``helm uninstall`` cannot remove them (helm does not own them) and
    Kubernetes garbage collection cannot either (no ownerReference to the deleted
    InferencePool). The route is stranded on the shared gateway with a catch-all
    '/'; Gateway API resolves '/'-vs-'/' by oldest-wins, so an accumulated stale
    route eventually steals traffic and returns 500 (breaks the next standup's
    smoketest health check).

    We sweep them after helm uninstall, keyed on a DANGLING InferencePool
    backendRef so a route whose backend is still live (e.g. a concurrent pair in
    the same namespace) is never touched. If the InferencePool API is unavailable
    we no-op rather than risk over-deleting.
    """
    pools = run(["kubectl", "get", "inferencepool", "-n", namespace,
                 "-o", "jsonpath={.items[*].metadata.name}"], check=False, capture=True)
    if pools.returncode != 0:
        # CRD/API unavailable: cannot tell live from dead — do nothing, but warn.
        # Silently no-op'ing here would let the exact orphaned-route leak this
        # sweep exists to fix recur with zero operator signal (issue #603).
        warn(f"{key}: cannot list InferencePools in {namespace} — skipping "
             f"HTTPRoute sweep (orphaned routes may remain)")
        return
    live = set(pools.stdout.split())

    got = run(["kubectl", "get", "httproute", "-n", namespace, "-o", "json"],
              check=False, capture=True)
    if got.returncode != 0:
        warn(f"{key}: cannot list HTTPRoutes in {namespace} — skipping sweep "
             f"(orphaned routes may remain)")
        return
    try:
        routes = json.loads(got.stdout).get("items", [])
    except (ValueError, TypeError):
        warn(f"{key}: unparseable HTTPRoute list in {namespace} — skipping sweep "
             f"(orphaned routes may remain)")
        return

    for r in routes:
        name = (r.get("metadata") or {}).get("name", "")
        if not name:
            continue
        pool_backends = [
            b.get("name")
            for rule in (r.get("spec") or {}).get("rules", []) or []
            for b in (rule.get("backendRefs") or []) or []
            if b.get("kind") == "InferencePool"
        ]
        # Only sweep routes that reference at least one InferencePool AND have no
        # live one — i.e. genuinely dangling. Routes to a live pool, or to plain
        # Services (no InferencePool backend), are left untouched.
        if pool_backends and all(b not in live for b in pool_backends):
            dr = run(["kubectl", "delete", "httproute", name, "-n", namespace,
                      "--ignore-not-found"], check=False, capture=True)
            if dr.returncode == 0:
                ok(f"Swept orphaned HTTPRoute {name} (dead InferencePool backend) in {namespace}")
            else:
                warn(f"{key}: failed to sweep orphaned HTTPRoute {name} in "
                     f"{namespace}: {dr.stderr.strip()}")


def _reset_pair(key: str, entry: dict, discovered: dict, *,
                dry_run: bool = False, namespaces: list[str] | None = None,
                preserve_done_status: bool = False) -> bool:
    """Delete PipelineRun and Helm releases for a pair, then reset state to pending.

    For done pairs with preserve_done_status=True: cluster cleanup only,
    status stays done.

    Returns True if reset was performed, False if it failed and state was NOT reset.
    """
    ns = entry.get("namespace")
    pr_name = discovered.get(key, {}).get("pr_name", "")
    is_done = entry.get("status") == "done"

    if not dry_run:
        status = entry.get("status", "unknown")
        slot = ns or "—"
        if not ns and not pr_name:
            action = "state-only reset"
        elif is_done:
            action = "deleting PipelineRun, checking orphaned releases"
        elif ns:
            action = "deleting PipelineRun, uninstalling helm releases"
        else:
            # ns is null but a PipelineRun is known — helm cleanup needs the
            # namespace, so it will be skipped (issue #277). Don't claim it.
            action = "deleting PipelineRun (namespace unknown — skipping helm cleanup)"
        info(f"Resetting {key} (status: {status}, ns: {slot}) — {action}")

    # No namespace and no pr_name — just reset state
    if not ns and not pr_name:
        if is_done:
            completed_ns = entry.get("completed_namespace")
            if completed_ns:
                if dry_run:
                    info(f"[DRY-RUN] {key}: would check for orphaned helm releases in {completed_ns}")
                    info(f"[DRY-RUN] {key}: would sweep orphaned HTTPRoutes in {completed_ns}")
                else:
                    _uninstall_orphaned_helm(key, completed_ns)
                    _sweep_orphaned_httproutes(key, completed_ns)
        elif not dry_run:
            # Terminal pair (callers only reset non-pending pairs) with no
            # namespace recorded — helm cleanup is skipped. Warn rather than
            # silently report success (issue #277).
            warn(f"{key}: namespace unknown — skipped helm cleanup; if releases "
                 f"were installed, remove them manually (helm list/uninstall across slots)")
        if not dry_run and not (is_done and preserve_done_status):
            entry["status"] = "pending"
            entry["retries"] = 0
            entry["pending_stalls"] = 0
            entry["pending_since"] = None
            _clear_runtime(entry)
            # Maintain the invariant: completed_namespace is meaningful only
            # while status == "done". Reset clears it alongside the live slot
            # so subsequent display/diagnostic reads do not surface stale
            # history (issue #366).
            entry["completed_namespace"] = None
        return True

    if dry_run:
        target = ns or "all namespace slots"
        info(f"[DRY-RUN] {key}: would delete pipelinerun {pr_name or '(unknown)'} in {target}")
        if not is_done:
            info(f"[DRY-RUN] {key}: would uninstall all helm releases in {target}")
            info(f"[DRY-RUN] {key}: would sweep orphaned HTTPRoutes in {target}")
        else:
            completed_ns = entry.get("completed_namespace")
            if completed_ns:
                info(f"[DRY-RUN] {key}: would check for orphaned helm releases in {completed_ns}")
                info(f"[DRY-RUN] {key}: would sweep orphaned HTTPRoutes in {completed_ns}")
        return True

    # Delete PipelineRun
    pr_deleted = False
    if not pr_name:
        warn(f"{key}: no PipelineRun name found — skipping PR deletion (manual check needed)")
    elif ns:
        if entry.get("status") == "running":
            pr_deleted = _cancel_and_delete_pipelinerun(pr_name, ns)
        else:
            result = run(["kubectl", "delete", "pipelinerun", pr_name, "-n", ns,
                         "--ignore-not-found"], check=False, capture=True)
            if result.returncode == 0:
                pr_deleted = True
            else:
                warn(f"{key}: kubectl delete pipelinerun failed in {ns}")
    elif namespaces:
        # Namespace already freed — search all slots
        for slot_ns in namespaces:
            result = run(["kubectl", "delete", "pipelinerun", pr_name, "-n", slot_ns,
                         "--ignore-not-found"], check=False, capture=True)
            if result.returncode == 0:
                pr_deleted = True
        if not pr_deleted:
            warn(f"{key}: kubectl delete pipelinerun failed across all namespace slots")
    else:
        warn(f"{key}: no namespace and no namespace slots — cannot delete pipelinerun {pr_name}")

    # For done pairs, Tekton finally task should have torn down Helm releases,
    # but check completed_namespace for orphans in case teardown failed.
    if is_done:
        completed_ns = entry.get("completed_namespace")
        if completed_ns:
            _uninstall_orphaned_helm(key, completed_ns)
            _sweep_orphaned_httproutes(key, completed_ns)
        if not preserve_done_status:
            entry["status"] = "pending"
            entry["namespace"] = None
            entry["retries"] = 0
            entry["pending_stalls"] = 0
            entry["pending_since"] = None
            _clear_runtime(entry)
            # See note above: completed_namespace is only meaningful while
            # status == "done" (issue #366).
            entry["completed_namespace"] = None
        return True

    if ns and not pr_deleted and pr_name:
        warn(f"{key}: PipelineRun not deleted — state NOT reset")
        return False

    # Discover and uninstall all Helm releases in the namespace
    if ns:
        result = run(["helm", "list", "-n", ns, "-q"], check=False, capture=True)
        if result.returncode != 0:
            warn(f"{key}: helm list failed in {ns} — skipping reset (manual intervention needed)")
            return False
        if result.stdout.strip():
            helm_failed = False
            for release in result.stdout.strip().splitlines():
                ur = run(["helm", "uninstall", release, "-n", ns], check=False, capture=True)
                if ur.returncode == 0:
                    ok(f"Uninstalled: {release} (ns: {ns})")
                else:
                    warn(f"Failed to uninstall {release} in {ns}")
                    helm_failed = True
            if helm_failed:
                warn(f"{key}: some releases failed to uninstall — state NOT reset")
                return False
        # Sweep HTTPRoutes orphaned by teardown (issue #603): they are not
        # helm-owned and not GC'd, so helm uninstall above never removes them.
        # Runs even when no live releases remain, to clear debris left by prior
        # runs in this namespace slot.
        _sweep_orphaned_httproutes(key, ns)
    else:
        # PipelineRun was known but no namespace recorded — helm needs the
        # namespace, so cleanup is skipped. Warn rather than silently report
        # success (issue #277).
        warn(f"{key}: namespace unknown — skipped helm cleanup; if releases "
             f"were installed, remove them manually (helm list/uninstall across slots)")

    # Reset state
    entry["status"] = "pending"
    entry["namespace"] = None
    entry["retries"] = 0
    entry["pending_stalls"] = 0
    entry["pending_since"] = None
    _clear_runtime(entry)
    # See note above: completed_namespace is only meaningful while
    # status == "done" (issue #366).
    entry["completed_namespace"] = None
    return True


def _force_reset(progress: dict, scope: set, discovered: dict | None = None,
                 namespaces: list[str] | None = None) -> int:
    """Reset all non-pending pairs in scope to pending.

    Calls _reset_pair for cluster teardown when possible. Pairs where
    reset fails are skipped (not counted, state preserved).
    """
    reset = 0
    for key in scope:
        entry = progress.get(key, {})
        if entry.get("status") not in (None, "pending"):
            try:
                if _reset_pair(key, entry, discovered or {},
                              namespaces=namespaces):
                    reset += 1
            except Exception as e:
                warn(f"{key}: reset failed during --force: {e}")
    return reset


def _apply_run_filters(progress: dict, args) -> set:
    """Return the set of pair keys in scope for this invocation.

    With no flags: returns empty set (caller interprets as all pairs in scope).
    With flags: returns only matching pairs. Filters compose via AND.
    """
    only = _parse_list(getattr(args, "only", None))
    workload = _parse_list(getattr(args, "workload", None))
    package = _parse_list(getattr(args, "package", None))
    status_filter = _parse_list(getattr(args, "status", None))
    iteration_spec = getattr(args, "iteration", None)

    # Parse --iteration up-front so malformed specs fail before any other work.
    iteration_set: "set[int] | None" = None
    if iteration_spec is not None:
        try:
            iteration_set = parse_iteration_spec(iteration_spec)
        except ValueError as exc:
            err(str(exc))
            sys.exit(1)

    if only:
        result = set()
        unresolved = []
        for key in only:
            if key in progress and _is_pair_key(key):
                result.add(key)
            else:
                prefixed = "wl-" + key
                if prefixed in progress:
                    info(f"--only: resolved '{key}' → '{prefixed}'")
                    result.add(prefixed)
                else:
                    unresolved.append(key)
        if unresolved:
            err(f"--only: no match for {unresolved}")
            valid = sorted(k for k in progress.keys() if _is_pair_key(k))
            print(f"  Valid pair keys: {valid}", file=sys.stderr)
            sys.exit(1)
        if iteration_set is not None:
            result = {k for k in result if _key_iteration(k) in iteration_set}
        return result

    if not any([workload, package, status_filter, iteration_set]):
        return set()

    pair_entries = {k: v for k, v in progress.items() if _is_pair_key(k)}

    if workload:
        valid_workloads = {v.get("workload", "") for v in pair_entries.values()} - {""}
        workload, unknown = _expand_glob_values(workload, valid_workloads)
        if unknown:
            err(f"--workload: unrecognized values {sorted(unknown)}")
            print(f"  Valid --workload values: {', '.join(sorted(valid_workloads))}", file=sys.stderr)
            sys.exit(1)

    if package:
        valid_packages = {v.get("package", "") for v in pair_entries.values()} - {""}
        package, unknown = _expand_glob_values(package, valid_packages)
        if unknown:
            err(f"--package: unrecognized values {sorted(unknown)}")
            print(f"  Valid --package values: {', '.join(sorted(valid_packages))}", file=sys.stderr)
            sys.exit(1)

    candidates = set(pair_entries.keys())
    if workload:
        workload_set = set(workload)
        candidates = {k for k in candidates if pair_entries[k].get("workload") in workload_set}
    if package:
        package_set = set(package)
        candidates = {k for k in candidates if pair_entries[k].get("package") in package_set}
    if status_filter:
        status_set = set(status_filter)
        candidates = {k for k in candidates if pair_entries[k].get("status") in status_set}
    if iteration_set is not None:
        candidates = {k for k in candidates if _key_iteration(k) in iteration_set}
    return candidates


def _resolve_scope(progress: dict, args) -> set:
    """Apply filter args and return the set of pair keys in scope.

    No flags → all pairs. Flags + match → narrowed set. Flags + no match → abort
    with valid values printed.
    """
    filters_given = any([
        getattr(args, "only", None) is not None,
        getattr(args, "workload", None) is not None,
        getattr(args, "package", None) is not None,
        getattr(args, "status", None) is not None,
        getattr(args, "iteration", None) is not None,
    ])
    filtered = _apply_run_filters(progress, args)
    if filters_given and not filtered:
        _report_filter_mismatch(progress, args)
        sys.exit(1)
    return filtered or {k for k in progress.keys() if _is_pair_key(k)}


def _report_filter_mismatch(progress: dict, args) -> None:
    """Print all valid filter values to help the user correct their filter flags."""
    only = _parse_list(getattr(args, "only", None))
    workload = _parse_list(getattr(args, "workload", None))
    package = _parse_list(getattr(args, "package", None))
    status_filter = _parse_list(getattr(args, "status", None))
    iteration_spec = getattr(args, "iteration", None)

    parts = []
    if only is not None:
        parts.append(f"--only '{','.join(only)}'")
    if workload is not None:
        parts.append(f"--workload '{','.join(workload)}'")
    if package is not None:
        parts.append(f"--package '{','.join(package)}'")
    if status_filter is not None:
        parts.append(f"--status '{','.join(status_filter)}'")
    if iteration_spec is not None:
        parts.append(f"--iteration '{iteration_spec}'")

    err(f"No pairs matched {', '.join(parts)}.\n")

    keys = sorted(k for k in progress.keys() if _is_pair_key(k))
    print(f"  Valid pair keys ({len(keys)}):", file=sys.stderr)
    for k in keys:
        print(f"    {k}", file=sys.stderr)

    pair_values = [v for k, v in progress.items() if _is_pair_key(k)]
    valid_workloads = sorted({v.get("workload", "") for v in pair_values} - {""})
    valid_packages = sorted({v.get("package", "") for v in pair_values} - {""})
    valid_statuses = sorted({v.get("status", "") for v in pair_values} - {""})
    valid_iterations = sorted({_key_iteration(k) for k in progress if _is_pair_key(k)})

    print(f"\n  Valid --workload values:   {', '.join(valid_workloads)}", file=sys.stderr)
    print(f"  Valid --package values:    {', '.join(valid_packages)}", file=sys.stderr)
    print(f"  Valid --status values:     {', '.join(valid_statuses)}", file=sys.stderr)
    print(f"  Valid --iteration values:  {', '.join(str(n) for n in valid_iterations)}", file=sys.stderr)


def _check_slot_ready(namespace: str, hf_secret_name: str = "hf-secret") -> tuple[bool, list[str]]:
    """Check that a namespace slot is ready to accept a new PipelineRun.

    Checks: PVCs bound, HF secret present.
    Returns (ready, list_of_failure_reasons).

    Note: Tekton tasks presence check is not yet implemented; assumes
    ``cluster.py provision`` has been run.
    """
    failures = []

    for pvc in ["data-pvc", "source-pvc"]:
        result = run(
            ["kubectl", "get", "pvc", pvc, f"-n={namespace}",
             "-o", "jsonpath={.status.phase}"],
            check=False, capture=True,
        )
        if result.returncode != 0 or result.stdout.strip() != "Bound":
            hint = " — re-run cluster.py provision to provision it" if pvc == "source-pvc" else ""
            failures.append(f"PVC {pvc} not Bound in {namespace}{hint}")

    result = run(
        ["kubectl", "get", "secret", hf_secret_name, f"-n={namespace}"],
        check=False, capture=True,
    )
    if result.returncode != 0:
        failures.append(f"Secret {hf_secret_name} missing in {namespace}")

    return len(failures) == 0, failures


def _reconcile_on_resume(progress: dict, discovered: dict, *,
                         preserve_pipelineruns: bool = False) -> None:
    """Reconcile pair statuses against cluster state when resuming an interrupted run.

    - running pairs: check PipelineRun status on cluster and update accordingly
    - unrecognized statuses (e.g. 'collecting' or 'collect-failed' from a
      pre-#120 progress data): reset to pending so they are re-dispatched.
      This is safe because both historical statuses imply the PipelineRun
      already succeeded.
    """
    _known = ("pending", "running", "done", "failed", "timed-out", "stalled")
    for key, entry in progress.items():
        if not _is_pair_key(key):
            continue
        if entry["status"] == "running":
            pr_meta = discovered.get(key, {})
            pr_name = pr_meta.get("pr_name", "")
            ns = entry.get("namespace") or ""
            if pr_name and ns:
                try:
                    actual = _check_pipelinerun_status(pr_name, ns)
                except Exception as exc:
                    warn(f"[{key}] failed to check PipelineRun status: {exc}")
                    continue
                if actual in ("Succeeded", "Completed"):
                    entry["status"] = "done"
                    entry["pending_since"] = None
                    _finalize_run(entry)
                    entry["completed_namespace"] = ns
                    if not preserve_pipelineruns:
                        try:
                            _delete_pipelinerun(pr_name, ns)
                        except Exception as exc:
                            warn(f"Failed to delete PipelineRun {pr_name!r} in {ns}: {exc}")
                    entry["namespace"] = None
                elif actual in ("Failed", "PipelineRunCancelled",
                               "PipelineRunCouldntGetPipeline",
                               "PipelineRunTimeout", "CreateRunFailed",
                               "PipelineRunStopping",
                               "PipelineRunStoppingTimeout"):
                    entry["status"] = "failed"
                    # Retain namespace so reset/cleanup can find the resources
                    entry["pending_since"] = None
                    _finalize_run(entry)
                elif actual == "Unknown":
                    warn(f"[{key}] PipelineRun not found on cluster → resetting to pending")
                    entry["status"] = "pending"
                    entry["namespace"] = None
                    entry["pending_since"] = None
                    _clear_runtime(entry)
            else:
                entry["status"] = "pending"
                entry["namespace"] = None
                entry["pending_since"] = None
                _clear_runtime(entry)
        elif entry["status"] not in _known:
            warn(f"[{key}] unrecognized status '{entry['status']}' → resetting to pending")
            entry["status"] = "pending"
            entry["namespace"] = None
            entry["pending_since"] = None
            _clear_runtime(entry)


def _derive_pair_gpu_costs(
    discovered: dict,
    *,
    defaults: dict | None,
    fallback_cost: int,
) -> dict[str, tuple[int, str]]:
    """Compute GPU cost per pair from its scenarioContent.

    Returns dict mapping pair key to (cost, source) where source is one of:
    - "derived": cost parsed from scenarioContent
    - "defaults-only": scenarioContent missing/invalid, derived from defaults
    - "fallback": derivation failed or defaults unavailable, using fallback_cost
    """
    from pipeline.lib.capacity import gpu_cost_per_pair

    costs: dict[str, tuple[int, str]] = {}
    for key, meta in discovered.items():
        if defaults is None:
            warn(f"{key}: no defaults available — using fallback cost ({fallback_cost})")
            costs[key] = (fallback_cost, "fallback")
            continue

        scenario_content = meta.get("scenario_content")
        resolved = None
        if scenario_content:
            try:
                resolved = yaml.safe_load(scenario_content)
            except yaml.YAMLError as e:
                warn(f"{key}: scenarioContent is invalid YAML ({e}) — deriving cost from defaults")

        if resolved and isinstance(resolved, dict):
            result = gpu_cost_per_pair(resolved, defaults)
            source = "derived"
        else:
            if scenario_content:
                warn(f"{key}: scenarioContent not parseable as dict — deriving cost from defaults only")
            else:
                warn(f"{key}: no scenarioContent — deriving cost from defaults only")
            result = gpu_cost_per_pair({}, defaults)
            source = "defaults-only"

        if isinstance(result, int):
            costs[key] = (result, source)
        else:
            warn(f"{key}: GPU cost derivation failed: {result} — using fallback ({fallback_cost})")
            costs[key] = (fallback_cost, "fallback")

    return costs


def _capacity_limited_pairs(
    pending: list[str],
    *,
    free_gpus: int,
    cost_map: dict[str, int],
) -> list[str]:
    """Select pending pairs that fit within available GPU capacity.

    Sorts by gpu_cost ascending to maximize dispatch count.
    """
    sorted_pending = sorted(pending, key=lambda k: cost_map[k])
    result = []
    budget = free_gpus
    for pair in sorted_pending:
        cost = cost_map[pair]
        if budget >= cost:
            budget -= cost
            result.append(pair)
    return result


def _select_dispatchable(
    pending: list[str],
    *,
    free_gpus: int,
    cost_map: dict[str, int],
) -> list[str]:
    """Shuffle pending then capacity-gate.

    Shuffling before the gate (rather than after) makes the chosen subset an
    unbiased random sample of the full pending list. With all-equal costs,
    `_capacity_limited_pairs`'s stable sort preserves shuffled order so the
    greedy fill picks a uniform random subset. With heterogeneous costs,
    smallest-cost-first packing is preserved across cost groups while
    randomization applies within each group.

    Does not mutate `pending` — operates on a shuffled copy.
    """
    shuffled = list(pending)
    random.shuffle(shuffled)
    return _capacity_limited_pairs(shuffled, free_gpus=free_gpus, cost_map=cost_map)


def _refresh_namespaces(current: list[str]) -> list[str]:
    """Re-read the slot list from cluster_config.json for live mid-run updates.

    Returns the refreshed list, or ``current`` unchanged when:
      - the file is unreadable / unparseable (best-effort: keep prior list),
      - the refreshed list is empty (treat as transient or accidental wipe),
      - ``namespaces[0]`` (the primary) differs from the startup primary
        (the run-scoped progress ConfigMap is bound there for the lifetime
        of the run; mismatches are logged and ignored).

    Logs once per actual change so quiet cycles stay silent.
    """
    try:
        fresh = _load_cluster_config()
    except Exception as exc:
        warn(f"cluster_config.json re-read failed: {exc} — keeping current slot list")
        return current
    fresh_ns = [n for n in (fresh.get("namespaces") or []) if n]
    if not fresh_ns:
        return current
    if fresh_ns[0] != current[0]:
        warn(f"cluster_config.json primary namespace changed "
             f"({current[0]} → {fresh_ns[0]}); ignoring (primary is pinned for the run)")
        return current
    if fresh_ns == current:
        return current
    added = [n for n in fresh_ns if n not in current]
    removed = [n for n in current if n not in fresh_ns]
    if added:
        info(f"Slot pool: +{','.join(added)}")
    if removed:
        info(f"Slot pool: -{','.join(removed)} (will drain on their own)")
    return fresh_ns


def _cmd_run(args, run_dir: Path, cluster_config: dict) -> None:
    """Orchestrate parallel pool execution across namespace slots."""
    import tempfile as _tmp
    from pipeline.lib.capacity import (
        probe_free_gpus, derive_gpu_resource_type, load_defaults,
        extract_node_filters, NodeFilter,
    )

    namespaces = cluster_config.get("namespaces") or []
    if not namespaces or not namespaces[0]:
        err(_no_namespaces_hint()); sys.exit(1)

    max_retries = getattr(args, "max_retries", 2)
    poll_interval = getattr(args, "poll_interval", 30)
    pending_threshold = getattr(args, "pending_threshold", 600)
    max_pending_stalls = getattr(args, "max_pending_stalls", 10)

    cluster_dir = run_dir / "cluster"
    primary_ns = _configmap_namespace(cluster_config, namespaces)
    if not primary_ns:
        err(_no_namespaces_hint()); sys.exit(1)
    store = _make_progress_store(primary_ns, run_dir)

    # Derive GPU resource type from baseline scenario
    # CLI --gpu-resource-type overrides auto-derivation when explicitly set
    gpu_resource_type = args.gpu_resource_type  # None means auto-derive
    fallback_cost = args.default_gpu_cost
    defaults_path_override = getattr(args, "defaults_path", None)
    if defaults_path_override:
        defaults_result = load_defaults(REPO_ROOT, defaults_path=defaults_path_override)
        if defaults_result is None:
            warn(f"--defaults-path {defaults_path_override} not found — GPU cost derivation will use fallback")
    else:
        defaults_result = load_defaults(REPO_ROOT)
    if isinstance(defaults_result, str):
        warn(defaults_result)
        defaults_result = None
    scenario_path = cluster_dir / "baseline.yaml"
    if not scenario_path.exists():
        for p in sorted(cluster_dir.glob("*.yaml")):
            if not p.name.startswith("pipelinerun-"):
                scenario_path = p
                info(f"Deriving GPU config from: {scenario_path.name}")
                break
    resolved = None
    if defaults_result and scenario_path.exists():
        try:
            resolved = yaml.safe_load(scenario_path.read_text()) or {}
        except yaml.YAMLError as e:
            warn(f"Could not parse {scenario_path.name}: {e}")
            resolved = None
        if resolved:
            if gpu_resource_type is None:
                gpu_resource_type = derive_gpu_resource_type(resolved, defaults_result)
    elif defaults_result is None and not scenario_path.exists():
        info("Defaults or scenario not found — using CLI defaults")
    if gpu_resource_type is None:
        gpu_resource_type = "nvidia.com/gpu"
    if gpu_resource_type != "nvidia.com/gpu":
        info(f"GPU resource type: {gpu_resource_type}")
    node_filters: dict = {}
    if resolved:
        node_filters = extract_node_filters(resolved)
    if node_filters:
        for role, f in node_filters.items():
            if f.required_gpu_products:
                info(f"Eligibility filter [{role}]: gpu.product ∈ {sorted(f.required_gpu_products)}")
            else:
                info(f"Eligibility filter [{role}]: no product constraint extracted — applying cordon/taint screening only")
    else:
        info("No per-role GPU product constraint extracted from scenario — applying cordon/taint screening only")
    _probe_fail_count = 0
    _last_probe_error = ""
    _last_log_state: dict[str, object] = {}
    _zero_dispatch_count = 0

    # Load or initialize progress
    progress = _load_progress(store, run_name=run_dir.name)

    discovered = _load_pairs(cluster_dir)
    if not discovered:
        err(f"run 'sim2real assemble --run {run_dir.name}' first"); sys.exit(1)

    # Initialize new entries (first run or new pairs added)
    for key, meta in discovered.items():
        if key not in progress:
            progress[key] = {
                "workload": meta["workload"],
                "package":  meta["package"],
                "status":   "pending",
                "namespace": None,
                "completed_namespace": None,
                "retries":  0,
                "pending_stalls": 0,
                "pending_since": None,
                "running_since": None,
                "last_duration": None,
            }

    _scope = _resolve_scope(progress, args)
    total_pairs = sum(1 for k in progress if _is_pair_key(k))
    if len(_scope) < total_pairs:
        info(f"Scope: {len(_scope)}/{total_pairs} pairs")

    scoped_discovered = {k: v for k, v in discovered.items() if k in _scope}
    pair_costs_with_prov = _derive_pair_gpu_costs(
        scoped_discovered, defaults=defaults_result, fallback_cost=fallback_cost,
    )
    pair_costs = {k: v[0] for k, v in pair_costs_with_prov.items()}
    pair_provenance = {k: v[1] for k, v in pair_costs_with_prov.items()}

    if getattr(args, "force", False):
        n = _force_reset(progress, _scope, discovered, namespaces=namespaces)
        if n:
            info(f"--force: reset {n} non-pending pair(s) to pending")
        else:
            info("--force: no non-pending pairs found in scope — nothing reset")
        store.save(progress)

    preserve_pipelineruns = getattr(args, "preserve_pipelineruns", False)
    _reconcile_on_resume(progress, discovered,
                         preserve_pipelineruns=preserve_pipelineruns)
    store.save(progress)

    # Orphans: pair_keys in progress (in scope, still active) but absent from
    # cluster/. Happens when sim2real assemble is re-run with a different
    # workload set between stop and the next run. Without this guard,
    # _pending_pairs would surface them and the dispatch loop's
    # pair_costs[pair_key] would KeyError at startup (#408).
    orphans = sorted(
        k for k, v in progress.items()
        if _is_pair_key(k) and k in _scope and k not in discovered
        and v.get("status") in ("pending", "running")
    )
    if orphans:
        warn(f"{len(orphans)} progress entries have no PipelineRun in cluster/ "
             f"(likely from a prior sim2real assemble): {orphans}. Skipping. "
             f"Remove the entries manually or via `deploy.py reset --only <key>` "
             f"if they should not return.")

    # Track which namespace is assigned to which pair
    slots_busy: dict[str, str] = {
        entry["namespace"]: key
        for key, entry in progress.items()
        if _is_pair_key(key) and entry.get("status") == "running" and entry.get("namespace")
    }

    def _pending_pairs() -> list[str]:
        return [k for k, v in progress.items()
                if _is_pair_key(k) and v.get("status") == "pending"
                and k in _scope and k in discovered]

    def _work_remaining() -> bool:
        return any(v.get("status") in ("pending", "running")
                   for k, v in progress.items()
                   if _is_pair_key(k) and k in _scope and k in discovered)

    timeout_hours = 4
    info(f"Orchestrator: {len(_scope)} pairs in scope, {len(namespaces)} slot(s)")
    if not _work_remaining() and not slots_busy:
        # Every pair in scope is in a terminal state. Count by status so
        # the operator can distinguish a legitimately finished scope from
        # one containing failed / timed-out / stalled pairs (issue #460).
        # Uses the canonical status tokens ("timed-out" with a hyphen) so
        # this line and `deploy.py status` speak the same language.
        _terminal_states = ("done", "failed", "timed-out", "stalled")
        _counts = {s: 0 for s in _terminal_states}
        for k, v in progress.items():
            if _is_pair_key(k) and k in _scope and k in discovered:
                _status = v.get("status", "")
                if _status in _counts:
                    _counts[_status] += 1
        _breakdown = ", ".join(f"{_counts[s]} {s}" for s in _terminal_states)
        info(f"All {len(_scope)} pairs in scope are in terminal states "
             f"({_breakdown}). Nothing to dispatch. Use "
             f"`deploy.py reset --only <key>` to retry a specific pair, or "
             f"`deploy.py run --force` to reset all pairs in scope.")
        return

    from pipeline.lib.health import RemediationTracker as _HealthTracker
    _health_tracker = _HealthTracker()

    from pipeline.lib.shadow import ShadowLedger
    shadow = ShadowLedger(ttl=args.shadow_ttl)

    while _work_remaining() or slots_busy:

        # ── Process completed/failed slots ───────────────────────────────
        for ns in list(slots_busy):
            pair_key = slots_busy[ns]
            entry = progress[pair_key]
            pr_meta = discovered.get(pair_key, {})
            pr_name = pr_meta.get("pr_name", "")

            status = _check_pipelinerun_status(pr_name, ns) if pr_name else "Unknown"

            if status in ("Succeeded", "Completed"):
                ok(f"[{pair_key}] {status} → done")
                entry["status"] = "done"
                _finalize_run(entry)
                entry["completed_namespace"] = ns
                entry["namespace"] = None
                store.save(progress)
                if not preserve_pipelineruns:
                    try:
                        _delete_pipelinerun(pr_name, ns)
                    except Exception as exc:
                        warn(f"Failed to delete PipelineRun {pr_name!r} in {ns}: {exc}")
                del slots_busy[ns]
                _last_log_state.pop("capacity", None)
                _last_log_state.pop("dispatch", None)
                _last_log_state.pop("slots_busy", None)
                _zero_dispatch_count = 0

            elif status in ("Failed", "PipelineRunCancelled", "PipelineRunCouldntGetPipeline",
                            "PipelineRunTimeout", "CreateRunFailed", "PipelineRunStopping",
                            "PipelineRunStoppingTimeout"):
                warn(f"[{pair_key}] hard failure ({status}) → failed")
                entry["status"] = "failed"
                _finalize_run(entry)
                # Retain namespace so reset/cleanup can find the helm releases
                # (issue #277). Mirrors _reconcile_on_resume's failure handling.
                store.save(progress)
                del slots_busy[ns]
                _last_log_state.pop("capacity", None)
                _last_log_state.pop("dispatch", None)
                _last_log_state.pop("slots_busy", None)
                _zero_dispatch_count = 0

            elif status in ("Running", "Started"):
                # Check for pending pods (before timeout)
                try:
                    reclaimed = _handle_pending_pods(
                        pr_name=pr_name, namespace=ns, entry=entry,
                        pending_threshold=pending_threshold,
                        max_pending_stalls=max_pending_stalls,
                    )
                except Exception as exc:
                    import traceback as _tb
                    err(f"[{pair_key}] pending check failed: {exc}")
                    _tb.print_exc(file=sys.stderr)
                    reclaimed = False
                if reclaimed:
                    del slots_busy[ns]
                    _last_log_state.pop("capacity", None)
                    _last_log_state.pop("dispatch", None)
                    _last_log_state.pop("slots_busy", None)
                    _zero_dispatch_count = 0
                    store.save(progress)
                    continue

                # Check for timeout
                timeout_result = _handle_timeout(
                    pr_name=pr_name, namespace=ns, entry=entry,
                    timeout_hours=timeout_hours, max_retries=max_retries,
                )
                if timeout_result is True:
                    del slots_busy[ns]
                    _last_log_state.pop("capacity", None)
                    _last_log_state.pop("dispatch", None)
                    _last_log_state.pop("slots_busy", None)
                    _zero_dispatch_count = 0
                    store.save(progress)
                    continue
                elif timeout_result is False:
                    continue

                # Check pod health (non-Tekton pods)
                try:
                    escalate = _check_pod_health(
                        namespace=ns, pair_key=pair_key,
                        tracker=_health_tracker,
                        skip_teardown=getattr(args, "skip_teardown", False),
                    )
                except Exception as exc:
                    warn(f"[{pair_key}] health check failed: {exc}")
                    escalate = False
                if escalate:
                    warn(f"[{pair_key}] pod health escalation → cancelling PipelineRun")
                    if _cancel_and_delete_pipelinerun(pr_name, ns):
                        entry["status"] = "failed"
                        _finalize_run(entry)
                        # Retain namespace so reset/cleanup can find the helm
                        # releases (issue #277).
                        store.save(progress)
                        del slots_busy[ns]
                        _last_log_state.pop("capacity", None)
                        _last_log_state.pop("dispatch", None)
                        _last_log_state.pop("slots_busy", None)
                        _zero_dispatch_count = 0
                    else:
                        warn(f"[{pair_key}] could not cancel PipelineRun — slot remains busy")

        # ── Skip GPU probe + dispatch when no slots are free ─────────────
        # When every slot is busy, only PipelineRun status checking (above)
        # runs this cycle. Polling stays at the base interval so slot
        # recovery is detected within one poll (issue #274).
        namespaces = _refresh_namespaces(namespaces)
        free_slots = [ns for ns in namespaces if ns not in slots_busy]
        pending = _pending_pairs()

        if free_slots:
            # ── Capacity probe ───────────────────────────────────────────
            capacity = probe_free_gpus(
                gpu_resource_type=gpu_resource_type,
                node_filters=list(node_filters.values()) or [NodeFilter()],
            )
            # Snapshot the shadow ledger once per cycle. Every log line and
            # gating decision in this cycle uses the same _reserved value,
            # so `free_gpus − _reserved == effective_free` holds in every
            # printed line (issue #272).
            if isinstance(capacity, tuple):
                free_gpus, allocatable, requested = capacity
                _reserved = shadow.reserved()
                effective_free = max(0, free_gpus - _reserved)
                _cap_state = (effective_free, free_gpus, _reserved, allocatable, requested)
                if pending and _cap_state != _last_log_state.get("capacity"):
                    info(_format_capacity(effective_free, free_gpus, _reserved,
                                          allocatable, requested))
                    _last_log_state["capacity"] = _cap_state
                if _probe_fail_count > 0:
                    info(f"Capacity probe recovered after {_probe_fail_count} failure(s)")
                _probe_fail_count = 0
                _last_probe_error = ""
            else:
                free_gpus = None
                _reserved = 0
                effective_free = 0
                _probe_fail_count += 1
                if capacity != _last_probe_error or _probe_fail_count % 10 == 0:
                    warn(f"Capacity probe failed: {capacity} — dispatching without GPU gating")
                _last_probe_error = capacity

            # ── Assign pending work to free slots ────────────────────────
            if free_gpus is not None and pending:
                dispatchable = _select_dispatchable(
                    pending,
                    free_gpus=effective_free, cost_map=pair_costs,
                )
                if len(dispatchable) == 0 and pending:
                    smallest = min(pair_costs[k] for k in pending)
                    _disp_state = ("zero", len(pending), effective_free, _reserved, smallest)
                    _zero_dispatch_count += 1
                    if _disp_state != _last_log_state.get("dispatch") or _zero_dispatch_count % 10 == 0:
                        warn(
                            f"Dispatching 0/{len(pending)} pending pairs — "
                            f"smallest cost ({smallest}) exceeds {effective_free} "
                            f"effective free GPUs ({free_gpus} probed − {_reserved} reserved)"
                        )
                        _last_log_state["dispatch"] = _disp_state
                elif len(dispatchable) < len(pending):
                    _disp_state = ("cap_limited", len(dispatchable), len(pending), free_gpus)
                    if _disp_state != _last_log_state.get("dispatch"):
                        info(f"Dispatching {len(dispatchable)}/{len(pending)} pending pairs (capacity-limited: {free_gpus} free GPUs)")
                        _last_log_state["dispatch"] = _disp_state
                elif len(free_slots) < len(dispatchable):
                    _disp_state = ("slot_limited", len(free_slots), len(pending))
                    if _disp_state != _last_log_state.get("dispatch"):
                        info(f"Dispatching {len(free_slots)}/{len(pending)} pending pairs (slot-limited)")
                        _last_log_state["dispatch"] = _disp_state
            else:
                dispatchable = list(pending)
                random.shuffle(dispatchable)

            for ns, pair_key in zip(free_slots, dispatchable):
                hf_secret_name = (cluster_config.get("secret_names") or {}).get("hf_token", "hf-secret")
                ready, reasons = _check_slot_ready(ns, hf_secret_name=hf_secret_name)
                if not ready:
                    warn(f"Slot {ns} not ready: {'; '.join(reasons)}")
                    continue

                pair_cost = pair_costs[pair_key]
                source = pair_provenance[pair_key]
                _source_labels = {"derived": "derived from scenarioContent", "defaults-only": "derived from defaults only", "fallback": "fallback default"}
                if free_gpus is not None:
                    info(_format_capacity(effective_free, free_gpus, _reserved,
                                          allocatable, requested))
                info(f"{pair_key} requires {pair_cost} GPUs ({_source_labels[source]})")
                pr_meta = discovered.get(pair_key, {})
                pr_path_str = pr_meta.get("pr_path", "")
                if not pr_path_str:
                    warn(f"No PipelineRun path for {pair_key}"); continue

                pr_data = yaml.safe_load(Path(pr_path_str).read_text())

                # Rewrite namespace in the PipelineRun before applying
                pr_data["metadata"]["namespace"] = ns
                for param in pr_data.get("spec", {}).get("params", []):
                    if param["name"] == "namespace":
                        param["value"] = ns

                if getattr(args, "skip_teardown", False):
                    params = pr_data.setdefault("spec", {}).setdefault("params", [])
                    for param in params:
                        if param["name"] == "skipTeardown":
                            param["value"] = "true"
                            break
                    else:
                        params.append({"name": "skipTeardown", "value": "true"})

                tf_path = None
                try:
                    with _tmp.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tf:
                        yaml.dump(pr_data, tf, default_flow_style=False)
                        tf_path = tf.name
                    pr_name = pr_data.get("metadata", {}).get("name", "")
                    # Delete any prior completed/failed PipelineRun before re-applying
                    if pr_name:
                        run(["kubectl", "delete", "pipelinerun", pr_name, f"-n={ns}",
                             "--ignore-not-found=true"],
                            check=False, capture=True)
                    result = run(["kubectl", "apply", "-f", tf_path, "-n", ns],
                                 check=False, capture=True)
                finally:
                    if tf_path:
                        Path(tf_path).unlink(missing_ok=True)

                if result.returncode != 0:
                    warn(f"[{pair_key}] kubectl apply failed: {result.stderr.strip()}")
                    continue

                entry = progress[pair_key]
                entry["status"] = "running"
                entry["namespace"] = ns
                entry["pending_since"] = None
                _mark_running(entry)
                slots_busy[ns] = pair_key
                store.save(progress)
                ok(f"[{pair_key}] → {ns} ({pr_name})")
                _last_log_state.pop("capacity", None)
                _last_log_state.pop("dispatch", None)
                _last_log_state.pop("slots_busy", None)
                _zero_dispatch_count = 0
                shadow.record(pair_cost)
        elif pending:
            _busy_state = (len(pending), len(namespaces))
            if _busy_state != _last_log_state.get("slots_busy"):
                info(f"Dispatching 0/{len(pending)} pending — all {len(namespaces)} slots busy")
                _last_log_state["slots_busy"] = _busy_state

        store.save(progress)

        if _work_remaining() or slots_busy:
            time.sleep(poll_interval)

    # Final summary
    counts: dict[str, int] = {}
    for k, v in progress.items():
        if k in _scope:
            counts[v["status"]] = counts.get(v["status"], 0) + 1
    print()
    ok("Run complete: " + "  ".join(f"{v} {k}" for k, v in sorted(counts.items())))
    print(f"  Progress: ConfigMap {store.configmap_name} in {primary_ns}")
    print()


def _cmd_reset(args, run_dir: Path, discovered: dict,
               namespaces: list[str] | None = None,
               cluster_config: dict | None = None) -> None:
    """Reset all non-pending pairs to pending (with cluster cleanup)."""
    primary_ns = _configmap_namespace(cluster_config, namespaces)
    if not primary_ns:
        err("No namespace configured. Run cluster.py provision with --namespaces.")
        sys.exit(1)
    store = _make_progress_store(primary_ns, run_dir)
    progress = _load_progress(store, run_name=run_dir.name)

    if not progress:
        info("No progress data found — nothing to reset")
        return

    _scope = _resolve_scope(progress, args)

    # Exclude pending (nothing to reset)
    actionable = {k for k in _scope
                  if progress[k].get("status") not in (None, "pending")}

    if not actionable:
        info("No pairs need reset (all pending)")
        return

    dry_run = getattr(args, "dry_run", False)
    preserve_done = getattr(args, "preserve_done_status", False)
    total_pairs = sum(1 for k in progress if _is_pair_key(k))
    info(f"Scope: {len(actionable)}/{total_pairs} pairs"
         + (" [DRY-RUN]" if dry_run else ""))

    cleaned = 0
    errors = 0
    for key in sorted(actionable):
        entry = progress[key]
        try:
            if _reset_pair(key, entry, discovered, dry_run=dry_run,
                          namespaces=namespaces,
                          preserve_done_status=preserve_done):
                cleaned += 1
            else:
                errors += 1
        except Exception as e:
            err(f"{key}: reset failed — {e}")
            errors += 1

    if not dry_run:
        store.save(progress)

    msg = f"{cleaned} pair(s) reset"
    if errors:
        msg += f" ({errors} failed — manual intervention needed)"
    ok(msg)


def _cmd_wipe(args, run_dir: Path,
              cluster_config: dict | None = None) -> None:
    """Delete local result files for pairs in scope."""
    primary_ns = _configmap_namespace(cluster_config)
    if not primary_ns:
        err("No namespace configured. Run cluster.py provision with --namespaces.")
        sys.exit(1)
    store = _make_progress_store(primary_ns, run_dir)
    progress = _load_progress(store, run_name=run_dir.name)

    if not progress:
        info("No progress data found — nothing to wipe")
        return

    _scope = _resolve_scope(progress, args)

    total_pairs = sum(1 for k in progress if _is_pair_key(k))
    results_dir = run_dir / "results"

    # A pair key encodes its iteration; the on-disk shape under step-5 is
    # results/<pkg>/<wl>/i<N>/. When the per-iteration dir is present, wipe
    # scoped to that key must delete only i<N>/ — not the whole workload
    # tree, which would silently destroy sibling iterations (issue #525).
    # Legacy single-replica runs whose files live directly under
    # results/<pkg>/<wl>/ have no i<N>/ subdirs; fall back to the workload
    # dir for those.
    _ITER_DIR_RE = re.compile(r"i[1-9][0-9]*")

    def _has_iN_subdirs(wl_dir: Path) -> bool:
        if not wl_dir.is_dir():
            return False
        return any(p.is_dir() and _ITER_DIR_RE.fullmatch(p.name)
                   for p in wl_dir.iterdir())

    targets = []
    for key in sorted(_scope):
        entry = progress[key]
        pkg = entry.get("package", "")
        wl = entry.get("workload", "")
        if not pkg or not wl:
            warn(f"{key}: missing package/workload fields — skipping")
            continue
        n = _key_iteration(key)
        wl_dir = results_dir / pkg / wl
        iN_dir = wl_dir / f"i{n}"
        if iN_dir.exists():
            target_dir = iN_dir
            display = f"results/{pkg}/{wl}/i{n}/"
        elif wl_dir.exists() and not _has_iN_subdirs(wl_dir):
            # Legacy single-replica layout — the workload dir holds the
            # trace files directly. Wipe the whole workload dir.
            target_dir = wl_dir
            display = f"results/{pkg}/{wl}/"
        else:
            # Step-5 layout with no matching i<N>/ (or nothing on disk).
            # Point at iN_dir so the "no results on disk" branch below
            # reports the specific path that was expected.
            target_dir = iN_dir
            display = f"results/{pkg}/{wl}/i{n}/"
        targets.append((key, pkg, wl, target_dir, display))

    info(f"Scope: {len(_scope)}/{total_pairs} pairs"
         + (" [DRY-RUN]" if args.dry_run else ""))

    if args.dry_run:
        for key, pkg, wl, target_dir, display in targets:
            exists = target_dir.exists()
            info(f"[DRY-RUN] {key}: would delete {display}"
                 + (" (exists)" if exists else " (not on disk)"))
        return

    if not args.yes:
        dirs_on_disk = sum(1 for _, _, _, p, _ in targets if p.exists())
        prompt = f"Wipe {len(targets)} pair(s) ({dirs_on_disk} with results on disk)? [y/N] "
        try:
            answer = input(prompt).strip().lower()
        except EOFError:
            info("Aborted (non-interactive — use --yes to skip confirmation)")
            return
        if answer != "y":
            info("Aborted")
            return

    wiped = 0
    errors = 0
    for key, pkg, wl, target_dir, display in targets:
        if target_dir.exists():
            try:
                shutil.rmtree(target_dir)
            except OSError as e:
                warn(f"{key}: failed to delete {display}: {e}")
                errors += 1
                continue
            ok(f"Deleted: {display}")
            # Best-effort cleanup of empty parents (workload dir, then
            # package dir). rmdir is a no-op if siblings remain.
            wl_dir = results_dir / pkg / wl
            try:
                wl_dir.rmdir()
            except OSError:
                pass
            pkg_dir = results_dir / pkg
            try:
                pkg_dir.rmdir()
            except OSError:
                pass
            wiped += 1
        else:
            info(f"{key}: no results on disk — skipped")

    msg = f"{wiped} pair(s) wiped"
    if errors:
        msg += f" ({errors} failed — check permissions)"
        warn(msg)
        sys.exit(1)
    ok(msg)


# ── Stop remote orchestrator ────────────────────────────────────────────────

from pipeline.lib.remote import JOB_NAME


def _cmd_stop(namespace: str) -> None:
    """Stop the remote orchestrator Job."""
    result = run(["kubectl", "get", "job", JOB_NAME, "-n", namespace],
                 check=False, capture=True)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        if "(NotFound)" in stderr:
            info(f"No remote orchestrator started in {namespace}")
            return
        err(f"Failed to check for orchestrator Job in {namespace}: {stderr}")
        sys.exit(1)

    result = run(["kubectl", "delete", "job", JOB_NAME, "-n", namespace,
                   "--cascade=foreground"], check=False, capture=True)
    if result.returncode != 0:
        detail = (result.stderr or "").strip()
        err(f"Failed to delete {JOB_NAME} in {namespace}"
            + (f": {detail}" if detail else ""))
        sys.exit(1)
    ok(f"Stopped {JOB_NAME} in {namespace}")


# ── Remote run ──────────────────────────────────────────────────────────────

_FAIL_FAST_REASONS = {
    "ImagePullBackOff",
    "ErrImagePull",
    "CrashLoopBackOff",
    "CreateContainerConfigError",
    "InvalidImageName",
    "RunContainerError",
    "ContainerCannotRun",
}


def _collect_run_flags(args) -> list[str]:
    """Collect run subcommand flags to forward to the in-cluster Job."""
    flags: list[str] = []
    for name in ("only", "workload", "package", "status", "iteration"):
        val = getattr(args, name, None)
        if val is not None:
            if isinstance(val, list):
                flags.extend([f"--{name}"] + val)
            else:
                flags.extend([f"--{name}", str(val)])
    if getattr(args, "force"):
        flags.append("--force")
    if getattr(args, "skip_teardown", False):
        flags.append("--skip-teardown")
    if getattr(args, "preserve_pipelineruns", False):
        flags.append("--preserve-pipelineruns")
    _defaults = {
        "max_retries": 2,
        "poll_interval": 30,
        "gpu_resource_type": None,
        "default_gpu_cost": 1,
        "pending_threshold": 600,
        "max_pending_stalls": 10,
        "shadow_ttl": 120,
    }
    for attr, default in _defaults.items():
        val = getattr(args, attr)
        if val != default:
            flag = f"--{attr.replace('_', '-')}"
            flags.extend([flag, str(val)])
    return flags


def _check_existing_job(namespace: str) -> "str | None":
    """Check whether the orchestrator Job already exists.

    Returns "active" if the Job has active pods, "completed" if it exists
    but is not active, or None if the Job doesn't exist.
    """
    result = run(["kubectl", "get", "job", JOB_NAME, "-n", namespace,
                   "-o", "json"], check=False, capture=True)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        if "(NotFound)" in stderr:
            return None
        err(f"Failed to check for orchestrator Job: {stderr}")
        sys.exit(1)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        err(f"kubectl get job returned invalid JSON: {result.stdout[:200]}")
        sys.exit(1)
    if data.get("status", {}).get("active", 0) > 0:
        return "active"
    return "completed"


def _report_failed_pod(pod: dict, namespace: str) -> None:
    """Print the cause of an orchestrator pod failure before the caller exits.

    `pod.status.message` is only populated for pod-level failures (evicted,
    preempted, OOMKilled at the pod level). When a container simply exits
    non-zero that field is empty, so fall back to the container's terminated
    `exitCode`/`reason` and tail the orchestrator logs, where the real
    diagnostic lives. See issue #276.
    """
    status = pod.get("status", {})
    msg = status.get("message", "")
    details = []
    all_statuses = (status.get("initContainerStatuses", [])
                    + status.get("containerStatuses", []))
    for cs in all_statuses:
        term = cs.get("state", {}).get("terminated", {})
        if term and term.get("exitCode", 0) != 0:
            cname = cs.get("name", "?")
            reason = term.get("reason", "")
            code = term.get("exitCode", "")
            details.append(f"{cname} exited {code}"
                           + (f" ({reason})" if reason else ""))

    header = "Orchestrator pod failed"
    if msg:
        header += f": {msg}"
    elif details:
        header += ": " + "; ".join(details)
    err(header)

    pod_name = pod.get("metadata", {}).get("name", "")
    if pod_name:
        logs = run(["kubectl", "logs", pod_name, "-n", namespace,
                    "-c", "orchestrator", "--tail=80"],
                   check=False, capture=True)
        if logs.stdout:
            err("Orchestrator pod logs:\n" + logs.stdout)


def _wait_for_job_pod(namespace: str, *, timeout: int = 120, poll: int = 5) -> None:
    """Poll until the orchestrator pod reaches Running or Succeeded.

    Fails fast on unrecoverable container states (ImagePullBackOff, etc.)
    and on pod phase Failed. Exits early if kubectl fails 3 times in a row.
    """
    deadline = time.time() + timeout
    consecutive_failures = 0
    last_error = ""
    while True:
        result = run(
            ["kubectl", "get", "pods",
             "-l", f"job-name={JOB_NAME}",
             "-n", namespace, "-o", "json"],
            check=False, capture=True,
        )
        if result.returncode != 0:
            consecutive_failures += 1
            last_error = (result.stderr or "").strip()
            if consecutive_failures >= 3:
                err(f"kubectl failed {consecutive_failures} times: {last_error}")
                sys.exit(1)
        else:
            try:
                data = json.loads(result.stdout)
            except json.JSONDecodeError:
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    err("kubectl returned invalid JSON 3 times in a row")
                    sys.exit(1)
                warn("kubectl returned invalid JSON — retrying")
                if time.time() >= deadline:
                    err(f"Timed out waiting for {JOB_NAME} pod in {namespace}")
                    sys.exit(1)
                time.sleep(poll)
                continue
            consecutive_failures = 0
            for pod in data.get("items", []):
                phase = pod.get("status", {}).get("phase", "")
                if phase in ("Running", "Succeeded"):
                    return
                if phase == "Failed":
                    _report_failed_pod(pod, namespace)
                    sys.exit(1)
                all_statuses = (pod.get("status", {}).get("initContainerStatuses", [])
                                + pod.get("status", {}).get("containerStatuses", []))
                for cs in all_statuses:
                    waiting = cs.get("state", {}).get("waiting", {})
                    reason = waiting.get("reason", "")
                    if reason in _FAIL_FAST_REASONS:
                        err(f"Pod failed: {reason} — {waiting.get('message', '')}")
                        sys.exit(1)
        if time.time() >= deadline:
            err(f"Timed out waiting for {JOB_NAME} pod in {namespace}")
            sys.exit(1)
        time.sleep(poll)


def _cmd_run_remote(args, run_dir: "Path", setup_config: dict,
                    cluster_config: dict) -> None:
    """Submit the orchestrator as an in-cluster Job."""
    from pipeline.lib.remote import (
        build_run_inputs_configmap, build_orchestrator_job,
    )

    namespaces = cluster_config.get("namespaces") or []
    namespace = namespaces[0] if namespaces else ""
    if not namespace:
        err("No namespaces configured. Run cluster.py provision with --namespaces.")
        sys.exit(1)

    orchestrator_image = setup_config.get("orchestrator_image")
    if not orchestrator_image:
        err("orchestrator_image not set in setup_config.json — add it before using --remote.")
        sys.exit(1)

    status = _check_existing_job(namespace)
    if status == "active":
        err(f"Orchestrator Job already running in {namespace}. Use 'deploy.py stop' first.")
        sys.exit(1)
    elif status == "completed":
        info(f"Deleting completed orchestrator Job in {namespace}")
        result = run(["kubectl", "delete", "job", JOB_NAME, "-n", namespace],
                     check=False, capture=True)
        if result.returncode != 0:
            detail = (result.stderr or "").strip()
            err(f"Failed to delete completed Job: {detail}")
            sys.exit(1)

    # Validate filter flags before dispatching PipelineRuns (fail fast)
    cluster_dir = run_dir / "cluster"
    discovered = _load_pairs(cluster_dir)
    if discovered:
        store = _make_progress_store(namespace, run_dir)
        try:
            progress = _load_progress(store, allow_unreachable=True,
                                      run_name=run_dir.name) or None
        except ProgressUnavailable as exc:
            warn(f"ConfigMap unreachable — skipping pre-flight filter "
                 f"validation: {exc}")
            progress = None
        if progress is not None:
            # Mirror _cmd_run's init loop locally so the pre-flight validator
            # sees pair_keys that sim2real assemble added since the last run.
            # The in-cluster orchestrator independently does its own init from
            # the ConfigMap and persists; only `workload` and `package` need
            # to be populated here — those are the fields _apply_run_filters
            # reads when building valid_workloads / valid_packages (#414).
            for key, meta in discovered.items():
                if key not in progress:
                    progress[key] = {
                        "workload": meta["workload"],
                        "package":  meta["package"],
                        "status":   "pending",
                    }
            _resolve_scope(progress, args)

    workspace_dir = EXPERIMENT_ROOT / "workspace"
    run_name = run_dir.name

    # Read defaults.yaml locally — not available in-cluster
    defaults_path = REPO_ROOT / "llm-d-benchmark" / "config" / "templates" / "values" / "defaults.yaml"
    defaults_content = None
    if defaults_path.exists():
        try:
            defaults_content = defaults_path.read_text()
        except OSError as exc:
            warn(f"defaults.yaml read failed: {exc} — remote Job will run without GPU cost defaults")

    try:
        cm = build_run_inputs_configmap(
            run_dir=run_dir, workspace_dir=workspace_dir,
            namespace=namespace, run_name=run_name,
            defaults_content=defaults_content,
        )
    except OSError as exc:
        err(f"{exc} — run setup.py and 'sim2real assemble --run {run_dir.name}' first")
        sys.exit(1)
    # subprocess.run used directly because the module's run() helper doesn't
    # support stdin input, which kubectl apply -f - requires.
    # --server-side avoids writing the kubectl.kubernetes.io/last-applied-configuration
    # annotation, which is capped at 256 KiB and overflowed once the run-inputs
    # ConfigMap accumulated enough cluster--*.yaml entries.
    info("Applying run-inputs ConfigMap")
    result = subprocess.run(
        ["kubectl", "apply", "--server-side", "--force-conflicts", "-f", "-"],
        input=json.dumps(cm), text=True, check=False, capture_output=True,
        timeout=120,
    )
    if result.returncode != 0:
        err(f"Failed to apply ConfigMap: {(result.stderr or '').strip()}")
        sys.exit(1)

    run_flags = _collect_run_flags(args)
    if defaults_content is not None:
        run_flags.append("--defaults-path")
        run_flags.append("/data/workspace/defaults.yaml")
    job = build_orchestrator_job(
        namespace=namespace, image=orchestrator_image,
        run_name=run_name, run_flags=run_flags,
        configmap_data=cm["data"],
    )
    info("Applying orchestrator Job")
    result = subprocess.run(
        ["kubectl", "apply", "--server-side", "--force-conflicts", "-f", "-"],
        input=json.dumps(job), text=True, check=False, capture_output=True,
        timeout=120,
    )
    if result.returncode != 0:
        err(f"Failed to apply Job: {(result.stderr or '').strip()}")
        sys.exit(1)

    _wait_for_job_pod(namespace)
    ok("Orchestrator pod is running")
    info(f"Tail logs: kubectl logs -f job/{JOB_NAME} -n {namespace}")


# ── CLI ──────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="deploy.py",
        description="sim2real deploy — orchestrate runs, collect results",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python pipeline/deploy.py run                        # Orchestrate all pairs
  python pipeline/deploy.py run --remote               # Submit orchestrator as in-cluster Job
  python pipeline/deploy.py status                     # Show progress snapshot
  python pipeline/deploy.py collect                    # Pull results for completed phases
  python pipeline/deploy.py collect --skip-logs        # Collect traces only (skip large logs)
  python pipeline/deploy.py stop                         # Stop remote orchestrator Job
  python pipeline/deploy.py reset                       # Reset stalled/failed pairs
  python pipeline/deploy.py reset --dry-run             # Preview what would be reset
  python pipeline/deploy.py wipe                          # Wipe all results for current run
  python pipeline/deploy.py wipe --workload sharegpt-32   # Wipe results for one workload
  python pipeline/deploy.py wipe --dry-run                # Preview what would be wiped
  python pipeline/deploy.py pairs                       # List pairs with workloads and packages
  python pipeline/deploy.py pairs --keys-only           # Machine-readable: keys only
""",
    )
    p.add_argument("--run", metavar="NAME",
                   help="Run name (overrides current_run in setup_config.json)")
    p.add_argument("--experiment-root", metavar="PATH", dest="experiment_root",
                   help="Root of the experiment repo (default: framework directory)")

    sub = p.add_subparsers(dest="command")
    collect_p = sub.add_parser("collect", help="Pull results for completed packages")
    collect_p.add_argument("--only",     nargs="+", metavar="PAIR",
                           help="Scope to specific pair keys (comma or space-separated, wl- prefix optional)")
    collect_p.add_argument("--workload", nargs="+", metavar="NAME",
                           help="Scope to pairs matching these workloads (comma or space-separated)")
    collect_p.add_argument("--package", nargs="+", metavar="NAME",
                           help="Collect only these packages (comma or space-separated)")
    collect_p.add_argument("--iteration", metavar="SPEC", dest="iteration",
                           help="Scope to iteration(s): '2', '1,3', '1-3', '1,3-5'")
    collect_p.add_argument("--skip-logs", action="store_true", dest="skip_logs",
                           help="Skip vLLM and EPP log files, collect only traces")

    status_p = sub.add_parser("status", help="Show progress of all (workload, package, iteration) triples")
    status_p.add_argument("--only",     nargs="+", metavar="PAIR",  help="Scope to specific pair keys (comma or space-separated, wl- prefix optional)")
    status_p.add_argument("--workload", nargs="+", metavar="NAME",  help="Scope to pairs matching these workloads (comma or space-separated)")
    status_p.add_argument("--package",  nargs="+", metavar="NAME",  help="Scope to pairs matching these packages (comma or space-separated)")
    status_p.add_argument("--status",   nargs="+", metavar="STATE", help="Scope to pairs matching these statuses (comma or space-separated; e.g. running, done, failed)")
    status_p.add_argument("--iteration", metavar="SPEC", dest="iteration",
                          help="Scope to iteration(s): '2', '1,3', '1-3', '1,3-5'")
    status_p.add_argument("-s", "--silent", action="store_true",
                          help="Suppress the per-pair table; print only the summary line")

    run_p = sub.add_parser("run", help="Orchestrate parallel pool execution")
    run_p.add_argument("--remote", action="store_true", default=False,
                       help="Submit orchestrator as in-cluster Job instead of running locally")
    run_p.add_argument("--only",         nargs="+", metavar="PAIR",  help="Scope execution to specific pair keys (comma or space-separated, wl- prefix optional)")
    run_p.add_argument("--workload",     nargs="+", metavar="NAME",  help="Scope execution to pairs matching these workloads (comma or space-separated)")
    run_p.add_argument("--package",      nargs="+", metavar="NAME",  help="Scope execution to pairs matching these packages (comma or space-separated)")
    run_p.add_argument("--status",       nargs="+", metavar="STATE", help="Scope execution to pairs matching these statuses (comma or space-separated; e.g. failed, timed-out)")
    run_p.add_argument("--iteration",    metavar="SPEC", dest="iteration",
                       help="Scope execution to iteration(s): '2', '1,3', '1-3', '1,3-5'")
    run_p.add_argument("--force",        action="store_true",
                       help="Reset non-pending pairs to pending, cleaning cluster resources for pairs with assigned namespaces")
    run_p.add_argument("--skip-teardown", action="store_true", dest="skip_teardown",
                       help="Skip teardown after PipelineRun completes (keeps namespace intact for debugging)")
    run_p.add_argument("--preserve-pipelineruns", action="store_true", dest="preserve_pipelineruns",
                       help="Do not delete PipelineRun objects after completion (keeps TaskRun logs for debugging)")
    run_p.add_argument("--max-retries",  type=int, default=2, dest="max_retries",
                       help="Max retries for timed-out pairs [2]")
    run_p.add_argument("--poll-interval", type=int, default=30, dest="poll_interval",
                       help="Seconds between status polls [30]")
    run_p.add_argument("--gpu-resource-type", default=None, dest="gpu_resource_type",
                       help="Override GPU resource name (default: derived from scenario, else nvidia.com/gpu)")
    run_p.add_argument("--default-gpu-cost", type=int, default=1, dest="default_gpu_cost",
                       help="Fallback GPU cost per pair when not derivable from scenario [1]")
    run_p.add_argument("--pending-threshold", type=int, default=600, dest="pending_threshold",
                       help="Seconds a pod may remain Pending (recoverable) before early reclaim [600]")
    run_p.add_argument("--max-pending-stalls", type=int, default=10, dest="max_pending_stalls",
                       help="Max early reclaims before marking pair stalled [10]")
    run_p.add_argument("--shadow-ttl", type=int, default=120, dest="shadow_ttl",
                       help="Seconds to retain shadow GPU reservations (prevents over-subscription from probe lag; 0 to disable) [120]")
    run_p.add_argument("--defaults-path", type=Path, default=None, dest="defaults_path",
                       help=argparse.SUPPRESS)

    sub.add_parser("stop", help="Stop the remote orchestrator Job")

    reset_p = sub.add_parser("reset", help="Reset all non-pending pairs to pending (with cluster cleanup)")
    reset_p.add_argument("--only",     nargs="+", metavar="PAIR",  help="Scope to specific pair keys (comma or space-separated, wl- prefix optional)")
    reset_p.add_argument("--workload", nargs="+", metavar="NAME",  help="Scope to pairs matching these workloads (comma or space-separated)")
    reset_p.add_argument("--package",  nargs="+", metavar="NAME",  help="Scope to pairs matching these packages (comma or space-separated)")
    reset_p.add_argument("--status",   nargs="+", metavar="STATE", help="Scope to pairs matching these statuses (comma or space-separated)")
    reset_p.add_argument("--iteration", metavar="SPEC", dest="iteration",
                         help="Scope to iteration(s): '2', '1,3', '1-3', '1,3-5'")
    reset_p.add_argument("--preserve-done-status", action="store_true", dest="preserve_done_status",
                         help="Keep done pairs' status unchanged (cluster cleanup only)")
    reset_p.add_argument("--dry-run",  action="store_true", dest="dry_run",
                         help="Print what would be reset without doing it")

    wipe_p = sub.add_parser("wipe", help="Delete local result files for pairs in scope")
    wipe_p.add_argument("--only",     nargs="+", metavar="PAIR",  help="Scope to specific pair keys (comma or space-separated, wl- prefix optional)")
    wipe_p.add_argument("--workload", nargs="+", metavar="NAME",  help="Scope to pairs matching these workloads (comma or space-separated)")
    wipe_p.add_argument("--package",  nargs="+", metavar="NAME",  help="Scope to pairs matching these packages (comma or space-separated)")
    wipe_p.add_argument("--iteration", metavar="SPEC", dest="iteration",
                        help="Scope to iteration(s): '2', '1,3', '1-3', '1,3-5'")
    wipe_p.add_argument("--dry-run",  action="store_true", dest="dry_run",
                         help="Print what would be wiped without doing it")
    wipe_p.add_argument("--yes", "-y", action="store_true",
                         help="Skip confirmation prompt")

    pairs_p = sub.add_parser("pairs", help="List available pair keys, workloads, and packages")
    pairs_group = pairs_p.add_mutually_exclusive_group()
    pairs_group.add_argument("--keys-only", action="store_true", dest="keys_only",
                             help="Print pair keys only (one per line)")
    pairs_group.add_argument("--workloads-only", action="store_true", dest="workloads_only",
                             help="Print distinct workload names only (one per line)")
    pairs_group.add_argument("--packages-only", action="store_true", dest="packages_only",
                             help="Print distinct package names only (one per line)")

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    machine_readable = (
        (args.command == "pairs" and
         any(getattr(args, f, False)
             for f in ("keys_only", "workloads_only", "packages_only"))) or
        (args.command == "status" and getattr(args, "silent", False))
    )
    if not machine_readable:
        print(_c("36", "\n━━━ sim2real-deploy ━━━\n"))

    _init_experiment_root(args)

    setup_config = _load_setup_config()

    cmd = args.command

    run_name = args.run or setup_config.get("current_run", "")
    if not run_name:
        err("No run name. Use --run NAME or set current_run in setup_config.json.")
        sys.exit(1)
    run_dir = EXPERIMENT_ROOT / "workspace" / "runs" / run_name

    cluster_config = _load_run_cluster_config(run_dir)

    if cmd == "run":
        if getattr(args, "remote", False):
            _cmd_run_remote(args, run_dir, setup_config, cluster_config)
        else:
            _cmd_run(args, run_dir, cluster_config)
    elif cmd == "status":
        _cmd_status(args, run_dir, cluster_config=cluster_config)
    elif cmd == "collect":
        _cmd_collect(args, run_dir, cluster_config)
    elif cmd == "reset":
        cluster_dir = run_dir / "cluster"
        discovered = _load_pairs(cluster_dir)
        namespaces = [ns for ns in (cluster_config.get("namespaces") or []) if ns]
        if not namespaces:
            warn("No namespaces in cluster_config — PipelineRun deletion for done pairs may be incomplete")
        _cmd_reset(args, run_dir, discovered,
                   namespaces=namespaces or None,
                   cluster_config=cluster_config)
    elif cmd == "wipe":
        _cmd_wipe(args, run_dir, cluster_config=cluster_config)
    elif cmd == "pairs":
        cluster_dir = run_dir / "cluster"
        _cmd_pairs(cluster_dir, keys_only=args.keys_only,
                   workloads_only=args.workloads_only,
                   packages_only=args.packages_only)
    elif cmd == "stop":
        namespaces = [ns for ns in (cluster_config.get("namespaces") or []) if ns]
        if not namespaces:
            err("No namespaces configured. Run cluster.py provision with --namespaces.")
            sys.exit(1)
        _cmd_stop(namespace=namespaces[0])
    else:
        err("No subcommand specified. Use: deploy.py run | status | collect | stop | reset | wipe | pairs")
        sys.exit(1)


if __name__ == "__main__":
    main()

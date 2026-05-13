#!/usr/bin/env python3
"""sim2real deploy — Build EPP, orchestrate runs, collect results.

Subcommands:
  run      Build EPP image, submit PipelineRuns
  status   Show progress of all (workload, package) pairs
  collect  Pull results from cluster for completed phases
  cleanup  Tear down cluster resources for all non-pending pairs
  pairs    List available pair keys, workloads, and packages
"""

import argparse
import json
import os
import subprocess
import sys
import time
import yaml

from pathlib import Path

# Ensure repo root is on sys.path when run as a script (python pipeline/deploy.py)
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ── Repo layout ──────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent

# Overridden in main() when --experiment-root is specified.
EXPERIMENT_ROOT = REPO_ROOT


# ── Color helpers ────────────────────────────────────────────────────────────
_tty = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _tty else text


def info(msg: str)  -> None: print(_c("34", "[INFO]  ") + msg)
def ok(msg: str)    -> None: print(_c("32", "[OK]    ") + msg)
def warn(msg: str)  -> None: print(_c("33", "[WARN]  ") + msg, file=sys.stderr)
def err(msg: str)   -> None: print(_c("31", "[ERROR] ") + msg, file=sys.stderr)


def _is_pair_key(key: str) -> bool:
    """Return True if key is a real pair entry (not metadata)."""
    return not key.startswith("_")


def step(n, title: str) -> None:
    print("\n" + _c("36", f"━━━ Step {n}: {title} ━━━"))


# ── Subprocess helper ────────────────────────────────────────────────────────

def run(cmd: list[str], *, check: bool = True, capture: bool = False,
        cwd: "Path | None" = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, text=True, capture_output=capture, cwd=cwd)


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

def _load_setup_config() -> dict:
    path = EXPERIMENT_ROOT / "workspace" / "setup_config.json"
    if not path.exists():
        path = REPO_ROOT / "workspace" / "setup_config.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


# ── EPP build decision ─────────────────────────────────────────────────────────

def _resolve_epp_action(run_dir: Path, skip_build_epp: bool) -> str:
    """Determine EPP build action based on run metadata.

    Returns one of: "build", "skip", "error:<message>"
    """
    run_meta_path = run_dir / "run_metadata.json"
    if not run_meta_path.exists():
        return "error:run_metadata.json not found — run setup.py first."
    try:
        run_meta = json.loads(run_meta_path.read_text())
    except json.JSONDecodeError as e:
        return f"error:run_metadata.json is not valid JSON: {e}. Re-run setup.py."

    component_image = run_meta.get("component_image")
    if component_image is None:
        return "skip"
    if not component_image:
        return "error:component_image is empty in run_metadata.json. Re-run setup.py with a valid --registry."
    if skip_build_epp:
        return "skip"
    return "build"


# ── EPP Image build ─────────────────────────────────────────────────────────

def _build_epp_image(run_dir: Path, run_name: str, namespace: str) -> str:
    """Build EPP image on-cluster. Returns the full image reference."""
    step(1, "Build EPP Image")

    # Locate build-epp.sh
    build_script = REPO_ROOT / "pipeline" / "scripts" / "build-epp.sh"
    if not build_script.exists():
        err(f"build-epp.sh not found at {build_script.relative_to(REPO_ROOT)} — ensure pipeline/scripts/build-epp.sh is present in the repo")
        sys.exit(1)

    result = run(
        ["bash", str(build_script),
         "--run-dir", str(run_dir),
         "--run-name", run_name,
         "--namespace", namespace,
         "--experiment-root", str(EXPERIMENT_ROOT)],
        check=False,
        cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        err("EPP build failed — see output above")
        sys.exit(1)

    # Read image reference from run_metadata.json
    meta_path = run_dir / "run_metadata.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        full_image = meta.get("component_image", "")
        if full_image:
            ok(f"EPP image: {full_image}")
            return full_image

    err("EPP build completed but component_image not set in run_metadata.json")
    sys.exit(1)


# ── PipelineRun helpers ──────────────────────────────────────────────────────

def _cancel_and_delete_pipelinerun(pr_name: str, namespace: str) -> None:
    """If a PipelineRun with the given name exists, cancel it, wait for it to
    finish cancelling, then delete it so a fresh one can be submitted."""
    exists = run(
        ["kubectl", "get", "pipelinerun", pr_name, "-n", namespace],
        check=False, capture=True,
    )
    if exists.returncode != 0:
        return  # doesn't exist, nothing to cancel

    status = _check_pipelinerun_status(pr_name, namespace)
    info(f"Existing PipelineRun {pr_name!r} found (status: {status}); cancelling …")

    if status in ("Running", "Started"):
        run(
            ["kubectl", "patch", "pipelinerun", pr_name,
             "--type=merge", "-p", '{"spec":{"status":"CancelledRunFinally"}}',
             "-n", namespace],
            check=False, capture=True,
        )
        for _ in range(40):  # wait up to 120 s
            time.sleep(3)
            current = _check_pipelinerun_status(pr_name, namespace)
            if current not in ("Running", "Started"):
                info(f"PipelineRun {pr_name!r} cancelled (now: {current})")
                break
        else:
            warn(f"PipelineRun {pr_name!r} did not cancel within 120 s; deleting anyway")

    run(
        ["kubectl", "delete", "pipelinerun", pr_name, "-n", namespace,
         "--ignore-not-found"],
        check=False, capture=True,
    )


# ── Deploy command ───────────────────────────────────────────────────────────

# ── Status command ───────────────────────────────────────────────────────────

def _cmd_status(args, progress_path: Path) -> None:
    """Print a snapshot table of all (workload, package) pair statuses."""
    from pipeline.lib.progress import LocalProgressStore
    store = LocalProgressStore(progress_path)
    progress = store.load()

    if not progress:
        suffix = " (no progress file)" if not progress_path.exists() else ""
        filters_given = any([
            getattr(args, "only", None) is not None,
            getattr(args, "workload", None) is not None,
            getattr(args, "package", None) is not None,
            getattr(args, "status", None) is not None,
        ])
        if filters_given:
            suffix += " — filters ignored"
        print(f"  0 pairs{suffix}")
        return

    pairs = {k: progress[k] for k in _resolve_scope(progress, args)}

    if not pairs:
        print("  0 pairs")
        orch = progress.get("_orchestrator")
        if isinstance(orch, dict) and orch.get("state") not in (None, "normal"):
            print(f"  Orchestrator: {orch.get('state', '?')} (level {orch.get('backoff_level', 0)})")
        print()
        return

    pair_w = max(len(k) for k in pairs) + 2
    col_status = 12
    col_slot = 14
    col_retries = 7

    header = (f"{'PAIR':<{pair_w}} {'STATUS':<{col_status}} {'SLOT':<{col_slot}} {'RETRIES':<{col_retries}}")
    print()
    print(header)
    print("-" * len(header))

    counts: dict[str, int] = {}
    for key, entry in sorted(pairs.items()):
        status = entry.get("status", "unknown")
        slot = entry.get("namespace") or "—"
        retries = entry.get("retries", 0)
        counts[status] = counts.get(status, 0) + 1
        print(f"{key:<{pair_w}} {status:<{col_status}} {slot:<{col_slot}} {retries}")

    print()
    summary_parts = [f"{v} {k}" for k, v in sorted(counts.items())]
    print(f"  {len(pairs)} pairs: " + "  ".join(summary_parts))

    orch = progress.get("_orchestrator")
    if isinstance(orch, dict) and orch.get("state") not in (None, "normal"):
        print(f"  Orchestrator: {orch.get('state', '?')} (level {orch.get('backoff_level', 0)}, "
              f"last probe: {orch.get('last_probe_free_gpus', '?')} free GPUs)")

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
        - status: "failed", namespace: None, pending_since: None
      On True (recoverable threshold exceeded):
        - status: "pending" or "stalled", namespace: None
        - pending_stalls: incremented, pending_since: None
      On False (no action):
        - pending_since: set on first recoverable detection, cleared when
          pods start running, reset on malformed timestamp
    """
    import datetime as _dt
    import json as _json
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
        pods_json = _json.loads(result.stdout)
    except _json.JSONDecodeError:
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
        _cancel_and_delete_pipelinerun(pr_name, namespace)
        entry["status"] = "failed"
        entry["namespace"] = None
        entry["pending_since"] = None
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
    _cancel_and_delete_pipelinerun(pr_name, namespace)
    stalls = entry.get("pending_stalls", 0) + 1
    entry["pending_stalls"] = stalls
    entry["pending_since"] = None
    entry["namespace"] = None
    if stalls >= max_pending_stalls:
        entry["status"] = "stalled"
        warn(f"[{entry.get('workload', '?')}] reached max pending stalls ({max_pending_stalls}) → stalled")
    else:
        entry["status"] = "pending"
    return True


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


def _extract_phases_from_pvc(phases: list[str], run_name: str, namespace: str,
                              run_dir: Path,
                              skip_logs: bool = False,
                              workload: "str | None" = None) -> dict[str, "Exception | None"]:
    """Extract results for one or more phases from data-pvc using a single pod.

    Data layout on PVC (written by run-workload-blis-observe):
      /data/{runName}/{phase}/{workloadName}/trace_header.yaml
      /data/{runName}/{phase}/{workloadName}/trace_data.csv

    When *workload* is set, only that workload's subdirectory is copied for
    each phase (used by inline collection during parallel pool execution).
    When *workload* is None (default), the entire phase directory is copied
    (used by `deploy.py collect` for bulk collection).

    When *skip_logs* is True, only trace files are copied (skipping vLLM and
    EPP log files which typically account for the bulk of the data).

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
    run(["kubectl", "run", pod_name, "--image=alpine:3.19", "--restart=Never",
         "--overrides", overrides, "-n", namespace])

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

            if skip_logs:
                # Selective copy: trace files + epp_logs via kubectl cp per
                # workload; skips vLLM server_logs which dominate data volume.
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
                        errors[phase] = RuntimeError(
                            f"failed to list workloads: {list_result.stderr.strip()}")
                        continue
                    wl_names = list_result.stdout.strip().split() if list_result.stdout.strip() else []
                phase_errors = []
                for wl_name in wl_names:
                    wl_dest = dest_dir / wl_name
                    wl_dest.mkdir(parents=True, exist_ok=True)
                    # Copy trace files
                    for fname in ("trace_data.csv", "trace_header.yaml", "epp_stream_done"):
                        src = f"{namespace}/{pod_name}:/data/{run_name}/{phase}/{wl_name}/{fname}"
                        r = run(["kubectl", "cp", src, str(wl_dest / fname), "--retries=3"],
                                check=False, capture=True)
                        if r.returncode != 0 and "no such file" not in r.stderr.lower():
                            phase_errors.append(f"{wl_name}/{fname}: {r.stderr.strip()}")
                    # Copy epp_logs directory
                    epp_src = f"{namespace}/{pod_name}:/data/{run_name}/{phase}/{wl_name}/epp_logs/"
                    epp_dest = wl_dest / "epp_logs"
                    epp_dest.mkdir(exist_ok=True)
                    r = run(["kubectl", "cp", epp_src, str(epp_dest), "--retries=3"],
                            check=False, capture=True)
                    if r.returncode != 0 and "no such file" not in r.stderr.lower():
                        phase_errors.append(f"{wl_name}/epp_logs: {r.stderr.strip()}")
                if phase_errors:
                    errors[phase] = RuntimeError("; ".join(phase_errors))
                else:
                    errors[phase] = None
            elif workload:
                # Workload-scoped full copy: only this workload's subdirectory.
                wl_dest = dest_dir / workload
                wl_dest.mkdir(parents=True, exist_ok=True)
                result = run(
                    ["kubectl", "cp",
                     f"{namespace}/{pod_name}:/data/{run_name}/{phase}/{workload}/",
                     str(wl_dest), "--retries=3"],
                    check=False, capture=True,
                )
                if result.returncode != 0:
                    errors[phase] = RuntimeError(
                        f"kubectl cp failed: {result.stderr.strip()}")
                else:
                    errors[phase] = None
            else:
                result = run(
                    ["kubectl", "cp",
                     f"{namespace}/{pod_name}:/data/{run_name}/{phase}/",
                     str(dest_dir), "--retries=3"],
                    check=False, capture=True,
                )
                if result.returncode != 0:
                    errors[phase] = RuntimeError(
                        f"kubectl cp failed: {result.stderr.strip()}")
                else:
                    errors[phase] = None
    finally:
        run(["kubectl", "delete", "pod", pod_name, "-n", namespace,
             "--ignore-not-found", "--force", "--grace-period=0"],
            check=False, capture=True)

    return errors


def _cmd_collect(args, run_dir: Path, setup_config: dict):
    """Pull results from cluster for completed phases."""
    namespace = os.environ.get("NAMESPACE", setup_config.get("namespace", ""))
    if not namespace:
        err("No namespace configured.")
        sys.exit(1)

    run_name = run_dir.name

    # Derive known phases from progress.json, fall back to _discover_phases()
    progress_path = run_dir / "progress.json"
    if progress_path.exists():
        try:
            progress = json.loads(progress_path.read_text())
        except json.JSONDecodeError:
            warn(f"Corrupt progress.json at {progress_path} — falling back to default phases")
            progress = None
        else:
            if not isinstance(progress, dict):
                warn("progress.json is not a JSON object — falling back to default phases")
                progress = None
    else:
        progress = None

    if progress:
        known_phases = sorted({
            entry.get("package", "")
            for entry in progress.values()
            if isinstance(entry, dict) and entry.get("status") in ("done", "collecting")
        } - {""})
    else:
        known_phases = []

    if not known_phases:
        cluster_dir = run_dir / "cluster"
        known_phases = _discover_phases(cluster_dir)
        if progress is None and not progress_path.exists():
            warn(f"No progress.json found — discovered phases from cluster/: {known_phases}")
        elif progress is not None:
            warn(f"No done/collecting phases in progress — discovered from cluster/: {known_phases}")

    if args.package:
        valid = set(known_phases) | {"experiment"}
        unknown = set(args.package) - valid
        if unknown:
            err(f"Unknown packages: {sorted(unknown)}. Valid: {sorted(valid)}")
            sys.exit(1)
        phases_to_collect: list[str] = []
        for p in args.package:
            if p == "experiment":
                phases_to_collect.extend(known_phases)
            else:
                phases_to_collect.append(p)
        seen: set[str] = set()
        phases_to_collect = [p for p in phases_to_collect
                             if not (p in seen or seen.add(p))]  # type: ignore[func-returns-value]
    else:
        phases_to_collect = list(known_phases)

    step(1, "Collecting Results")

    collected: list[str] = []
    failed: list[str] = []

    if phases_to_collect:
        try:
            skip_logs = getattr(args, "skip_logs", False)
            errors = _extract_phases_from_pvc(
                phases_to_collect, run_name, namespace, run_dir,
                skip_logs=skip_logs)
        except RuntimeError as e:
            warn(f"Extractor pod failed: {e}")
            failed.extend(phases_to_collect)
        else:
            for phase, exc in errors.items():
                if exc is None:
                    ok(f"Collected: {phase}")
                    collected.append(phase)
                else:
                    warn(f"Extraction failed for {phase}: {exc}")
                    failed.append(phase)

    # Print summary
    print(f"\n  Collected: {len(collected)}/{len(phases_to_collect)} phases")
    if failed:
        print(f"  Failed:    {', '.join(failed)}")
    if collected:
        dirs = "  ".join(f"results/{p}/" for p in collected)
        print(f"  Results:   {run_dir}/{dirs}")
        print("\n  Next:      /sim2real-analyze")
    print()


# ── Run helpers ─────────────────────────────────────────────────────────────

def _load_pairs(cluster_dir: Path) -> dict:
    """Discover all (workload, package) pairs from pipelinerun-*.yaml at cluster/ root.

    Returns dict keyed by "wl-" + filename stem (minus "pipelinerun-" prefix).
    """
    pairs = {}
    if not cluster_dir.exists():
        return pairs
    for pr_path in sorted(cluster_dir.glob("pipelinerun-*.yaml")):
        try:
            pr_data = yaml.safe_load(pr_path.read_text())
            pr_name = pr_data.get("metadata", {}).get("name", pr_path.stem)
            params = {p["name"]: p["value"] for p in pr_data.get("spec", {}).get("params", [])}
            workload = params.get("workloadName", "")
            package = params.get("phase", "")
            key = "wl-" + pr_path.stem.removeprefix("pipelinerun-")
            pairs[key] = {
                "workload": workload,
                "package": package,
                "pr_name": pr_name,
                "pr_path": str(pr_path),
                "namespace": pr_data.get("metadata", {}).get("namespace", ""),
                "scenario_content": params.get("scenarioContent"),
            }
        except Exception as e:
            warn(f"Skipping {pr_path.name}: {e}")
            continue
    return pairs


def _cleanup_pair(key: str, entry: dict, discovered: dict, *,
                  dry_run: bool = False, namespaces: list[str] | None = None) -> bool:
    """Delete PipelineRun and Helm releases for a pair.

    For non-done pairs: also resets state to pending.
    For done pairs: deletes PipelineRun only (Helm already torn down by Tekton).

    Returns True if cleanup was performed, False if it failed and state was NOT reset.
    """
    ns = entry.get("namespace")
    pr_name = discovered.get(key, {}).get("pr_name", "")
    is_done = entry.get("status") == "done"

    # No namespace and no pr_name — just reset state (e.g. collect-failed)
    if not ns and not pr_name:
        if not dry_run and not is_done:
            entry["status"] = "pending"
            entry["retries"] = 0
            entry["pending_stalls"] = 0
            entry["pending_since"] = None
        return True

    if dry_run:
        target = ns or "all namespace slots"
        info(f"[DRY-RUN] {key}: would delete pipelinerun {pr_name or '(unknown)'} in {target}")
        if not is_done:
            info(f"[DRY-RUN] {key}: would uninstall all helm releases in {target}")
        return True

    # Delete PipelineRun
    pr_deleted = False
    if not pr_name:
        warn(f"{key}: no PipelineRun name found — skipping PR deletion (manual check needed)")
    elif ns:
        if entry.get("status") == "running":
            _cancel_and_delete_pipelinerun(pr_name, ns)
            pr_deleted = True
        else:
            result = run(["kubectl", "delete", "pipelinerun", pr_name, "-n", ns,
                         "--ignore-not-found"], check=False, capture=True)
            if result.returncode == 0:
                pr_deleted = True
            else:
                warn(f"{key}: kubectl delete pipelinerun failed in {ns}")
    elif namespaces:
        # Namespace already freed (done/collect-failed) — search all slots
        for slot_ns in namespaces:
            result = run(["kubectl", "delete", "pipelinerun", pr_name, "-n", slot_ns,
                         "--ignore-not-found"], check=False, capture=True)
            if result.returncode == 0:
                pr_deleted = True
        if not pr_deleted:
            warn(f"{key}: kubectl delete pipelinerun failed across all namespace slots")
    else:
        warn(f"{key}: no namespace and no namespace slots — cannot delete pipelinerun {pr_name}")

    # For done pairs, Tekton finally task already tore down Helm releases
    if is_done:
        return True

    if ns and not pr_deleted and pr_name:
        warn(f"{key}: PipelineRun not deleted — state NOT reset")
        return False

    # Discover and uninstall all Helm releases in the namespace
    if ns:
        result = run(["helm", "list", "-n", ns, "-q"], check=False, capture=True)
        if result.returncode != 0:
            warn(f"{key}: helm list failed in {ns} — skipping cleanup (manual intervention needed)")
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

    # Reset state
    entry["status"] = "pending"
    entry["namespace"] = None
    entry["retries"] = 0
    entry["pending_stalls"] = 0
    entry["pending_since"] = None
    return True


def _force_reset(progress: dict, scope: set, discovered: dict | None = None,
                 namespaces: list[str] | None = None) -> int:
    """Reset non-pending, non-done pairs in scope to pending.

    Calls _cleanup_pair for cluster teardown when possible. Pairs where
    cleanup fails are skipped (not counted, state preserved).
    """
    reset = 0
    for key in scope:
        entry = progress.get(key, {})
        if entry.get("status") not in (None, "pending", "done"):
            try:
                if _cleanup_pair(key, entry, discovered or {},
                                 namespaces=namespaces):
                    reset += 1
            except Exception as e:
                warn(f"{key}: cleanup failed during --force: {e}")
    return reset


def _apply_run_filters(progress: dict, args) -> set:
    """Return the set of pair keys in scope for this invocation.

    With no flags: returns empty set (caller interprets as all pairs in scope).
    With flags: returns only matching pairs.
    """
    only = getattr(args, "only", None)
    workload = getattr(args, "workload", None)
    package = getattr(args, "package", None)
    status_filter = getattr(args, "status", None)

    if only:
        only = only.strip()
        if only in progress and _is_pair_key(only):
            return {only}
        prefixed = "wl-" + only
        if prefixed in progress:
            info(f"--only: resolved '{only}' → '{prefixed}'")
            return {prefixed}
        return set()

    if not any([workload, package, status_filter]):
        return set()

    candidates = {k for k in progress.keys() if _is_pair_key(k)}
    if workload:
        candidates = {k for k in candidates if progress[k].get("workload") == workload}
    if package:
        candidates = {k for k in candidates if progress[k].get("package") == package}
    if status_filter:
        candidates = {k for k in candidates if progress[k].get("status") == status_filter}
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
    ])
    filtered = _apply_run_filters(progress, args)
    if filters_given and not filtered:
        _report_filter_mismatch(progress, args)
        sys.exit(1)
    return filtered or {k for k in progress.keys() if _is_pair_key(k)}


def _report_filter_mismatch(progress: dict, args) -> None:
    """Print all valid filter values to help the user correct their filter flags."""
    only = getattr(args, "only", None)
    workload = getattr(args, "workload", None)
    package = getattr(args, "package", None)
    status_filter = getattr(args, "status", None)

    parts = []
    if only is not None:
        parts.append(f"--only '{only}'")
    if workload is not None:
        parts.append(f"--workload '{workload}'")
    if package is not None:
        parts.append(f"--package '{package}'")
    if status_filter is not None:
        parts.append(f"--status '{status_filter}'")

    err(f"No pairs matched {', '.join(parts)}.\n")

    keys = sorted(k for k in progress.keys() if _is_pair_key(k))
    print(f"  Valid pair keys ({len(keys)}):", file=sys.stderr)
    for k in keys:
        print(f"    {k}", file=sys.stderr)

    pair_values = [v for k, v in progress.items() if _is_pair_key(k)]
    valid_workloads = sorted({v.get("workload", "") for v in pair_values} - {""})
    valid_packages = sorted({v.get("package", "") for v in pair_values} - {""})
    valid_statuses = sorted({v.get("status", "") for v in pair_values} - {""})

    print(f"\n  Valid --workload values: {', '.join(valid_workloads)}", file=sys.stderr)
    print(f"  Valid --package values:  {', '.join(valid_packages)}", file=sys.stderr)
    print(f"  Valid --status values:   {', '.join(valid_statuses)}", file=sys.stderr)


def _check_slot_ready(namespace: str, hf_secret_name: str = "hf-secret") -> tuple[bool, list[str]]:
    """Check that a namespace slot is ready to accept a new PipelineRun.

    Checks: PVCs bound, HF secret present.
    Returns (ready, list_of_failure_reasons).

    Note: Tekton tasks presence check is not yet implemented; assumes
    ``setup.py`` has been run.
    """
    failures = []

    for pvc in ["data-pvc", "source-pvc"]:
        result = run(
            ["kubectl", "get", "pvc", pvc, f"-n={namespace}",
             "-o", "jsonpath={.status.phase}"],
            check=False, capture=True,
        )
        if result.returncode != 0 or result.stdout.strip() != "Bound":
            hint = " — re-run setup.py to provision it" if pvc == "source-pvc" else ""
            failures.append(f"PVC {pvc} not Bound in {namespace}{hint}")

    result = run(
        ["kubectl", "get", "secret", hf_secret_name, f"-n={namespace}"],
        check=False, capture=True,
    )
    if result.returncode != 0:
        failures.append(f"Secret {hf_secret_name} missing in {namespace}")

    return len(failures) == 0, failures


def _collect_pair(pair_key: str, entry: dict, run_dir: Path) -> bool:
    """Collect results for a completed pair inline. Returns True on success."""
    namespace = entry.get("namespace", "")
    package = entry.get("package", "")
    wl_name = entry.get("workload", "")
    run_name = run_dir.name

    if not namespace or not package:
        return False

    try:
        errors = _extract_phases_from_pvc(
            phases=[package],
            run_name=run_name,
            namespace=namespace,
            run_dir=run_dir,
            skip_logs=False,
            workload=wl_name or None,
        )
        return errors.get(package) is None
    except RuntimeError as e:
        warn(f"Collection failed for {pair_key}: {e}")
        return False


def _reconcile_collecting(key: str, entry: dict, run_dir: Path) -> None:
    pkg = entry.get("package", "")
    wl = entry.get("workload", "")
    if (run_dir / "results" / pkg / wl / "trace_data.csv").exists():
        entry["status"] = "done"
    else:
        entry["status"] = "done" if _collect_pair(key, entry, run_dir) else "pending"
    entry["namespace"] = None


def _do_collect(pair_key: str, entry: dict, run_dir: Path, store, progress: dict) -> bool:
    ok = False
    try:
        ok = _collect_pair(pair_key, entry, run_dir)
        entry["status"] = "done" if ok else "collect-failed"
    except BaseException:
        entry["status"] = "collect-failed"
        raise
    finally:
        entry["namespace"] = None
        store.save(progress)
    return ok


def _derive_pair_gpu_costs(
    discovered: dict,
    *,
    defaults: dict | None,
    fallback_cost: int,
) -> dict[str, int]:
    """Compute GPU cost per pair from its scenarioContent.

    Fallback chain per pair:
      1. Parse scenarioContent → gpu_cost_per_pair(scenario, defaults)
      2. If scenarioContent missing/invalid → gpu_cost_per_pair({}, defaults)
      3. If defaults unavailable or derivation fails → fallback_cost
    """
    from pipeline.lib.capacity import gpu_cost_per_pair

    costs = {}
    for key, meta in discovered.items():
        if defaults is None:
            costs[key] = fallback_cost
            continue

        scenario_content = meta.get("scenario_content")
        resolved = None
        if scenario_content:
            try:
                resolved = yaml.safe_load(scenario_content)
            except yaml.YAMLError:
                pass

        if resolved and isinstance(resolved, dict):
            result = gpu_cost_per_pair(resolved, defaults)
        else:
            result = gpu_cost_per_pair({}, defaults)

        if isinstance(result, int):
            costs[key] = result
        else:
            costs[key] = fallback_cost

    return costs


def _capacity_limited_pairs(
    pending: list[str],
    progress: dict,
    *,
    free_gpus: int,
    default_gpu_cost: int,
) -> list[str]:
    """Select pending pairs that fit within available GPU capacity.

    Sorts by gpu_cost ascending to maximize dispatch count.
    """
    sorted_pending = sorted(
        pending,
        key=lambda k: progress[k].get("gpu_cost", default_gpu_cost),
    )
    result = []
    budget = free_gpus
    for pair in sorted_pending:
        cost = progress[pair].get("gpu_cost", default_gpu_cost)
        if budget >= cost:
            budget -= cost
            result.append(pair)
    return result


def _cmd_run(args, run_dir: Path, setup_config: dict) -> None:
    """Orchestrate parallel pool execution across namespace slots."""
    import datetime as _dt
    import tempfile as _tmp
    from pipeline.lib.progress import LocalProgressStore
    from pipeline.lib.backoff import BackoffController
    from pipeline.lib.capacity import (
        probe_free_gpus, gpu_cost_per_pair, derive_gpu_resource_type, load_defaults
    )

    namespaces = setup_config.get("namespaces") or [setup_config.get("namespace", "")]
    if not namespaces or not namespaces[0]:
        err("No namespaces configured. Run setup.py with --namespaces."); sys.exit(1)

    # Decide whether to build EPP image
    epp_action = _resolve_epp_action(run_dir, args.skip_build_epp)
    if epp_action.startswith("error:"):
        err(epp_action[len("error:"):]); sys.exit(1)
    elif epp_action == "skip":
        info("No component_image in run metadata — skipping EPP build")
    else:
        _build_epp_image(run_dir, run_dir.name, namespaces[0])

    max_retries = getattr(args, "max_retries", 2)
    poll_interval = getattr(args, "poll_interval", 30)
    pending_threshold = getattr(args, "pending_threshold", 600)
    max_pending_stalls = getattr(args, "max_pending_stalls", 10)

    cluster_dir = run_dir / "cluster"
    progress_path = run_dir / "progress.json"
    store = LocalProgressStore(progress_path)

    # Derive GPU resource type and cost from scenario + defaults
    # CLI --gpu-resource-type overrides auto-derivation when explicitly set
    gpu_resource_type = args.gpu_resource_type  # None means auto-derive
    pair_gpu_cost = args.default_gpu_cost
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
    if defaults_result and scenario_path.exists():
        try:
            resolved = yaml.safe_load(scenario_path.read_text()) or {}
        except yaml.YAMLError as e:
            warn(f"Could not parse {scenario_path.name}: {e}")
            resolved = None
        if resolved:
            if gpu_resource_type is None:
                gpu_resource_type = derive_gpu_resource_type(resolved, defaults_result)
            derived_cost = gpu_cost_per_pair(resolved, defaults_result)
            if isinstance(derived_cost, int):
                pair_gpu_cost = derived_cost
            else:
                warn(f"GPU cost derivation failed: {derived_cost} — using fallback ({pair_gpu_cost})")
    elif defaults_result is None and not scenario_path.exists():
        info("Defaults or scenario not found — using CLI defaults")
    if gpu_resource_type is None:
        gpu_resource_type = "nvidia.com/gpu"
    if gpu_resource_type != "nvidia.com/gpu":
        info(f"GPU resource type: {gpu_resource_type}")
    info(f"GPU cost per pair: {pair_gpu_cost}")
    _probe_fail_count = 0
    _last_probe_error = ""

    # Load or initialize progress
    progress = store.load()

    max_backoff = getattr(args, "max_backoff", 600)
    existing_orch = progress.get("_orchestrator")
    if isinstance(existing_orch, dict):
        backoff = BackoffController.from_dict(existing_orch, base_interval=poll_interval, max_backoff=max_backoff)
        if backoff.state != "normal":
            info(f"Resuming in {backoff.state} state (level {backoff.backoff_level})")
    else:
        backoff = BackoffController(base_interval=poll_interval, max_backoff=max_backoff)

    discovered = _load_pairs(cluster_dir)
    if not discovered:
        err("No pairs found in cluster/. Run prepare.py first."); sys.exit(1)

    # Initialize new entries (first run or new pairs added)
    for key, meta in discovered.items():
        if key not in progress:
            progress[key] = {
                "workload": meta["workload"],
                "package":  meta["package"],
                "status":   "pending",
                "namespace": None,
                "retries":  0,
                "gpu_cost": pair_gpu_cost,
                "pending_stalls": 0,
                "pending_since": None,
            }

    _scope = _resolve_scope(progress, args)
    total_pairs = sum(1 for k in progress if _is_pair_key(k))
    if len(_scope) < total_pairs:
        info(f"Scope: {len(_scope)}/{total_pairs} pairs")

    if getattr(args, "force", False):
        n = _force_reset(progress, _scope, discovered, namespaces=namespaces)
        if n:
            info(f"--force: reset {n} non-pending pair(s) to pending")
        else:
            info("--force: no non-pending pairs found in scope — nothing reset")
        store.save(progress)

    # Reconcile 'running' entries against actual cluster state on resume
    for key, entry in progress.items():
        if not _is_pair_key(key):
            continue
        if entry["status"] == "running":
            pr_meta = discovered.get(key, {})
            pr_name = pr_meta.get("pr_name", "")
            ns = entry.get("namespace") or ""
            if pr_name and ns:
                actual = _check_pipelinerun_status(pr_name, ns)
                if actual == "Succeeded":
                    entry["status"] = "collecting"
                    entry["pending_since"] = None
                elif actual in ("Failed", "PipelineRunCancelled"):
                    entry["status"] = "failed"
                    entry["pending_since"] = None
                elif actual == "Unknown":
                    warn(f"[{key}] PipelineRun not found on cluster → resetting to pending")
                    entry["status"] = "pending"
                    entry["namespace"] = None
                    entry["pending_since"] = None
                # If still "Running"/"Started", leave as "running" — will be monitored
            else:
                entry["status"] = "pending"
                entry["namespace"] = None
                entry["pending_since"] = None
        elif entry["status"] == "collecting":
            _reconcile_collecting(key, entry, run_dir)
    store.save(progress)

    # Track which namespace is assigned to which pair
    slots_busy: dict[str, str] = {
        entry["namespace"]: key
        for key, entry in progress.items()
        if _is_pair_key(key) and entry.get("status") == "running" and entry.get("namespace")
    }

    def _pending_pairs() -> list[str]:
        return [k for k, v in progress.items()
                if _is_pair_key(k) and v.get("status") == "pending" and k in _scope]

    def _work_remaining() -> bool:
        return any(v.get("status") in ("pending", "running", "collecting")
                   for k, v in progress.items() if _is_pair_key(k) and k in _scope)

    timeout_hours = 4
    info(f"Orchestrator: {len(_scope)} pairs in scope, {len(namespaces)} slot(s)")
    if not _work_remaining() and not slots_busy:
        info(f"All {len(_scope)} pairs in scope already done — nothing to dispatch (use --force to reset)")
        return

    while _work_remaining() or slots_busy:

        # ── Process completed/failed slots ───────────────────────────────
        for ns in list(slots_busy):
            pair_key = slots_busy[ns]
            entry = progress[pair_key]
            pr_meta = discovered.get(pair_key, {})
            pr_name = pr_meta.get("pr_name", "")

            status = _check_pipelinerun_status(pr_name, ns) if pr_name else "Unknown"

            if status == "Succeeded":
                info(f"[{pair_key}] Succeeded → collecting")
                entry["status"] = "collecting"
                store.save(progress)
                ok_collect = False
                try:
                    ok_collect = _do_collect(pair_key, entry, run_dir, store, progress)
                finally:
                    del slots_busy[ns]
                if ok_collect:
                    ok(f"[{pair_key}] {entry['status']}")
                else:
                    warn(f"[{pair_key}] {entry['status']}")

            elif status in ("Failed", "PipelineRunCancelled", "PipelineRunCouldntGetPipeline",
                            "PipelineRunTimeout", "CreateRunFailed", "PipelineRunStopping",
                            "PipelineRunStoppingTimeout"):
                warn(f"[{pair_key}] hard failure ({status}) → failed")
                entry["status"] = "failed"
                entry["namespace"] = None
                store.save(progress)
                del slots_busy[ns]

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
                    if entry.get("status") == "pending":
                        try:
                            prev_state = backoff.state
                            backoff.signal_reclaim()
                            if prev_state == "normal" and backoff.state == "backing_off":
                                warn(f"Reclaims triggered backoff (next poll: {backoff.effective_interval}s)")
                        except (ValueError, TypeError) as exc:
                            warn(f"Backoff signal_reclaim failed: {exc} — ignoring")
                    del slots_busy[ns]
                    progress["_orchestrator"] = backoff.to_dict()
                    store.save(progress)
                    continue

                # Check for timeout
                ts_result = run(
                    ["kubectl", "get", "pipelinerun", pr_name, f"-n={ns}",
                     "-o", "jsonpath={.metadata.creationTimestamp}"],
                    check=False, capture=True,
                )
                if ts_result.returncode == 0 and ts_result.stdout.strip():
                    try:
                        created = _dt.datetime.fromisoformat(
                            ts_result.stdout.strip().replace("Z", "+00:00"))
                        age_h = (_dt.datetime.now(_dt.timezone.utc) - created).total_seconds() / 3600
                        if age_h > timeout_hours:
                            retries = entry.get("retries", 0)
                            _cancel_and_delete_pipelinerun(pr_name, ns)
                            if retries < max_retries:
                                warn(f"[{pair_key}] timed out → requeue (attempt {retries + 1}/{max_retries})")
                                entry["status"] = "pending"
                                entry["retries"] = retries + 1
                            else:
                                warn(f"[{pair_key}] timed out, max retries → timed-out")
                                entry["status"] = "timed-out"
                            entry["namespace"] = None
                            entry["pending_since"] = None
                            del slots_busy[ns]
                            store.save(progress)
                    except ValueError:
                        pass

        # ── Capacity probe ───────────────────────────────────────────────
        capacity = probe_free_gpus(gpu_resource_type=gpu_resource_type)
        if isinstance(capacity, tuple):
            free_gpus, allocatable, requested = capacity
            info(f"Capacity: {free_gpus} free GPUs ({allocatable} allocatable − {requested} requested)")
            if _probe_fail_count > 0:
                info(f"Capacity probe recovered after {_probe_fail_count} failure(s)")
            _probe_fail_count = 0
            _last_probe_error = ""
        else:
            free_gpus = None
            _probe_fail_count += 1
            if capacity != _last_probe_error or _probe_fail_count % 10 == 0:
                warn(f"Capacity probe failed: {capacity} — dispatching without GPU gating")
            _last_probe_error = capacity

        # ── Backoff signals ──────────────────────────────────────────────
        if free_gpus is not None:
            backoff.last_probe_free_gpus = free_gpus
        pending = _pending_pairs()
        if free_gpus is not None and pending:
            min_cost = min(progress[k].get("gpu_cost", pair_gpu_cost) for k in pending)
            max_cost = max(progress[k].get("gpu_cost", pair_gpu_cost) for k in pending)
            if free_gpus < min_cost:
                prev_state = backoff.state
                backoff.signal_scarcity(free_gpus=free_gpus, min_cost=min_cost)
                if prev_state == "normal":
                    warn(f"Scarcity detected: {free_gpus} free GPUs, entering backoff (next poll: {backoff.effective_interval}s)")
                else:
                    info(f"Backoff level {backoff.backoff_level} — next poll in {backoff.effective_interval}s")
            elif free_gpus >= max_cost:
                if backoff.state != "normal":
                    info(f"Backoff probe: {free_gpus} free GPUs available → resuming normal dispatch")
                backoff.signal_capacity(free_gpus=free_gpus, max_cost=max_cost)

        # ── Assign pending work to free slots ────────────────────────────
        free_slots = [ns for ns in namespaces if ns not in slots_busy]
        pending = _pending_pairs()
        if free_gpus is not None and pending:
            min_cost = min(progress[k].get("gpu_cost", pair_gpu_cost) for k in pending)
            if not backoff.should_dispatch(free_gpus=free_gpus, min_cost=min_cost):
                info(f"Backoff: skipping dispatch ({len(pending)} pending, {free_gpus} free GPUs)")
                dispatchable = []
            else:
                dispatchable = _capacity_limited_pairs(
                    pending, progress,
                    free_gpus=free_gpus, default_gpu_cost=pair_gpu_cost,
                )
                if len(dispatchable) == 0 and pending:
                    smallest = min(progress[k].get("gpu_cost", pair_gpu_cost) for k in pending)
                    warn(f"Dispatching 0/{len(pending)} pending pairs — smallest cost ({smallest}) exceeds free GPUs ({free_gpus})")
                elif len(dispatchable) < len(pending):
                    info(f"Dispatching {len(dispatchable)}/{len(pending)} pending pairs (capacity-limited: {free_gpus} free GPUs)")
                elif len(free_slots) < len(dispatchable):
                    info(f"Dispatching {len(free_slots)}/{len(pending)} pending pairs (slot-limited)")
        else:
            dispatchable = pending

        for ns, pair_key in zip(free_slots, dispatchable):
            hf_secret_name = setup_config.get("hf_secret_name", "hf-secret")
            ready, reasons = _check_slot_ready(ns, hf_secret_name=hf_secret_name)
            if not ready:
                warn(f"Slot {ns} not ready: {'; '.join(reasons)}")
                continue

            entry = progress[pair_key]
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

            entry["status"] = "running"
            entry["namespace"] = ns
            entry["pending_since"] = None
            slots_busy[ns] = pair_key
            store.save(progress)
            ok(f"[{pair_key}] → {ns} ({pr_name})")
            if backoff.state != "normal":
                backoff.signal_scheduling_success()
                info("Scheduling success → backoff reset")

        # Persist backoff state
        progress["_orchestrator"] = backoff.to_dict()
        store.save(progress)

        if _work_remaining() or slots_busy:
            time.sleep(backoff.effective_interval)

    # Final summary
    counts: dict[str, int] = {}
    for k, v in progress.items():
        if k in _scope:
            counts[v["status"]] = counts.get(v["status"], 0) + 1
    print()
    ok("Run complete: " + "  ".join(f"{v} {k}" for k, v in sorted(counts.items())))
    print(f"  Progress: {progress_path}")
    print()


def _cmd_cleanup(args, progress_path: Path, discovered: dict,
                 namespaces: list[str] | None = None) -> None:
    """Tear down cluster resources for all non-pending pairs."""
    from pipeline.lib.progress import LocalProgressStore
    store = LocalProgressStore(progress_path)
    progress = store.load()

    if not progress:
        info("No progress data found — nothing to clean up")
        return

    _scope = _resolve_scope(progress, args)

    # Exclude pending (nothing to clean)
    actionable = {k for k in _scope
                  if progress[k].get("status") not in (None, "pending")}

    if not actionable:
        info("No pairs need cleanup (all pending)")
        return

    dry_run = getattr(args, "dry_run", False)
    total_pairs = sum(1 for k in progress if _is_pair_key(k))
    info(f"Scope: {len(actionable)}/{total_pairs} pairs"
         + (" [DRY-RUN]" if dry_run else ""))

    cleaned = 0
    errors = 0
    for key in sorted(actionable):
        entry = progress[key]
        try:
            if _cleanup_pair(key, entry, discovered, dry_run=dry_run,
                             namespaces=namespaces):
                cleaned += 1
            else:
                errors += 1
        except Exception as e:
            err(f"{key}: cleanup failed — {e}")
            errors += 1

    if not dry_run:
        store.save(progress)

    msg = f"{cleaned} pair(s) cleaned up"
    if errors:
        msg += f" ({errors} failed — manual intervention needed)"
    ok(msg)


# ── CLI ──────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="deploy.py",
        description="sim2real deploy — Build EPP, orchestrate runs, collect results",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python pipeline/deploy.py run                        # Build EPP + orchestrate all pairs
  python pipeline/deploy.py run --skip-build-epp       # Orchestrate without EPP build
  python pipeline/deploy.py status                     # Show progress snapshot
  python pipeline/deploy.py collect                    # Pull results for completed phases
  python pipeline/deploy.py collect --skip-logs        # Collect traces only (skip large logs)
  python pipeline/deploy.py cleanup                    # Tear down stalled/failed pairs
  python pipeline/deploy.py cleanup --dry-run          # Preview what would be cleaned
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
    collect_p.add_argument("--package", nargs="+", metavar="NAME",
                           help="Collect only these packages")
    collect_p.add_argument("--skip-logs", action="store_true", dest="skip_logs",
                           help="Skip vLLM and EPP log files, collect only traces")

    status_p = sub.add_parser("status", help="Show progress of all (workload, package) pairs")
    status_p.add_argument("--only",     metavar="PAIR",  help="Scope to one specific pair key (wl- prefix optional)")
    status_p.add_argument("--workload", metavar="NAME",  help="Scope to pairs matching this workload")
    status_p.add_argument("--package",  metavar="NAME",  help="Scope to pairs matching this package")
    status_p.add_argument("--status",   metavar="STATE", help="Scope to pairs with this status (e.g. running, done, failed)")

    run_p = sub.add_parser("run", help="Orchestrate parallel pool execution")
    run_p.add_argument("--skip-build-epp", action="store_true", dest="skip_build_epp",
                       help="Skip EPP image build")
    run_p.add_argument("--only",         metavar="PAIR",  help="Scope execution to one specific pair key (wl- prefix optional)")
    run_p.add_argument("--workload",     metavar="NAME",  help="Scope execution to pairs matching this workload")
    run_p.add_argument("--package",      metavar="NAME",  help="Scope execution to pairs matching this package")
    run_p.add_argument("--status",       metavar="STATE", help="Scope execution to pairs with this status (e.g. failed, timed-out)")
    run_p.add_argument("--force",        action="store_true",
                       help="Reset non-pending pairs to pending, cleaning cluster resources for pairs with assigned namespaces")
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
    run_p.add_argument("--max-backoff", type=int, default=600, dest="max_backoff",
                       help="Maximum backoff interval in seconds during GPU scarcity [600]")

    cleanup_p = sub.add_parser("cleanup", help="Tear down cluster resources for all non-pending pairs")
    cleanup_p.add_argument("--only",     metavar="PAIR",  help="Scope to one specific pair key (wl- prefix optional)")
    cleanup_p.add_argument("--workload", metavar="NAME",  help="Scope to pairs matching this workload")
    cleanup_p.add_argument("--package",  metavar="NAME",  help="Scope to pairs matching this package")
    cleanup_p.add_argument("--status",   metavar="STATE", help="Scope to pairs with this status")
    cleanup_p.add_argument("--dry-run",  action="store_true", dest="dry_run",
                           help="Print what would be cleaned up without doing it")

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
    machine_readable = (args.command == "pairs" and
                         any(getattr(args, f, False)
                             for f in ("keys_only", "workloads_only", "packages_only")))
    if not machine_readable:
        print(_c("36", "\n━━━ sim2real-deploy ━━━\n"))

    global EXPERIMENT_ROOT
    EXPERIMENT_ROOT = Path(args.experiment_root).resolve() if args.experiment_root else Path.cwd()

    setup_config = _load_setup_config()
    run_name = args.run or setup_config.get("current_run", "")
    if not run_name:
        err("No run name. Use --run NAME or set current_run in setup_config.json.")
        sys.exit(1)
    run_dir = EXPERIMENT_ROOT / "workspace" / "runs" / run_name

    if not run_dir.exists():
        err(f"Run directory not found: {run_dir}")
        sys.exit(1)

    cmd = args.command
    if cmd == "run":
        _cmd_run(args, run_dir, setup_config)
    elif cmd == "status":
        progress_path = run_dir / "progress.json"
        _cmd_status(args, progress_path)
    elif cmd == "collect":
        _cmd_collect(args, run_dir, setup_config)
    elif cmd == "cleanup":
        progress_path = run_dir / "progress.json"
        cluster_dir = run_dir / "cluster"
        discovered = _load_pairs(cluster_dir)
        namespaces = [ns for ns in (setup_config.get("namespaces") or
                      [setup_config.get("namespace", "")]) if ns]
        if not namespaces:
            warn("No namespaces in setup_config — PipelineRun deletion for done pairs may be incomplete")
        _cmd_cleanup(args, progress_path, discovered, namespaces=namespaces or None)
    elif cmd == "pairs":
        cluster_dir = run_dir / "cluster"
        _cmd_pairs(cluster_dir, keys_only=args.keys_only,
                   workloads_only=args.workloads_only,
                   packages_only=args.packages_only)
    else:
        err("No subcommand specified. Use: deploy.py run | status | collect | cleanup | pairs")
        sys.exit(1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""sim2real deploy — Build EPP, Cluster Benchmarks, PR."""

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import threading
import time
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# ── Repo layout ───────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent
VENV_PYTHON = str(REPO_ROOT / ".venv/bin/python")
CLI = str(REPO_ROOT / "tools/transfer_cli.py")

PIPELINE_TIMEOUT_SECS = 14400  # 4 hours

# Lock for concurrent benchmark-state JSON updates
_bench_state_lock = threading.Lock()


class PhaseError(RuntimeError):
    """Raised when a pipeline phase fails (replaces sys.exit in threaded context)."""


def _preflight_cmd(phase: str, values_path: str, namespace: str,
                   manifest: dict | None = None) -> list[str]:
    """Build the preflight CLI command, adding manifest-derived flags when available."""
    cmd = [VENV_PYTHON, CLI, "preflight",
           "--phase", phase,
           "--values", values_path,
           "--namespace", namespace]
    if manifest:
        helm_path = manifest.get("config", {}).get("helm_path")
        if helm_path:
            cmd += ["--helm-path", f"stack.{helm_path}"]
        # Use first test command as build check (e.g. ["go", "build", "./..."])
        test_cmds = manifest.get("target", {}).get("test_commands", [])
        if test_cmds:
            import shlex
            cmd += ["--build-command", shlex.join(test_cmds[0])]
    return cmd

# ── Color helpers ─────────────────────────────────────────────────────────────
_tty = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _tty else text


def info(msg: str) -> None: print(_c("34", "[INFO]  ") + msg)
def ok(msg: str)   -> None: print(_c("32", "[OK]    ") + msg)
def warn(msg: str) -> None: print(_c("33", "[WARN]  ") + msg)
def err(msg: str)  -> None: print(_c("31", "[ERROR] ") + msg, file=sys.stderr)


def step(n, title: str) -> None:
    print("\n" + _c("36", f"━━━ Step {n}: {title} ━━━"))


# ── Subprocess helper ─────────────────────────────────────────────────────────

def run(cmd: list[str], *, check: bool = True, capture: bool = False,
        input: str | None = None, cwd: Path | None = None,
        env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, check=check, text=True,
        capture_output=capture, input=input,
        cwd=cwd, env=env,
    )


# ── Argument parser ───────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="deploy.py",
        description="sim2real deploy: Build EPP → Cluster Benchmarks → PR",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/deploy.py                      # EPP build + benchmarks (no PR)
  python scripts/deploy.py --pr                 # EPP build + benchmarks + PR creation
  python scripts/deploy.py --skip-build-epp     # resume benchmarks (EPP already built)
  python scripts/deploy.py --skip-build-epp --pr  # resume and create PR
  python scripts/deploy.py --skip-build-epp --force-rerun  # re-run all done phases

Environment variables:
  NAMESPACE   Override namespace from workspace/setup_config.json
""",
    )
    p.add_argument("--run", metavar="NAME",
                   help="Run name to operate on (overrides current_run in setup_config.json)")
    p.add_argument("--manifest", type=Path, default=REPO_ROOT / "config/transfer.yaml",
                   help="Path to transfer.yaml manifest")
    p.add_argument("--skip-build-epp", action="store_true",
                   help="Skip EPP image build (use if already built this run)")
    p.add_argument("--pr", action="store_true",
                   help="Create PR after benchmarks pass (default: skip, review results first)")
    p.add_argument("--force-rerun", action="store_true",
                   help="Re-run already-done benchmark phases without prompting")
    p.add_argument("--phases", nargs="+", metavar="PHASE",
                   choices=["noise", "baseline", "treatment"],
                   help="Run only these benchmark phases (e.g. --phases baseline)")
    p.add_argument("--parallel", type=int, default=1, metavar="N",
                   help="Max pipeline phases to run concurrently (default: 1)")
    p.add_argument("--parallel-workloads", type=int, default=1, metavar="N",
                   dest="parallel_workloads",
                   help="Max workload stacks to run concurrently within a phase (default: 1)")
    return p


# ── Pure data helpers (unit-tested) ──────────────────────────────────────────

def _inject_image_reference(alg_values: dict, hub: str, name: str, tag: str) -> dict:
    """Inject EPP image hub+name+tag into algorithm_values dict. Returns modified dict.

    Uses direct assignment (not update) to fully replace any prior image dict, ensuring
    hub, name, and tag are always consistent. Mirrors the build-push-epp CLI behavior.
    """
    (alg_values
        .setdefault("stack", {})
        .setdefault("gaie", {})
        .setdefault("treatment", {})
        .setdefault("helmValues", {})
        .setdefault("inferenceExtension", {})
        ["image"]) = {"hub": hub, "name": name, "tag": tag}
    return alg_values


def _construct_validation_results(equiv: dict, fast_iter: bool) -> dict:
    """Build partial validation_results dict from equivalence gate output.

    In fast mode, adds overall_verdict. In full mode, leaves overall_verdict
    absent (set later by _merge_benchmark_into_validation).

    The equivalence gate stores results under dynamic command names (not
    hardcoded suite_a/b/c), so we copy all entries into val["equivalence"].
    """
    val = {"equivalence": {k: v for k, v in equiv.items() if k != "skipped"}}
    if fast_iter:
        passed = all(
            entry.get("passed") for entry in val["equivalence"].values()
            if entry.get("fatal", True)
        )
        val["overall_verdict"] = "PASS" if passed else "FAIL"
    return val


def _merge_benchmark_into_validation(val: dict, bench: dict) -> dict:
    """Merge benchmark_output dict into validation_results dict.

    noise_cv goes to top-level; all other bench keys go under val['benchmark'].
    overall_verdict is derived from mechanism_check_verdict and suite results.
    """
    val["benchmark"] = {k: v for k, v in bench.items() if k != "noise_cv"}
    val["noise_cv"] = bench["noise_cv"]

    mech = bench.get("mechanism_check_verdict", "ERROR")
    equiv_passed = all(
        entry.get("passed") for entry in val.get("equivalence", {}).values()
        if entry.get("fatal", True)
    )
    if mech == "PASS" and equiv_passed:
        val["overall_verdict"] = "PASS"
    elif mech == "INCONCLUSIVE":
        val["overall_verdict"] = "INCONCLUSIVE"
    else:
        val["overall_verdict"] = "FAIL"
    return val


# ── Per-workload isolation helpers ────────────────────────────────────────────

def _workload_slug(name: str) -> str:
    """Convert workload name to a DNS-safe slug (lowercase, hyphens, max 40 chars).

    Strips a leading 'workload_' or 'workload-' prefix if present, to keep
    k8s resource names short (workload names in values.yaml often carry this prefix).
    """
    import re
    name = re.sub(r"^workload[_-]", "", name)
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:40].rstrip("-")


def _make_run_name(phase: str, ts: int | None = None) -> str:
    """Shared run name for a phase invocation. Used as PVC results directory."""
    if ts is None:
        ts = int(time.time())
    return f"sim2real-{phase}-{ts}"


def _make_experiment_id(phase: str, workload_name: str, ts: int | None = None,
                         idx: int = 0) -> str:
    """Unique PipelineRun name for a workload. Used for k8s resource naming.

    Constrained by the k8s DNS label limit (63 chars) on the Istio-generated service:
      "gw-{eid}-inference-gateway-istio" <= 63  →  eid <= 36
    (Helm's 53-char release name limit is satisfied by all patterns within that budget.)
    Total experiment ID is capped at 36 chars.
    """
    if ts is None:
        ts = int(time.time())
    ts_str = str(ts)
    idx_str = str(idx)
    # Budget: len("sim2real-") + len(phase) + 2 dashes + slug + len(ts_str) + 1 dash + len(idx_str) <= 36
    max_slug = 36 - 9 - len(phase) - 2 - len(ts_str) - 1 - len(idx_str)
    slug = _workload_slug(workload_name)[:max(1, max_slug)].rstrip("-")
    return f"sim2real-{phase}-{slug}-{ts}-{idx}"


def _build_pipelinerun_yaml(phase: str, experiment_id: str, namespace: str,
                             run_name: str, workload_name: str, workload_spec: str,
                             run_index: int = 0) -> str:
    """Build PipelineRun YAML string for a single workload. No I/O."""
    import yaml
    pipeline_ref_name = f"sim2real-{phase}"
    doc = {
        "apiVersion": "tekton.dev/v1",
        "kind": "PipelineRun",
        "metadata": {
            "name": experiment_id,
            "namespace": namespace,
            "labels": {"sim2real-phase": phase},
        },
        "spec": {
            "pipelineRef": {"name": pipeline_ref_name},
            "taskRunTemplate": {
                "serviceAccountName": "helm-installer",
                "podTemplate": {"securityContext": {"runAsUser": 0}},
            },
            "params": [
                {"name": "experimentId", "value": experiment_id},
                {"name": "namespace", "value": namespace},
                {"name": "runName", "value": run_name},
                {"name": "workloadName", "value": workload_name},
                {"name": "workloadSpec", "value": workload_spec},
                {"name": "sleepDuration", "value": "30s"},
            ],
            "workspaces": [
                {"name": "model-cache", "persistentVolumeClaim": {"claimName": "model-pvc"}},
                {"name": "hf-credentials", "secret": {"secretName": "hf-secret"}},
                {"name": "data-storage", "persistentVolumeClaim": {"claimName": "data-pvc"}},
            ],
        },
    }
    return yaml.dump(doc, default_flow_style=False)


def _clear_phase_state(phase: str, bench_state_file: Path) -> None:
    """Remove status and results_path for a phase so it will re-run.

    Preserves other keys in the phase entry. No-op if file or phase absent.
    """
    if not bench_state_file.exists():
        return
    state = json.loads(bench_state_file.read_text())
    phase_dict = state.get("phases", {}).get(phase, {})
    phase_dict.pop("status", None)
    phase_dict.pop("results_path", None)
    bench_state_file.write_text(json.dumps(state, indent=2))


def _should_skip_phase(phase: str, bench_state_file: Path,
                       force_rerun: bool, interactive: bool,
                       results_path: Path | None = None) -> tuple[bool, str]:
    """Return (should_skip, reason_message) for a benchmark phase.

    Returns (False, "") when the phase is not marked done.
    A phase is considered done if benchmark_state.json marks it "done" OR
    if results_path already exists on disk (fallback for runs without state file).
    """
    is_done = False
    if bench_state_file.exists():
        state = json.loads(bench_state_file.read_text())
        if state.get("phases", {}).get(phase, {}).get("status") == "done":
            is_done = True
    if not is_done and results_path is not None and results_path.exists():
        is_done = True
    if not is_done:
        return False, ""
    if force_rerun:
        return False, f"Phase {phase} already done — re-running (--force-rerun)"
    if interactive:
        answer = input(f"  Phase {phase} already done — re-run? [y/N]: ").strip().lower()
        if answer == "y":
            return False, ""
        return True, f"Skipping {phase}"
    return True, f"Phase {phase} already done — skipping (non-interactive)"


def _should_skip_workload(phase: str, workload_name: str, bench_state_file: Path,
                           force_rerun: bool) -> bool:
    """Return True if this workload is already done and should be skipped."""
    if force_rerun:
        return False
    if not bench_state_file.exists():
        return False
    with _bench_state_lock:
        state = json.loads(bench_state_file.read_text())
    wl = state.get("phases", {}).get(phase, {}).get("workloads", {}).get(workload_name, {})
    return wl.get("status") == "done"


def _run_workloads_for_phase(phase: str, workloads: list[dict], run_dir: Path,
                              namespace: str, bench_state_file: Path,
                              force_rerun: bool, parallel_workloads: int,
                              run_name: str, manifest: dict | None = None) -> None:
    """Submit one PipelineRun per workload and wait for all to complete.

    workloads: list of dicts with keys 'name' and 'spec' (from values.yaml observe.workloads).
    Raises PhaseError if any workload fails; completed workloads are preserved in state.
    """
    ts = int(time.time())

    # Record run_name in state
    with _bench_state_lock:
        run([VENV_PYTHON, CLI, "benchmark-state",
             "--workspace", str(run_dir.parent.parent),
             "--set-phase", phase, "--status", "running",
             "--run-name", run_name],
            check=False, cwd=REPO_ROOT)

    # Preflight once per phase (not per workload — phase-level validation)
    values_path = str(run_dir / "prepare_tekton" / "values.yaml")
    preflight_result = run(
        _preflight_cmd(phase, values_path, namespace, manifest),
        check=False, capture=True, cwd=REPO_ROOT,
    )
    if preflight_result.returncode != 0:
        raise PhaseError(f"Preflight failed for {phase}: {preflight_result.stderr}")

    def _submit_one(idx_wl: tuple[int, dict]) -> tuple[str, str]:
        idx, wl = idx_wl
        wl_name = wl["name"]
        spec_val = wl.get("spec", "")
        # spec is a raw YAML string with actual newlines. Replace newlines with literal
        # \n so yaml.dump uses a single-quoted scalar instead of double-quoting the
        # whole value (which would add outer " that break YAML parsing in the task).
        # The task's `sed 's/\\n/\n/g'` step decodes them back.
        if not isinstance(spec_val, str):
            import yaml as _yaml
            spec_val = _yaml.dump(spec_val, default_flow_style=False)
        wl_spec = spec_val.replace("\n", r"\n")

        if _should_skip_workload(phase, wl_name, bench_state_file, force_rerun):
            info(f"[{phase}/{wl_name}] Already done — skipping")
            return wl_name, "skipped"

        experiment_id = _make_experiment_id(phase, wl_name, ts=ts, idx=idx)
        info(f"[{phase}/{wl_name}] Submitting PipelineRun: {experiment_id}")
        _run_pipeline_phase(phase, experiment_id, namespace, run_dir,
                            run_name=run_name, workload_name=wl_name,
                            workload_spec=wl_spec, run_index=idx)
        ok(f"[{phase}/{wl_name}] Complete")
        return wl_name, "done"

    failed: list[tuple[str, str]] = []

    if parallel_workloads <= 1:
        for idx, wl in enumerate(workloads):
            try:
                _submit_one((idx, wl))
            except PhaseError as exc:
                failed.append((wl["name"], str(exc)))
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=parallel_workloads) as pool:
            futures = {pool.submit(_submit_one, (idx, wl)): wl["name"]
                       for idx, wl in enumerate(workloads)}
            for future in concurrent.futures.as_completed(futures):
                wl_name = futures[future]
                try:
                    future.result()
                except PhaseError as exc:
                    failed.append((wl_name, str(exc)))

    if failed:
        lines = "\n".join(f"  - {name}: {reason}" for name, reason in failed)
        done_names = [w["name"] for w in workloads
                      if _should_skip_workload(phase, w["name"], bench_state_file, False)]
        done_lines = "\n".join(f"  - {n} (preserved)" for n in done_names)
        msg = (
            f"{phase} phase failed — {len(failed)}/{len(workloads)} workload(s) failed:\n"
            f"{lines}"
        )
        if done_names:
            msg += f"\nCompleted workloads preserved:\n{done_lines}"
        msg += f"\nResume: re-run deploy.py (failed workloads will be retried)"
        raise PhaseError(msg)


# ── Run metadata ──────────────────────────────────────────────────────────────

def update_run_metadata(run_dir: Path, stage: str = "deploy", **fields) -> None:
    meta_path = run_dir / "run_metadata.json"
    if not meta_path.exists():
        return
    meta = json.loads(meta_path.read_text())
    meta["stages"].setdefault(stage, {}).update(fields)
    meta_path.write_text(json.dumps(meta, indent=2))


# ── Setup config ──────────────────────────────────────────────────────────────

def load_setup_config(run_override: str | None = None) -> tuple[dict, str, Path]:
    """Load workspace/setup_config.json. Returns (cfg, current_run, run_dir)."""
    cfg_path = REPO_ROOT / "workspace" / "setup_config.json"
    if not cfg_path.exists():
        err("workspace/setup_config.json not found — run python scripts/setup.py first")
        sys.exit(1)
    cfg = json.loads(cfg_path.read_text())
    current_run = run_override or cfg["current_run"]
    run_dir = REPO_ROOT / "workspace" / "runs" / current_run
    if not run_dir.exists():
        err(f"Run directory not found: {run_dir}")
        sys.exit(1)
    if run_override:
        info(f"Run override: {current_run}")
    ok(f"Run: {current_run}  ({run_dir})")
    return cfg, current_run, run_dir


# ── Prerequisites ─────────────────────────────────────────────────────────────

def check_prerequisites(run_dir: Path, manifest: dict,
                        skip_plugin_check: bool = False) -> tuple[str, bool]:
    """Verify Phase 1 artifacts and cluster readiness.

    Returns (plugin_file_path, fast_iter).
    Exits 1 on any failure.
    """
    step(0, "Checking prerequisites")

    # Phase 1 artifact files
    required = [
        run_dir / "prepare_algorithm_summary.json",
        run_dir / "prepare_signal_coverage.json",
        run_dir / "prepare_stage3_output.json",
        run_dir / "prepare_tekton" / "values.yaml",
        run_dir / "prepare_translation_reviews.json",
        run_dir / "prepare_equivalence_results.json",
    ]
    missing = [str(f) for f in required if not f.exists()]
    if missing:
        err("Missing Phase 1 artifacts (run python scripts/prepare.py first):")
        for m in missing:
            print(f"  • {m}")
        sys.exit(1)

    # Verify Phase 1 JSON artifacts are readable (schema was validated by prepare.py when written;
    # validate-schema derives schema name from filename stem, so prepare_*.json would look for
    # prepare_*.schema.json which doesn't exist)
    for art in ["prepare_algorithm_summary.json", "prepare_signal_coverage.json",
                "prepare_stage3_output.json"]:
        try:
            json.loads((run_dir / art).read_text())
        except (json.JSONDecodeError, OSError) as e:
            err(f"Cannot read {art}: {e}")
            sys.exit(1)

    # AI review verdict check: passed if consensus or user explicitly accepted
    reviews = json.loads((run_dir / "prepare_translation_reviews.json").read_text())
    if not (reviews.get("passed") or reviews.get("accepted_by_user")):
        err("AI review did not pass — re-run prepare or investigate reviews")
        sys.exit(1)

    # Equivalence gate check (generic — reads prepare_equivalence_results and checks for fatal failures)
    equiv_path = run_dir / "prepare_equivalence_results.json"
    if equiv_path.exists():
        equiv = json.loads(equiv_path.read_text())
        if not equiv.get("skipped"):
            for name, result in equiv.items():
                if isinstance(result, dict) and result.get("fatal", True) and not result.get("passed", False):
                    err(f"Equivalence test '{name}' did not pass — re-run prepare")
                    sys.exit(1)

    # Plugin file exists and builds
    stage3 = json.loads((run_dir / "prepare_stage3_output.json").read_text())
    plugin_file = stage3["plugin_file"]
    if skip_plugin_check:
        info("Skipping plugin file check (--skip-build-epp)")
    else:
        if not Path(plugin_file).exists():
            err(f"Plugin file missing: {plugin_file}")
            sys.exit(1)
        result = run(
            ["go", "build", "./..."],
            check=False, capture=True,
            cwd=REPO_ROOT / manifest["target"]["repo"],
            env={**os.environ, "GOWORK": "off"},
        )
        if result.returncode != 0:
            err("Plugin build failed:")
            print(result.stderr)
            sys.exit(1)

    # Registry configuration check
    try:
        import yaml
        cfg = yaml.safe_load((REPO_ROOT / manifest["config"]["env_defaults"]).read_text())
        hub = (cfg.get("stack", {})
                  .get("gaie", {})
                  .get("epp_image", {})
                  .get("build", {})
                  .get("hub", ""))
        if not hub or "REPLACE_ME" in hub:
            err(f"Set epp_image.build.hub in {manifest['config']['env_defaults']} before deploying")
            sys.exit(1)
        fast_iter = bool(cfg.get("pipeline", {}).get("fast_iteration", True))
    except Exception as e:
        err(f"Cannot read {manifest['config']['env_defaults']}: {e}")
        sys.exit(1)

    ok("All prerequisites satisfied")
    info(f"Fast iteration mode: {fast_iter}")
    return plugin_file, fast_iter


# ── Stage 1: Build EPP ────────────────────────────────────────────────────────

def stage_build_epp(run_dir: Path, current_run: str, namespace: str, manifest: dict) -> str:
    """Build EPP image on-cluster, update algorithm_values, re-merge, compile+apply pipelines.

    Returns the full image reference (e.g. quay.io/me/llm-d:run-name).
    """
    step(1, "Build EPP Image (in-cluster via BuildKit)")

    # Locate build-epp.sh inside the skill directory
    candidates = list(REPO_ROOT.glob(".claude/skills/sim2real-deploy/scripts/build-epp.sh"))
    if not candidates:
        err("build-epp.sh not found at .claude/skills/sim2real-deploy/scripts/build-epp.sh")
        sys.exit(1)
    build_script = candidates[0]

    result = run(
        ["bash", str(build_script),
         "--run-dir", str(run_dir),
         "--run-name", current_run,
         "--namespace", namespace],
        check=False,
        cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        err("EPP build failed — see output above")
        sys.exit(1)

    # Read image reference written by build-epp.sh into run_metadata.json
    meta = json.loads((run_dir / "run_metadata.json").read_text())
    full_image = meta.get("epp_image", "")
    if not full_image:
        err("build-epp.sh completed but epp_image not set in run_metadata.json")
        sys.exit(1)
    ok(f"EPP image: {full_image}")

    # Inject image reference into algorithm_values.yaml
    step("1a", "Injecting image reference into algorithm_values.yaml")
    import yaml
    alg_values_path = run_dir / "prepare_tekton" / "algorithm_values.yaml"
    alg_values = yaml.safe_load(alg_values_path.read_text())

    # Read hub+name from env_defaults build config (already validated in prerequisites).
    # Only the tag comes from the newly built image reference.
    env_cfg = yaml.safe_load((REPO_ROOT / manifest["config"]["env_defaults"]).read_text())
    build_cfg = (env_cfg.get("stack", {}).get("gaie", {})
                        .get("epp_image", {}).get("build", {}))
    epp_hub  = build_cfg.get("hub", "")
    epp_name = build_cfg.get("name", "")
    epp_tag  = full_image.rsplit(":", 1)[1] if ":" in full_image else current_run
    alg_values = _inject_image_reference(alg_values, epp_hub, epp_name, epp_tag)
    alg_values_path.write_text(yaml.dump(alg_values, default_flow_style=False, sort_keys=False))
    ok("algorithm_values.yaml updated")

    # Re-merge values
    step("1b", "Re-merging values")
    values_out = run_dir / "prepare_tekton" / "values.yaml"
    helm_path = manifest["config"]["helm_path"]
    run([VENV_PYTHON, CLI, "merge-values",
         "--env", str(REPO_ROOT / manifest["config"]["env_defaults"]),
         "--algorithm", str(alg_values_path),
         "--out", str(values_out),
         "--helm-path", f"stack.{helm_path}"],
        cwd=REPO_ROOT)
    ok("values.yaml re-merged")

    # Compile and apply Tekton pipeline YAMLs
    step("1c", "Compiling and applying Tekton pipelines")
    pipelines_dir = run_dir / "prepare_tekton" / "pipelines"
    pipelines_dir.mkdir(parents=True, exist_ok=True)
    for phase in ["noise", "baseline", "treatment"]:
        run([VENV_PYTHON, CLI, "compile-pipeline",
             "--template-dir", str(REPO_ROOT / "tektonc-data-collection/tektoncsample/sim2real"),
             "--values", str(values_out),
             "--phase", phase,
             "--out", str(pipelines_dir)],
            cwd=REPO_ROOT)
    for yaml_file in sorted(pipelines_dir.glob("*.yaml")):
        run(["kubectl", "apply", "-f", str(yaml_file), f"-n={namespace}"])
    ok("Tekton pipelines applied to cluster")

    update_run_metadata(run_dir, last_completed_step="build_epp")
    return full_image


# ── Stage 2 helpers: pipeline runner + result extractor ───────────────────────

def _run_pipeline_phase(phase: str, experiment_id: str, namespace: str,
                        run_dir: Path, run_name: str, workload_name: str,
                        workload_spec: str, run_index: int = 0) -> None:
    """Submit a Tekton PipelineRun and wait for it to complete.

    Prints a monitoring hint so the user can tail logs in a second terminal.
    Updates benchmark-state on success/failure. Raises PhaseError on failure.
    """
    pipelines_dir = run_dir / "prepare_tekton" / "pipelines"
    pipeline_yaml = pipelines_dir / f"{phase}-pipeline.yaml"

    # Apply pipeline definition
    result = run(["kubectl", "apply", "-f", str(pipeline_yaml), f"-n={namespace}"],
                 check=False, capture=True)
    if result.returncode != 0:
        raise PhaseError(f"kubectl apply failed for {phase} pipeline: {result.stderr}")

    # Write PipelineRun YAML directly (avoids dependency on pre-existing template files)
    pipelinerun_yaml = str(run_dir / f"pipelinerun-{phase}-{run_index}.yaml")
    Path(pipelinerun_yaml).write_text(
        _build_pipelinerun_yaml(phase, experiment_id, namespace,
                                run_name, workload_name, workload_spec, run_index)
    )

    # Submit PipelineRun
    result = run(["kubectl", "apply", "-f", pipelinerun_yaml, f"-n={namespace}"],
                 check=False, capture=True)
    if result.returncode != 0:
        raise PhaseError(f"kubectl apply pipelinerun failed for {phase}: {result.stderr}")

    with _bench_state_lock:
        run([VENV_PYTHON, CLI, "benchmark-state",
         "--workspace", str(run_dir.parent.parent),
         "--set-phase", phase, "--workload", workload_name,
         "--status", "running",
         "--pipelinerun", experiment_id],
        check=False, cwd=REPO_ROOT)

    # Poll until terminal state
    info(f"Waiting for {phase} PipelineRun: {experiment_id} (timeout {PIPELINE_TIMEOUT_SECS}s)...")
    info(f"  To tail logs: tkn pr logs {experiment_id} -n {namespace} -f")
    elapsed = 0
    while True:
        result = run(
            ["tkn", "pr", "describe", experiment_id,
             "-o", "jsonpath={.status.conditions[0].reason}",
             "-n", namespace],
            check=False, capture=True,
        )
        reason = result.stdout.strip()
        if any(t in reason for t in ["Succeeded", "Failed", "PipelineRunCancelled", "CouldntGetTask"]):
            break
        time.sleep(30)
        elapsed += 30
        if elapsed >= PIPELINE_TIMEOUT_SECS:
            with _bench_state_lock:
                run([VENV_PYTHON, CLI, "benchmark-state",
                     "--workspace", str(run_dir.parent.parent),
                     "--set-phase", phase, "--workload", workload_name,
                     "--status", "failed",
                     "--failure-reason", f"Polling timeout after {PIPELINE_TIMEOUT_SECS}s on run {run_index}"],
                    check=False, cwd=REPO_ROOT)
            raise PhaseError(f"{phase} run {run_index} timed out after {PIPELINE_TIMEOUT_SECS}s")

    if "Succeeded" not in reason:
        fail_result = run(
            ["tkn", "pr", "describe", experiment_id,
             "-o", "jsonpath={.status.conditions[0].message}",
             "-n", namespace],
            check=False, capture=True,
        )
        fail_reason = fail_result.stdout.strip() or "PipelineRun failed"
        with _bench_state_lock:
            run([VENV_PYTHON, CLI, "benchmark-state",
                 "--workspace", str(run_dir.parent.parent),
                 "--set-phase", phase, "--workload", workload_name,
                 "--status", "failed",
                 "--failure-reason", fail_reason],
                check=False, cwd=REPO_ROOT)
        raise PhaseError(f"{phase} run {run_index} failed: {fail_reason}")

    ok(f"{phase} PipelineRun succeeded: {experiment_id}")

    with _bench_state_lock:
        run([VENV_PYTHON, CLI, "benchmark-state",
             "--workspace", str(run_dir.parent.parent),
             "--set-phase", phase, "--workload", workload_name,
             "--status", "done"],
            check=False, cwd=REPO_ROOT)


def _restructure_for_convert_trace(raw_dir: Path, phase: str) -> Path:
    """Reorganise kubectl-cp output into the layout convert-trace expects.

    kubectl cp /data/{phase}/ → raw_dir gives:
      raw_dir/{runName}/{workloadName}/trace_data.csv  (one runName per phase pass)

    convert-trace expects:
      raw_dir/{workloadName}/trace_data.csv         — baseline / treatment
      raw_dir/{workloadName}/run-{i}/trace_data.csv — noise (multiple passes)

    Returns path to the restructured directory (sibling of raw_dir).
    """
    import shutil

    run_name_dirs = sorted(d for d in raw_dir.iterdir() if d.is_dir())
    if not run_name_dirs:
        return raw_dir  # nothing to restructure; let convert-trace fail with a clear error

    structured = raw_dir.parent / f"deploy_{phase}_structured"
    if structured.exists():
        shutil.rmtree(structured)
    structured.mkdir()

    for i, rn_dir in enumerate(run_name_dirs):
        for wl_dir in sorted(d for d in rn_dir.iterdir() if d.is_dir()):
            if phase == "noise":
                dest = structured / wl_dir.name / f"run-{i}"
                dest.mkdir(parents=True, exist_ok=True)
                shutil.copytree(str(wl_dir), str(dest), dirs_exist_ok=True)
            else:
                dest = structured / wl_dir.name
                dest.mkdir(parents=True, exist_ok=True)
                shutil.copytree(str(wl_dir), str(dest), dirs_exist_ok=True)

    return structured


def _extract_phase_results(phase: str, namespace: str, run_dir: Path) -> Path:
    """Extract results from cluster data-pvc via extractor pod.

    Returns path to validated results JSON.
    """
    pod_name = f"sim2real-extract-{phase}"

    # Clean up any leftover pod
    run(["kubectl", "delete", "pod", pod_name, "-n", namespace,
         "--ignore-not-found", "--force", "--grace-period=0"],
        check=False, capture=True)

    overrides = json.dumps({
        "spec": {
            "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": "data-pvc"}}],
            "containers": [{
                "name": "e", "image": "alpine:3.19",
                "command": ["sleep", "600"],
                "volumeMounts": [{"name": "data", "mountPath": "/data"}],
            }],
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
        raise PhaseError(f"Extractor pod {pod_name} not ready")

    raw_dir = run_dir / f"deploy_{phase}_log"
    raw_dir.mkdir(parents=True, exist_ok=True)
    result = run(
        ["kubectl", "cp", f"{namespace}/{pod_name}:/data/{phase}/", str(raw_dir), "--retries=3"],
        check=False, capture=True,
    )
    run(["kubectl", "delete", "pod", pod_name, "-n", namespace,
         "--ignore-not-found", "--force", "--grace-period=0"],
        check=False, capture=True)
    if result.returncode != 0:
        raise PhaseError(f"kubectl cp failed for {phase}")

    # Collect vLLM decode pod logs
    log_result = run(
        ["kubectl", "get", "pods", "-n", namespace,
         "--no-headers", "-o", "custom-columns=NAME:.metadata.name"],
        check=False, capture=True,
    )
    if log_result.returncode == 0:
        decode_pods = [p for p in log_result.stdout.splitlines() if "decode" in p.lower()]
        for n, pod in enumerate(decode_pods):
            pod_log = run(
                ["kubectl", "logs", pod, "-n", namespace],
                check=False, capture=True,
            )
            if pod_log.returncode == 0 and pod_log.stdout:
                (raw_dir / f"{pod}_decode_{n}.log").write_text(pod_log.stdout)

    # Restructure: kubectl cp gives raw_dir/{runName}/{workloadName}/; convert-trace
    # expects raw_dir/{workloadName}/ (baseline/treatment) or run-{i}/ nesting (noise).
    convert_input_dir = _restructure_for_convert_trace(raw_dir, phase)

    results_path = run_dir / f"deploy_{phase}_results.json"
    result = run(
        [VENV_PYTHON, CLI, "convert-trace",
         "--input-dir", str(convert_input_dir),
         "--output", str(results_path)],
        check=False, capture=True, cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        raise PhaseError(
            f"convert-trace failed for {phase}:\n{result.stderr or result.stdout}"
        )

    result = run([VENV_PYTHON, CLI, "validate-schema", str(results_path)],
                 check=False, capture=True, cwd=REPO_ROOT)
    if result.returncode != 0:
        raise PhaseError(f"Schema validation failed for {phase}_results.json — do not mark phase done")

    with _bench_state_lock:
        run([VENV_PYTHON, CLI, "benchmark-state",
             "--workspace", str(run_dir.parent.parent),
             "--set-phase", phase, "--status", "done",
             "--results", str(results_path)],
            check=False, cwd=REPO_ROOT)

    ok(f"{phase} results extracted: {results_path}")
    return results_path


# ── Stage 2 helper: noise loop ────────────────────────────────────────────────

def _run_noise_phase(run_dir: Path, namespace: str, workspace_dir: Path,
                     parallel_workloads: int = 1,
                     force_rerun: bool = False,
                     manifest: dict | None = None) -> None:
    """Run sequential noise passes; within each pass, workloads run in parallel."""
    import yaml

    values_path = run_dir / "prepare_tekton" / "values.yaml"
    values = yaml.safe_load(values_path.read_text())
    noise_runs = values.get("observe", {}).get("noise_runs", 3)
    workloads = values.get("observe", {}).get("workloads", [])
    info(f"Running {noise_runs} noise pass(es) × {len(workloads)} workload(s)...")

    bench_state_file = workspace_dir / "benchmark_state.json"

    for i in range(noise_runs):
        pass_run_name = _make_run_name("noise", ts=int(time.time()))
        info(f"Noise pass {i}/{noise_runs - 1}: run_name={pass_run_name}")

        _run_workloads_for_phase(
            phase="noise",
            workloads=workloads,
            run_dir=run_dir,
            namespace=namespace,
            bench_state_file=bench_state_file,
            force_rerun=force_rerun,
            parallel_workloads=parallel_workloads,
            run_name=pass_run_name,
            manifest=manifest,
        )

    _extract_phase_results("noise", namespace, run_dir)
    ok(f"Noise characterization complete: {run_dir / 'deploy_noise_results.json'}")


# ── Stage 2: Cluster benchmarks ───────────────────────────────────────────────

def _gpu_warning(parallel: int, parallel_workloads: int, values_path: Path) -> None:
    """Warn about total GPU demand when running phases or workloads in parallel."""
    if parallel <= 1 and parallel_workloads <= 1:
        return
    try:
        import yaml
        values = yaml.safe_load(values_path.read_text())
        decode = values.get("stack", {}).get("model", {}).get("helmValues", {}).get("decode", {})
        replicas = decode.get("replicas", 1)
        gpu_per_pod = int(
            decode.get("resources", {}).get("limits", {}).get("nvidia.com/gpu", "1")
        )
        gpus_per_stack = replicas * gpu_per_pod
        total = gpus_per_stack * parallel * parallel_workloads
        warn(
            f"parallel={parallel}, parallel_workloads={parallel_workloads} "
            f"→ {total} GPUs required simultaneously "
            f"({gpus_per_stack} per stack × {parallel} concurrent phase(s) "
            f"× {parallel_workloads} concurrent workload(s) per phase)."
        )
        warn(
            "If fewer GPUs are available, Kubernetes will queue pending pods — "
            "phases will complete but wall-clock savings will be reduced."
        )
    except Exception:
        warn(
            f"parallel={parallel}, parallel_workloads={parallel_workloads}: "
            "could not compute GPU demand from values.yaml."
        )


def _run_single_phase(phase: str, run_dir: Path, namespace: str,
                      workspace_dir: Path, bench_state_file: Path,
                      force_rerun: bool, parallel_workloads: int = 1,
                      manifest: dict | None = None) -> tuple[str, str]:
    """Run one benchmark phase end-to-end. Designed for ThreadPoolExecutor.

    Returns (phase, status) where status is "done" or "skipped".
    Raises PhaseError on failure.
    """
    # Skip check (non-interactive when parallel — can't prompt from threads)
    skip, msg = _should_skip_phase(
        phase, bench_state_file,
        force_rerun=force_rerun,
        interactive=False,
        results_path=run_dir / f"deploy_{phase}_results.json",
    )
    if skip:
        info(f"[{phase}] {msg}")
        return phase, "skipped"
    if msg:
        info(f"[{phase}] {msg}")
    _clear_phase_state(phase, bench_state_file)

    if phase == "noise":
        _run_noise_phase(run_dir, namespace, workspace_dir,
                         parallel_workloads=parallel_workloads,
                         force_rerun=force_rerun, manifest=manifest)
    else:
        import yaml
        values = yaml.safe_load(
            (run_dir / "prepare_tekton" / "values.yaml").read_text()
        )
        workloads = values.get("observe", {}).get("workloads", [])
        run_name = _make_run_name(phase)
        _run_workloads_for_phase(
            phase=phase,
            workloads=workloads,
            run_dir=run_dir,
            namespace=namespace,
            bench_state_file=bench_state_file,
            force_rerun=force_rerun,
            parallel_workloads=parallel_workloads,
            run_name=run_name,
            manifest=manifest,
        )
        _extract_phase_results(phase, namespace, run_dir)

        # ── Post-collection trace audit (non-blocking) ────────────────────────
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from lib.validate_checks import run_post_collection_checks
            col_report = run_post_collection_checks(phase, run_dir)
            col_json = json.dumps(col_report.to_dict(), indent=2)
            (run_dir / f"validate_post_collection_{phase}.json").write_text(col_json)
            if col_report.failed:
                warn(f"[{phase}] Post-collection validation FAILED — see validate_post_collection_{phase}.json")
            else:
                ok(f"[{phase}] Post-collection validation passed (overall={col_report.overall})")
        except Exception as e:
            warn(f"[{phase}] Post-collection validation error (non-blocking): {e}")

    ok(f"[{phase}] Phase complete")
    return phase, "done"


def stage_benchmarks(run_dir: Path, namespace: str, fast_iter: bool, manifest: dict,
                     force_rerun: bool = False, parallel: int = 1,
                     parallel_workloads: int = 1,
                     phases_filter: list[str] | None = None) -> str:
    """Run cluster benchmarks. Returns overall_verdict string."""
    step(2, f"Cluster Benchmarks (fast_iteration={fast_iter})")

    equiv = json.loads((run_dir / "prepare_equivalence_results.json").read_text())
    workspace_dir = run_dir.parent.parent  # sim2real/workspace/
    val_path = run_dir / "deploy_validation_results.json"

    if fast_iter:
        info("FAST MODE: Skipping noise gate and mechanism check (pipeline.fast_iteration=true)")
        val = _construct_validation_results(equiv, fast_iter=True)
        val_path.write_text(json.dumps(val, indent=2))
        ok(f"Wrote deploy_validation_results.json (fast mode, overall_verdict={val['overall_verdict']})")
    else:
        # Full mode: partial file without overall_verdict (set after mechanism check)
        val = _construct_validation_results(equiv, fast_iter=False)
        val_path.write_text(json.dumps(val, indent=2))

        # Initialize benchmark state
        result = run(
            [VENV_PYTHON, CLI, "benchmark-state",
             "--workspace", str(workspace_dir), "--namespace", namespace],
            check=False, capture=True, cwd=REPO_ROOT,
        )
        if result.returncode == 2:
            err("benchmark-state failed — missing workspace/algorithm_summary.json")
            sys.exit(1)
        elif result.returncode != 0:
            err(f"benchmark-state failed (exit {result.returncode})")
            sys.exit(1)

    # ── Pre-deploy validation ─────────────────────────────────────────────────
    info("Pre-deploy validation")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from lib.validate_checks import run_pre_deploy_checks
    try:
        pre_report = run_pre_deploy_checks(run_dir)
        val_json = json.dumps(pre_report.to_dict(), indent=2)
        (run_dir / "validate_pre_deploy.json").write_text(val_json)
        if pre_report.failed:
            err("Pre-deploy validation FAILED — see validate_pre_deploy.json")
            sys.exit(1)
        ok("Pre-deploy validation passed")
    except FileNotFoundError as e:
        err(f"Pre-deploy validation error (missing artifact): {e}")
        sys.exit(2)

    # ── Run phases ────────────────────────────────────────────────────────────
    phases_to_run = ([] if fast_iter else ["noise"]) + ["baseline", "treatment"]
    if phases_filter:
        phases_to_run = [p for p in phases_to_run if p in phases_filter]
    bench_state_file = workspace_dir / "benchmark_state.json"

    _gpu_warning(parallel, parallel_workloads, run_dir / "prepare_tekton" / "values.yaml")
    info(f"Running {len(phases_to_run)} phase(s) with --parallel {parallel}: {phases_to_run}")

    if parallel <= 1:
        # Sequential mode: preserves interactive skip prompts
        for phase in phases_to_run:
            skip, msg = _should_skip_phase(
                phase, bench_state_file,
                force_rerun=force_rerun,
                interactive=sys.stdin.isatty(),
                results_path=run_dir / f"deploy_{phase}_results.json",
            )
            if skip:
                info(msg)
                continue
            if msg:
                info(msg)
            _clear_phase_state(phase, bench_state_file)
            _run_single_phase(phase, run_dir, namespace, workspace_dir,
                              bench_state_file, force_rerun=force_rerun,
                              parallel_workloads=parallel_workloads,
                              manifest=manifest)
    else:
        # Parallel mode: ThreadPoolExecutor dispatches up to N phases concurrently
        with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as pool:
            futures = {
                pool.submit(
                    _run_single_phase, phase, run_dir, namespace,
                    workspace_dir, bench_state_file,
                    force_rerun=force_rerun,
                    parallel_workloads=parallel_workloads,
                    manifest=manifest,
                ): phase
                for phase in phases_to_run
            }
            for future in concurrent.futures.as_completed(futures):
                phase = futures[future]
                try:
                    _, status = future.result()
                    info(f"[{phase}] finished: {status}")
                except PhaseError as exc:
                    err(f"[{phase}] {exc}")
                    # Cancel remaining futures and exit
                    for f in futures:
                        f.cancel()
                    sys.exit(1)

    # ── Mechanism check (full mode only) ─────────────────────────────────────
    if not fast_iter:
        step("2a", "Mechanism check")
        bench_out = run_dir / "deploy_benchmark_output.json"
        workloads_dir = REPO_ROOT / manifest["algorithm"]["experiment_dir"] / manifest["algorithm"]["workloads"]
        result = run(
            [VENV_PYTHON, CLI, "benchmark",
             "--noise", str(run_dir / "deploy_noise_results.json"),
             "--baseline", str(run_dir / "deploy_baseline_results.json"),
             "--treatment", str(run_dir / "deploy_treatment_results.json"),
             "--signal-coverage", str(run_dir / "prepare_signal_coverage.json"),
             "--workloads-dir", str(workloads_dir),
             "--out", str(bench_out)],
            check=False, capture=True, cwd=REPO_ROOT,
        )
        if result.returncode == 1:
            err("Mechanism check FAIL — see deploy_benchmark_output.json")
            sys.exit(1)
        if result.returncode == 2:
            err("Mechanism check infrastructure error — see stderr")
            sys.exit(1)

        bench = json.loads(bench_out.read_text())
        mech_verdict = bench.get("mechanism_check_verdict", "ERROR")
        if mech_verdict == "INCONCLUSIVE":
            warn("Mechanism check verdict is INCONCLUSIVE.")
            warn("Options:")
            warn("  1) Re-run during a lower-variance window")
            warn("  2) Inspect per-workload improvements in deploy_benchmark_output.json")
            warn("  3) Accept as soft-pass: set operator_notes in deploy_validation_results.json,")
            warn("     then re-run with --skip-build-epp --pr")
            # Exit 3 = INCONCLUSIVE pause (distinct from error=1 and infra=2)
            sys.exit(3)

        # Merge benchmark into validation_results
        val = json.loads(val_path.read_text())
        val = _merge_benchmark_into_validation(val, bench)
        val_path.write_text(json.dumps(val, indent=2))
        ok(f"Mechanism check: {mech_verdict}")

    # ── Comparison table ──────────────────────────────────────────────────────
    step("2b", "Comparison table")
    table_path = run_dir / "deploy_comparison_table.txt"
    result = run(
        [VENV_PYTHON, CLI, "compare",
         "--baseline", str(run_dir / "deploy_baseline_results.json"),
         "--treatment", str(run_dir / "deploy_treatment_results.json"),
         "--out", str(table_path)],
        check=False, capture=True, cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        err("compare failed")
        sys.exit(1)
    if table_path.exists():
        print(table_path.read_text())

    update_run_metadata(run_dir, last_completed_step="benchmarks")

    verdict = json.loads(val_path.read_text()).get("overall_verdict", "UNKNOWN")
    ok(f"Benchmarks complete. overall_verdict: {verdict}")
    return verdict


# ── Stage 3: PR creation ──────────────────────────────────────────────────────

def stage_pr(run_dir: Path, manifest: dict) -> str | None:
    """Create PR in target repository. Returns PR URL, or None if skipped."""
    step(3, "PR Creation")

    # Fast-iteration guard (pipeline.fast_iteration=true means no PR)
    try:
        import yaml
        cfg = yaml.safe_load((REPO_ROOT / manifest["config"]["env_defaults"]).read_text())
        fast_iter = bool(cfg.get("pipeline", {}).get("fast_iteration", True))
    except Exception as e:
        err(f"Cannot read {manifest['config']['env_defaults']}: {e}")
        sys.exit(1)

    if fast_iter:
        info("Fast-iteration mode: PR creation skipped.")
        info("Review deploy_comparison_table.txt and set pipeline.fast_iteration=false when ready.")
        return None

    # Prerequisite artifacts
    val_path = run_dir / "deploy_validation_results.json"
    result = run([VENV_PYTHON, CLI, "validate-schema", str(val_path)],
                 check=False, capture=True, cwd=REPO_ROOT)
    if result.returncode != 0:
        err("deploy_validation_results.json missing or invalid")
        sys.exit(1)

    evidence_path = run_dir / "deploy_transfer_evidence.md"
    if not evidence_path.exists() or not evidence_path.read_text().strip():
        result = run(
            [VENV_PYTHON, CLI, "generate-evidence",
             "--workspace", str(run_dir.parent.parent),
             "--out", str(evidence_path)],
            check=False, capture=True, cwd=REPO_ROOT,
        )
        if result.returncode != 0:
            err("generate-evidence failed")
            sys.exit(1)

    val = json.loads(val_path.read_text())
    verdict = val.get("overall_verdict", "")

    if verdict == "FAIL":
        err("overall_verdict is FAIL — do not create PR")
        sys.exit(1)
    if verdict == "INCONCLUSIVE":
        if not val.get("operator_notes", "").strip():
            err("overall_verdict is INCONCLUSIVE but operator_notes is absent or empty.")
            err("Set operator_notes in deploy_validation_results.json before re-running.")
            sys.exit(1)
        warn(f"Proceeding with INCONCLUSIVE verdict under operator sign-off: {val['operator_notes']}")
    elif verdict != "PASS":
        err(f"Unexpected overall_verdict: '{verdict}'")
        sys.exit(1)

    # gh auth check
    result = run(["gh", "auth", "status"], check=False, capture=True)
    if result.returncode != 0:
        err("gh auth check failed — run 'gh auth login' and retry")
        sys.exit(1)

    # Push branch to target repository
    alg_name = json.loads((run_dir / "prepare_algorithm_summary.json").read_text())["algorithm_name"]
    branch = f"transfer/{alg_name}"
    target_dir = REPO_ROOT / manifest["target"]["repo"]

    result = run(
        ["git", "ls-remote", "--exit-code", "--heads", "origin", branch],
        check=False, capture=True, cwd=target_dir,
    )
    if result.returncode == 0:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        branch = f"{branch}-{timestamp}"
        warn(f"Branch already exists — using timestamped branch: {branch}")

    run(["git", "checkout", "-b", branch], cwd=target_dir)
    run(["git", "add", "-A"], cwd=target_dir)
    result = run(["git", "diff", "--cached", "--quiet"], check=False, cwd=target_dir)
    if result.returncode != 0:
        # There are staged changes to commit
        run(["git", "commit", "-m",
             f"feat: add {alg_name} {manifest['target']['package']} plugin (sim2real transfer)"],
            cwd=target_dir)
    else:
        warn(f"No changes to commit in {manifest['target']['repo']} — pushing branch as-is")
    result = run(["git", "push", "-u", "origin", branch], check=False, capture=True, cwd=target_dir)
    if result.returncode != 0:
        err(f"git push failed for branch {branch}")
        sys.exit(1)
    ok(f"Pushed branch: {branch}")

    # Append calibration log
    result = run(
        [VENV_PYTHON, CLI, "append-calibration-log",
         "--workspace", str(run_dir.parent.parent),
         "--calibration-log", str(REPO_ROOT / "docs/transfer/calibration_log.md")],
        check=False, capture=True, cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        err("append-calibration-log failed — inspect calibration_log.md before continuing")
        sys.exit(1)
    ok("Calibration log appended")

    # Create PR
    equiv_entries = val.get("equivalence", {})
    equiv_lines = []
    for ename, edata in equiv_entries.items():
        status = "pass" if edata.get("passed") else "fail"
        equiv_lines.append(f"- {ename}: `{status}`")
    equiv_summary = "\n".join(equiv_lines) if equiv_lines else "- (none)"
    mech = val.get("benchmark", {}).get("mechanism_check_verdict", "N/A")
    evidence = evidence_path.read_text()

    pr_body = f"""## Summary

Sim-to-production transfer: `{alg_name}`

**Validation:**
{equiv_summary}
- Mechanism check: `{mech}`
- Overall verdict: `{verdict}`

## Evidence

{evidence}

## Rollback

To disable: Disable the {alg_name} plugin in the treatment config.
"""

    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write(pr_body)
        body_file = f.name

    try:
        result = run(
            ["gh", "pr", "create",
             "--title", f"feat({manifest['target']['package']}): add {alg_name} sim-to-production plugin",
             "--base", "main",
             "--head", branch,
             "--body-file", body_file],
            check=False, capture=True, cwd=target_dir,
        )
        if result.returncode != 0:
            err(f"gh pr create failed. Branch '{branch}' is already pushed — create PR manually.")
            sys.exit(1)
    finally:
        Path(body_file).unlink(missing_ok=True)

    pr_url_result = run(["gh", "pr", "view", "--json", "url", "-q", ".url"],
                        check=False, capture=True, cwd=target_dir)
    pr_url = pr_url_result.stdout.strip()
    ok(f"PR created: {pr_url}")
    return pr_url


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> int:
    args = build_parser().parse_args()
    print(_c("36", "\n━━━ sim2real-deploy ━━━\n"))

    # Load manifest
    from lib.manifest import load_manifest, ManifestError
    try:
        manifest = load_manifest(args.manifest)
    except ManifestError as e:
        err(f"Manifest error: {e}")
        sys.exit(1)

    cfg, current_run, run_dir = load_setup_config(run_override=args.run)
    namespace = os.environ.get("NAMESPACE", cfg["namespace"])

    update_run_metadata(run_dir, status="in_progress",
                        started_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

    plugin_file, fast_iter = check_prerequisites(run_dir, manifest,
                                                  skip_plugin_check=args.skip_build_epp)

    if not args.skip_build_epp:
        full_image = stage_build_epp(run_dir, current_run, namespace, manifest)
    else:
        meta = json.loads((run_dir / "run_metadata.json").read_text())
        full_image = meta.get("epp_image", "")
        if not full_image:
            err("--skip-build-epp set but no epp_image in run_metadata.json")
            sys.exit(1)
        info(f"Skipping EPP build. Using image: {full_image}")

    verdict = stage_benchmarks(run_dir, namespace, fast_iter, manifest,
                               force_rerun=args.force_rerun,
                               parallel=args.parallel,
                               parallel_workloads=args.parallel_workloads,
                               phases_filter=args.phases)

    pr_url = None
    if args.pr:
        pr_url = stage_pr(run_dir, manifest)

    update_run_metadata(run_dir,
                        status="completed",
                        completed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        summary="Build EPP, benchmarks completed",
                        artifacts=["deploy_validation_results.json",
                                   "deploy_comparison_table.txt"])

    # ── Final summary ─────────────────────────────────────────────────────────
    print()
    print(_c("32", "━━━ /sim2real-deploy complete ━━━"))
    print()
    print(f"Verdict:   {verdict}")
    print(f"EPP Image: {full_image}")
    print()
    print("Artifacts:")
    for art in ["deploy_validation_results.json", "deploy_comparison_table.txt"]:
        path = run_dir / art
        status = "✓" if path.exists() else "✗ (missing)"
        print(f"  {run_dir}/{art}  {status}")
    print()
    if not args.pr:
        print("PR: skipped (use --pr to create)")
    else:
        print(f"PR: {pr_url or 'skipped (fast_iteration mode)'}")
    print()
    print("Next: python scripts/analyze.py  OR  /sim2real-analyze in Claude")
    return 0


if __name__ == "__main__":
    sys.exit(main())

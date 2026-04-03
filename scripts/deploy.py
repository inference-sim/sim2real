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
    p.add_argument("--manifest", type=Path, default=REPO_ROOT / "config/transfer.yaml",
                   help="Path to transfer.yaml manifest")
    p.add_argument("--skip-build-epp", action="store_true",
                   help="Skip EPP image build (use if already built this run)")
    p.add_argument("--pr", action="store_true",
                   help="Create PR after benchmarks pass (default: skip, review results first)")
    p.add_argument("--force-rerun", action="store_true",
                   help="Re-run already-done benchmark phases without prompting")
    p.add_argument("--parallel", type=int, default=1, metavar="N",
                   help="Max pipeline phases to run concurrently (default: 1)")
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


def _clear_phase_state(phase: str, bench_state_file: Path) -> None:
    """Remove status and results_path for a phase so it will re-run.

    Preserves other keys in the phase entry. No-op if file or phase absent.
    """
    if not bench_state_file.exists():
        return
    state = json.loads(bench_state_file.read_text())
    phase_dict = state.get("phases", {}).get(phase, {})
    phase_dict["status"] = "pending"
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


# ── Run metadata ──────────────────────────────────────────────────────────────

def update_run_metadata(run_dir: Path, stage: str = "deploy", **fields) -> None:
    meta_path = run_dir / "run_metadata.json"
    if not meta_path.exists():
        return
    meta = json.loads(meta_path.read_text())
    meta["stages"].setdefault(stage, {}).update(fields)
    meta_path.write_text(json.dumps(meta, indent=2))


# ── Setup config ──────────────────────────────────────────────────────────────

def load_setup_config() -> tuple[dict, str, Path]:
    """Load workspace/setup_config.json. Returns (cfg, current_run, run_dir)."""
    cfg_path = REPO_ROOT / "workspace" / "setup_config.json"
    if not cfg_path.exists():
        err("workspace/setup_config.json not found — run python scripts/setup.py first")
        sys.exit(1)
    cfg = json.loads(cfg_path.read_text())
    current_run = cfg["current_run"]
    run_dir = REPO_ROOT / "workspace" / "runs" / current_run
    ok(f"Run: {current_run}  ({run_dir})")
    return cfg, current_run, run_dir


# ── Prerequisites ─────────────────────────────────────────────────────────────

def check_prerequisites(run_dir: Path, manifest: dict) -> tuple[str, bool]:
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

def _run_pipeline_phase(phase: str, pipelinecurrent_run: str, namespace: str,
                        run_dir: Path, run_index: int = 0) -> None:
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
    pipeline_ref_name = f"sim2real-{phase}"
    pipelinerun_manifest = f"""apiVersion: tekton.dev/v1
kind: PipelineRun
metadata:
  name: {pipelinecurrent_run}
  namespace: {namespace}
  labels:
    sim2real-phase: {phase}
spec:
  pipelineRef:
    name: {pipeline_ref_name}
  taskRunTemplate:
    serviceAccountName: helm-installer
    podTemplate:
      securityContext:
        runAsUser: 0
  params:
    - name: experimentId
      value: {pipelinecurrent_run}
    - name: namespace
      value: {namespace}
  workspaces:
    - name: model-cache
      persistentVolumeClaim:
        claimName: model-pvc
    - name: hf-credentials
      secret:
        secretName: hf-secret
    - name: data-storage
      persistentVolumeClaim:
        claimName: data-pvc
"""
    Path(pipelinerun_yaml).write_text(pipelinerun_manifest)

    # Submit PipelineRun
    result = run(["kubectl", "apply", "-f", pipelinerun_yaml, f"-n={namespace}"],
                 check=False, capture=True)
    if result.returncode != 0:
        raise PhaseError(f"kubectl apply pipelinerun failed for {phase}: {result.stderr}")

    with _bench_state_lock:
        run([VENV_PYTHON, CLI, "benchmark-state",
         "--workspace", str(run_dir.parent.parent),
         "--set-phase", phase, "--status", "running",
         "--pipelinerun", pipelinecurrent_run],
        check=False, cwd=REPO_ROOT)

    # Poll until terminal state
    info(f"Waiting for {phase} PipelineRun: {pipelinecurrent_run} (timeout {PIPELINE_TIMEOUT_SECS}s)...")
    info(f"  To tail logs: tkn pr logs {pipelinecurrent_run} -n {namespace} -f")
    elapsed = 0
    while True:
        result = run(
            ["tkn", "pr", "describe", pipelinecurrent_run,
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
                     "--set-phase", phase, "--status", "failed",
                     "--failure-reason", f"Polling timeout after {PIPELINE_TIMEOUT_SECS}s on run {run_index}"],
                    check=False, cwd=REPO_ROOT)
            raise PhaseError(f"{phase} run {run_index} timed out after {PIPELINE_TIMEOUT_SECS}s")

    if "Succeeded" not in reason:
        fail_result = run(
            ["tkn", "pr", "describe", pipelinecurrent_run,
             "-o", "jsonpath={.status.conditions[0].message}",
             "-n", namespace],
            check=False, capture=True,
        )
        fail_reason = fail_result.stdout.strip() or "PipelineRun failed"
        with _bench_state_lock:
            run([VENV_PYTHON, CLI, "benchmark-state",
                 "--workspace", str(run_dir.parent.parent),
                 "--set-phase", phase, "--status", "failed",
                 "--failure-reason", fail_reason],
                check=False, cwd=REPO_ROOT)
        raise PhaseError(f"{phase} run {run_index} failed: {fail_reason}")

    ok(f"{phase} PipelineRun succeeded: {pipelinecurrent_run}")


def _reorganize_noise_results(staging: Path, dest: Path, n_runs: int) -> None:
    """Convert staging/run-{i}/{wl_name}/ → dest/{wl_name}/run-{i}/ for convert-trace."""
    import shutil
    for i in range(n_runs):
        run_stage = staging / f"run-{i}"
        if not run_stage.is_dir():
            continue
        for wl_dir in sorted(run_stage.iterdir()):
            if not wl_dir.is_dir():
                continue
            run_dest = dest / wl_dir.name / f"run-{i}"
            run_dest.mkdir(parents=True, exist_ok=True)
            for f in wl_dir.iterdir():
                shutil.copy2(f, run_dest / f.name)


def _extract_phase_results(phase: str, namespace: str, run_dir: Path,
                            experiment_ids: list[str] | None = None) -> Path:
    """Extract results from cluster data-pvc via extractor pod.

    experiment_ids: list of PipelineRun experiment IDs written to the PVC.
      - Baseline/treatment: single-element list → copies {phase}/{id}/ directly.
      - Noise: multi-element list → copies each {phase}/{id}/ into a staging area,
        then reorganizes into {wl_name}/run-{i}/ shape for convert-trace.
      - None: legacy fallback, copies the entire {phase}/ directory.

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

    if experiment_ids and len(experiment_ids) > 1:
        # Noise: copy each run into a staging subdir, then reorganize for convert-trace
        staging = raw_dir / "_stage"
        staging.mkdir(exist_ok=True)
        for i, exp_id in enumerate(experiment_ids):
            run_stage = staging / f"run-{i}"
            run_stage.mkdir(exist_ok=True)
            result = run(
                ["kubectl", "cp",
                 f"{namespace}/{pod_name}:/data/{phase}/{exp_id}/",
                 str(run_stage), "--retries=3"],
                check=False, capture=True,
            )
            if result.returncode != 0:
                run(["kubectl", "delete", "pod", pod_name, "-n", namespace,
                     "--ignore-not-found", "--force", "--grace-period=0"],
                    check=False, capture=True)
                raise PhaseError(f"kubectl cp failed for {phase} run {i} ({exp_id})")
    elif experiment_ids:
        # Baseline/treatment: single run-specific subdirectory
        result = run(
            ["kubectl", "cp",
             f"{namespace}/{pod_name}:/data/{phase}/{experiment_ids[0]}/",
             str(raw_dir), "--retries=3"],
            check=False, capture=True,
        )
    else:
        # Legacy fallback: copy entire phase directory
        result = run(
            ["kubectl", "cp", f"{namespace}/{pod_name}:/data/{phase}/", str(raw_dir), "--retries=3"],
            check=False, capture=True,
        )

    run(["kubectl", "delete", "pod", pod_name, "-n", namespace,
         "--ignore-not-found", "--force", "--grace-period=0"],
        check=False, capture=True)

    # For single-ID and legacy paths, check result here (multi-ID checked inside the loop)
    if not (experiment_ids and len(experiment_ids) > 1) and result.returncode != 0:
        raise PhaseError(f"kubectl cp failed for {phase}")

    if experiment_ids and len(experiment_ids) > 1:
        # Reorganize noise staging into wl_name/run-{i}/ structure for convert-trace
        _reorganize_noise_results(staging, raw_dir, len(experiment_ids))

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

    results_path = run_dir / f"deploy_{phase}_results.json"
    result = run(
        [VENV_PYTHON, CLI, "convert-trace",
         "--input-dir", str(raw_dir),
         "--output", str(results_path)],
        check=False, capture=True, cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        raise PhaseError(f"convert-trace failed for {phase}")

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

def _run_noise_phase(run_dir: Path, namespace: str, workspace_dir: Path) -> None:
    """Run the sequential noise characterization loop, then extract all results."""
    import yaml

    values_path = run_dir / "prepare_tekton" / "values.yaml"
    values = yaml.safe_load(values_path.read_text())
    noise_runs = values.get("observe", {}).get("noise_runs", 3)
    info(f"Running {noise_runs} noise characterization run(s)...")

    noise_experiment_ids: list[str] = []
    for i in range(noise_runs):
        pipelinecurrent_run = f"sim2real-noise-run{i}-{int(time.time())}"
        info(f"Noise run {i} of {noise_runs - 1}: {pipelinecurrent_run}")

        # Pre-flight (retry once if prior run's teardown is still completing)
        for attempt in range(2):
            result = run(
                [VENV_PYTHON, CLI, "preflight",
                 "--phase", "noise",
                 "--values", str(values_path),
                 "--namespace", namespace],
                check=False, capture=True, cwd=REPO_ROOT,
            )
            if result.returncode == 0:
                break
            if attempt == 0:
                warn("Preflight failed, retrying in 30s...")
                time.sleep(30)
            else:
                raise PhaseError("Preflight failed for noise phase")

        _run_pipeline_phase("noise", pipelinecurrent_run, namespace, run_dir, run_index=i)
        noise_experiment_ids.append(pipelinecurrent_run)

    # Extract all noise runs at once via a single extractor pod, reorganized for convert-trace
    _extract_phase_results("noise", namespace, run_dir, experiment_ids=noise_experiment_ids)
    ok(f"Noise characterization complete: {run_dir / 'deploy_noise_results.json'}")


# ── Stage 2: Cluster benchmarks ───────────────────────────────────────────────

def _gpu_warning(parallel: int, values_path: Path) -> None:
    """Warn about total GPU demand when running phases in parallel."""
    if parallel <= 1:
        return
    import yaml
    try:
        values = yaml.safe_load(values_path.read_text())
        decode = values.get("stack", {}).get("model", {}).get("helmValues", {}).get("decode", {})
        replicas = decode.get("replicas", 1)
        gpu_per_pod = int(
            decode.get("resources", {}).get("limits", {}).get("nvidia.com/gpu", "1")
        )
        gpus_per_phase = replicas * gpu_per_pod
        total = gpus_per_phase * parallel
        warn(
            f"Parallel={parallel} requires {total} GPUs "
            f"({gpus_per_phase} per phase × {parallel} concurrent)."
        )
        warn(
            "If fewer GPUs are available, Kubernetes will queue pending pods — "
            "phases will complete but wall-clock savings will be reduced."
        )
    except Exception:
        warn(f"Parallel={parallel}: could not compute GPU demand from values.yaml.")


def _run_single_phase(phase: str, run_dir: Path, namespace: str,
                      workspace_dir: Path, bench_state_file: Path,
                      force_rerun: bool,
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
        _run_noise_phase(run_dir, namespace, workspace_dir)
    else:
        values_path = str(run_dir / "prepare_tekton" / "values.yaml")
        result = run(
            _preflight_cmd(phase, values_path, namespace, manifest),
            check=False, capture=True, cwd=REPO_ROOT,
        )
        if result.returncode != 0:
            raise PhaseError(f"Preflight failed for {phase}")

        pipelinecurrent_run = f"sim2real-{phase}-{int(time.time())}"
        info(f"[{phase}] Submitting PipelineRun: {pipelinecurrent_run}")
        _run_pipeline_phase(phase, pipelinecurrent_run, namespace, run_dir)
        _extract_phase_results(phase, namespace, run_dir, experiment_ids=[pipelinecurrent_run])

    ok(f"[{phase}] Phase complete")
    return phase, "done"


def stage_benchmarks(run_dir: Path, namespace: str, fast_iter: bool, manifest: dict,
                     force_rerun: bool = False, parallel: int = 1) -> str:
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

    # Initialize benchmark state (both fast and full mode need this so that
    # _run_pipeline_phase can update phase status without --namespace)
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

    # ── Run phases ────────────────────────────────────────────────────────────
    phases_to_run = ([] if fast_iter else ["noise"]) + ["baseline", "treatment"]
    bench_state_file = workspace_dir / "benchmark_state.json"

    _gpu_warning(parallel, run_dir / "prepare_tekton" / "values.yaml")
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

            if phase == "noise":
                _run_noise_phase(run_dir, namespace, workspace_dir)
            else:
                result = run(
                    _preflight_cmd(phase, str(run_dir / "prepare_tekton" / "values.yaml"),
                                   namespace, manifest),
                    check=False, capture=True, cwd=REPO_ROOT,
                )
                if result.returncode != 0:
                    err(f"Preflight failed for {phase}")
                    sys.exit(1)
                pipelinecurrent_run = f"sim2real-{phase}-{int(time.time())}"
                _run_pipeline_phase(phase, pipelinecurrent_run, namespace, run_dir)
                _extract_phase_results(phase, namespace, run_dir, experiment_ids=[pipelinecurrent_run])
    else:
        # Parallel mode: ThreadPoolExecutor dispatches up to N phases concurrently
        with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as pool:
            futures = {
                pool.submit(
                    _run_single_phase, phase, run_dir, namespace,
                    workspace_dir, bench_state_file, force_rerun,
                    manifest,
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

    cfg, current_run, run_dir = load_setup_config()
    namespace = os.environ.get("NAMESPACE", cfg["namespace"])

    update_run_metadata(run_dir, status="in_progress",
                        started_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

    plugin_file, fast_iter = check_prerequisites(run_dir, manifest)

    if not args.skip_build_epp:
        full_image = stage_build_epp(run_dir, current_run, namespace, manifest)
    else:
        meta = json.loads((run_dir / "run_metadata.json").read_text())
        full_image = meta.get("epp_image", "")
        if not full_image:
            err("--skip-build-epp set but no epp_image in run_metadata.json")
            sys.exit(1)
        info(f"Skipping EPP build. Using image: {full_image}")

    verdict = stage_benchmarks(run_dir, namespace, fast_iter, manifest, args.force_rerun,
                               parallel=args.parallel)

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

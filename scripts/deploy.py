#!/usr/bin/env python3
"""sim2real deploy — Build EPP, Cluster Benchmarks, PR."""

import argparse
import json
import os
import subprocess
import sys
import time
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# ── Repo layout ───────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent
VENV_PYTHON = str(REPO_ROOT / ".venv/bin/python")
CLI = str(REPO_ROOT / "tools/transfer_cli.py")

PIPELINE_TIMEOUT_SECS = 14400  # 4 hours

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
    p.add_argument("--skip-build-epp", action="store_true",
                   help="Skip EPP image build (use if already built this run)")
    p.add_argument("--pr", action="store_true",
                   help="Create PR after benchmarks pass (default: skip, review results first)")
    p.add_argument("--force-rerun", action="store_true",
                   help="Re-run already-done benchmark phases without prompting")
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
    """
    val = {
        "suite_a": equiv["suite_a"],
        "suite_b": equiv["suite_b"],
        "suite_c": equiv["suite_c"],
    }
    if fast_iter:
        passed = (
            val.get("suite_a", {}).get("passed")
            and val.get("suite_c", {}).get("passed")
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
    if (mech == "PASS"
            and val.get("suite_a", {}).get("passed")
            and val.get("suite_c", {}).get("passed")):
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

def check_prerequisites(run_dir: Path) -> tuple[str, bool]:
    """Verify Phase 1 artifacts and cluster readiness.

    Returns (scorer_file_path, fast_iter).
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

    # Equivalence gate check (Suite A + C required)
    equiv = json.loads((run_dir / "prepare_equivalence_results.json").read_text())
    if not equiv.get("suite_a", {}).get("passed"):
        err("Suite A did not pass — re-run prepare equivalence gate")
        sys.exit(1)
    if not equiv.get("suite_c", {}).get("passed"):
        err("Suite C did not pass — re-run prepare equivalence gate")
        sys.exit(1)

    # Scorer file exists and builds
    stage3 = json.loads((run_dir / "prepare_stage3_output.json").read_text())
    scorer_file = stage3["scorer_file"]
    if not Path(scorer_file).exists():
        err(f"Scorer file missing: {scorer_file}")
        sys.exit(1)
    result = run(
        ["go", "build", "./..."],
        check=False, capture=True,
        cwd=REPO_ROOT / "llm-d-inference-scheduler",
        env={**os.environ, "GOWORK": "off"},
    )
    if result.returncode != 0:
        err("Scorer build failed:")
        print(result.stderr)
        sys.exit(1)

    # Registry configuration check
    try:
        import yaml
        cfg = yaml.safe_load((REPO_ROOT / "config" / "env_defaults.yaml").read_text())
        hub = (cfg.get("stack", {})
                  .get("gaie", {})
                  .get("epp_image", {})
                  .get("build", {})
                  .get("hub", ""))
        if not hub or "REPLACE_ME" in hub:
            err("Set epp_image.build.hub in config/env_defaults.yaml before deploying")
            sys.exit(1)
        fast_iter = bool(cfg.get("pipeline", {}).get("fast_iteration", True))
    except Exception as e:
        err(f"Cannot read config/env_defaults.yaml: {e}")
        sys.exit(1)

    ok("All prerequisites satisfied")
    info(f"Fast iteration mode: {fast_iter}")
    return scorer_file, fast_iter


# ── Stage 1: Build EPP ────────────────────────────────────────────────────────

def stage_build_epp(run_dir: Path, current_run: str, namespace: str) -> str:
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
    env_cfg = yaml.safe_load((REPO_ROOT / "config" / "env_defaults.yaml").read_text())
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
    run([VENV_PYTHON, CLI, "merge-values",
         "--env", str(REPO_ROOT / "config" / "env_defaults.yaml"),
         "--algorithm", str(alg_values_path),
         "--out", str(values_out)],
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
    Updates benchmark-state on success/failure. Exits 1 on failure or timeout.
    """
    pipelines_dir = run_dir / "prepare_tekton" / "pipelines"
    pipeline_yaml = pipelines_dir / f"{phase}-pipeline.yaml"

    # Apply pipeline definition
    result = run(["kubectl", "apply", "-f", str(pipeline_yaml), f"-n={namespace}"],
                 check=False, capture=True)
    if result.returncode != 0:
        err(f"kubectl apply failed for {phase} pipeline: {result.stderr}")
        sys.exit(1)

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
        err(f"kubectl apply pipelinerun failed for {phase}: {result.stderr}")
        sys.exit(1)

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
            run([VENV_PYTHON, CLI, "benchmark-state",
                 "--workspace", str(run_dir.parent.parent),
                 "--set-phase", phase, "--status", "failed",
                 "--failure-reason", f"Polling timeout after {PIPELINE_TIMEOUT_SECS}s on run {run_index}"],
                check=False, cwd=REPO_ROOT)
            err(f"{phase} run {run_index} timed out after {PIPELINE_TIMEOUT_SECS}s")
            sys.exit(1)

    if "Succeeded" not in reason:
        fail_result = run(
            ["tkn", "pr", "describe", pipelinecurrent_run,
             "-o", "jsonpath={.status.conditions[0].message}",
             "-n", namespace],
            check=False, capture=True,
        )
        fail_reason = fail_result.stdout.strip() or "PipelineRun failed"
        run([VENV_PYTHON, CLI, "benchmark-state",
             "--workspace", str(run_dir.parent.parent),
             "--set-phase", phase, "--status", "failed",
             "--failure-reason", fail_reason],
            check=False, cwd=REPO_ROOT)
        err(f"{phase} run {run_index} failed: {fail_reason}")
        sys.exit(1)

    ok(f"{phase} PipelineRun succeeded: {pipelinecurrent_run}")


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
        err(f"Extractor pod {pod_name} not ready")
        sys.exit(1)

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
        err(f"kubectl cp failed for {phase}")
        sys.exit(1)

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
        err(f"convert-trace failed for {phase}")
        sys.exit(1)

    result = run([VENV_PYTHON, CLI, "validate-schema", str(results_path)],
                 check=False, capture=True, cwd=REPO_ROOT)
    if result.returncode != 0:
        err(f"Schema validation failed for {phase}_results.json — do not mark phase done")
        sys.exit(1)

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
                err("Preflight failed for noise phase")
                sys.exit(1)

        _run_pipeline_phase("noise", pipelinecurrent_run, namespace, run_dir, run_index=i)

    # Extract all noise runs at once via a single extractor pod
    _extract_phase_results("noise", namespace, run_dir)
    ok(f"Noise characterization complete: {run_dir / 'deploy_noise_results.json'}")


# ── Stage 2: Cluster benchmarks ───────────────────────────────────────────────

def stage_benchmarks(run_dir: Path, namespace: str, fast_iter: bool, force_rerun: bool = False) -> str:
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

    # ── Run phases ────────────────────────────────────────────────────────────
    phases_to_run = ([] if fast_iter else ["noise"]) + ["baseline", "treatment"]
    bench_state_file = workspace_dir / "benchmark_state.json"

    for phase in phases_to_run:
        # Skip or re-run if already done
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
        _clear_phase_state(phase, bench_state_file)  # no-op if file absent

        if phase == "noise":
            _run_noise_phase(run_dir, namespace, workspace_dir)
        else:
            # Pre-flight
            result = run(
                [VENV_PYTHON, CLI, "preflight",
                 "--phase", phase,
                 "--values", str(run_dir / "prepare_tekton" / "values.yaml"),
                 "--namespace", namespace],
                check=False, capture=True, cwd=REPO_ROOT,
            )
            if result.returncode != 0:
                err(f"Preflight failed for {phase}")
                sys.exit(1)

            pipelinecurrent_run = f"sim2real-{phase}-{int(time.time())}"
            _run_pipeline_phase(phase, pipelinecurrent_run, namespace, run_dir)
            _extract_phase_results(phase, namespace, run_dir)

    # ── Mechanism check (full mode only) ─────────────────────────────────────
    if not fast_iter:
        step("2a", "Mechanism check")
        bench_out = run_dir / "deploy_benchmark_output.json"
        result = run(
            [VENV_PYTHON, CLI, "benchmark",
             "--noise", str(run_dir / "deploy_noise_results.json"),
             "--baseline", str(run_dir / "deploy_baseline_results.json"),
             "--treatment", str(run_dir / "deploy_treatment_results.json"),
             "--signal-coverage", str(run_dir / "prepare_signal_coverage.json"),
             "--workloads-dir", str(REPO_ROOT / "blis_router/workloads/"),
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

def stage_pr(run_dir: Path) -> str | None:
    """Create PR in llm-d-inference-scheduler. Returns PR URL, or None if skipped."""
    step(3, "PR Creation")

    # Fast-iteration guard (pipeline.fast_iteration=true means no PR)
    try:
        import yaml
        cfg = yaml.safe_load((REPO_ROOT / "config" / "env_defaults.yaml").read_text())
        fast_iter = bool(cfg.get("pipeline", {}).get("fast_iteration", True))
    except Exception as e:
        err(f"Cannot read config/env_defaults.yaml: {e}")
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

    # Push branch to llm-d-inference-scheduler
    alg_name = json.loads((run_dir / "prepare_algorithm_summary.json").read_text())["algorithm_name"]
    branch = f"transfer/{alg_name}"
    scheduler_dir = REPO_ROOT / "llm-d-inference-scheduler"

    result = run(
        ["git", "ls-remote", "--exit-code", "--heads", "origin", branch],
        check=False, capture=True, cwd=scheduler_dir,
    )
    if result.returncode == 0:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        branch = f"{branch}-{timestamp}"
        warn(f"Branch already exists — using timestamped branch: {branch}")

    run(["git", "checkout", "-b", branch], cwd=scheduler_dir)
    run(["git", "add", "-A"], cwd=scheduler_dir)
    result = run(["git", "diff", "--cached", "--quiet"], check=False, cwd=scheduler_dir)
    if result.returncode != 0:
        # There are staged changes to commit
        run(["git", "commit", "-m",
             f"feat: add {alg_name} scorer plugin (sim2real transfer)"],
            cwd=scheduler_dir)
    else:
        warn("No changes to commit in llm-d-inference-scheduler — pushing branch as-is")
    result = run(["git", "push", "-u", "origin", branch], check=False, capture=True, cwd=scheduler_dir)
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
    suite_a_tau = val.get("suite_a", {}).get("kendall_tau", "N/A")
    suite_c_pass = str(val.get("suite_c", {}).get("passed", False)).lower()
    mech = val.get("benchmark", {}).get("mechanism_check_verdict", "N/A")
    evidence = evidence_path.read_text()

    pr_body = f"""## Summary

Sim-to-production transfer: `{alg_name}`

**Validation:**
- Suite A Kendall-tau: `{suite_a_tau}` (threshold: 0.8)
- Suite C concurrent safety: `{suite_c_pass}`
- Mechanism check: `{mech}`
- Overall verdict: `{verdict}`

## Evidence

{evidence}

## Rollback

To disable: in EndpointPickerConfig, set `parameters.enabled: false` on the blis-weighted-scorer plugin entry.
"""

    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write(pr_body)
        body_file = f.name

    try:
        result = run(
            ["gh", "pr", "create",
             "--title", f"feat(scorer): add {alg_name} sim-to-production scorer plugin",
             "--base", "main",
             "--head", branch,
             "--body-file", body_file],
            check=False, capture=True, cwd=scheduler_dir,
        )
        if result.returncode != 0:
            err(f"gh pr create failed. Branch '{branch}' is already pushed — create PR manually.")
            sys.exit(1)
    finally:
        Path(body_file).unlink(missing_ok=True)

    pr_url_result = run(["gh", "pr", "view", "--json", "url", "-q", ".url"],
                        check=False, capture=True, cwd=scheduler_dir)
    pr_url = pr_url_result.stdout.strip()
    ok(f"PR created: {pr_url}")
    return pr_url


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> int:
    args = build_parser().parse_args()
    print(_c("36", "\n━━━ sim2real-deploy ━━━\n"))

    cfg, current_run, run_dir = load_setup_config()
    namespace = os.environ.get("NAMESPACE", cfg["namespace"])

    update_run_metadata(run_dir, status="in_progress",
                        started_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

    scorer_file, fast_iter = check_prerequisites(run_dir)

    if not args.skip_build_epp:
        full_image = stage_build_epp(run_dir, current_run, namespace)
    else:
        meta = json.loads((run_dir / "run_metadata.json").read_text())
        full_image = meta.get("epp_image", "")
        if not full_image:
            err("--skip-build-epp set but no epp_image in run_metadata.json")
            sys.exit(1)
        info(f"Skipping EPP build. Using image: {full_image}")

    verdict = stage_benchmarks(run_dir, namespace, fast_iter, args.force_rerun)

    pr_url = None
    if args.pr:
        pr_url = stage_pr(run_dir)

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

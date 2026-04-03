"""
BLIS Admission Control Evaluator for SkyDiscover

Evaluates evolved admission control policies by:
1. Extracting Go code from Python wrapper
2. Writing evolved admission.go to BLIS source (restored after evaluation)
3. Building BLIS
4. Running simulations with --results-path for per-request JSON
5. Computing multi-objective score: weighted_slo + capped_throughput + jain_fairness

Score = 0.50 * weighted_slo + 0.30 * capped_throughput + 0.20 * jain_fairness
  - weighted_slo: Class-weighted SLO attainment. Weights: critical=4, standard=2,
    sheddable=1, batch=0.5. Shed-tolerant: rejected sheddable/batch are excluded
    from the denominator (shedding them is a correct decision, not a failure).
    Rejected critical/standard still count as SLO misses.
  - capped_throughput: min(completed/num_requests, THROUGHPUT_CAP) / THROUGHPUT_CAP.
  - jain_fairness: Jain index over per-tenant completion rates. N = total tenants in workload.

Higher is better; 0 = reject-all, ~0.64 = always-admit baseline under 2x overload,
~0.87 = good admission control with selective shedding.

Experiment isolation:
- admission.go is saved before and restored after every evaluation
- baseline_metrics.json is written to BLIS_OUTPUT_DIR (not benchmark dir)
- No artifacts are left in the benchmark directory between evaluations

NOTE: This evaluator is NOT safe for concurrent execution.
Multiple evaluate() calls MUST be serialized because they share admission.go.
"""

import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import traceback
from collections import defaultdict
from difflib import unified_diff
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

WORKLOADS = [
    ("overload_mixed_slo", "workload_overload_mixed_slo.yaml"),
    ("bursty_adversary", "workload_bursty_adversary.yaml"),
]

# Model configs: (short_name, model_id, extra CLI args)
_MODEL_QWEN_7B = ("qwen_7b", "qwen/qwen2.5-7b-instruct", [
    "--hardware", "H100", "--tp", "1",
])
_MODEL_QWEN_14B = ("qwen_14b", "qwen/qwen3-14b", [
    "--hardware", "H100", "--tp", "1",
])

_DEFAULT_SEEDS = ["42", "456"]
SIM_SEEDS = (
    os.environ.get("BLIS_SEED", "").split(",")
    if os.environ.get("BLIS_SEED")
    else _DEFAULT_SEEDS
)
SIM_NUM_INSTANCES = os.environ.get("BLIS_NUM_INSTANCES", "4")
MULTI_LLM = os.environ.get("BLIS_MULTI_LLM", "0") == "1"
SIM_SNAPSHOT_REFRESH = os.environ.get("BLIS_SNAPSHOT_REFRESH", "5000000")

# Scoring parameters
THROUGHPUT_CAP = float(os.environ.get("BLIS_THROUGHPUT_CAP", "0.85"))

# SLO targets (ms) — calibrated from Phase 0 pilot.
# Override via BLIS_SLO_TARGETS='{"critical":1000,"standard":3000,...}' or calibration.json.
_DEFAULT_SLO_TARGETS = {
    "critical": 1000,
    "standard": 3000,
    "sheddable": 8000,
    "batch": 15000,
    "background": 30000,
}


def _load_slo_targets(script_dir: Path) -> dict:
    """Load SLO targets from env, calibration.json, or defaults.

    Also loads throughput_cap from calibration.json if present (updates global THROUGHPUT_CAP).
    """
    global THROUGHPUT_CAP

    env_targets = os.environ.get("BLIS_SLO_TARGETS")
    if env_targets:
        try:
            return json.loads(env_targets)
        except json.JSONDecodeError:
            logger.warning("Invalid BLIS_SLO_TARGETS JSON, using defaults")

    cal_path = script_dir / "calibration.json"
    if cal_path.exists():
        try:
            with open(cal_path) as f:
                cal = json.load(f)
            # Load throughput_cap if present and not overridden by env
            if "throughput_cap" in cal and not os.environ.get("BLIS_THROUGHPUT_CAP"):
                THROUGHPUT_CAP = float(cal["throughput_cap"])
                logger.info("Loaded throughput_cap=%.2f from %s", THROUGHPUT_CAP, cal_path)
            if "slo_targets" in cal:
                logger.info("Loaded SLO targets from %s", cal_path)
                return cal["slo_targets"]
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read calibration.json: %s", exc)

    logger.info("Using default SLO targets (run calibrate_capacity.py for data-driven targets)")
    return _DEFAULT_SLO_TARGETS


def _get_models():
    """Return list of model configs to evaluate against."""
    if MULTI_LLM:
        return [_MODEL_QWEN_7B, _MODEL_QWEN_14B]
    return [_MODEL_QWEN_7B]


def _get_output_dir() -> Path:
    """Get the experiment output directory from env, falling back to a temp location."""
    output_dir = os.environ.get("BLIS_OUTPUT_DIR")
    if output_dir:
        return Path(output_dir)
    logger.warning(
        "BLIS_OUTPUT_DIR not set. Artifacts will be written to benchmark directory. "
        "Set BLIS_OUTPUT_DIR for experiment runs to ensure isolation."
    )
    fallback = Path(__file__).parent / ".eval_artifacts"
    fallback.mkdir(exist_ok=True)
    return fallback


def _load_num_requests(workload_path: Path) -> int:
    """Parse num_requests from a workload YAML file."""
    with open(workload_path) as f:
        spec = yaml.safe_load(f)
    nr = spec.get("num_requests")
    if not nr or nr <= 0:
        raise ValueError(f"Workload {workload_path} must have num_requests > 0, got {nr}")
    return int(nr)


def _load_workload_tenant_fractions(workload_path: Path) -> dict[str, float]:
    """Extract per-tenant expected request fractions from workload YAML."""
    with open(workload_path) as f:
        spec = yaml.safe_load(f)
    fractions: dict[str, float] = defaultdict(float)
    for client in spec.get("clients", []):
        tid = client.get("tenant_id", "")
        frac = client.get("rate_fraction", 0.0)
        if tid:
            fractions[tid] += frac
    for cohort in spec.get("cohorts", []):
        tid = cohort.get("tenant_id", "")
        frac = cohort.get("rate_fraction", 0.0)
        if tid:
            fractions[tid] += frac
    return dict(fractions)


def _load_workload_class_fractions(workload_path: Path) -> dict[str, float]:
    """Extract per-SLO-class expected request fractions from workload YAML."""
    with open(workload_path) as f:
        spec = yaml.safe_load(f)
    fractions: dict[str, float] = defaultdict(float)
    for client in spec.get("clients", []):
        cls = client.get("slo_class", "standard")
        frac = client.get("rate_fraction", 0.0)
        fractions[cls] += frac
    for cohort in spec.get("cohorts", []):
        cls = cohort.get("slo_class", "standard")
        frac = cohort.get("rate_fraction", 0.0)
        fractions[cls] += frac
    return dict(fractions)


def _build_sim_cmd(
    inference_sim_dir: Path, policy_config_path: Path, workload_path: Path,
    model_id: str, extra_args: list[str], seed: str, results_path: str,
) -> list[str]:
    return [
        "./simulation_worker", "run",
        "--model", model_id,
        "--num-instances", SIM_NUM_INSTANCES,
        "--policy-config", str(policy_config_path),
        "--workload-spec", str(workload_path),
        "--snapshot-refresh-interval", SIM_SNAPSHOT_REFRESH,
        "--results-path", results_path,
        "--log", "info",
        "--seed", seed,
    ] + extra_args


def _restore_admission_go(admission_go_path: Path, original_content: str):
    """Restore admission.go to its original content. Logs and re-raises on failure."""
    try:
        admission_go_path.write_text(original_content)
    except OSError as exc:
        logger.error(
            "CRITICAL: Failed to restore admission.go after evaluation! "
            "File may be corrupted. Path: %s, Error: %s",
            admission_go_path, exc,
        )
        raise


def extract_evolve_block(code: str) -> str:
    pattern = r"// EVOLVE-BLOCK-START(.*?)// EVOLVE-BLOCK-END"
    match = re.search(pattern, code, re.DOTALL)
    return match.group(1).strip() if match else ""


def print_diff(initial_code: str, current_code: str):
    initial_block = extract_evolve_block(initial_code)
    current_block = extract_evolve_block(current_code)
    if not initial_block or not current_block:
        return
    diff = list(unified_diff(
        initial_block.splitlines(keepends=True),
        current_block.splitlines(keepends=True),
        lineterm="",
    ))
    if not diff:
        logger.info("NO DIFF — code unchanged from initial")
        return
    removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
    added = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
    logger.info(f"Diff vs initial: -{removed} / +{added} lines")


_SLO_CLASS_WEIGHTS = {
    "critical": 4.0,
    "standard": 2.0,
    "sheddable": 1.0,
    "batch": 0.5,
}

# Classes where rejection is a correct decision (not penalized in SLO attainment)
_SHEDDABLE_CLASSES = {"sheddable", "batch"}


def _compute_slo_attainment(
    requests: list[dict], num_requests: int, slo_targets: dict,
    class_fractions: dict[str, float] | None = None,
) -> float:
    """Compute class-weighted, shed-tolerant SLO attainment.

    Per-class SLO attainment:
      - For critical/standard: met / expected. Rejected = SLO miss.
      - For sheddable/batch: met / injected. Rejected = excluded (correct decision).
    Weighted average using _SLO_CLASS_WEIGHTS.
    """
    if num_requests <= 0:
        return 0.0

    if not class_fractions:
        # Fallback: simple SLO attainment (all classes equal, rejected = miss)
        meeting = 0
        for req in requests:
            e2e_ms = req.get("e2e_ms", 0)
            if e2e_ms <= 0:
                continue
            target = slo_targets.get(req.get("slo_class", "standard"), 3000)
            if e2e_ms <= target:
                meeting += 1
        return meeting / num_requests

    # Count injected and SLO-meeting per class
    injected_per_class: dict[str, int] = defaultdict(int)
    met_per_class: dict[str, int] = defaultdict(int)
    for req in requests:
        cls = req.get("slo_class", "standard")
        injected_per_class[cls] += 1
        e2e_ms = req.get("e2e_ms", 0)
        if e2e_ms <= 0:
            continue
        target = slo_targets.get(cls, slo_targets.get("standard", 3000))
        if e2e_ms <= target:
            met_per_class[cls] += 1

    # Expected per class from workload spec
    expected_per_class = {cls: int(frac * num_requests)
                         for cls, frac in class_fractions.items()}

    # Weighted average of per-class SLO attainment
    total_weight = 0.0
    weighted_sum = 0.0
    for cls, expected in expected_per_class.items():
        if expected <= 0:
            continue
        w = _SLO_CLASS_WEIGHTS.get(cls, 1.0)
        if cls in _SHEDDABLE_CLASSES:
            # Shed-tolerant: denominator = injected (rejected are excluded)
            denom = injected_per_class.get(cls, 0)
            if denom > 0:
                att = met_per_class.get(cls, 0) / denom
            else:
                att = 1.0  # All rejected = correct shedding decision
        else:
            # Critical/standard: denominator = expected. Rejected = SLO miss.
            att = met_per_class.get(cls, 0) / expected
        weighted_sum += w * att
        total_weight += w

    return weighted_sum / total_weight if total_weight > 0 else 0.0


def _compute_capped_throughput(completed: int, num_requests: int) -> float:
    """Compute throughput capped at THROUGHPUT_CAP.

    min(completed/num_requests, cap) / cap
    So always-admit (throughput=1.0) and moderate shedding (throughput=0.85)
    both score 1.0 on this term.
    """
    if num_requests <= 0:
        return 0.0
    raw = completed / num_requests
    return min(raw, THROUGHPUT_CAP) / THROUGHPUT_CAP


def _compute_jain_fairness(
    requests: list[dict], tenant_fractions: dict[str, float], num_requests: int,
) -> float:
    """Compute Jain fairness index over per-tenant completion rates.

    N = total tenants in workload spec (not tenants with completions).
    xi = completed_tenant_i / expected_tenant_i.
    Jain = (sum(xi))^2 / (N * sum(xi^2)).
    Guard: if all xi=0, return 0.0.
    """
    if not tenant_fractions:
        return 1.0  # Single or no tenant — fairness is trivially 1.0

    # Count completions per tenant (only requests with e2e_ms > 0 are truly completed)
    completed_per_tenant: dict[str, int] = defaultdict(int)
    for req in requests:
        tid = req.get("tenant_id", "")
        if tid and req.get("e2e_ms", 0) > 0:
            completed_per_tenant[tid] += 1

    # Compute per-tenant completion rates
    n = len(tenant_fractions)
    xi_values = []
    for tid, frac in tenant_fractions.items():
        expected = frac * num_requests
        if expected > 0:
            xi = completed_per_tenant.get(tid, 0) / expected
        else:
            xi = 0.0
        xi_values.append(xi)

    sum_xi = sum(xi_values)
    sum_xi_sq = sum(x * x for x in xi_values)

    if sum_xi_sq == 0:
        return 0.0  # All tenants have zero completions

    return (sum_xi * sum_xi) / (n * sum_xi_sq)


def _error_result(error_msg: str, error_type: str, suggestion: str = "", **extra_artifacts) -> dict:
    return {
        "combined_score": -100000.0,
        "avg_e2e_ms": float("inf"),
        "error": error_msg,
        "artifacts": {
            "error_type": error_type,
            "error_message": error_msg,
            "suggestion": suggestion,
            **extra_artifacts,
        },
    }


def _run_single_sim(
    inference_sim_dir: Path, policy_config_path: Path, workload_path: Path,
    model_id: str, extra_args: list[str], seed: str, log_key: str,
) -> dict | None:
    """Run a single simulation with --results-path and return parsed JSON, or None on failure."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        results_path = tmp.name

    try:
        cmd = _build_sim_cmd(
            inference_sim_dir, policy_config_path, workload_path,
            model_id, extra_args, seed, results_path,
        )
        sim_result = subprocess.run(
            cmd, cwd=inference_sim_dir, capture_output=True, text=True, timeout=180,
        )
        if sim_result.returncode != 0:
            logger.warning("%s failed (rc=%d): %s", log_key, sim_result.returncode, sim_result.stderr[:300])
            return None

        # Parse --results-path JSON
        try:
            with open(results_path) as f:
                data = json.load(f)
            return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("%s: failed to parse results JSON: %s", log_key, exc)
            return None

    except subprocess.TimeoutExpired:
        logger.warning("%s: simulation timed out (180s)", log_key)
        return None
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("%s failed: %s", log_key, exc)
        return None
    finally:
        try:
            os.unlink(results_path)
        except OSError:
            pass


def _run_workloads(
    models, script_dir: Path, inference_sim_dir: Path, policy_config_path: Path,
    seeds: list[str], slo_targets: dict,
) -> dict:
    """Run all workloads across seeds and models. Compute per-workload multi-objective scores.

    Returns dict with per-workload scores and aggregate results.
    """
    workload_scores = {}

    for workload_name, workload_file in WORKLOADS:
        workload_path = script_dir / "workloads" / workload_file
        num_requests = _load_num_requests(workload_path)
        tenant_fractions = _load_workload_tenant_fractions(workload_path)
        class_fractions = _load_workload_class_fractions(workload_path)

        # Collect results across seeds × models
        wl_slo_attainments = []
        wl_throughputs = []
        wl_fairnesses = []
        wl_e2e_values = []
        wl_p95_values = []

        for model_name, model_id, extra_args in models:
            for seed in seeds:
                seed_tag = f"[seed={seed}]" if len(seeds) > 1 else ""
                model_tag = f"{model_name}/" if len(models) > 1 else ""
                log_key = f"{model_tag}{workload_name}{seed_tag}"

                data = _run_single_sim(
                    inference_sim_dir, policy_config_path, workload_path,
                    model_id, extra_args, seed, log_key,
                )
                if data is None:
                    continue

                completed = data.get("completed_requests", 0)
                injected = data.get("injected_requests", 0)
                rejected = max(0, num_requests - injected)  # INV-1 derivation (for logging only)
                requests = data.get("requests", [])

                slo_att = _compute_slo_attainment(requests, num_requests, slo_targets, class_fractions)
                throughput = _compute_capped_throughput(completed, num_requests)
                fairness = _compute_jain_fairness(requests, tenant_fractions, num_requests)

                wl_slo_attainments.append(slo_att)
                wl_throughputs.append(throughput)
                wl_fairnesses.append(fairness)

                e2e_mean = data.get("e2e_mean_ms", 0)
                e2e_p95 = data.get("e2e_p95_ms", 0)
                if e2e_mean > 0:
                    wl_e2e_values.append(e2e_mean)
                if e2e_p95 > 0:
                    wl_p95_values.append(e2e_p95)

                logger.info(
                    "%s: completed=%d, rejected=%d, slo_att=%.3f, throughput=%.3f, fairness=%.3f, e2e=%.1fms",
                    log_key, completed, rejected, slo_att, throughput, fairness, e2e_mean,
                )

        if wl_slo_attainments:
            avg_slo = sum(wl_slo_attainments) / len(wl_slo_attainments)
            avg_thr = sum(wl_throughputs) / len(wl_throughputs)
            avg_fair = sum(wl_fairnesses) / len(wl_fairnesses)
            wl_score = 0.50 * avg_slo + 0.30 * avg_thr + 0.20 * avg_fair
        else:
            avg_slo = avg_thr = avg_fair = wl_score = 0.0

        workload_scores[workload_name] = {
            "score": wl_score,
            "slo_attainment": avg_slo,
            "capped_throughput": avg_thr,
            "jain_fairness": avg_fair,
            "avg_e2e_ms": sum(wl_e2e_values) / len(wl_e2e_values) if wl_e2e_values else None,
            "avg_p95_ms": sum(wl_p95_values) / len(wl_p95_values) if wl_p95_values else None,
            "num_runs": len(wl_slo_attainments),
        }

    return workload_scores


def extract_go_code(program_text: str) -> str:
    match = re.search(r'GO_ADMISSION_CODE\s*=\s*"""(.*?)"""', program_text, re.DOTALL)
    if match:
        return match.group(1).strip()
    if "package sim" in program_text:
        return program_text
    return ""


def get_or_compute_baseline(
    script_dir: Path, inference_sim_dir: Path, policy_config_path: Path,
    slo_targets: dict,
) -> dict:
    """Compute baseline metrics, caching in the experiment output directory."""
    output_dir = _get_output_dir()
    cache_path = output_dir / "baseline_metrics.json"

    try:
        with open(cache_path, "r") as f:
            cached = json.load(f)
        cached_seeds = cached.get("seeds")
        if cached_seeds == sorted(SIM_SEEDS):
            return cached
        logger.info("Baseline seeds changed (%s -> %s), recomputing", cached_seeds, sorted(SIM_SEEDS))
    except FileNotFoundError:
        pass
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read baseline cache, recomputing: %s", exc)

    initial_program_path = script_dir / "initial_program.go"
    if not initial_program_path.exists():
        logger.warning("initial_program.go not found; cannot compute baseline")
        return {}

    with open(initial_program_path, "r") as f:
        initial_text = f.read()

    go_code = extract_go_code(initial_text)
    if not go_code:
        logger.warning("Could not extract Go code from initial program for baseline")
        return {}

    admission_go_path = inference_sim_dir / "sim" / "admission.go"
    if not admission_go_path.exists():
        logger.warning("admission.go not found at %s", admission_go_path)
        return {}

    original_admission = admission_go_path.read_text()
    try:
        admission_go_path.write_text(go_code)

        build_result = subprocess.run(
            ["go", "build", "-o", "simulation_worker", "main.go"],
            cwd=inference_sim_dir, capture_output=True, text=True, timeout=60,
        )
        if build_result.returncode != 0:
            logger.warning("Baseline build failed: %s", build_result.stderr[:300])
            return {}

        models = _get_models()
        workload_scores = _run_workloads(
            models, script_dir, inference_sim_dir, policy_config_path,
            seeds=SIM_SEEDS, slo_targets=slo_targets,
        )

        # Compute combined baseline score (average across workloads)
        wl_scores = [ws["score"] for ws in workload_scores.values() if ws.get("num_runs", 0) > 0]
        combined = sum(wl_scores) / len(wl_scores) if wl_scores else 0.0

        baseline = {
            "seeds": sorted(SIM_SEEDS),
            "combined_score": combined,
            "workload_scores": workload_scores,
            "slo_targets": slo_targets,
            "throughput_cap": THROUGHPUT_CAP,
        }

        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_path, "w") as f:
                json.dump(baseline, f, indent=2)
            logger.info("Cached baseline metrics to %s (score=%.4f)", cache_path, combined)
        except OSError as exc:
            logger.warning("Failed to cache baseline metrics: %s", exc)

        return baseline

    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("Baseline computation error: %s", exc)
        return {}
    finally:
        _restore_admission_go(admission_go_path, original_admission)


def evaluate(program_path: str) -> dict:
    try:
        with open(program_path, "r") as f:
            program_text = f.read()
    except (OSError, IOError) as exc:
        return _error_result(
            f"Cannot read program file: {exc}", "FileReadError",
            f"Check that {program_path} exists and is readable",
        )

    script_dir = Path(__file__).parent
    inference_sim_dir = script_dir / "inference-sim"
    admission_go_path = inference_sim_dir / "sim" / "admission.go"
    policy_config_path = script_dir / "routing" / "routing_policy.yaml"

    if not admission_go_path.exists():
        return _error_result(
            f"admission.go not found at {admission_go_path}",
            "SetupError",
            "Initialize the inference-sim submodule: git submodule update --init",
        )

    go_code = extract_go_code(program_text)
    if not go_code:
        return _error_result(
            "Failed to extract Go code", "ExtractionError",
            'Ensure program contains GO_ADMISSION_CODE = """...""" or starts with "package sim"',
        )

    logger.info(f"Extracted Go code: {len(go_code)} chars")

    try:
        initial_program_path = script_dir / "initial_program.go"
        if initial_program_path.exists():
            with open(initial_program_path, "r") as f:
                initial_go_code = extract_go_code(f.read())
            if initial_go_code:
                print_diff(initial_go_code, go_code)
    except Exception as exc:
        logger.warning("Failed to compute diff against initial program (non-fatal): %s", exc)

    slo_targets = _load_slo_targets(script_dir)

    baseline = get_or_compute_baseline(
        script_dir, inference_sim_dir, policy_config_path, slo_targets,
    )
    if not baseline:
        logger.warning("Baseline metrics unavailable; score comparison will not be possible")

    # Save original admission.go before mutation
    original_admission = admission_go_path.read_text()

    try:
        try:
            admission_go_path.write_text(go_code)
        except Exception as e:
            return _error_result(
                f"Failed to write file: {e}", "FileWriteError",
                full_traceback=traceback.format_exc(),
            )

        try:
            result = subprocess.run(
                ["go", "build", "-o", "simulation_worker", "main.go"],
                cwd=inference_sim_dir, capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                error_summary = result.stderr.strip()[:500] if result.stderr else "Unknown build error"
                return _error_result(
                    f"Build failed: {error_summary}", "BuildError",
                    "Check for Go syntax errors in the evolved EVOLVE-BLOCK section",
                    build_stderr=result.stderr,
                )
        except subprocess.TimeoutExpired:
            return _error_result("Build timeout (60s)", "BuildTimeout")
        except Exception as e:
            return _error_result(
                f"Build error: {e}", type(e).__name__,
                full_traceback=traceback.format_exc(),
            )

        models = _get_models()
        workload_scores = _run_workloads(
            models, script_dir, inference_sim_dir, policy_config_path,
            seeds=SIM_SEEDS, slo_targets=slo_targets,
        )

        # Compute combined score: average across workloads
        wl_scores = [ws["score"] for ws in workload_scores.values() if ws.get("num_runs", 0) > 0]
        if not wl_scores:
            return _error_result(
                "All workloads failed", "AllWorkloadsFailed",
                "Check BLIS simulation errors. May be admission logic causing crashes.",
                workload_scores=workload_scores,
            )

        combined_score = sum(wl_scores) / len(wl_scores)

        # Compute baseline-relative improvement for reporting
        baseline_score = baseline.get("combined_score", 0)
        if baseline_score > 0:
            improvement_pct = (combined_score - baseline_score) / baseline_score * 100
        else:
            improvement_pct = 0.0

        # Aggregate metrics across workloads
        all_slo = [ws["slo_attainment"] for ws in workload_scores.values() if ws.get("num_runs", 0) > 0]
        all_thr = [ws["capped_throughput"] for ws in workload_scores.values() if ws.get("num_runs", 0) > 0]
        all_fair = [ws["jain_fairness"] for ws in workload_scores.values() if ws.get("num_runs", 0) > 0]
        all_e2e = [ws["avg_e2e_ms"] for ws in workload_scores.values()
                   if ws.get("avg_e2e_ms") is not None]

        total_runs = len(SIM_SEEDS) * len(models) * len(WORKLOADS)
        num_successful = sum(ws.get("num_runs", 0) for ws in workload_scores.values())

        return {
            "combined_score": combined_score,
            "improvement_vs_baseline_pct": improvement_pct,
            "slo_attainment": sum(all_slo) / len(all_slo) if all_slo else 0,
            "capped_throughput": sum(all_thr) / len(all_thr) if all_thr else 0,
            "jain_fairness": sum(all_fair) / len(all_fair) if all_fair else 0,
            "avg_e2e_ms": sum(all_e2e) / len(all_e2e) if all_e2e else float("inf"),
            **{f"{wk}_score": workload_scores.get(wk, {}).get("score", 0)
               for wk, _ in WORKLOADS},
            "success_rate": num_successful / total_runs if total_runs > 0 else 0,
            "num_successful": num_successful,
            "artifacts": {
                "workload_scores": workload_scores,
                "slo_targets": slo_targets,
                "throughput_cap": THROUGHPUT_CAP,
                "baseline_score": baseline_score,
            },
        }

    finally:
        # Always restore original admission.go — no state leaks between evaluations
        _restore_admission_go(admission_go_path, original_admission)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    print(f"Testing admission evaluator with initial program (seeds={SIM_SEEDS})...")
    script_dir = Path(__file__).parent
    prog = script_dir / "initial_program.go"
    result = evaluate(str(prog))
    if "error" in result:
        print(f"  ERROR: {result['error']}")
        print(f"  Suggestion: {result.get('artifacts', {}).get('suggestion', 'N/A')}")
        sys.exit(1)
    score = result.get("combined_score")
    print(f"  Score: {score:.4f}" if score is not None else "  Score: N/A")
    print(f"  SLO attainment: {result.get('slo_attainment', 0):.3f}")
    print(f"  Throughput: {result.get('capped_throughput', 0):.3f}")
    print(f"  Fairness: {result.get('jain_fairness', 0):.3f}")
    print(f"  Avg E2E: {result.get('avg_e2e_ms', 'N/A')}")

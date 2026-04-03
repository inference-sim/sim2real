#!/usr/bin/env python3
"""
BLIS Admission Control Sim2Real Reproduction Script — admission_by60

Runs each admission policy (always-admit baseline + evolved best from 260324_30i_nohint_2seed) against both
overload workloads and prints a multi-objective comparison table.

Usage:
    cd sim2real/admission_by60
    python repro.py [--seeds 42,456] [--num-instances 4]

Prerequisites:
    - inference-sim cloned into this directory (see README.md)
    - Go installed (go build must work)
    - PyYAML installed (pip install pyyaml)
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths (all relative to this directory — fully self-contained)
# ---------------------------------------------------------------------------
SIM2REAL_DIR = Path(__file__).parent
INFERENCE_SIM_DIR = SIM2REAL_DIR / "inference-sim"
ADMISSION_GO_PATH = INFERENCE_SIM_DIR / "sim" / "admission.go"
POLICY_CONFIG = SIM2REAL_DIR / "routing_config" / "routing_policy.yaml"
WORKLOADS_DIR = SIM2REAL_DIR / "workloads"
CALIBRATION_PATH = SIM2REAL_DIR / "others" / "calibration.json"

# Programs to evaluate: (display_name, path_to_go_file, is_control)
PROGRAMS = [
    ("Always-admit (base)", SIM2REAL_DIR / "baselines" / "baseline_always_admit.go", True),
    ("Evolved (best)",      SIM2REAL_DIR / "best" / "best_program.go",               False),
]

# Workloads: (display_name, yaml_file)
WORKLOADS = [
    ("overload_mixed_slo", "workload_overload_mixed_slo.yaml"),
    ("bursty_adversary",   "workload_bursty_adversary.yaml"),
]

MODEL_ID = "qwen/qwen2.5-7b-instruct"
MODEL_EXTRA_ARGS = ["--hardware", "H100", "--tp", "1"]
SNAPSHOT_REFRESH = "5000000"  # 5 seconds in microseconds

# Scoring weights
W_SLO = 0.50
W_THR = 0.30
W_FAIR = 0.20

# SLO class weights (for weighted attainment)
_SLO_CLASS_WEIGHTS = {"critical": 4.0, "standard": 2.0, "sheddable": 1.0, "batch": 0.5}
_SHEDDABLE_CLASSES = {"sheddable", "batch"}


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------

def load_calibration() -> tuple[dict, float]:
    """Load SLO targets and throughput_cap from calibration.json."""
    defaults = {
        "critical": 1000, "standard": 3000, "sheddable": 8000, "batch": 15000,
    }
    default_cap = 0.50
    if not CALIBRATION_PATH.exists():
        return defaults, default_cap
    try:
        with open(CALIBRATION_PATH) as f:
            cal = json.load(f)
        targets = cal.get("slo_targets", defaults)
        cap = float(cal.get("throughput_cap", default_cap))
        return targets, cap
    except (json.JSONDecodeError, OSError):
        return defaults, default_cap


# ---------------------------------------------------------------------------
# Workload metadata
# ---------------------------------------------------------------------------

def _load_workload_meta(workload_path: Path) -> tuple[int, dict, dict]:
    """Return (num_requests, tenant_fractions, class_fractions) from YAML."""
    try:
        import yaml
    except ImportError:
        print("ERROR: PyYAML not installed. Run: uv sync  or  pip install pyyaml", file=sys.stderr)
        sys.exit(1)

    with open(workload_path) as f:
        spec = yaml.safe_load(f)

    num_requests = int(spec.get("num_requests", 0))
    tenant_fracs: dict[str, float] = defaultdict(float)
    class_fracs: dict[str, float] = defaultdict(float)

    for entry in list(spec.get("clients", [])) + list(spec.get("cohorts", [])):
        tid = entry.get("tenant_id", "")
        cls = entry.get("slo_class", "standard")
        frac = float(entry.get("rate_fraction", 0.0))
        if tid:
            tenant_fracs[tid] += frac
        class_fracs[cls] += frac

    return num_requests, dict(tenant_fracs), dict(class_fracs)


# ---------------------------------------------------------------------------
# Scoring functions (mirror evaluator.py)
# ---------------------------------------------------------------------------

def compute_slo_attainment(requests: list[dict], num_requests: int,
                           slo_targets: dict, class_fracs: dict) -> float:
    if num_requests <= 0:
        return 0.0

    expected_per_class = {cls: int(frac * num_requests) for cls, frac in class_fracs.items()}
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

    total_weight = 0.0
    weighted_sum = 0.0
    for cls, expected in expected_per_class.items():
        if expected <= 0:
            continue
        w = _SLO_CLASS_WEIGHTS.get(cls, 1.0)
        if cls in _SHEDDABLE_CLASSES:
            denom = injected_per_class.get(cls, 0)
            att = met_per_class.get(cls, 0) / denom if denom > 0 else 1.0
        else:
            att = met_per_class.get(cls, 0) / expected
        weighted_sum += w * att
        total_weight += w

    return weighted_sum / total_weight if total_weight > 0 else 0.0


def compute_capped_throughput(completed: int, num_requests: int, cap: float) -> float:
    if num_requests <= 0:
        return 0.0
    raw = completed / num_requests
    return min(raw, cap) / cap


def compute_jain_fairness(requests: list[dict], tenant_fracs: dict, num_requests: int) -> float:
    if not tenant_fracs:
        return 1.0
    completed_per_tenant: dict[str, int] = defaultdict(int)
    for req in requests:
        tid = req.get("tenant_id", "")
        if tid and req.get("e2e_ms", 0) > 0:
            completed_per_tenant[tid] += 1

    xi_values = []
    for tid, frac in tenant_fracs.items():
        expected = frac * num_requests
        xi = completed_per_tenant.get(tid, 0) / expected if expected > 0 else 0.0
        xi_values.append(xi)

    n = len(xi_values)
    sum_xi = sum(xi_values)
    sum_xi_sq = sum(x * x for x in xi_values)
    if sum_xi_sq == 0:
        return 0.0
    return (sum_xi * sum_xi) / (n * sum_xi_sq)


# ---------------------------------------------------------------------------
# Simulation helpers
# ---------------------------------------------------------------------------

def build_sim(admission_go_src: Path) -> bool:
    shutil.copy2(admission_go_src, ADMISSION_GO_PATH)
    result = subprocess.run(
        ["go", "build", "-o", "simulation_worker", "main.go"],
        cwd=INFERENCE_SIM_DIR, capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        print(f"  [BUILD ERROR] {result.stderr.strip()[:200]}", file=sys.stderr)
        return False
    return True


def run_sim(workload_yaml: Path, seed: str, num_instances: str) -> dict | None:
    """Run a single simulation with --results-path, return parsed JSON or None."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        results_path = tmp.name

    try:
        cmd = [
            "./simulation_worker", "run",
            "--model", MODEL_ID,
            "--num-instances", num_instances,
            "--policy-config", str(POLICY_CONFIG),
            "--workload-spec", str(workload_yaml),
            "--snapshot-refresh-interval", SNAPSHOT_REFRESH,
            "--results-path", results_path,
            "--log", "info",
            "--seed", seed,
        ] + MODEL_EXTRA_ARGS

        result = subprocess.run(
            cmd, cwd=INFERENCE_SIM_DIR, capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            print(f"  [SIM ERROR rc={result.returncode}] {result.stderr.strip()[:200]}", file=sys.stderr)
            return None

        try:
            with open(results_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"  [PARSE ERROR] {exc}", file=sys.stderr)
            return None

    except subprocess.TimeoutExpired:
        print(f"  [TIMEOUT] seed={seed}", file=sys.stderr)
        return None
    finally:
        try:
            os.unlink(results_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Main evaluation loop
# ---------------------------------------------------------------------------

def evaluate_program(
    name: str, admission_go: Path, seeds: list[str], num_instances: str,
    slo_targets: dict, throughput_cap: float,
) -> dict | None:
    """Evaluate one program across all workloads × seeds. Returns per-workload scores."""
    original = ADMISSION_GO_PATH.read_text()
    try:
        if not build_sim(admission_go):
            return None

        workload_scores = {}
        for wl_name, wl_file in WORKLOADS:
            wl_path = WORKLOADS_DIR / wl_file
            num_requests, tenant_fracs, class_fracs = _load_workload_meta(wl_path)

            slo_attainments, throughputs, fairnesses = [], [], []
            e2e_values, p95_values = [], []

            for seed in seeds:
                data = run_sim(wl_path, seed, num_instances)
                if data is None:
                    print(f"  [WARN] {name} / {wl_name} / seed={seed}: no data", file=sys.stderr)
                    continue

                completed = data.get("completed_requests", 0)
                requests = data.get("requests", [])

                slo_att = compute_slo_attainment(requests, num_requests, slo_targets, class_fracs)
                throughput = compute_capped_throughput(completed, num_requests, throughput_cap)
                fairness = compute_jain_fairness(requests, tenant_fracs, num_requests)

                slo_attainments.append(slo_att)
                throughputs.append(throughput)
                fairnesses.append(fairness)

                e2e = data.get("e2e_mean_ms", 0)
                p95 = data.get("e2e_p95_ms", 0)
                if e2e > 0:
                    e2e_values.append(e2e)
                if p95 > 0:
                    p95_values.append(p95)

            if slo_attainments:
                avg_slo = sum(slo_attainments) / len(slo_attainments)
                avg_thr = sum(throughputs) / len(throughputs)
                avg_fair = sum(fairnesses) / len(fairnesses)
                score = W_SLO * avg_slo + W_THR * avg_thr + W_FAIR * avg_fair
                workload_scores[wl_name] = {
                    "score": score,
                    "slo_attainment": avg_slo,
                    "capped_throughput": avg_thr,
                    "jain_fairness": avg_fair,
                    "avg_e2e_ms": sum(e2e_values) / len(e2e_values) if e2e_values else None,
                    "avg_p95_ms": sum(p95_values) / len(p95_values) if p95_values else None,
                    "num_runs": len(slo_attainments),
                }
            else:
                workload_scores[wl_name] = None

        if not any(v for v in workload_scores.values()):
            return None

        # Combined score (average across workloads)
        wl_scores = [v["score"] for v in workload_scores.values() if v]
        combined = sum(wl_scores) / len(wl_scores) if wl_scores else 0.0
        all_slo = [v["slo_attainment"] for v in workload_scores.values() if v]
        all_thr = [v["capped_throughput"] for v in workload_scores.values() if v]
        all_fair = [v["jain_fairness"] for v in workload_scores.values() if v]
        all_e2e = [v["avg_e2e_ms"] for v in workload_scores.values() if v and v["avg_e2e_ms"]]

        return {
            "combined": combined,
            "slo_attainment": sum(all_slo) / len(all_slo) if all_slo else 0,
            "capped_throughput": sum(all_thr) / len(all_thr) if all_thr else 0,
            "jain_fairness": sum(all_fair) / len(all_fair) if all_fair else 0,
            "avg_e2e_ms": sum(all_e2e) / len(all_e2e) if all_e2e else None,
            "workloads": workload_scores,
        }
    finally:
        ADMISSION_GO_PATH.write_text(original)


# ---------------------------------------------------------------------------
# Table printing
# ---------------------------------------------------------------------------

def fmt_score(v: float | None) -> str:
    return f"{v:.4f}" if v is not None else "  N/A  "


def fmt_pct(v: float | None) -> str:
    return f"{v * 100:.1f}%" if v is not None else " N/A "


def fmt_ms(v: float | None) -> str:
    return f"{v:.0f} ms" if v is not None else "  N/A "


def fmt_imp(v: float | None, base: float | None) -> str:
    if v is None or base is None or base == 0:
        return "   N/A  "
    imp = (v - base) / base * 100
    sign = "+" if imp >= 0 else ""
    return f"{sign}{imp:.1f}%"


def print_table(all_results: dict, seeds: list[str], throughput_cap: float):
    sep = "=" * 100

    print()
    print(sep)
    print(f"BLIS Admission Control Repro  —  model: {MODEL_ID}  |  seeds: {'+'.join(seeds)}")
    print(f"Scoring: {W_SLO}*SLO_attainment + {W_THR}*capped_throughput + {W_FAIR}*Jain_fairness  |  throughput_cap={throughput_cap}")
    print(sep)
    print()

    # Find baseline for improvement computation
    ctrl_res = None
    for name, _, is_ctrl in PROGRAMS:
        if is_ctrl:
            ctrl_res = all_results.get(name)
            break

    # Summary table
    col_w = 22
    print(f"{'Program':<{col_w}}  {'Combined':>8}  {'SLO attn':>8}  {'Throughput':>10}  {'Fairness':>8}  {'Avg E2E':>10}  {'vs base':>8}")
    print("-" * (col_w + 70))
    for name, _, is_ctrl in PROGRAMS:
        res = all_results.get(name)
        marker = " *" if is_ctrl else "  "
        label = f"{name}{marker}"
        if res is None:
            print(f"{label:<{col_w}}  FAILED")
            continue
        imp_str = (
            "(control)" if is_ctrl
            else fmt_imp(res["combined"], ctrl_res["combined"] if ctrl_res else None)
        )
        print(
            f"{label:<{col_w}}"
            f"  {fmt_score(res['combined']):>8}"
            f"  {fmt_pct(res['slo_attainment']):>8}"
            f"  {fmt_pct(res['capped_throughput']):>10}"
            f"  {fmt_pct(res['jain_fairness']):>8}"
            f"  {fmt_ms(res['avg_e2e_ms']):>10}"
            f"  {imp_str:>8}"
        )
    print()

    # Per-workload scores
    print("Per-workload scores:")
    wl_names = [wl for wl, _ in WORKLOADS]
    print(f"{'Program':<{col_w}}", end="")
    for wl in wl_names:
        print(f"  {wl:>22}", end="")
    print()
    print("-" * (col_w + len(wl_names) * 24))
    for name, _, _ in PROGRAMS:
        res = all_results.get(name)
        print(f"{name:<{col_w}}", end="")
        if res is None:
            print("  FAILED")
            continue
        for wl in wl_names:
            wl_data = res["workloads"].get(wl)
            score_str = f"{wl_data['score']:.4f}" if wl_data else "  N/A  "
            print(f"  {score_str:>22}", end="")
        print()
    print()

    # Per-workload detail (SLO, throughput, fairness, E2E)
    print("Per-workload detail:")
    detail_header = f"{'Program':<{col_w}}  {'Workload':<22}  {'score':>6}  {'SLO':>6}  {'thr':>6}  {'fair':>6}  {'E2E':>10}  {'P95':>10}"
    print(detail_header)
    print("-" * len(detail_header))
    for name, _, _ in PROGRAMS:
        res = all_results.get(name)
        if res is None:
            print(f"{name:<{col_w}}  FAILED")
            continue
        for wl in wl_names:
            wl_data = res["workloads"].get(wl)
            if wl_data is None:
                print(f"{name:<{col_w}}  {wl:<22}  FAILED")
                continue
            print(
                f"{name:<{col_w}}"
                f"  {wl:<22}"
                f"  {wl_data['score']:>6.4f}"
                f"  {wl_data['slo_attainment']:>6.3f}"
                f"  {wl_data['capped_throughput']:>6.3f}"
                f"  {wl_data['jain_fairness']:>6.3f}"
                f"  {fmt_ms(wl_data['avg_e2e_ms']):>10}"
                f"  {fmt_ms(wl_data['avg_p95_ms']):>10}"
            )

    print()
    print("* = control (always-admit baseline). vs base = (evolved - base) / base * 100")
    print("  SLO attainment: class-weighted, shed-tolerant (batch/sheddable rejections excluded)")
    print(f"  Throughput: min(completed/total, {throughput_cap}) / {throughput_cap}  — capped to reward moderate shedding")
    print(f"  Fairness: Jain index over per-tenant completion rates")
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="BLIS Admission Control Sim2Real Repro")
    parser.add_argument("--seeds", default="42,456",
                        help="Comma-separated simulation seeds (default: 42)")
    parser.add_argument("--num-instances", default="4",
                        help="Number of simulated LLM instances (default: 4)")
    args = parser.parse_args()

    seeds = [s.strip() for s in args.seeds.split(",") if s.strip()]

    # Validate prerequisites
    if not INFERENCE_SIM_DIR.exists():
        print(f"ERROR: inference-sim not found at {INFERENCE_SIM_DIR}", file=sys.stderr)
        print("  Run: git clone https://github.com/inference-sim/inference-sim.git inference-sim", file=sys.stderr)
        print("       git -C inference-sim checkout 7fd7a88d5d5005b15b142fa8e70cf5d8537ceebe", file=sys.stderr)
        sys.exit(1)
    if not ADMISSION_GO_PATH.exists():
        print(f"ERROR: admission.go not found at {ADMISSION_GO_PATH}", file=sys.stderr)
        sys.exit(1)
    if not WORKLOADS_DIR.exists():
        print(f"ERROR: workloads not found at {WORKLOADS_DIR}", file=sys.stderr)
        sys.exit(1)

    slo_targets, throughput_cap = load_calibration()

    print(f"BLIS Admission Control Repro")
    print(f"  seeds={seeds}  num_instances={args.num_instances}")
    print(f"  inference-sim: {INFERENCE_SIM_DIR}")
    print(f"  workloads: {WORKLOADS_DIR}")
    print(f"  slo_targets: {slo_targets}")
    print(f"  throughput_cap: {throughput_cap}")
    print()

    all_results = {}
    for prog_name, prog_path, is_control in PROGRAMS:
        marker = " [control]" if is_control else ""
        print(f"Evaluating: {prog_name}{marker}  ({prog_path.name})")
        if not prog_path.exists():
            print(f"  [SKIP] file not found: {prog_path}", file=sys.stderr)
            all_results[prog_name] = None
            continue
        results = evaluate_program(
            prog_name, prog_path, seeds, args.num_instances, slo_targets, throughput_cap,
        )
        all_results[prog_name] = results
        if results:
            print(f"  combined={results['combined']:.4f}  slo={results['slo_attainment']:.3f}  "
                  f"thr={results['capped_throughput']:.3f}  fair={results['jain_fairness']:.3f}")
            if results.get("avg_e2e_ms"):
                print(f"  avg_e2e={results['avg_e2e_ms']:.1f}ms")
        print()

    print_table(all_results, seeds, throughput_cap)


if __name__ == "__main__":
    main()

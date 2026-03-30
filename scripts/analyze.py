#!/usr/bin/env python3
"""sim2real analyze — latency comparison charts from run artifacts."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
VENV_PYTHON = str(REPO_ROOT / ".venv" / "bin" / "python")
CLI = str(REPO_ROOT / "tools" / "transfer_cli.py")

METRICS = [
    ("ttft_mean", "TTFT mean"),
    ("ttft_p50",  "TTFT p50"),
    ("ttft_p99",  "TTFT p99"),
    ("tpot_mean", "TPOT mean"),
    ("tpot_p50",  "TPOT p50"),
    ("tpot_p99",  "TPOT p99"),
    ("e2e_mean",  "E2E mean"),
    ("e2e_p50",   "E2E p50"),
    ("e2e_p99",   "E2E p99"),
]

_tty = sys.stdout.isatty()

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _tty else text

def info(msg: str) -> None: print(_c("34", "[INFO]  ") + msg)
def err(msg: str)  -> None: print(_c("31", "[ERROR] ") + msg, file=sys.stderr)


def resolve_run(args: argparse.Namespace, workspace_dir: Path) -> tuple[str, Path]:
    """Resolve current_run and run_dir from args or setup_config.json."""
    if args.run:
        current_run = args.run
    else:
        cfg_path = workspace_dir / "setup_config.json"
        if not cfg_path.exists():
            err("No --run given and workspace/setup_config.json not found")
            sys.exit(1)
        try:
            current_run = json.loads(cfg_path.read_text())["current_run"]
        except (json.JSONDecodeError, KeyError) as e:
            err(f"Cannot read current_run from setup_config.json: {e}")
            sys.exit(1)

    run_dir = workspace_dir / "runs" / current_run
    if not run_dir.is_dir():
        err(f"Run directory not found: {run_dir}")
        sys.exit(1)
    return current_run, run_dir


def load_artifacts(run_dir: Path) -> tuple[dict, dict, "dict | None"]:
    """Load baseline, treatment, and optional validation results."""
    for fname in ("deploy_baseline_results.json", "deploy_treatment_results.json"):
        if not (run_dir / fname).exists():
            err(f"Required artifact missing: {run_dir / fname}")
            sys.exit(1)
    try:
        baseline = json.loads((run_dir / "deploy_baseline_results.json").read_text())
        treatment = json.loads((run_dir / "deploy_treatment_results.json").read_text())
    except (json.JSONDecodeError, OSError) as e:
        err(f"Cannot parse results JSON: {e}")
        sys.exit(1)

    validation = None
    val_path = run_dir / "deploy_validation_results.json"
    if val_path.exists():
        try:
            validation = json.loads(val_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            info(f"deploy_validation_results.json unreadable ({e}) — skipping verdict display")
    else:
        info("deploy_validation_results.json not found — skipping verdict/mechanism check display")

    return baseline, treatment, validation


def _make_workload_map(results: dict) -> "dict[str, dict]":
    """Map workload name → metrics dict."""
    return {w["name"]: w["metrics"] for w in results.get("workloads", [])}


def print_summary(current_run: str, validation: "dict | None") -> None:
    """Print terminal summary block."""
    print(f"\n━━━ sim2real Results: {current_run} ━━━\n")
    if validation is None:
        return

    noise_cv  = validation.get("noise_cv")
    benchmark = validation.get("benchmark", {})
    t_eff     = benchmark.get("t_eff")

    parts = []
    if noise_cv is not None:
        parts.append(f"Noise CV: {noise_cv:.3f}")
    if t_eff is not None:
        parts.append(f"T_eff: {t_eff * 100:.1f}%")
    if parts:
        print("  ".join(parts))

    classifications = benchmark.get("workload_classification", [])
    if classifications:
        print("\nWorkload classifications:")
        for wl in classifications:
            pct = wl["improvement"] * 100
            print(f"  {wl['workload']:<20} {wl['classification']:<12} (improvement: {pct:.1f}%)")


def plot_workload_chart(
    workload_name: str,
    b_metrics: dict,
    t_metrics: dict,
    out_path: Path,
) -> None:
    """Save per-workload grouped bar chart (3×3 subplots, one per metric) to out_path."""
    fig, axes = plt.subplots(3, 3, figsize=(14, 9))
    axes_flat = axes.flatten()

    fig.suptitle(f"Workload: {workload_name}", fontsize=13, fontweight="bold")

    for idx, (key, label) in enumerate(METRICS):
        ax = axes_flat[idx]
        bval = b_metrics.get(key)
        tval = t_metrics.get(key)

        if bval is None and tval is None:
            ax.set_visible(False)
            continue

        ax.set_title(label, fontsize=9)
        ax.set_ylabel("ms", fontsize=8)
        ax.tick_params(axis="both", labelsize=8)

        bar_heights = [bval if bval is not None else 0,
                       tval if tval is not None else 0]
        bar_colors  = ["#aaaaaa", "#4477aa"]
        bar_labels  = [f"{bval:.1f}" if bval is not None else "N/A",
                       f"{tval:.1f}" if tval is not None else "N/A"]
        y_ref = max(v for v in [bval, tval] if v is not None)

        rects = ax.bar([0, 1], bar_heights, color=bar_colors, width=0.6)
        ax.set_xticks([0, 1])
        ax.set_xticklabels(["Baseline", "Treatment"], fontsize=8)

        for rect, lbl in zip(rects, bar_labels):
            ax.text(
                rect.get_x() + rect.get_width() / 2,
                rect.get_height() + y_ref * 0.02,
                lbl, ha="center", va="bottom", fontsize=7,
            )

        if bval is not None and tval is not None and bval != 0:
            delta_pct = (tval - bval) / bval * 100
            sign  = "−" if delta_pct < 0 else "+"
            color = "green" if delta_pct < 0 else "red"
            ax.text(
                1, y_ref * 1.12,
                f"{sign}{abs(delta_pct):.1f}%",
                ha="center", va="bottom", fontsize=8,
                color=color, fontweight="bold",
            )

    plt.tight_layout()
    fig.savefig(str(out_path), dpi=120, bbox_inches="tight")
    plt.close(fig)


def plot_heatmap(
    workload_names: list,
    workload_data: list,  # list of (b_metrics dict, t_metrics dict)
    out_path: Path,
    current_run: str,
) -> None:
    """Save summary heatmap (workloads × metrics, % change) to out_path."""
    metric_keys   = [k   for k, _   in METRICS]
    metric_labels = [lbl for _, lbl in METRICS]

    data = np.full((len(workload_names), len(metric_keys)), np.nan)
    for r, (b_metrics, t_metrics) in enumerate(workload_data):
        for c, key in enumerate(metric_keys):
            bval = b_metrics.get(key)
            tval = t_metrics.get(key)
            if bval is not None and tval is not None and bval != 0:
                data[r, c] = (tval - bval) / bval * 100

    fig_w = max(10, len(metric_keys) * 1.4)
    fig_h = max(3, len(workload_names) * 0.9 + 2)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))

    fig.suptitle(f"sim2real Transfer: {current_run}", fontsize=12, fontweight="bold")

    # RdYlGn_r: negative (improvement) → green, positive (regression) → red
    cmap = plt.cm.RdYlGn_r.copy()
    cmap.set_bad(color="#cccccc")

    finite = data[~np.isnan(data)]
    abs_max = float(np.max(np.abs(finite))) if finite.size > 0 else 1.0
    abs_max = max(abs_max, 1.0)

    im = ax.imshow(data, cmap=cmap, vmin=-abs_max, vmax=abs_max, aspect="auto")

    ax.set_xticks(range(len(metric_keys)))
    ax.set_xticklabels(metric_labels, rotation=45, ha="right", fontsize=9)
    ax.set_yticks(range(len(workload_names)))
    ax.set_yticklabels(workload_names, fontsize=9)

    for r in range(len(workload_names)):
        for c in range(len(metric_keys)):
            v = data[r, c]
            if np.isnan(v):
                ax.text(c, r, "N/A", ha="center", va="center", fontsize=8, color="#666666")
            else:
                sign = "−" if v < 0 else "+"
                txt_color = "white" if abs(v) > abs_max * 0.6 else "black"
                ax.text(c, r, f"{sign}{abs(v):.1f}%",
                        ha="center", va="center", fontsize=8, color=txt_color)

    plt.colorbar(im, ax=ax, label="% change (negative = improvement)")
    plt.tight_layout()
    fig.savefig(str(out_path), dpi=120, bbox_inches="tight")
    plt.close(fig)


def print_comparison_table(run_dir: Path) -> None:
    """Call transfer_cli.py compare and print the resulting table."""
    table_path = run_dir / "deploy_comparison_table.txt"
    result = subprocess.run(
        [VENV_PYTHON, CLI, "compare",
         "--baseline", str(run_dir / "deploy_baseline_results.json"),
         "--treatment", str(run_dir / "deploy_treatment_results.json"),
         "--out", str(table_path)],
        check=False, capture_output=True, cwd=REPO_ROOT,
    )
    if table_path.exists():
        print(table_path.read_text())
    elif result.returncode != 0:
        info("comparison table unavailable (transfer_cli.py compare failed)")


def main_with_args(argv: "list[str] | None" = None) -> int:
    """Testable entry point. argv=None reads sys.argv."""
    p = argparse.ArgumentParser(
        prog="analyze.py",
        description="sim2real analyze: latency comparison charts from run artifacts",
    )
    p.add_argument("--run", metavar="NAME",
                   help="Run name (default: from workspace/setup_config.json)")
    args = p.parse_args(argv)

    workspace_dir = REPO_ROOT / "workspace"
    current_run, run_dir = resolve_run(args, workspace_dir)
    baseline, treatment, validation = load_artifacts(run_dir)

    charts_dir = run_dir / "results_charts"
    charts_dir.mkdir(parents=True, exist_ok=True)

    b_map = _make_workload_map(baseline)
    t_map = _make_workload_map(treatment)
    all_workloads = sorted(set(b_map) | set(t_map))

    for wname in all_workloads:
        out_path = charts_dir / f"workload_{wname}.png"
        plot_workload_chart(wname, b_map.get(wname, {}), t_map.get(wname, {}), out_path)
        info(f"Saved: {out_path.relative_to(REPO_ROOT)}")

    heatmap_path = charts_dir / "summary_heatmap.png"
    workload_data = [(b_map.get(w, {}), t_map.get(w, {})) for w in all_workloads]
    plot_heatmap(all_workloads, workload_data, heatmap_path, current_run)
    info(f"Saved: {heatmap_path.relative_to(REPO_ROOT)}")

    print_comparison_table(run_dir)
    print_summary(current_run, validation)
    print(f"\nCharts saved to workspace/runs/{current_run}/results_charts/")
    print("\nNext: /sim2real-results for a full written analysis report")
    return 0


def main() -> int:
    return main_with_args()


if __name__ == "__main__":
    sys.exit(main())

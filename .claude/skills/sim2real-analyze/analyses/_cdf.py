"""Shared CDF plotting for `ttft_cdf.py` / `tpot_cdf.py` / `e2e_cdf.py`.

Each per-metric script is a thin wrapper that calls `cdf_main(metric=...)`.
Kept out of `_common.py` because plotting pulls in matplotlib, and only the
chart scripts (not `error_rate.py`) need it.
"""
import argparse
import sys

from _common import (
    PHASE_COLORS,
    PHASES,
    charts_dir,
    discover_workloads,
    display_name,
    latency_values,
    load_csv,
    phase_log_dirs,
    require_matplotlib,
    resolve_run,
    warn,
)


VALID_METRICS = ("TTFT", "TPOT", "E2E")


def _empirical_cdf(values, np):
    """Return (sorted_values, cumulative_fraction) with 1/n step points.

    Matches the standard textbook empirical CDF where y = k/n at the k-th
    ordered sample.
    """
    if not values:
        return np.array([]), np.array([])
    v = np.sort(np.asarray(values, dtype=float))
    n = len(v)
    y = np.arange(1, n + 1, dtype=float) / n
    return v, y


def cdf_main(metric: str) -> None:
    if metric not in VALID_METRICS:
        raise ValueError(f"unknown metric {metric!r} — expected one of {VALID_METRICS}")

    parser = argparse.ArgumentParser(
        description=f"Plot empirical CDF of {metric} per workload, baseline vs treatment"
    )
    parser.add_argument("--run", metavar="NAME",
                        help="Run name (default: current_run from setup_config.json)")
    args = parser.parse_args()

    run = resolve_run(args.run)
    plt, np = require_matplotlib()
    baseline_log, treatment_log = phase_log_dirs(run)
    workloads = discover_workloads(baseline_log, treatment_log)

    ncols = min(3, len(workloads))
    nrows = (len(workloads) + ncols - 1) // ncols
    fig, axes = plt.subplots(
        nrows, ncols,
        figsize=(5 * ncols, 3.5 * nrows),
        squeeze=False,
    )

    rendered_panels = 0
    for idx, wl in enumerate(workloads):
        ax = axes[idx // ncols][idx % ncols]
        panel_has_data = False
        for phase, log_dir in zip(PHASES, (baseline_log, treatment_log)):
            rows = load_csv(log_dir / wl / "trace_data.csv")
            ok = [r for r in rows if r["status"] == "ok"]
            values = latency_values(ok, metric)
            if not values:
                warn(f"skipping {metric} for workload '{wl}' phase '{phase}' — no valid rows")
                continue
            x, y = _empirical_cdf(values, np)
            ax.plot(x, y, label=f"{phase} (n={len(values)})",
                    color=PHASE_COLORS[phase], linewidth=1.5)
            panel_has_data = True

        if panel_has_data:
            rendered_panels += 1
            ax.set_title(display_name(wl))
            ax.set_xlabel(f"{metric} (ms)")
            ax.set_ylabel("CDF")
            ax.set_ylim(0.0, 1.0)
            ax.grid(True, alpha=0.4, linestyle=":")
            ax.legend(loc="lower right", fontsize=8)
        else:
            ax.axis("off")

    for empty_idx in range(len(workloads), nrows * ncols):
        axes[empty_idx // ncols][empty_idx % ncols].axis("off")

    if rendered_panels == 0:
        print(f"Error: no {metric} data to plot in any workload", file=sys.stderr)
        sys.exit(1)

    fig.suptitle(f"{run} — {metric} empirical CDF (baseline vs treatment)", fontsize=12)
    plt.tight_layout(rect=(0, 0, 1, 0.97))

    out_path = charts_dir(run) / f"{metric.lower()}_cdf.png"
    plt.savefig(out_path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out_path}")

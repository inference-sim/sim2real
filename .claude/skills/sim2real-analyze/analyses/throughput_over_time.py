#!/usr/bin/env python3
"""Requests-per-second timeline per workload, baseline vs treatment.

Invocation:
    python .claude/skills/sim2real-analyze/analyses/throughput_over_time.py --run <name>
    # --run defaults to current_run from workspace/setup_config.json
"""
import argparse
import sys
from pathlib import Path

from _common import (
    PHASE_COLORS,
    PHASES,
    charts_dir,
    discover_workloads,
    display_name,
    load_csv,
    phase_log_dirs,
    require_matplotlib,
    resolve_run,
    warn,
)


def _rate_series(rows: list[dict]) -> tuple[list[int], list[int]]:
    """Return (t_sec, requests_per_sec) bucketed by integer send-time second."""
    if not rows:
        return [], []
    times_us = [int(r["send_time_us"]) for r in rows]
    t0 = min(times_us)
    buckets: dict[int, int] = {}
    for t in times_us:
        sec = (t - t0) // 1_000_000
        buckets[sec] = buckets.get(sec, 0) + 1
    if not buckets:
        return [], []
    max_sec = max(buckets)
    xs = list(range(max_sec + 1))
    ys = [buckets.get(s, 0) for s in xs]
    return xs, ys


def _plot(run: str, workloads: list[str], baseline_log: Path, treatment_log: Path) -> Path:
    plt, _ = require_matplotlib()

    ncols = min(3, len(workloads))
    nrows = (len(workloads) + ncols - 1) // ncols
    fig, axes = plt.subplots(
        nrows, ncols,
        figsize=(5.5 * ncols, 3.5 * nrows),
        squeeze=False,
    )

    rendered = 0
    for idx, wl in enumerate(workloads):
        ax = axes[idx // ncols][idx % ncols]
        panel_has_data = False
        for phase, log_dir in zip(PHASES, (baseline_log, treatment_log)):
            rows = load_csv(log_dir / wl / "trace_data.csv")
            # No status filter: this chart measures OFFERED load. A failed
            # request was still sent, and its send_time_us is still a valid
            # datapoint on the injection timeline.
            if not rows:
                warn(f"skipping throughput for workload '{wl}' phase '{phase}' — no rows")
                continue
            xs, ys = _rate_series(rows)
            if not xs:
                continue
            ax.plot(xs, ys, label=f"{phase} (n={len(rows)})",
                    color=PHASE_COLORS[phase], linewidth=1.4)
            panel_has_data = True

        if panel_has_data:
            rendered += 1
            ax.set_title(display_name(wl))
            ax.set_xlabel("Time since first send (s)")
            ax.set_ylabel("Requests / s")
            ax.grid(True, alpha=0.4, linestyle=":")
            ax.legend(loc="best", fontsize=8)
        else:
            ax.axis("off")

    for empty in range(len(workloads), nrows * ncols):
        axes[empty // ncols][empty % ncols].axis("off")

    if rendered == 0:
        print("Error: no throughput data to plot in any workload", file=sys.stderr)
        sys.exit(1)

    fig.suptitle(f"{run} — throughput over time (sends per second)", fontsize=12)
    plt.tight_layout(rect=(0, 0, 1, 0.97))

    out = charts_dir(run) / "throughput_over_time.png"
    plt.savefig(out, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plot requests/sec over time per workload, baseline vs treatment"
    )
    parser.add_argument("--run", metavar="NAME",
                        help="Run name (default: current_run from setup_config.json)")
    args = parser.parse_args()

    run = resolve_run(args.run)
    baseline_log, treatment_log = phase_log_dirs(run)
    workloads = discover_workloads(baseline_log, treatment_log)
    out = _plot(run, workloads, baseline_log, treatment_log)
    print(f"Saved: {out}")


if __name__ == "__main__":
    main()

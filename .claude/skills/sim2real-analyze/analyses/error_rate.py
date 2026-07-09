#!/usr/bin/env python3
"""Per-workload error-rate table, baseline vs treatment.

Invocation:
    python .claude/skills/sim2real-analyze/analyses/error_rate.py --run <name>
    # --run defaults to current_run from workspace/setup_config.json

Unlike the latency analyses, this script does NOT filter to status=="ok"
— error rows are the metric.
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

from _common import (
    discover_workloads,
    display_name,
    load_csv,
    phase_log_dirs,
    resolve_run,
    run_dir,
    warn,
)

SEP = "  " + "─" * 78


def _phase_stats(csv_path: Path) -> tuple[int, int, Counter]:
    """Return (total, errors, status_counts) for one CSV."""
    rows = load_csv(csv_path, require_metric_cols=False)
    total = len(rows)
    counts = Counter(r["status"] for r in rows)
    errors = total - counts.get("ok", 0)
    return total, errors, counts


def _format_pct(part: int, whole: int) -> str:
    if whole == 0:
        return "N/A"
    return f"{part / whole * 100:.2f}%"


def _format_row(label: str, total: int, errors: int, statuses: Counter) -> str:
    """Fixed-width row: label, total, errors, %, top non-ok statuses."""
    non_ok = sorted(
        ((k, v) for k, v in statuses.items() if k != "ok"),
        key=lambda kv: (-kv[1], kv[0]),
    )
    top_str = ", ".join(f"{k}={v}" for k, v in non_ok[:3]) if non_ok else "—"
    return (
        f"  {label:<12}"
        f"{total:>10}"
        f"{errors:>10}"
        f"{_format_pct(errors, total):>10}"
        f"    {top_str}"
    )


def _section(wl_dir_name: str, baseline_log: Path, treatment_log: Path) -> list[str]:
    b_total, b_err, b_counts = _phase_stats(baseline_log / wl_dir_name / "trace_data.csv")
    t_total, t_err, t_counts = _phase_stats(treatment_log / wl_dir_name / "trace_data.csv")

    if b_total == 0 and t_total == 0:
        warn(f"skipping workload '{wl_dir_name}' — no rows in either phase")
        return []

    lines = [
        f"=== Workload: {display_name(wl_dir_name)} ===",
        "  Phase          Total    Errors     Rate    Top statuses (non-ok)",
        SEP,
        _format_row("baseline", b_total, b_err, b_counts),
        _format_row("treatment", t_total, t_err, t_counts),
    ]
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Report per-workload error rate for baseline vs treatment"
    )
    parser.add_argument("--run", metavar="NAME",
                        help="Run name (default: current_run from setup_config.json)")
    args = parser.parse_args()

    run = resolve_run(args.run)
    baseline_log, treatment_log = phase_log_dirs(run)
    workloads = discover_workloads(baseline_log, treatment_log)

    sections = [s for wl in workloads if (s := _section(wl, baseline_log, treatment_log))]
    if not sections:
        print("Error: no workloads had any rows to report", file=sys.stderr)
        sys.exit(1)

    output = "\n\n".join("\n".join(s) for s in sections)
    print(output)

    out_file = run_dir(run) / "error_rate.txt"
    out_file.write_text(output + "\n")


if __name__ == "__main__":
    main()

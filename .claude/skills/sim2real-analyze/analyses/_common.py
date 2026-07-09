"""Shared helpers for sim2real-analyze catalog scripts.

Keeps every runner-script analysis operating on the same run-resolution,
CSV-load, and workload-discovery contract so per-script bodies stay
focused on the metric. Modeled after `latency_table.py` — anything that
would otherwise be duplicated across `ttft_cdf.py`, `tpot_cdf.py`,
`e2e_cdf.py`, `throughput_over_time.py`, and `error_rate.py` lives here.

Underscore-prefixed filename so `list_analyses.py` treats it as a
helper, not a catalog entry (only `.md` files are enumerated anyway,
but the prefix documents intent).
"""
import csv
import json
import sys
from pathlib import Path

# Script location: {repo}/.claude/skills/sim2real-analyze/analyses/_common.py
REPO_ROOT = Path(__file__).resolve().parents[4]
WORKSPACE_DIR = REPO_ROOT / "workspace"

REQUIRED_COLS = {
    "send_time_us",
    "first_chunk_time_us",
    "last_chunk_time_us",
    "output_tokens",
    "status",
}

PHASES = ("baseline", "treatment")
PHASE_COLORS = {"baseline": "#1f77b4", "treatment": "#d62728"}


def err(msg: str) -> None:
    print(f"Error: {msg}", file=sys.stderr)


def warn(msg: str) -> None:
    print(f"Warning: {msg}", file=sys.stderr)


def resolve_run(run_arg: str | None) -> str:
    """Return run name from argument or `current_run` in setup_config.json."""
    if run_arg:
        return run_arg
    cfg_path = WORKSPACE_DIR / "setup_config.json"
    try:
        cfg = json.loads(cfg_path.read_text())
        run = cfg.get("current_run", "")
        if run:
            return run
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    err("no run specified — use --run NAME or set current_run in workspace/setup_config.json")
    sys.exit(1)


def display_name(dir_name: str) -> str:
    """workload_fm8_short_output_highrate → fm8-short-output-highrate."""
    name = dir_name[len("workload_"):] if dir_name.startswith("workload_") else dir_name
    return name.replace("_", "-")


def load_csv(csv_path: Path, *, require_metric_cols: bool = True) -> list[dict]:
    """Load a trace CSV. Exits 1 on failure.

    `require_metric_cols=False` skips the strict metric-column check —
    used by `error_rate.py`, which only needs the `status` column.
    """
    try:
        text = csv_path.read_text()
    except OSError:
        err(f"{csv_path}: failed to parse CSV")
        sys.exit(1)

    if not text.strip():
        err(f"{csv_path}: empty or invalid CSV file")
        sys.exit(1)

    try:
        reader = csv.DictReader(text.splitlines())
        rows = list(reader)
        fieldnames = reader.fieldnames or []
    except Exception:
        err(f"{csv_path}: failed to parse CSV")
        sys.exit(1)

    if not fieldnames:
        err(f"{csv_path}: empty or invalid CSV file")
        sys.exit(1)

    required = REQUIRED_COLS if require_metric_cols else {"status"}
    missing = sorted(required - set(fieldnames))
    if missing:
        err(f"{csv_path}: missing required columns: {', '.join(missing)}")
        sys.exit(1)

    return rows


def run_dir(run_name: str) -> Path:
    return WORKSPACE_DIR / "runs" / run_name


def phase_log_dirs(run_name: str) -> tuple[Path, Path]:
    """(baseline_log, treatment_log). Exits 1 if either is missing."""
    rd = run_dir(run_name)
    baseline = rd / "results" / "baseline"
    treatment = rd / "results" / "treatment"
    if not baseline.exists() or not treatment.exists():
        err("need both results/baseline/ and results/treatment/ — run 'pipeline/deploy.py collect' first")
        sys.exit(1)
    return baseline, treatment


def discover_workloads(baseline_log: Path, treatment_log: Path) -> list[str]:
    """Return sorted workload dir names present in both phases. Warns on solos.

    Exits 1 if the intersection is empty.
    """
    baseline_wl = {
        d.name for d in baseline_log.iterdir()
        if d.is_dir() and (d / "trace_data.csv").exists()
    }
    treatment_wl = {
        d.name for d in treatment_log.iterdir()
        if d.is_dir() and (d / "trace_data.csv").exists()
    }
    for wl in sorted(baseline_wl - treatment_wl):
        warn(f"skipping workload '{wl}' — not present in both phases")
    for wl in sorted(treatment_wl - baseline_wl):
        warn(f"skipping workload '{wl}' — not present in both phases")
    common = sorted(baseline_wl & treatment_wl)
    if not common:
        err("no workloads found in both baseline and treatment logs")
        sys.exit(1)
    return common


def charts_dir(run_name: str) -> Path:
    """Ensure and return results_charts/ for the run."""
    d = run_dir(run_name) / "results_charts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def latency_values(rows: list[dict], metric: str) -> list[float]:
    """Compute a latency vector in ms from ok-status rows.

    metric ∈ {"TTFT", "TPOT", "E2E"}. TPOT filters to rows with
    output_tokens > 1 (the only rows for which per-output-token latency
    is defined). Callers pass rows already filtered to status == "ok".
    """
    if metric == "TTFT":
        return [(int(r["first_chunk_time_us"]) - int(r["send_time_us"])) / 1000 for r in rows]
    if metric == "E2E":
        return [(int(r["last_chunk_time_us"]) - int(r["send_time_us"])) / 1000 for r in rows]
    if metric == "TPOT":
        return [
            (int(r["last_chunk_time_us"]) - int(r["first_chunk_time_us"]))
            / (int(r["output_tokens"]) - 1)
            / 1000
            for r in rows if int(r["output_tokens"]) > 1
        ]
    raise ValueError(f"unknown metric {metric!r}")


def require_matplotlib():
    """Import and return (plt, np) or exit 1 with a helpful message."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        err("matplotlib and numpy required — install with `pip install matplotlib numpy`")
        sys.exit(1)
    return plt, np

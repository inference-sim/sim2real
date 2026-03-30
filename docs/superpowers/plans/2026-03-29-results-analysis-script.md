# Results Analysis Script Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `scripts/analyze.py` that reads sim2real run artifacts and produces per-workload bar charts, a summary heatmap, and a terminal summary.

**Architecture:** Single script reads `deploy_baseline_results.json`, `deploy_treatment_results.json`, and optionally `deploy_validation_results.json` from `workspace/runs/<run_name>/`. Pure helper functions take explicit path arguments for testability. matplotlib Agg backend is set at import time so the script runs headless.

**Tech Stack:** Python 3.10+, matplotlib, numpy, pytest

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `scripts/analyze.py` | Create | All chart + summary logic |
| `tests/test_analyze.py` | Create | Unit + integration tests |
| `tests/fixtures/analyze/deploy_baseline_results.json` | Create | Fixture: baseline metrics |
| `tests/fixtures/analyze/deploy_treatment_results.json` | Create | Fixture: treatment metrics |
| `tests/fixtures/analyze/deploy_validation_results.json` | Create | Fixture: full-mode validation |
| `requirements.txt` | Modify | Add matplotlib, numpy |

---

## Chunk 1: Setup, run resolution

### Task 1: Add dependencies and fixture files

**Files:**
- Modify: `requirements.txt`
- Create: `tests/fixtures/analyze/baseline_results.json`
- Create: `tests/fixtures/analyze/treatment_results.json`
- Create: `tests/fixtures/analyze/validation_results.json`

- [ ] **Step 1: Add matplotlib and numpy to requirements.txt**

```
jinja2>=3.1.0
PyYAML>=6.0
requests>=2.28.0
matplotlib>=3.7.0
numpy>=1.24.0
```

- [ ] **Step 2: Install new deps**

```bash
pip install -r requirements.txt
```

Expected: installs matplotlib and numpy without error.

- [ ] **Step 3: Create `tests/fixtures/analyze/deploy_baseline_results.json`**

```json
{
  "workloads": [
    {
      "name": "chat-short",
      "metrics": {
        "ttft_mean": 120.0, "ttft_p50": 100.0, "ttft_p99": 200.0,
        "tpot_mean": 50.0,  "tpot_p50": 45.0,  "tpot_p99": 80.0,
        "e2e_mean":  800.0, "e2e_p50":  750.0, "e2e_p99": 1200.0
      }
    },
    {
      "name": "code-gen",
      "metrics": {
        "ttft_mean": 200.0, "ttft_p50": 180.0, "ttft_p99": 350.0,
        "tpot_mean": 70.0,  "tpot_p50": 65.0,  "tpot_p99": 110.0,
        "e2e_mean": 1200.0, "e2e_p50": 1100.0, "e2e_p99": 1800.0
      }
    }
  ]
}
```

- [ ] **Step 4: Create `tests/fixtures/analyze/deploy_treatment_results.json`**

```json
{
  "workloads": [
    {
      "name": "chat-short",
      "metrics": {
        "ttft_mean": 102.0, "ttft_p50": 85.0,  "ttft_p99": 170.0,
        "tpot_mean": 43.0,  "tpot_p50": 38.0,  "tpot_p99": 70.0,
        "e2e_mean":  700.0, "e2e_p50":  660.0, "e2e_p99": 1050.0
      }
    },
    {
      "name": "code-gen",
      "metrics": {
        "ttft_mean": 183.0, "ttft_p50": 165.0, "ttft_p99": 320.0,
        "tpot_mean": 64.0,  "tpot_p50": 60.0,  "tpot_p99": 100.0,
        "e2e_mean": 1100.0, "e2e_p50": 1010.0, "e2e_p99": 1650.0
      }
    }
  ]
}
```

- [ ] **Step 5: Create `tests/fixtures/analyze/deploy_validation_results.json`**

```json
{
  "suite_a": {"passed": true, "kendall_tau": 0.92, "max_abs_error": 0.05, "tuple_count": 100},
  "suite_b": {"passed": true, "rank_stability_tau": 0.95, "threshold_crossing_pct": 2.0, "informational_only": true},
  "suite_c": {"passed": true, "deterministic": true, "max_pile_on_ratio": 1.1},
  "benchmark": {
    "passed": true,
    "mechanism_check_verdict": "PASS",
    "t_eff": 0.05,
    "workload_classification": [
      {"workload": "chat-short", "classification": "matched",   "improvement": 0.1210, "matched_signals": ["queue_depth"]},
      {"workload": "code-gen",   "classification": "unmatched", "improvement": 0.0850, "matched_signals": []}
    ],
    "specificity_notes": []
  },
  "overall_verdict": "PASS",
  "noise_cv": 0.043
}
```

- [ ] **Step 6: Commit**

```bash
git add requirements.txt tests/fixtures/
git commit -m "chore: add matplotlib/numpy deps and analyze fixtures"
```

---

### Task 2: Skeleton + run resolution

**Files:**
- Create: `scripts/analyze.py`
- Modify: `tests/test_analyze.py`

- [ ] **Step 1: Create `tests/test_analyze.py` with run resolution tests**

```python
import json
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import importlib
analyze = importlib.import_module("analyze")


FIXTURES = Path(__file__).parent / "fixtures" / "analyze"


def _make_workspace(tmp_path, run_name="test-run", has_run_dir=True):
    """Helper: create a minimal workspace directory structure."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    cfg = {"run_name": run_name}
    (ws / "setup_config.json").write_text(json.dumps(cfg))
    if has_run_dir:
        run_dir = ws / "runs" / run_name
        run_dir.mkdir(parents=True)
        for fname in ("deploy_baseline_results.json", "deploy_treatment_results.json"):
            (run_dir / fname).write_text((FIXTURES / fname).read_text())
    return ws


class FakeArgs:
    def __init__(self, run=None):
        self.run = run


def test_resolve_run_from_arg(tmp_path):
    ws = _make_workspace(tmp_path, run_name="my-run")
    run_name, run_dir = analyze.resolve_run(FakeArgs(run="my-run"), ws)
    assert run_name == "my-run"
    assert run_dir == ws / "runs" / "my-run"


def test_resolve_run_from_setup_config(tmp_path):
    ws = _make_workspace(tmp_path, run_name="config-run")
    run_name, run_dir = analyze.resolve_run(FakeArgs(), ws)
    assert run_name == "config-run"


def test_resolve_run_missing_setup_config_exits(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    with pytest.raises(SystemExit) as exc:
        analyze.resolve_run(FakeArgs(), ws)
    assert exc.value.code == 1


def test_resolve_run_dir_not_found_exits(tmp_path):
    ws = _make_workspace(tmp_path, run_name="exists", has_run_dir=False)
    with pytest.raises(SystemExit) as exc:
        analyze.resolve_run(FakeArgs(run="nonexistent"), ws)
    assert exc.value.code == 1
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
python -m pytest tests/test_analyze.py -v 2>&1 | head -20
```

Expected: `ImportError` or `ModuleNotFoundError` — `analyze` doesn't exist yet.

- [ ] **Step 3: Create `scripts/analyze.py` with skeleton + `resolve_run`**

```python
#!/usr/bin/env python3
"""sim2real analyze — latency comparison charts from run artifacts."""

import argparse
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent

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
    """Resolve run_name and run_dir from args or setup_config.json."""
    if args.run:
        run_name = args.run
    else:
        cfg_path = workspace_dir / "setup_config.json"
        if not cfg_path.exists():
            err("No --run given and workspace/setup_config.json not found")
            sys.exit(1)
        try:
            run_name = json.loads(cfg_path.read_text())["run_name"]
        except (json.JSONDecodeError, KeyError) as e:
            err(f"Cannot read run_name from setup_config.json: {e}")
            sys.exit(1)

    run_dir = workspace_dir / "runs" / run_name
    if not run_dir.is_dir():
        err(f"Run directory not found: {run_dir}")
        sys.exit(1)
    return run_name, run_dir
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
python -m pytest tests/test_analyze.py::test_resolve_run_from_arg \
    tests/test_analyze.py::test_resolve_run_from_setup_config \
    tests/test_analyze.py::test_resolve_run_missing_setup_config_exits \
    tests/test_analyze.py::test_resolve_run_dir_not_found_exits -v
```

Expected: 4 PASSED.

- [ ] **Step 5: Commit**

```bash
git add scripts/analyze.py tests/test_analyze.py
git commit -m "feat(analyze): skeleton + run resolution with tests"
```

---

## Chunk 2: Artifact loading, terminal summary, bar charts

### Task 3: Artifact loading

**Files:**
- Modify: `scripts/analyze.py` (add `load_artifacts`, `_make_workload_map`)
- Modify: `tests/test_analyze.py` (add artifact loading tests)

- [ ] **Step 1: Add failing tests for artifact loading**

Append to `tests/test_analyze.py`:

```python
# ── load_artifacts ────────────────────────────────────────────────────

def test_load_artifacts_returns_baseline_and_treatment(tmp_path):
    ws = _make_workspace(tmp_path)
    run_dir = ws / "runs" / "test-run"
    baseline, treatment, validation = analyze.load_artifacts(run_dir)
    assert "workloads" in baseline
    assert "workloads" in treatment
    assert len(baseline["workloads"]) == 2


def test_load_artifacts_returns_validation_when_present(tmp_path):
    ws = _make_workspace(tmp_path)
    run_dir = ws / "runs" / "test-run"
    (run_dir / "deploy_validation_results.json").write_text(
        (FIXTURES / "deploy_validation_results.json").read_text()
    )
    _, _, validation = analyze.load_artifacts(run_dir)
    assert validation is not None
    assert validation["overall_verdict"] == "PASS"


def test_load_artifacts_validation_absent_returns_none(tmp_path, capsys):
    ws = _make_workspace(tmp_path)
    run_dir = ws / "runs" / "test-run"
    _, _, validation = analyze.load_artifacts(run_dir)
    assert validation is None
    captured = capsys.readouterr()
    assert "deploy_validation_results.json not found" in captured.out


def test_load_artifacts_missing_baseline_exits(tmp_path):
    ws = _make_workspace(tmp_path)
    run_dir = ws / "runs" / "test-run"
    (run_dir / "deploy_baseline_results.json").unlink()
    with pytest.raises(SystemExit) as exc:
        analyze.load_artifacts(run_dir)
    assert exc.value.code == 1


def test_load_artifacts_malformed_baseline_exits(tmp_path):
    ws = _make_workspace(tmp_path)
    run_dir = ws / "runs" / "test-run"
    (run_dir / "deploy_baseline_results.json").write_text("not json {{{")
    with pytest.raises(SystemExit) as exc:
        analyze.load_artifacts(run_dir)
    assert exc.value.code == 1


def test_make_workload_map():
    results = {"workloads": [
        {"name": "wl-a", "metrics": {"ttft_p50": 100.0}},
        {"name": "wl-b", "metrics": {"ttft_p50": 200.0}},
    ]}
    m = analyze._make_workload_map(results)
    assert m["wl-a"]["ttft_p50"] == 100.0
    assert "wl-b" in m
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
python -m pytest tests/test_analyze.py -k "load_artifacts or make_workload" -v 2>&1 | tail -10
```

Expected: `AttributeError: module 'analyze' has no attribute 'load_artifacts'`

- [ ] **Step 3: Add `load_artifacts` and `_make_workload_map` to `scripts/analyze.py`**

Add after `resolve_run`:

```python
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
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
python -m pytest tests/test_analyze.py -k "load_artifacts or make_workload" -v
```

Expected: 6 PASSED.

- [ ] **Step 5: Commit**

```bash
git add scripts/analyze.py tests/test_analyze.py
git commit -m "feat(analyze): artifact loading with tests"
```

---

### Task 4: Terminal summary

**Files:**
- Modify: `scripts/analyze.py` (add `print_summary`)
- Modify: `tests/test_analyze.py` (add summary tests)

- [ ] **Step 1: Add failing tests for terminal summary**

Append to `tests/test_analyze.py`:

```python
# ── print_summary ─────────────────────────────────────────────────────

def _load_validation():
    return json.loads((FIXTURES / "deploy_validation_results.json").read_text())


def test_print_summary_shows_verdict(capsys):
    analyze.print_summary("my-run", _load_validation())
    out = capsys.readouterr().out
    assert "PASS" in out
    assert "my-run" in out


def test_print_summary_shows_noise_cv_and_teff(capsys):
    analyze.print_summary("my-run", _load_validation())
    out = capsys.readouterr().out
    assert "0.043" in out   # noise_cv
    assert "5.0%" in out    # t_eff = 0.05 → 5.0%


def test_print_summary_shows_workload_classifications(capsys):
    analyze.print_summary("my-run", _load_validation())
    out = capsys.readouterr().out
    assert "chat-short" in out
    assert "matched" in out
    assert "12.1%" in out   # 0.1210 * 100


def test_print_summary_no_validation_omits_verdict(capsys):
    analyze.print_summary("my-run", None)
    out = capsys.readouterr().out
    assert "my-run" in out
    assert "PASS" not in out
    assert "Verdict" not in out
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
python -m pytest tests/test_analyze.py -k "print_summary" -v 2>&1 | tail -10
```

Expected: `AttributeError: module 'analyze' has no attribute 'print_summary'`

- [ ] **Step 3: Add `print_summary` to `scripts/analyze.py`**

Add after `_make_workload_map`:

```python
def print_summary(run_name: str, validation: "dict | None") -> None:
    """Print terminal summary block."""
    print(f"\n━━━ sim2real Results: {run_name} ━━━\n")
    if validation is None:
        return

    verdict   = validation.get("overall_verdict", "UNKNOWN")
    noise_cv  = validation.get("noise_cv")
    benchmark = validation.get("benchmark", {})
    t_eff     = benchmark.get("t_eff")

    parts = [f"Verdict:   {verdict}"]
    if noise_cv is not None:
        parts.append(f"Noise CV: {noise_cv:.3f}")
    if t_eff is not None:
        parts.append(f"T_eff: {t_eff * 100:.1f}%")
    print("  ".join(parts))

    classifications = benchmark.get("workload_classification", [])
    if classifications:
        print("\nWorkload classifications:")
        for wl in classifications:
            pct = wl["improvement"] * 100
            print(f"  {wl['workload']:<20} {wl['classification']:<12} (improvement: {pct:.1f}%)")
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
python -m pytest tests/test_analyze.py -k "print_summary" -v
```

Expected: 4 PASSED.

- [ ] **Step 5: Commit**

```bash
git add scripts/analyze.py tests/test_analyze.py
git commit -m "feat(analyze): terminal summary with tests"
```

---

### Task 5: Per-workload bar charts

**Files:**
- Modify: `scripts/analyze.py` (add `plot_workload_chart`)
- Modify: `tests/test_analyze.py` (add bar chart tests)

- [ ] **Step 1: Add failing tests for bar charts**

Append to `tests/test_analyze.py`:

```python
# ── plot_workload_chart ───────────────────────────────────────────────

def _load_b_metrics():
    data = json.loads((FIXTURES / "deploy_baseline_results.json").read_text())
    return data["workloads"][0]["metrics"]  # chat-short

def _load_t_metrics():
    data = json.loads((FIXTURES / "deploy_treatment_results.json").read_text())
    return data["workloads"][0]["metrics"]


def test_workload_chart_creates_png(tmp_path):
    out = tmp_path / "workload_chat-short.png"
    analyze.plot_workload_chart("chat-short", _load_b_metrics(), _load_t_metrics(), out)
    assert out.exists()
    assert out.stat().st_size > 1000


def test_workload_chart_with_verdict(tmp_path):
    out = tmp_path / "workload_chat-short.png"
    analyze.plot_workload_chart("chat-short", _load_b_metrics(), _load_t_metrics(), out, verdict="PASS")
    assert out.exists()


def test_workload_chart_metric_absent_both_sides(tmp_path):
    # Metrics without e2e fields — axes for e2e should be hidden (not crash)
    b = {"ttft_p50": 100.0, "ttft_p99": 200.0}
    t = {"ttft_p50": 85.0,  "ttft_p99": 170.0}
    out = tmp_path / "partial.png"
    analyze.plot_workload_chart("wl", b, t, out)
    assert out.exists()


def test_workload_chart_metric_absent_one_side(tmp_path):
    # tpot_mean present in baseline but absent in treatment — should not crash
    b = {"ttft_p50": 100.0, "tpot_mean": 50.0}
    t = {"ttft_p50": 85.0}
    out = tmp_path / "one_side.png"
    analyze.plot_workload_chart("wl", b, t, out)
    assert out.exists()
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
python -m pytest tests/test_analyze.py -k "workload_chart" -v 2>&1 | tail -10
```

Expected: `AttributeError: module 'analyze' has no attribute 'plot_workload_chart'`

- [ ] **Step 3: Add `plot_workload_chart` to `scripts/analyze.py`**

Add after `print_summary`:

```python
def plot_workload_chart(
    workload_name: str,
    b_metrics: dict,
    t_metrics: dict,
    out_path: Path,
    verdict: "str | None" = None,
) -> None:
    """Save per-workload grouped bar chart (3×3 subplots, one per metric) to out_path."""
    fig, axes = plt.subplots(3, 3, figsize=(14, 9))
    axes_flat = axes.flatten()

    title = f"Workload: {workload_name}"
    if verdict:
        title += f"  |  {verdict}"
    fig.suptitle(title, fontsize=13, fontweight="bold")

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
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
python -m pytest tests/test_analyze.py -k "workload_chart" -v
```

Expected: 4 PASSED.

- [ ] **Step 5: Commit**

```bash
git add scripts/analyze.py tests/test_analyze.py
git commit -m "feat(analyze): per-workload bar charts with tests"
```

---

## Chunk 3: Heatmap, main() wiring

### Task 6: Summary heatmap

**Files:**
- Modify: `scripts/analyze.py` (add `plot_heatmap`)
- Modify: `tests/test_analyze.py` (add heatmap tests)

- [ ] **Step 1: Add failing tests for heatmap**

Append to `tests/test_analyze.py`:

```python
# ── plot_heatmap ──────────────────────────────────────────────────────

def _two_workload_data():
    bl = json.loads((FIXTURES / "deploy_baseline_results.json").read_text())
    tr = json.loads((FIXTURES / "deploy_treatment_results.json").read_text())
    b_map = analyze._make_workload_map(bl)
    t_map = analyze._make_workload_map(tr)
    names = sorted(b_map)
    data  = [(b_map[n], t_map[n]) for n in names]
    return names, data


def test_heatmap_creates_png(tmp_path):
    names, data = _two_workload_data()
    out = tmp_path / "summary_heatmap.png"
    analyze.plot_heatmap(names, data, out, "test-run")
    assert out.exists()
    assert out.stat().st_size > 1000


def test_heatmap_with_verdict(tmp_path):
    names, data = _two_workload_data()
    out = tmp_path / "heatmap.png"
    analyze.plot_heatmap(names, data, out, "test-run", verdict="PASS")
    assert out.exists()


def test_heatmap_missing_metrics_does_not_crash(tmp_path):
    # workload with no e2e metrics — nan cells should render without crash
    b = {"ttft_p50": 100.0}
    t = {"ttft_p50": 85.0}
    out = tmp_path / "sparse.png"
    analyze.plot_heatmap(["sparse-wl"], [(b, t)], out, "test-run")
    assert out.exists()
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
python -m pytest tests/test_analyze.py -k "heatmap" -v 2>&1 | tail -10
```

Expected: `AttributeError: module 'analyze' has no attribute 'plot_heatmap'`

- [ ] **Step 3: Add `plot_heatmap` to `scripts/analyze.py`**

Add after `plot_workload_chart`:

```python
def plot_heatmap(
    workload_names: list,
    workload_data: list,  # list of (b_metrics dict, t_metrics dict)
    out_path: Path,
    run_name: str,
    verdict: "str | None" = None,
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

    title = f"sim2real Transfer: {run_name}"
    if verdict:
        title += f"  |  Verdict: {verdict}"
    fig.suptitle(title, fontsize=12, fontweight="bold")

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
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
python -m pytest tests/test_analyze.py -k "heatmap" -v
```

Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add scripts/analyze.py tests/test_analyze.py
git commit -m "feat(analyze): summary heatmap with tests"
```

---

### Task 7: main() wiring + integration

**Files:**
- Modify: `scripts/analyze.py` (add `main()` + `if __name__` block)
- Modify: `tests/test_analyze.py` (add integration tests)

- [ ] **Step 1: Add failing integration tests**

Append to `tests/test_analyze.py`:

```python
# ── integration / main() ─────────────────────────────────────────────

def _make_full_workspace(tmp_path, with_validation=True):
    ws = _make_workspace(tmp_path)
    run_dir = ws / "runs" / "test-run"
    if with_validation:
        (run_dir / "deploy_validation_results.json").write_text(
            (FIXTURES / "deploy_validation_results.json").read_text()
        )
    return ws, run_dir


def test_main_creates_all_outputs(tmp_path, monkeypatch):
    ws, run_dir = _make_full_workspace(tmp_path)
    monkeypatch.setattr(analyze, "REPO_ROOT", tmp_path)
    # Patch workspace dir: main() uses REPO_ROOT / "workspace"
    # Since REPO_ROOT is patched to tmp_path, workspace = tmp_path / "workspace" = ws
    rc = analyze.main_with_args(["--run", "test-run"])
    assert rc == 0
    charts_dir = run_dir / "results_charts"
    assert (charts_dir / "workload_chat-short.png").exists()
    assert (charts_dir / "workload_code-gen.png").exists()
    assert (charts_dir / "summary_heatmap.png").exists()


def test_main_without_validation_still_produces_charts(tmp_path, monkeypatch):
    ws, run_dir = _make_full_workspace(tmp_path, with_validation=False)
    monkeypatch.setattr(analyze, "REPO_ROOT", tmp_path)
    rc = analyze.main_with_args(["--run", "test-run"])
    assert rc == 0
    assert (run_dir / "results_charts" / "summary_heatmap.png").exists()


def test_main_shows_verdict_in_output(tmp_path, monkeypatch, capsys):
    ws, run_dir = _make_full_workspace(tmp_path)
    monkeypatch.setattr(analyze, "REPO_ROOT", tmp_path)
    analyze.main_with_args(["--run", "test-run"])
    out = capsys.readouterr().out
    assert "PASS" in out
    assert "test-run" in out


def test_main_missing_baseline_exits_1(tmp_path, monkeypatch):
    ws, run_dir = _make_full_workspace(tmp_path)
    (run_dir / "deploy_baseline_results.json").unlink()
    monkeypatch.setattr(analyze, "REPO_ROOT", tmp_path)
    with pytest.raises(SystemExit) as exc:
        analyze.main_with_args(["--run", "test-run"])
    assert exc.value.code == 1
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
python -m pytest tests/test_analyze.py -k "main" -v 2>&1 | tail -10
```

Expected: `AttributeError: module 'analyze' has no attribute 'main_with_args'`

- [ ] **Step 3: Add `main_with_args()` and `main()` to `scripts/analyze.py`**

Add at the bottom of `scripts/analyze.py`:

```python
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
    run_name, run_dir = resolve_run(args, workspace_dir)
    baseline, treatment, validation = load_artifacts(run_dir)

    verdict = validation.get("overall_verdict") if validation else None

    charts_dir = run_dir / "results_charts"
    charts_dir.mkdir(parents=True, exist_ok=True)

    b_map = _make_workload_map(baseline)
    t_map = _make_workload_map(treatment)
    all_workloads = sorted(set(b_map) | set(t_map))

    for wname in all_workloads:
        out_path = charts_dir / f"workload_{wname}.png"
        plot_workload_chart(wname, b_map.get(wname, {}), t_map.get(wname, {}), out_path, verdict)
        info(f"Saved: {out_path.relative_to(REPO_ROOT)}")

    heatmap_path = charts_dir / "summary_heatmap.png"
    workload_data = [(b_map.get(w, {}), t_map.get(w, {})) for w in all_workloads]
    plot_heatmap(all_workloads, workload_data, heatmap_path, run_name, verdict)
    info(f"Saved: {heatmap_path.relative_to(REPO_ROOT)}")

    print_summary(run_name, validation)
    print(f"\nCharts saved to workspace/runs/{run_name}/results_charts/")
    return 0


def main() -> int:
    return main_with_args()


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run all tests — verify they pass**

```bash
python -m pytest tests/test_analyze.py -v
```

Expected: all tests PASSED (15+ tests total).

- [ ] **Step 5: Run the script against the real workspace to verify it works end-to-end**

```bash
python scripts/analyze.py
```

Expected: INFO lines, summary block with verdict, "Charts saved to ..." at the end. Check `workspace/runs/sim2real-test4/results_charts/` for PNG files.

- [ ] **Step 6: Run full test suite to check for regressions**

```bash
python -m pytest tests/ -v
```

Expected: all existing tests (test_deploy, test_consensus, test_llm) still pass.

- [ ] **Step 7: Commit**

```bash
git add scripts/analyze.py tests/test_analyze.py
git commit -m "feat(analyze): main() wiring + integration tests"
```

"""Tests for the CDF analyses (_cdf.py + ttft_cdf/tpot_cdf/e2e_cdf wrappers)."""
import sys

import pytest

import _cdf
import _common
import e2e_cdf
import ttft_cdf
import tpot_cdf


def _patch_workspace(monkeypatch, ws):
    """Point the WORKSPACE_DIR module-globals used by _common at the fixture."""
    monkeypatch.setattr(_common, "WORKSPACE_DIR", ws)


# ── Unit: _empirical_cdf ──────────────────────────────────────────────────────

def test_empirical_cdf_empty():
    import numpy as np
    x, y = _cdf._empirical_cdf([], np)
    assert len(x) == 0 and len(y) == 0


def test_empirical_cdf_sorted_and_stepped():
    import numpy as np
    values = [3.0, 1.0, 2.0, 4.0]
    x, y = _cdf._empirical_cdf(values, np)
    assert list(x) == [1.0, 2.0, 3.0, 4.0]
    assert list(y) == [0.25, 0.5, 0.75, 1.0]


def test_empirical_cdf_reaches_one_at_max():
    import numpy as np
    x, y = _cdf._empirical_cdf([5.0, 10.0, 15.0], np)
    assert y[-1] == pytest.approx(1.0)


# ── Integration: happy paths across the three metric wrappers ─────────────────

@pytest.mark.parametrize("metric,out_stem", [
    ("TTFT", "ttft_cdf"),
    ("TPOT", "tpot_cdf"),
    ("E2E",  "e2e_cdf"),
])
def test_cdf_produces_png_for_each_metric(workspace, monkeypatch, capsys, metric, out_stem):
    _patch_workspace(monkeypatch, workspace["ws"])
    monkeypatch.setattr(sys, "argv", [f"{out_stem}.py", "--run", workspace["run"]])
    _cdf.cdf_main(metric)
    out = capsys.readouterr().out
    assert "Saved:" in out
    png = workspace["ws"] / "runs" / workspace["run"] / "results_charts" / f"{out_stem}.png"
    assert png.exists()
    assert png.stat().st_size > 0


def test_metric_wrapper_modules_re_export_cdf_main():
    """Wrappers must expose the shared entry point so the skill sees a stable API."""
    for m in (ttft_cdf, tpot_cdf, e2e_cdf):
        assert getattr(m, "cdf_main", None) is _cdf.cdf_main, (
            f"{m.__name__} is missing the cdf_main re-export"
        )


# ── Integration: TPOT single-token exclusion produces empty panels ────────────

def test_tpot_all_single_token_exits_nonzero(workspace, monkeypatch, capsys, make_row, make_csv):
    """When output_tokens <= 1 in every row, TPOT has no valid values."""
    _patch_workspace(monkeypatch, workspace["ws"])
    # Overwrite fixtures with single-token rows
    rows = [make_row(send=i * 100_000, first=(i * 100_000) + 500_000,
                     last=(i * 100_000) + 1_500_000, tokens=1)
            for i in range(10)]
    make_csv(workspace["baseline"] / workspace["workload"] / "trace_data.csv", rows)
    make_csv(workspace["treatment"] / workspace["workload"] / "trace_data.csv", rows)
    monkeypatch.setattr(sys, "argv", ["tpot_cdf.py", "--run", workspace["run"]])
    with pytest.raises(SystemExit) as exc:
        _cdf.cdf_main("TPOT")
    assert exc.value.code == 1
    assert "no TPOT data to plot" in capsys.readouterr().err


# ── Integration: no ok rows → wrapper exits with a clear error ────────────────

def test_all_error_rows_exits_nonzero(workspace, monkeypatch, capsys, make_row, make_csv):
    _patch_workspace(monkeypatch, workspace["ws"])
    bad = [make_row(status="error") for _ in range(10)]
    make_csv(workspace["baseline"] / workspace["workload"] / "trace_data.csv", bad)
    make_csv(workspace["treatment"] / workspace["workload"] / "trace_data.csv", bad)
    monkeypatch.setattr(sys, "argv", ["ttft_cdf.py", "--run", workspace["run"]])
    with pytest.raises(SystemExit) as exc:
        _cdf.cdf_main("TTFT")
    assert exc.value.code == 1


# ── Guard: unknown metric raises before argument parsing ──────────────────────

def test_unknown_metric_raises():
    with pytest.raises(ValueError, match="unknown metric"):
        _cdf.cdf_main("BOGUS")


# ── Guard: run resolution defers to setup_config.json when --run omitted ──────

def test_cdf_reads_current_run_from_config(workspace, monkeypatch, capsys):
    import json
    (workspace["ws"]).mkdir(parents=True, exist_ok=True)
    (workspace["ws"] / "setup_config.json").write_text(
        json.dumps({"current_run": workspace["run"]})
    )
    _patch_workspace(monkeypatch, workspace["ws"])
    monkeypatch.setattr(sys, "argv", ["ttft_cdf.py"])  # no --run
    _cdf.cdf_main("TTFT")
    assert "Saved:" in capsys.readouterr().out

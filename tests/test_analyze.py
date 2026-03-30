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

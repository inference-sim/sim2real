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

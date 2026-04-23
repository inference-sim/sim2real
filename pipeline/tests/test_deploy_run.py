"""Tests for deploy.py run orchestrator and status subcommand."""
import json


_PROGRESS = {
    "wl-smoke-baseline":   {"workload": "wl-smoke",  "package": "baseline",   "status": "done",      "namespace": "sim2real-0", "retries": 0},
    "wl-smoke-treatment":  {"workload": "wl-smoke",  "package": "treatment",  "status": "running",   "namespace": "sim2real-1", "retries": 0},
    "wl-load-baseline":    {"workload": "wl-load",   "package": "baseline",   "status": "pending",   "namespace": None,         "retries": 0},
    "wl-load-treatment":   {"workload": "wl-load",   "package": "treatment",  "status": "timed-out", "namespace": "sim2real-2", "retries": 1},
    "wl-heavy-baseline":   {"workload": "wl-heavy",  "package": "baseline",   "status": "failed",    "namespace": "sim2real-0", "retries": 0},
}


def test_status_output_contains_all_pairs(tmp_path, capsys):
    from pipeline.deploy import _cmd_status
    progress_path = tmp_path / "progress.json"
    progress_path.write_text(json.dumps(_PROGRESS))

    class _Args:
        workload = None
        package = None
        live = False

    _cmd_status(_Args(), progress_path)
    out = capsys.readouterr().out
    for key in _PROGRESS:
        assert key in out


def test_status_filter_by_workload(tmp_path, capsys):
    from pipeline.deploy import _cmd_status
    progress_path = tmp_path / "progress.json"
    progress_path.write_text(json.dumps(_PROGRESS))

    class _Args:
        workload = "wl-smoke"
        package = None
        live = False

    _cmd_status(_Args(), progress_path)
    out = capsys.readouterr().out
    assert "wl-smoke-baseline" in out
    assert "wl-smoke-treatment" in out
    assert "wl-load-baseline" not in out


def test_status_filter_by_package(tmp_path, capsys):
    from pipeline.deploy import _cmd_status
    progress_path = tmp_path / "progress.json"
    progress_path.write_text(json.dumps(_PROGRESS))

    class _Args:
        workload = None
        package = "treatment"
        live = False

    _cmd_status(_Args(), progress_path)
    out = capsys.readouterr().out
    assert "wl-smoke-treatment" in out
    assert "wl-load-treatment" in out
    assert "wl-smoke-baseline" not in out


def test_status_summary_line(tmp_path, capsys):
    from pipeline.deploy import _cmd_status
    progress_path = tmp_path / "progress.json"
    progress_path.write_text(json.dumps(_PROGRESS))

    class _Args:
        workload = None
        package = None
        live = False

    _cmd_status(_Args(), progress_path)
    out = capsys.readouterr().out
    assert "5 pairs" in out
    assert "1 done" in out
    assert "1 running" in out
    assert "1 pending" in out


def test_status_missing_progress_file(tmp_path, capsys):
    from pipeline.deploy import _cmd_status

    class _Args:
        workload = None
        package = None
        live = False

    _cmd_status(_Args(), tmp_path / "missing.json")
    out = capsys.readouterr().out
    assert "0 pairs" in out


def test_load_pairs_discovers_all_pairs(tmp_path):
    from pipeline.deploy import _load_pairs
    for wl, pkg in [("smoke", "baseline"), ("smoke", "treatment"), ("load", "baseline")]:
        d = tmp_path / f"wl-{wl}-{pkg}"
        d.mkdir()
        pr = {
            "apiVersion": "tekton.dev/v1", "kind": "PipelineRun",
            "metadata": {"name": f"{pkg}-{wl}-run1", "namespace": "sim2real-0"},
            "spec": {"params": [
                {"name": "workloadName", "value": f"wl-{wl}"},
                {"name": "phase", "value": pkg},
            ]},
        }
        import yaml as _yaml
        (d / f"pipelinerun-{wl}-{pkg}.yaml").write_text(_yaml.dump(pr))

    pairs = _load_pairs(tmp_path)
    assert "wl-smoke-baseline" in pairs
    assert "wl-smoke-treatment" in pairs
    assert "wl-load-baseline" in pairs
    assert len(pairs) == 3


def test_apply_run_filters_by_status():
    from pipeline.deploy import _apply_run_filters

    class _Args:
        only = None; workload = None; package = None; status = "failed"

    result = _apply_run_filters(dict(_PROGRESS), _Args())
    assert result == {"wl-heavy-baseline"}


def test_apply_run_filters_compose():
    from pipeline.deploy import _apply_run_filters

    class _Args:
        only = None; workload = None; package = "treatment"; status = "timed-out"

    result = _apply_run_filters(dict(_PROGRESS), _Args())
    assert result == {"wl-load-treatment"}


def test_apply_run_filters_only_flag():
    from pipeline.deploy import _apply_run_filters

    class _Args:
        only = "wl-smoke-baseline"; workload = None; package = None; status = None

    result = _apply_run_filters(dict(_PROGRESS), _Args())
    assert result == {"wl-smoke-baseline"}


def test_apply_run_filters_no_flags_returns_empty():
    from pipeline.deploy import _apply_run_filters

    class _Args:
        only = None; workload = None; package = None; status = None

    result = _apply_run_filters(dict(_PROGRESS), _Args())
    assert result == set()

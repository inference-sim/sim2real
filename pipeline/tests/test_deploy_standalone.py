"""Tests that deploy.py operates without a manifest (transfer.yaml)."""

import json
from unittest.mock import patch

from pipeline.lib.progress import ConfigMapProgressStore


def test_build_parser_no_manifest_flag():
    """build_parser() does NOT expose a --manifest argument."""
    from pipeline.deploy import build_parser

    parser = build_parser()
    # Collect all option strings from all actions
    all_options = set()
    for action in parser._actions:
        all_options.update(action.option_strings)

    assert "--manifest" not in all_options


def test_main_works_without_transfer_yaml(tmp_path, monkeypatch):
    """main() dispatches 'status' without needing transfer.yaml anywhere."""
    import pipeline.deploy as deploy

    # Set up minimal workspace structure
    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    run_dir.mkdir(parents=True)

    # setup_config.json with current_run; cluster_config holds the namespace
    ws = tmp_path / "workspace"
    (ws / "setup_config.json").write_text(json.dumps({"current_run": "test-run"}))

    # Mock ConfigMapProgressStore to return progress data
    progress = {"wl-a-baseline": {"workload": "wl-a", "package": "baseline", "status": "pending", "namespace": None, "retries": 0}}
    monkeypatch.setattr(ConfigMapProgressStore, "load", lambda self: progress)
    monkeypatch.setattr(ConfigMapProgressStore, "save", lambda self, d: None)

    # Patch sys.argv to simulate CLI invocation
    monkeypatch.setattr("sys.argv", ["deploy.py", "--experiment-root", str(tmp_path), "status"])

    # Patch EXPERIMENT_ROOT and the config loaders to use our tmp_path
    monkeypatch.setattr(deploy, "EXPERIMENT_ROOT", tmp_path)

    status_called = []

    def mock_status(args, run_dir, cluster_config=None):
        status_called.append(run_dir)

    with patch.object(deploy, "_cmd_status", mock_status):
        with patch.object(deploy, "_load_setup_config", return_value={"current_run": "test-run"}), \
             patch.object(deploy, "_load_run_cluster_config", return_value={"namespaces": ["ns-0"]}):
            deploy.main()

    assert len(status_called) == 1
    assert status_called[0] == run_dir


def test_main_status_silent_suppresses_banner(tmp_path, monkeypatch, capsys):
    """deploy.py status -s must not print the cyan '━━━ sim2real-deploy ━━━' banner
    so the output is purely the summary line for scripting (issue #290)."""
    import pipeline.deploy as deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    run_dir.mkdir(parents=True)
    (run_dir / "run_metadata.json").write_text(
        json.dumps({"scenario": "test-scenario", "run_name": "test-run"})
    )
    ws = tmp_path / "workspace"
    (ws / "setup_config.json").write_text(json.dumps({"current_run": "test-run"}))

    progress = {"wl-a-baseline": {"workload": "wl-a", "package": "baseline", "status": "done", "namespace": "ns-0", "retries": 0}}
    monkeypatch.setattr(ConfigMapProgressStore, "load", lambda self: progress)
    monkeypatch.setattr(ConfigMapProgressStore, "save", lambda self, d: None)

    monkeypatch.setattr("sys.argv", ["deploy.py", "--experiment-root", str(tmp_path), "status", "-s"])
    monkeypatch.setattr(deploy, "EXPERIMENT_ROOT", tmp_path)

    with patch.object(deploy, "_load_setup_config",
                      return_value={"current_run": "test-run"}), \
         patch.object(deploy, "_load_run_cluster_config",
                      return_value={"namespaces": ["ns-0"]}):
        deploy.main()

    out = capsys.readouterr().out
    assert "sim2real-deploy" not in out
    assert "1 pairs" in out
    assert "1 done" in out


def test_main_status_without_silent_keeps_banner(tmp_path, monkeypatch, capsys):
    """Without -s, deploy.py status still prints the banner (regression guard)."""
    import pipeline.deploy as deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    run_dir.mkdir(parents=True)
    (run_dir / "run_metadata.json").write_text(
        json.dumps({"scenario": "test-scenario", "run_name": "test-run"})
    )
    ws = tmp_path / "workspace"
    (ws / "setup_config.json").write_text(json.dumps({"current_run": "test-run"}))

    progress = {"wl-a-baseline": {"workload": "wl-a", "package": "baseline", "status": "done", "namespace": "ns-0", "retries": 0}}
    monkeypatch.setattr(ConfigMapProgressStore, "load", lambda self: progress)
    monkeypatch.setattr(ConfigMapProgressStore, "save", lambda self, d: None)

    monkeypatch.setattr("sys.argv", ["deploy.py", "--experiment-root", str(tmp_path), "status"])
    monkeypatch.setattr(deploy, "EXPERIMENT_ROOT", tmp_path)

    with patch.object(deploy, "_load_setup_config",
                      return_value={"current_run": "test-run"}), \
         patch.object(deploy, "_load_run_cluster_config",
                      return_value={"namespaces": ["ns-0"]}):
        deploy.main()

    out = capsys.readouterr().out
    assert "sim2real-deploy" in out

"""Tests for deploy.py stop subcommand."""

import subprocess
from unittest.mock import patch

import pipeline.deploy as mod


def test_stop_deletes_orchestrator_job(monkeypatch, capsys):
    """When the orchestrator Job exists, stop deletes it."""
    calls = []

    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        calls.append(cmd)
        class _R:
            returncode = 0
            stdout = ""
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)

    mod._cmd_stop(namespace="sim2real-dev")

    assert len(calls) == 2
    assert calls[0] == [
        "kubectl", "get", "job", "sim2real-orchestrator",
        "-n", "sim2real-dev",
    ]
    assert calls[1] == [
        "kubectl", "delete", "job", "sim2real-orchestrator",
        "-n", "sim2real-dev", "--cascade=foreground",
    ]
    out = capsys.readouterr().out
    assert "sim2real-orchestrator" in out
    assert "sim2real-dev" in out


def test_stop_no_job_prints_message(monkeypatch, capsys):
    """When no orchestrator Job exists, print message and return."""
    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        if cmd[:2] == ["kubectl", "get"]:
            raise subprocess.CalledProcessError(1, cmd)
        raise AssertionError(f"Unexpected call: {cmd}")

    monkeypatch.setattr(mod, "run", fake_run)

    mod._cmd_stop(namespace="sim2real-dev")

    out = capsys.readouterr().out
    assert "no remote orchestrator started" in out.lower()


def test_main_dispatches_stop(tmp_path, monkeypatch):
    """main() routes 'stop' to _cmd_stop with the primary namespace."""
    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    run_dir.mkdir(parents=True)

    monkeypatch.setattr("sys.argv", [
        "deploy.py", "--experiment-root", str(tmp_path), "stop",
    ])
    monkeypatch.setattr(mod, "EXPERIMENT_ROOT", tmp_path)

    stop_calls = []

    def mock_stop(namespace):
        stop_calls.append(namespace)

    with patch.object(mod, "_cmd_stop", mock_stop):
        with patch.object(mod, "_load_setup_config", return_value={
            "current_run": "test-run",
            "namespace": "sim2real-0",
            "namespaces": ["sim2real-0", "sim2real-1"],
        }):
            mod.main()

    assert stop_calls == ["sim2real-0"]


def test_main_stop_no_namespace_exits(tmp_path, monkeypatch):
    """stop exits with error when no namespaces configured."""
    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    run_dir.mkdir(parents=True)

    monkeypatch.setattr("sys.argv", [
        "deploy.py", "--experiment-root", str(tmp_path), "stop",
    ])
    monkeypatch.setattr(mod, "EXPERIMENT_ROOT", tmp_path)

    import pytest
    with patch.object(mod, "_load_setup_config", return_value={
        "current_run": "test-run",
    }):
        with pytest.raises(SystemExit):
            mod.main()

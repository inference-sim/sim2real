"""Tests for shell completion scripts."""

import shutil
import subprocess
from pathlib import Path

import pytest

PIPELINE_DIR = Path(__file__).resolve().parent.parent
COMPLETIONS_ZSH = PIPELINE_DIR / "completions.zsh"
COMPLETIONS_BASH = PIPELINE_DIR / "completions.bash"


def test_zsh_script_exists():
    assert COMPLETIONS_ZSH.exists()


def test_bash_script_exists():
    assert COMPLETIONS_BASH.exists()


@pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh not available")
def test_zsh_syntax_valid():
    result = subprocess.run(
        ["zsh", "-n", str(COMPLETIONS_ZSH)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"zsh syntax error: {result.stderr}"


def test_bash_syntax_valid():
    result = subprocess.run(
        ["bash", "-n", str(COMPLETIONS_BASH)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"bash syntax error: {result.stderr}"


def test_zsh_declares_subcommands():
    content = COMPLETIONS_ZSH.read_text()
    for subcmd in ("run", "status", "collect", "cleanup", "pairs"):
        assert subcmd in content


def test_bash_declares_subcommands():
    content = COMPLETIONS_BASH.read_text()
    for subcmd in ("run", "status", "collect", "cleanup", "pairs"):
        assert subcmd in content


def test_zsh_handles_status_values():
    content = COMPLETIONS_ZSH.read_text()
    for val in ("pending", "running", "done", "failed", "timed-out",
                "stalled", "collect-failed", "collecting"):
        assert val in content


def test_bash_handles_status_values():
    content = COMPLETIONS_BASH.read_text()
    for val in ("pending", "running", "done", "failed", "timed-out",
                "stalled", "collect-failed", "collecting"):
        assert val in content


def test_zsh_calls_pairs_subcommand():
    content = COMPLETIONS_ZSH.read_text()
    assert "pairs --keys-only" in content
    assert "pairs --workloads-only" in content
    assert "pairs --packages-only" in content


def test_bash_calls_pairs_subcommand():
    content = COMPLETIONS_BASH.read_text()
    assert "pairs --keys-only" in content
    assert "pairs --workloads-only" in content
    assert "pairs --packages-only" in content


def test_zsh_graceful_on_failure():
    content = COMPLETIONS_ZSH.read_text()
    assert "2>/dev/null" in content


def test_bash_graceful_on_failure():
    content = COMPLETIONS_BASH.read_text()
    assert "2>/dev/null" in content


def test_bash_skips_flag_values_in_subcommand_detection():
    content = COMPLETIONS_BASH.read_text()
    assert "--experiment-root|--run) ((i++))" in content


def test_zsh_has_python_wrapper():
    content = COMPLETIONS_ZSH.read_text()
    assert "_python_deploy_py" in content
    assert "compdef _python_deploy_py python" in content


def test_bash_has_python_wrapper():
    content = COMPLETIONS_BASH.read_text()
    assert "_python_deploy_py" in content
    assert "complete -F _python_deploy_py python" in content


def test_banner_suppressed_for_machine_readable_pairs(tmp_path):
    """deploy.py pairs --keys-only must not print the banner to stdout."""
    import yaml as _yaml
    from pipeline.deploy import _cmd_pairs

    pr = {
        "metadata": {"name": "run1", "namespace": "ns"},
        "spec": {"params": [
            {"name": "workloadName", "value": "wl-smoke"},
            {"name": "phase", "value": "baseline"},
        ]},
    }
    cluster_dir = tmp_path / "cluster"
    cluster_dir.mkdir()
    (cluster_dir / "pipelinerun-smoke-baseline.yaml").write_text(_yaml.dump(pr))

    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _cmd_pairs(cluster_dir, keys_only=True)
    output = buf.getvalue()
    assert "sim2real-deploy" not in output
    assert "wl-smoke-baseline" in output


def test_cmd_pairs_silent_when_empty_and_machine_readable(tmp_path):
    """deploy.py pairs --keys-only with no pairs produces no stdout."""
    from pipeline.deploy import _cmd_pairs

    cluster_dir = tmp_path / "cluster"
    cluster_dir.mkdir()

    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _cmd_pairs(cluster_dir, keys_only=True)
    assert buf.getvalue() == ""

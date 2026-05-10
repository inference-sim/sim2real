"""Tests for shell completion scripts."""

import subprocess
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent.parent
COMPLETIONS_ZSH = PIPELINE_DIR / "completions.zsh"
COMPLETIONS_BASH = PIPELINE_DIR / "completions.bash"


def test_zsh_script_exists():
    assert COMPLETIONS_ZSH.exists()


def test_bash_script_exists():
    assert COMPLETIONS_BASH.exists()


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
    for val in ("pending", "running", "done", "failed", "timed-out"):
        assert val in content


def test_bash_handles_status_values():
    content = COMPLETIONS_BASH.read_text()
    for val in ("pending", "running", "done", "failed", "timed-out"):
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

"""Unit tests for pipeline/lib/proc.py — the shared subprocess execution seam.

Covers:
  - run(): text mode, check=True/False, capture, cwd, input, timeout
  - which(): resolution of existing and missing commands
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from pipeline.lib import proc


# ── run() tests ──────────────────────────────────────────────────────────────

class TestRun:
    """Tests for proc.run()."""

    def test_basic_echo(self):
        """run() executes a command and returns CompletedProcess."""
        result = proc.run(["echo", "hello"], capture=True)
        assert result.returncode == 0
        assert result.stdout.strip() == "hello"

    def test_text_mode_enabled(self):
        """run() always uses text mode (stdout is str, not bytes)."""
        result = proc.run(["echo", "text-mode"], capture=True)
        assert isinstance(result.stdout, str)

    def test_check_true_raises_on_failure(self):
        """run() with check=True raises CalledProcessError on non-zero exit."""
        with pytest.raises(subprocess.CalledProcessError) as exc_info:
            proc.run(["false"], check=True)
        assert exc_info.value.returncode != 0

    def test_check_false_no_raise(self):
        """run() with check=False does not raise on non-zero exit."""
        result = proc.run(["false"], check=False)
        assert result.returncode != 0

    def test_capture_stdout(self):
        """run() with capture=True captures stdout."""
        result = proc.run(["echo", "captured"], capture=True)
        assert "captured" in result.stdout

    def test_capture_stderr(self):
        """run() with capture=True captures stderr."""
        result = proc.run(
            ["sh", "-c", "echo err >&2"],
            capture=True, check=True,
        )
        assert "err" in result.stderr

    def test_no_capture(self):
        """run() with capture=False does not capture (stdout/stderr are None)."""
        result = proc.run(["true"], capture=False)
        assert result.stdout is None
        assert result.stderr is None

    def test_cwd(self, tmp_path):
        """run() with cwd= executes in the specified directory."""
        result = proc.run(["pwd"], capture=True, cwd=str(tmp_path))
        assert result.stdout.strip() == str(tmp_path)

    def test_input_feeds_stdin(self):
        """run() with input= pipes data to stdin."""
        result = proc.run(["cat"], capture=True, input="hello stdin")
        assert result.stdout == "hello stdin"

    def test_timeout_raises(self):
        """run() with timeout= raises TimeoutExpired if command exceeds limit."""
        with pytest.raises(subprocess.TimeoutExpired):
            proc.run(["sleep", "10"], timeout=1)

    def test_timeout_not_exceeded(self):
        """run() with timeout= completes normally when command finishes in time."""
        result = proc.run(["true"], timeout=5)
        assert result.returncode == 0

    def test_multiword_command(self):
        """run() handles multi-word commands correctly."""
        result = proc.run(["echo", "a", "b", "c"], capture=True)
        assert result.stdout.strip() == "a b c"

    def test_check_default_is_true(self):
        """run() defaults to check=True."""
        with pytest.raises(subprocess.CalledProcessError):
            proc.run(["sh", "-c", "exit 42"])

    @patch("pipeline.lib.proc.subprocess.run")
    def test_delegates_to_subprocess(self, mock_run):
        """run() delegates to subprocess.run with expected args."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=["test"], returncode=0, stdout="ok", stderr=""
        )
        proc.run(["test"], capture=True, cwd="/tmp", input="x", timeout=30)
        mock_run.assert_called_once_with(
            ["test"],
            check=True,
            text=True,
            capture_output=True,
            cwd="/tmp",
            input="x",
            timeout=30,
        )


# ── which() tests ────────────────────────────────────────────────────────────

class TestWhich:
    """Tests for proc.which()."""

    def test_existing_command(self):
        """which() returns True for commands that exist on PATH."""
        assert proc.which("sh") is True

    def test_nonexistent_command(self):
        """which() returns False for commands not on PATH."""
        assert proc.which("nonexistent_binary_xyz_abc_12345") is False

    def test_returns_bool(self):
        """which() always returns a bool, not a path string."""
        result = proc.which("sh")
        assert result is True
        assert isinstance(result, bool)

    @patch("pipeline.lib.proc.shutil.which", return_value=None)
    def test_which_none_returns_false(self, mock_which):
        """which() returns False when shutil.which returns None."""
        assert proc.which("anything") is False
        mock_which.assert_called_once_with("anything")

    @patch("pipeline.lib.proc.shutil.which", return_value="/usr/bin/something")
    def test_which_path_returns_true(self, mock_which):
        """which() returns True when shutil.which returns a path."""
        assert proc.which("something") is True
        mock_which.assert_called_once_with("something")

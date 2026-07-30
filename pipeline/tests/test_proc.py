"""Tests for pipeline.lib.proc — the central subprocess-execution seam."""

import subprocess
from types import SimpleNamespace
from unittest.mock import call, patch

import pytest

from pipeline.lib import proc


def _completed(*, returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class TestProcRun:
    """Tests for proc.run()."""

    def test_delegates_to_subprocess_run(self):
        """proc.run() passes cmd and standard kwargs to subprocess.run."""
        with patch("subprocess.run", return_value=_completed()) as mock_run:
            proc.run(["kubectl", "get", "nodes"])
        mock_run.assert_called_once_with(
            ["kubectl", "get", "nodes"],
            check=True,
            text=True,
            capture_output=False,
            cwd=None,
            input=None,
            timeout=None,
        )

    def test_timeout_passed_through(self):
        """proc.run(timeout=120) propagates timeout to subprocess.run."""
        with patch("subprocess.run", return_value=_completed()) as mock_run:
            proc.run(["kubectl", "apply", "-f", "manifest.yaml"], timeout=120)
        _, kwargs = mock_run.call_args
        assert kwargs["timeout"] == 120

    def test_timeout_none_by_default(self):
        """proc.run() passes timeout=None (no hang protection) by default."""
        with patch("subprocess.run", return_value=_completed()) as mock_run:
            proc.run(["kubectl", "get", "pods"])
        _, kwargs = mock_run.call_args
        assert kwargs["timeout"] is None

    def test_check_raises_on_nonzero(self):
        """proc.run(check=True) raises CalledProcessError on failure."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, ["cmd"])
            with pytest.raises(subprocess.CalledProcessError):
                proc.run(["bad-command"])

    def test_check_false_no_raise(self):
        """proc.run(check=False) does not raise on nonzero exit."""
        with patch("subprocess.run", return_value=_completed(returncode=1)) as mock_run:
            result = proc.run(["kubectl", "get", "nonexistent"], check=False)
        assert result.returncode == 1


class TestProcWhich:
    """Tests for proc.which()."""

    def test_returns_true_for_installed_cmd(self):
        with patch("shutil.which", return_value="/usr/bin/kubectl"):
            assert proc.which("kubectl") is True

    def test_returns_false_for_missing_cmd(self):
        with patch("shutil.which", return_value=None):
            assert proc.which("nonexistent-tool") is False

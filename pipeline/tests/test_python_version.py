"""Tests for the Python version floor (issue #906).

Two kinds of assertion live here:

* **The guard itself** — that ``check_python_version`` accepts supported
  interpreters and rejects older ones with a message naming both the
  requirement and what is actually running.
* **Drift guards** — that the floor is stated identically in ``MIN_PYTHON``,
  ``pyproject.toml``'s ``requires-python``, and the CI matrix. Those three
  files are edited by different people at different times for different
  reasons; nothing but a test keeps them agreeing, and the whole point of
  #906 was that they had already silently diverged.
"""
import tomllib

import pytest
import yaml

from pipeline import MIN_PYTHON, check_python_version, format_unsupported_python_message
from pipeline.lib import layout

REPO_ROOT = layout.repo_root()


# ── The guard ────────────────────────────────────────────────────────────────

def test_min_python_is_3_12():
    assert MIN_PYTHON == (3, 12)


def test_accepts_the_floor_exactly():
    check_python_version((3, 12, 0))


def test_accepts_newer():
    check_python_version((3, 14, 5))


@pytest.mark.parametrize("version", [(3, 11, 9), (3, 10, 0), (2, 7, 18)])
def test_rejects_older_and_names_both_versions(version):
    with pytest.raises(RuntimeError) as exc:
        check_python_version(version)
    msg = str(exc.value)
    assert ">= 3.12" in msg
    assert ".".join(str(part) for part in version) in msg


def test_message_explains_the_numpy_coupling():
    """The message must say why the floor exists, or the reader's next move is
    to 'fix' the tests rather than change interpreter."""
    msg = format_unsupported_python_message((3, 11, 9))
    assert "numpy" in msg
    assert "requirements.txt" in msg


def test_running_interpreter_satisfies_the_floor():
    """The suite must itself be running on a supported interpreter."""
    check_python_version()


# ── Drift guards ─────────────────────────────────────────────────────────────

def test_repo_root_conftest_imports_the_guard():
    """The repo-root conftest must trigger the interpreter check.

    Without it an unsupported Python surfaces as ten ModuleNotFoundErrors for
    numpy/matplotlib inside the sim2real-analyze plotting tests — the exact
    symptom #906 reported — rather than one clear message.
    """
    assert "import pipeline" in (REPO_ROOT / "conftest.py").read_text()


def test_pyproject_requires_python_matches_min_python():
    """The declaration and the enforcement must not drift apart."""
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    assert data["project"]["requires-python"] == ">=%d.%d" % MIN_PYTHON


def _ci_python_versions():
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/test.yml").read_text())
    return workflow["jobs"]["test"]["strategy"]["matrix"]["python-version"]


def test_ci_matrix_includes_the_floor():
    assert "%d.%d" % MIN_PYTHON in _ci_python_versions()


def test_ci_matrix_lowest_leg_is_the_floor():
    """A floor nobody tests is a floor that quietly stops being true."""
    legs = [tuple(int(part) for part in str(v).split(".")) for v in _ci_python_versions()]
    assert min(legs) == MIN_PYTHON

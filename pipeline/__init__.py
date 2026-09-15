"""sim2real pipeline package, and the single enforcement point for the
project's minimum supported Python.

The floor is dependency-driven, not syntax-driven: ``requirements.txt`` pins
numpy at a version whose own ``Requires-Python`` is ``>=3.12``, so an older
interpreter cannot resolve the dependency set at all (issue #906). The code
itself carries no 3.12-only syntax — the whole suite passes on 3.11.

Every CLI entry point (``pipeline/{cluster,deploy,setup,sim2real}.py``) does
``from pipeline.lib import ...``, which executes this module first, so the
check below runs on every CLI invocation. The repo-root ``conftest.py``
imports this module for the same reason, which covers every pytest
invocation. The logic lives here and is imported there so that there is one
implementation rather than two that can drift.
"""
from __future__ import annotations

import sys

#: Minimum supported Python. Must stay in sync with ``requires-python`` in
#: pyproject.toml and the lowest leg of the CI matrix in
#: .github/workflows/test.yml — ``pipeline/tests/test_python_version.py``
#: asserts that all three agree.
MIN_PYTHON: tuple[int, int] = (3, 12)


def format_unsupported_python_message(version_info: tuple[int, ...]) -> str:
    """Build the message shown when the running interpreter is too old.

    Names the requirement, what is actually running, and why the floor exists
    — without the last part the reader's next move is to "fix" the failing
    tests rather than to change interpreter.
    """
    running = ".".join(str(part) for part in version_info[:3])
    required = ".".join(str(part) for part in MIN_PYTHON)
    return (
        f"sim2real requires Python >= {required}, but this interpreter is "
        f"{running}. requirements.txt pins numpy at a version whose own "
        f"Requires-Python is >= {required}, so `pip install -r requirements.txt` "
        f"cannot resolve on this interpreter either — install Python {required} "
        f"or newer. See pyproject.toml (requires-python) and issue #906."
    )


def check_python_version(version_info: tuple[int, ...] | None = None) -> None:
    """Raise ``RuntimeError`` if the interpreter is below :data:`MIN_PYTHON`.

    ``version_info`` defaults to the running interpreter; tests pass an
    explicit tuple so both branches are reachable on a supported Python.
    """
    info = tuple(sys.version_info[:3]) if version_info is None else tuple(version_info)
    if info[:2] < MIN_PYTHON:
        raise RuntimeError(format_unsupported_python_message(info))


check_python_version()

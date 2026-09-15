"""Repo-root pytest configuration.

Exists for one reason: enforce the minimum supported Python (issue #906)
before any test module is imported.

pytest loads this file for every invocation rooted at the repo — the
``pipeline/`` suite and each ``.claude/skills/*/tests/`` suite alike — so
importing ``pipeline`` here makes the interpreter check in
``pipeline/__init__.py`` fire once, up front. Without it, an unsupported
interpreter surfaces as ten ``ModuleNotFoundError``s for numpy/matplotlib
from inside the sim2real-analyze plotting tests, which reads as ten broken
tests rather than as one environment that cannot satisfy requirements.txt.

This is not redundant with the check in ``pipeline/__init__.py``: those ten
tests never import ``pipeline``, and conversely this file does not cover
``python pipeline/deploy.py``. The two sites cover disjoint entry paths. The
logic lives in ``pipeline/__init__.py`` and is merely triggered here, so the
CLI and the test suite share one implementation.
"""
import pipeline  # noqa: F401 — the import itself runs the interpreter check

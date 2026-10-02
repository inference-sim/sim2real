"""Every tracked Python file must compile without warnings (issue #930).

A ``SyntaxWarning`` is invisible in CI — it fails neither the lint nor the test
run — so the construct that produces one can be reintroduced freely. #930 was
exactly that: a ``break`` inside a ``finally`` block warned on every
``sim2real.py`` invocation under Python 3.14 for as long as it took someone to
read the noise and ask about it.

``ruff --select B012`` (``jump-statement-in-finally``) is **not** a substitute.
It inspects the ``finally`` body directly, so it misses a jump sitting inside an
``except`` handler nested in the ``finally`` — which is the shape #930 had.
CPython's own compiler catches it, so the compiler is the gate.

This file is vacuously green on Python 3.12, which does not emit the warning
(PEP 765 landed in 3.14). The CI matrix runs both legs, so the 3.14 leg is what
enforces it.
"""
import py_compile
import warnings

import pytest

from pipeline.lib import layout

REPO_ROOT = layout.repo_root()

# Directories holding this repo's own Python. Submodules (inference-sim,
# tektonc-data-collection, llm-d-benchmark) are deliberately excluded — their
# warnings are not ours to fix, and they are not on our CI gate.
OWN_PYTHON_ROOTS = ("pipeline", ".claude/skills")

SKIP_DIR_PARTS = {".venv", "__pycache__", "node_modules", ".git"}


def _own_python_files():
    files = [REPO_ROOT / "conftest.py"]
    for root in OWN_PYTHON_ROOTS:
        files.extend((REPO_ROOT / root).rglob("*.py"))
    return sorted(
        f
        for f in files
        if f.is_file() and not SKIP_DIR_PARTS & set(f.relative_to(REPO_ROOT).parts)
    )


def _files_under(root):
    return [f for f in _own_python_files() if f.is_relative_to(REPO_ROOT / root)]


def test_found_the_python_files():
    """A glob that silently matches nothing would make this file vacuous."""
    files = _own_python_files()
    assert len(files) > 50
    assert REPO_ROOT / "pipeline" / "sim2real.py" in files


@pytest.mark.parametrize("root", OWN_PYTHON_ROOTS)
def test_every_root_contributes_files(root):
    """Each root must match on its own.

    ``Path.rglob`` on a directory that no longer exists yields nothing and
    raises nothing, and ``pipeline/`` alone clears the aggregate floor above —
    so renaming or relocating a root would drop its files out of the warning
    gate while every other assertion here still passed.
    """
    assert _files_under(root), f"{root} matched no Python files"


@pytest.mark.parametrize(
    "path", _own_python_files(), ids=lambda p: str(p.relative_to(REPO_ROOT))
)
def test_compiles_without_warnings(path, tmp_path):
    cfile = tmp_path / (path.stem + ".pyc")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        py_compile.compile(str(path), cfile=str(cfile), doraise=True)
    assert not caught, "; ".join(
        f"{w.category.__name__} at line {w.lineno}: {w.message}" for w in caught
    )

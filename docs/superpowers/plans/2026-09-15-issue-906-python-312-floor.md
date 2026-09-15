# Python 3.12 Floor — Declare and Enforce

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Declare Python >= 3.12 as sim2real's supported floor in a machine-readable place, enforce it at both the CLI and test entry points with one shared check, and test the floor in CI — so an unsupported interpreter fails with one clear message instead of ten `ModuleNotFoundError`s.

**Architecture:** One canonical check in `pipeline/__init__.py` (which every CLI entry point already executes via `from pipeline.lib import ...`), imported by a new repo-root `conftest.py` (which pytest loads for every suite, including the `.claude/skills/*/tests/` ones). A new `pyproject.toml` carries `requires-python` as the machine-readable declaration. Three drift tests assert the floor is stated identically in `MIN_PYTHON`, `pyproject.toml`, and the CI matrix, because those three files are edited by different people at different times.

**Tech Stack:** Python >= 3.12, `tomllib` (stdlib), PyYAML, pytest. No new third-party dependencies.

**Spec:** GitHub issue #906, plus the vetting record and two design decisions taken in conversation (both guards, not one; CI matrix gets a 3.12 leg).

## Global Constraints

- Floor is exactly `(3, 12)`. Stated verbatim as `MIN_PYTHON = (3, 12)`, `requires-python = ">=3.12"`, and CI matrix member `"3.12"`.
- The floor is **dependency-driven, not syntax-driven.** The full suite passes 3016/3016 on Python 3.11 today. Nothing may claim the code requires 3.12.
- Keep `pip install -r requirements.txt` as the documented install path. Do **not** restructure the repo into an installable package.
- Do **not** use environment markers (`; python_version >= "3.12"`) — a false marker *skips* the line, silently reproducing the `ModuleNotFoundError` this issue is about.
- Do **not** overclaim `pyproject.toml`'s reach: its `requires-python` does not gate a `requirements.txt` install. It is a declaration (and a dependabot input), not the enforcement.
- Do **not** edit the ~15 `docs/superpowers/plans/*.md` "Tech Stack: Python 3.10+" lines — point-in-time design records, deliberately left alone.
- Lint gate: `ruff check pipeline/ .claude/skills/ --select F`. Coverage gate: `--cov=pipeline --cov-fail-under=90`.

---

### Task 1: Canonical version guard in `pipeline/__init__.py`

**Files:**
- Modify: `pipeline/__init__.py` (currently empty, 0 bytes)
- Test: `pipeline/tests/test_python_version.py` (create)

**Interfaces:**
- Produces: `pipeline.MIN_PYTHON: tuple[int, int]`, `pipeline.check_python_version(version_info: tuple[int, ...] | None = None) -> None` (raises `RuntimeError`), `pipeline.format_unsupported_python_message(version_info: tuple[int, ...]) -> str`.
- Consumed by: Task 2 (`conftest.py`), Task 3 and Task 4 (drift tests).

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the Python version floor (issue #906)."""
import pytest

from pipeline import MIN_PYTHON, check_python_version, format_unsupported_python_message


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
    assert ".".join(str(p) for p in version) in msg


def test_message_explains_the_numpy_coupling():
    msg = format_unsupported_python_message((3, 11, 9))
    assert "numpy" in msg
    assert "requirements.txt" in msg


def test_running_interpreter_satisfies_the_floor():
    """The suite must itself run on a supported interpreter."""
    check_python_version()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest pipeline/tests/test_python_version.py -v`
Expected: FAIL — `ImportError: cannot import name 'MIN_PYTHON' from 'pipeline'`

- [ ] **Step 3: Implement the guard**

```python
"""sim2real pipeline package, and the single enforcement point for the
project's minimum supported Python.

The floor is dependency-driven, not syntax-driven: `requirements.txt` pins
numpy at a version whose own `Requires-Python` is `>=3.12`, so an older
interpreter cannot resolve the dependency set at all (issue #906). The code
carries no 3.12-only syntax — the whole suite passes on 3.11.

Every CLI entry point (`pipeline/{cluster,deploy,setup,sim2real}.py`) does
`from pipeline.lib import ...`, which executes this module first, so the check
below runs on every CLI invocation. The repo-root `conftest.py` imports this
module for the same reason, covering every pytest invocation. The logic lives
here and is imported there so there is one implementation, not two.
"""
from __future__ import annotations

import sys

#: Minimum supported Python. Must stay in sync with `requires-python` in
#: pyproject.toml and the lowest leg of the CI matrix in
#: .github/workflows/test.yml — `pipeline/tests/test_python_version.py`
#: asserts all three agree.
MIN_PYTHON: tuple[int, int] = (3, 12)


def format_unsupported_python_message(version_info: tuple[int, ...]) -> str:
    """Build the message shown when the running interpreter is too old."""
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
    """Raise `RuntimeError` if the interpreter is below `MIN_PYTHON`.

    `version_info` defaults to the running interpreter; tests pass a tuple.
    """
    info = tuple(sys.version_info[:3]) if version_info is None else tuple(version_info)
    if info[:2] < MIN_PYTHON:
        raise RuntimeError(format_unsupported_python_message(info))


check_python_version()
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest pipeline/tests/test_python_version.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Confirm the CLI path is actually covered**

Run: `python -c "from pipeline.lib import layout; print('guard ran, import ok')"`
Expected: prints `guard ran, import ok` on a supported interpreter.

- [ ] **Step 6: Commit**

```bash
git add pipeline/__init__.py pipeline/tests/test_python_version.py
git commit -m "feat(python): enforce a 3.12 floor at the pipeline package import"
```

---

### Task 2: Repo-root `conftest.py` so every pytest run is covered

**Files:**
- Create: `conftest.py` (repo root)
- Test: `pipeline/tests/test_python_version.py` (extend)

**Interfaces:**
- Consumes: `pipeline.check_python_version` / the import side effect from Task 1.
- Produces: nothing importable; a pytest-time guarantee.

Why a second site: the ten tests that fail today live in `.claude/skills/sim2real-analyze/tests/` and never import `pipeline`, so Task 1 alone would not catch them. Conversely `conftest.py` does not cover `python pipeline/deploy.py`. The two sites cover disjoint paths; both are needed. Verified empirically that a repo-root `conftest.py` executes for both `pipeline/` and skills-only invocations.

- [ ] **Step 1: Write the failing test**

```python
def test_repo_root_conftest_imports_the_guard():
    """The repo-root conftest must trigger the interpreter check, so an
    unsupported Python fails once and clearly rather than as ten
    ModuleNotFoundErrors inside the analyze plotting tests."""
    text = (REPO_ROOT / "conftest.py").read_text()
    assert "import pipeline" in text
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest pipeline/tests/test_python_version.py::test_repo_root_conftest_imports_the_guard -v`
Expected: FAIL — `FileNotFoundError: conftest.py`

- [ ] **Step 3: Create the conftest**

```python
"""Repo-root pytest configuration.

Exists for one reason: enforce the minimum supported Python (issue #906)
before any test module is imported.

pytest loads this file for every invocation rooted at the repo — the
`pipeline/` suite and each `.claude/skills/*/tests/` suite alike — so
importing `pipeline` here makes the interpreter check in
`pipeline/__init__.py` fire once, up front. Without it, an unsupported
interpreter surfaces as ten `ModuleNotFoundError`s for numpy/matplotlib from
inside the sim2real-analyze plotting tests, which reads as broken tests
rather than as an environment that cannot satisfy requirements.txt.

The check lives in `pipeline/__init__.py`, not here, so the CLI and the test
suite share one implementation.
"""
import pipeline  # noqa: F401 — the import itself runs the interpreter check
```

- [ ] **Step 4: Run to verify it passes, and that nothing else broke**

Run: `python -m pytest pipeline/tests/test_python_version.py -v`
Expected: PASS

Run: `python -m pytest .claude/skills/sim2real-analyze/tests/ -q`
Expected: 79 passed — proves the root conftest does not disturb the skills suites' own `sys.path` setup.

- [ ] **Step 5: Commit**

```bash
git add conftest.py pipeline/tests/test_python_version.py
git commit -m "feat(python): guard every pytest invocation via a repo-root conftest"
```

---

### Task 3: `pyproject.toml` declaration + drift test

**Files:**
- Create: `pyproject.toml` (repo root)
- Test: `pipeline/tests/test_python_version.py` (extend)

**Interfaces:**
- Consumes: `pipeline.MIN_PYTHON` (Task 1).
- Produces: `[project] requires-python = ">=3.12"` — the machine-readable declaration (issue #906 AC 2), and the input dependabot reads when choosing candidate versions.

- [ ] **Step 1: Write the failing test**

```python
import tomllib


def test_pyproject_requires_python_matches_min_python():
    """The declaration and the enforcement must not drift apart."""
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    assert data["project"]["requires-python"] == ">=%d.%d" % MIN_PYTHON
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest pipeline/tests/test_python_version.py::test_pyproject_requires_python_matches_min_python -v`
Expected: FAIL — `FileNotFoundError: pyproject.toml`

- [ ] **Step 3: Create pyproject.toml**

```toml
# Declaration only — sim2real is not built or installed as a package.
# The install path remains `pip install -r requirements.txt`; this file exists
# so the supported Python range is stated somewhere machine-readable (#906).
#
# Two consumers actually read `requires-python` here:
#   * dependabot's pip updater, which picks the interpreter it resolves
#     against from this field. Without it, it resolves against the newest
#     pre-installed Python, which is how numpy's lower bound ratcheted
#     2.5.1 -> 2.5.2 -> 2.5.3 and dragged the floor up unnoticed.
#   * humans and tooling asking what this repo supports.
#
# It does NOT gate `pip install -r requirements.txt` — pip only checks
# Requires-Python for a distribution it is installing, and never builds one
# for the ambient directory. Enforcement is `pipeline/__init__.py` plus the
# repo-root `conftest.py`.
#
# No [build-system] table: nothing here is meant to be built.
[project]
name = "sim2real"
version = "0.0.0"
requires-python = ">=3.12"
```

- [ ] **Step 4: Run to verify it passes, and that adding pyproject.toml broke nothing**

Run: `python -m pytest pipeline/tests/test_python_version.py -v`
Expected: PASS

Run: `ruff check pipeline/ .claude/skills/ --select F`
Expected: clean. (ruff reads `pyproject.toml` and infers `target-version` from `requires-python`; with `--select F` only this must remain a no-op.)

Run: `python -m pytest pipeline/ .claude/skills/sim2real-analyze/tests/ -q`
Expected: unchanged pass count — proves pytest's rootdir detection did not shift in a way that breaks collection.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml pipeline/tests/test_python_version.py
git commit -m "feat(python): declare requires-python >=3.12 in pyproject.toml"
```

---

### Task 4: CI matrix — test the floor, not just the ceiling

**Files:**
- Modify: `.github/workflows/test.yml` (job `test`: add `strategy.matrix`, parameterize `python-version`, refresh the detect-secrets comment)
- Test: `pipeline/tests/test_python_version.py` (extend)

**Interfaces:**
- Consumes: `pipeline.MIN_PYTHON` (Task 1).
- Produces: a `python-version` matrix whose lowest member equals the floor.

Already verified locally on 3.12.13, so this leg is expected green: full suite `3016 passed, 9 skipped, 2 xfailed`, coverage 97.86%; `detect-secrets 0.13.1+ibm.64.dss` and `pyahocorasick` both install and run.

- [ ] **Step 1: Write the failing tests**

```python
import yaml


def _ci_python_versions():
    wf = yaml.safe_load((REPO_ROOT / ".github/workflows/test.yml").read_text())
    return wf["jobs"]["test"]["strategy"]["matrix"]["python-version"]


def test_ci_matrix_includes_the_floor():
    assert "%d.%d" % MIN_PYTHON in _ci_python_versions()


def test_ci_matrix_lowest_leg_is_the_floor():
    """A floor nobody tests is a floor that quietly stops being true."""
    legs = [tuple(int(p) for p in str(v).split(".")) for v in _ci_python_versions()]
    assert min(legs) == MIN_PYTHON
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest pipeline/tests/test_python_version.py -k ci_matrix -v`
Expected: FAIL — `KeyError: 'strategy'`

- [ ] **Step 3: Add the matrix**

In `.github/workflows/test.yml`, under `jobs.test`, after `runs-on: ubuntu-latest`:

```yaml
    strategy:
      # Both legs report independently: a floor failure and a ceiling failure
      # have different causes and we want to see both.
      fail-fast: false
      matrix:
        # Floor and ceiling. The floor must equal pyproject.toml's
        # requires-python — pipeline/tests/test_python_version.py asserts it.
        python-version: ["3.12", "3.14"]
```

Then replace the hardcoded interpreter in the `setup-python` step:

```yaml
        with:
          python-version: ${{ matrix.python-version }}
```

And refresh the stale parenthetical in the detect-secrets comment, which currently names one interpreter:

```
          # Verified to install and run on both CI Pythons (3.12 and 3.14).
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest pipeline/tests/test_python_version.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/test.yml pipeline/tests/test_python_version.py
git commit -m "ci: test the 3.12 floor alongside the 3.14 ceiling"
```

---

### Task 5: Make the documented floor match the real one

**Files:**
- Modify: `CLAUDE.md:160`
- Modify: `CONTRIBUTING.md:21`
- Modify: `requirements.txt:5` (comment only — do not change the pin)

Both docs currently claim 3.10+, which has been false since the numpy pin crossed 3.12. This is the "make things consistent" half of the issue.

- [ ] **Step 1: Update CLAUDE.md**

Replace `- Python >= 3.10` with:

```markdown
- Python >= 3.12 — declared in `pyproject.toml` (`requires-python`), enforced at
  import by `pipeline/__init__.py` (CLI) and the repo-root `conftest.py` (tests).
  The floor is set by `requirements.txt`'s numpy pin, not by the code (#906).
```

- [ ] **Step 2: Update CONTRIBUTING.md**

Replace `- **Python 3.10+** (CI runs 3.14)` with:

```markdown
- **Python 3.12+** (CI runs 3.12 and 3.14) — older interpreters cannot resolve
  `requirements.txt`; see `pyproject.toml`
```

- [ ] **Step 3: Annotate the pin that sets the floor**

In `requirements.txt`, append a comment to the numpy line so the coupling is visible where the next bump lands:

```
numpy>=2.5.3,<3           # lower bound sets the Python floor: numpy >=2.5 needs Python >=3.12 (pyproject.toml requires-python, #906)
```

- [ ] **Step 4: Verify no other live doc still claims 3.10**

Run:
```bash
grep -rn "3\.10" --include=*.md . | grep -v docs/superpowers/plans/ | grep -v ./inference-sim | grep -v ./llm-d-benchmark
```
Expected: no hit that asserts a supported-Python floor. (`docs/superpowers/plans/*` hits are point-in-time records and stay.)

- [ ] **Step 5: Full verification**

Run: `ruff check pipeline/ .claude/skills/ --select F`
Expected: clean.

Run:
```bash
python -m pytest pipeline/ .claude/skills/sim2real-analyze/tests/ \
  .claude/skills/sim2real-bootstrap/tests/ .claude/skills/sim2real-specify/tests/ \
  .claude/skills/sim2real-translate/tests/ .claude/skills/sim2real-check/tests/ \
  --cov=pipeline --cov-report=term-missing --cov-fail-under=90 -q
```
Expected: all pass, coverage >= 90%, count = prior 3016 plus the new tests.

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md CONTRIBUTING.md requirements.txt
git commit -m "docs(python): state the real 3.12 floor in CLAUDE.md and CONTRIBUTING.md"
```

---

## Out of scope (deliberate)

- **No `.python-version` file.** It is a second thing dependabot reads, so it would be belt-and-braces against the ratchet, but it also silently retargets pyenv for every developer. `pyproject.toml` is the documented-by-code mechanism; if the ratchet recurs, `.python-version` is the fallback and the `"Dependabot is using Python version ..."` line in the update log is how to confirm which input won.
- **No `.github/dependabot.yml` change.** No option exists to pin the resolution interpreter (dependabot-core #13424 is an open request for one).
- **No change to `Dockerfile`** (`python:3.14-slim`) — already above the floor.
- **No repackaging.** `requirements.txt` stays the install path.

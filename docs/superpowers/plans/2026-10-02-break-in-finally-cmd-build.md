# Issue #930: `break` in a `finally` block — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the `break` that sits inside `_cmd_build`'s `finally` block in `pipeline/sim2real.py`, so Python 3.14 stops warning on every invocation and an uncaught exception from the baseline restore propagates instead of being silently converted to exit 2.

**Architecture:** The `finally` block records the restore failure in a per-iteration flag instead of jumping out of the loop; a single `if` *after* the `try`/`finally` performs the `break`. Control flow the `try` body had already started — a pending `return 2`, a pending `break`, or an in-flight exception — is no longer cancelled. A repo-hygiene test then compiles every tracked Python file with `SyntaxWarning` promoted to an error, so the construct cannot come back.

**Tech Stack:** Python >= 3.12 (CI matrix `["3.12", "3.14"]`), pytest, ruff (`--select F`).

**Spec:** GitHub issue [#930](https://github.com/inference-sim/sim2real/issues/930) — read it alongside this plan. Its "Behaviour to preserve" table is the contract this change must not alter.

## Global Constraints

- Python floor is `>=3.12` (`pyproject.toml:27`, `pipeline/__init__.py:MIN_PYTHON`). The fix must be valid on 3.12 *and* 3.14; the new hygiene test must pass on both legs (on 3.12 it is vacuously green, since 3.12 does not emit this warning).
- CI gate: `ruff check pipeline/ .claude/skills/ --select F`, then `pytest` with `--cov=pipeline --cov-fail-under=90`.
- `pipeline/README.md` must be updated after any change to `pipeline/` that affects CLI flags, subcommand behavior, artifact schema, or new modules. **This change affects none of those** — exit codes, flags, artifacts and messages are all unchanged — so no README update is warranted. Recorded here so the omission reads as a decision, not an oversight.
- `break` must stay lexically inside the `for algo in algorithms:` loop body; it cannot be moved into a helper function.
- Do not touch the `break` at `pipeline/sim2real.py:2472`. It is inside the `try`, not the `finally`, and is not what CPython flags.

## Review Focus

Five conditions the spec implies but does not pin with a test. Each has a test assigned to the task that owns the code.

1. **An exception already propagating out of the try body while the restore fails with a caught error.** This is the only shape that reproduces the swallow, and it needs both halves. An exception raised by the restore *itself* escapes the `finally` before reaching the `break` and behaves identically before and after the fix — the `break` runs only when the inner `except (subprocess.CalledProcessError, OSError)` catches. `probe_image_digest` (`sim2real.py:2481`) sits in the try with no handler and supplies the first half. A reasonable person expects a traceback, not a tidy exit 2 that looks like a handled build failure. *Covered: Task 2, Step 1.*
2. **The restore fails on the LAST algorithm**, so there is no next iteration for the `break` to protect. Exit code must still be 2. *Covered: Task 2, Step 5.*
3. **The restore fails while the `try` body is already returning 2** (digest write failed, line 2489). Exit must stay 2, and no later algorithm may be attempted. *Covered: Task 2, Step 7.*
4. **A second `break`-in-`finally` added anywhere in the repo later.** The warning is invisible in CI today — nothing fails on it. *Covered: Task 3.*
5. **The flag leaking across iterations**, breaking algorithm N+1 because algorithm N's restore failed. Structurally impossible once the flag is reset at the top of each iteration, but only a multi-algorithm happy-path assertion proves it. *Covered: Task 1, Step 4 (existing test `test_overlay_applied_per_algo_before_dispatch` asserts two dispatches and `rc == 0`).*

---

### Task 1: Replace the `break` in the `finally` with a flag

**Files:**
- Modify: `pipeline/sim2real.py:2371-2518` (`_cmd_build`)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: no new public symbols. `_cmd_build(args) -> int` keeps its signature and its exit codes (0 success, 2 any failure). Later tasks rely only on that unchanged contract.

- [ ] **Step 1: Run the existing tests that pin this code path, to confirm they pass BEFORE the change**

These three already cover the behaviour the issue's table says must not change. They are the regression guard for this task; if any is red before you start, stop and investigate.

```bash
.venv/bin/python -m pytest pipeline/tests/test_sim2real.py -v -k \
  "overlay_applied_per_algo_before_dispatch or post_build_cleanup_after_buildkit_failure or finally_restore_baseline_failure_fails_loud"
```

Expected: 3 passed.

- [ ] **Step 2: Add the per-iteration flag at the top of the loop body**

In `_cmd_build`, the loop begins at line 2372. Find:

```python
    for algo in algorithms:
        algo_name = algo["name"]
```

Replace with:

```python
    for algo in algorithms:
        algo_name = algo["name"]
        # Set by the finally-block restore below; acted on after the
        # try/finally has unwound. Reset per iteration so one algorithm's
        # failed restore cannot break the next.
        restore_failed = False
```

Initialise here rather than next to the `try:` at line 2450 as the issue suggests: three `continue` statements (2388, 2392, 2410) skip past that point, and a flag whose initialisation can be jumped over is harder to reason about than one set unconditionally.

- [ ] **Step 3: Turn the `break` into a flag write, and `break` after the `finally`**

Find the tail of the `finally` block (lines 2511-2518):

```python
                any_failure = True
                break

    return 2 if any_failure else 0
```

Replace with:

```python
                any_failure = True
                restore_failed = True

        # Outside the finally: a `break` *inside* it would cancel whatever
        # the try body had already started — a pending `return 2`, a pending
        # `break`, or an in-flight exception (PEP 765; SyntaxWarning on 3.14).
        if restore_failed:
            break

    return 2 if any_failure else 0
```

Also update the `finally` block's own comment, which currently promises the old mechanism. Find (lines 2500-2502):

```python
            # unknown state and subsequent iterations would silently upload
            # the wrong sources — fail loud, set any_failure, and break so
            # the caller sees exit 2 instead of a false success.
```

Replace with:

```python
            # unknown state and subsequent iterations would silently upload
            # the wrong sources — fail loud, set any_failure, and flag the
            # loop to break once this block has unwound, so the caller sees
            # exit 2 instead of a false success.
```

- [ ] **Step 4: Run the same three tests and confirm they still pass**

```bash
.venv/bin/python -m pytest pipeline/tests/test_sim2real.py -v -k \
  "overlay_applied_per_algo_before_dispatch or post_build_cleanup_after_buildkit_failure or finally_restore_baseline_failure_fails_loud"
```

Expected: 3 passed. `test_finally_restore_baseline_failure_fails_loud` is the important one — it asserts `rc == 2`, that only `algo1` was dispatched, and that stderr names `algo1`. All three must hold through the mechanism change.

- [ ] **Step 5: Confirm the warning is gone**

```bash
.venv/bin/python -W error::SyntaxWarning -c \
  "import py_compile; py_compile.compile('pipeline/sim2real.py', cfile='/tmp/ac1.pyc', doraise=True)"
echo "exit=$?"
```

Expected: `exit=0` and no output. Before the change this exits 1 with `SyntaxError: 'break' in a 'finally' block`.

- [ ] **Step 6: Commit**

```bash
git add pipeline/sim2real.py
git commit -m "fix(build): flag the restore failure instead of breaking inside the finally

A break in a finally cancels whatever control flow the try body had
already started. Record the failure and break once the block has
unwound, so a pending return or an in-flight exception survives.

Refs #930"
```

---

### Task 2: Pin the behaviour the mechanism change actually alters

**Files:**
- Modify: `pipeline/tests/test_sim2real.py` (add to `class TestBuildOverlayLifecycle`, after `test_finally_restore_baseline_failure_fails_loud`, which ends at line 2977)

**Interfaces:**
- Consumes: `self._make_fixture(tmp_path, monkeypatch) -> (exp_root, src, thash)` from the same class (defined at line 2778). It builds a two-algorithm translation (`algo1`, `algo2`) with a real git repo at `exp_root/myrepo`.
- Produces: nothing other tasks consume.

The existing suite already covers the paths that *do not* change (normal, buildkit-failure, restore-failure-mid-loop). These three tests cover what the fix actually changes or leaves un-pinned.

- [ ] **Step 1: Write the failing test for exception propagation**

This is the one real behaviour change in the fix. It needs BOTH halves to
reproduce: an uncaught exception already propagating out of the try body, and a
restore failure the `finally`'s handler *does* catch. An exception raised by
`restore_baseline` itself does not work -- it escapes the `finally` before
reaching the `break`, identically before and after the fix. `probe_image_digest`
(`sim2real.py:2481`) sits in the try with no handler, so it supplies the first
half.

Append to `class TestCmdBuildOverlayLifecycle` a test
`test_in_flight_exception_survives_a_failed_restore` that monkeypatches
`dispatch_buildkit_build` to return 0, `probe_image_digest` to raise
`RuntimeError("probe blew up")`, and `restore_baseline` to raise
`subprocess.CalledProcessError` on its second call (algo1's post-build
restore), then asserts `pytest.raises(RuntimeError, match="probe blew up")`
around `sim2real.main(["build", "--translation", thash, "--force-rebuild"])`.

- [ ] **Step 2: Run it to verify it fails on the unfixed code**

Task 1 is already committed, so recover the pre-fix file from history rather
than touching the stash stack (which is shared with every other worktree):
write `HEAD~1`'s copy of `pipeline/sim2real.py` over the working file, run the
three tests, then restore the working file from `HEAD`.

```bash
.venv/bin/python -m pytest pipeline/tests/test_sim2real.py -q -k \
  "in_flight_exception_survives or finally_restore_failure_on_last_algo or digest_write_failure_and_failed_restore"
```

Expected on the pre-fix file: `1 failed, 2 passed`. The in-flight test fails
with `DID NOT RAISE <class 'RuntimeError'>` -- the old `break` discarded it and
`main` returned 2. The other two passing on BOTH files is the evidence that the
mechanism change preserved behaviour.

- [ ] **Step 3: Run it against the fixed code**

```bash
.venv/bin/python -m pytest pipeline/tests/test_sim2real.py -v -k \
  "finally_restore_unexpected_exception_propagates"
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add pipeline/tests/test_sim2real.py
git commit -m "test(build): an unexpected restore exception must propagate

Refs #930"
```

- [ ] **Step 5: Write the last-algorithm test**

Review Focus item 2: when the restore fails on the final algorithm there is no next iteration, so the `break` protects nothing and only the exit code carries the failure. Append to the same class:

```python
    def test_finally_restore_failure_on_last_algo_still_exits_2(
        self, tmp_path, monkeypatch, capsys
    ):
        """A failed restore on the final algorithm still fails the run.

        With no next iteration to protect, the exit code is the only thing
        carrying the failure — `any_failure` must be what decides it, not
        the jump out of the loop (issue #930).
        """
        import subprocess as _sub
        exp_root, src, thash = self._make_fixture(tmp_path, monkeypatch)

        dispatched = []
        monkeypatch.setattr(
            "pipeline.lib.build.probe_image_digest", lambda *a, **k: "sha256:x"
        )

        def fake_dispatch(*, image_ref, **_kw):
            dispatched.append(image_ref)
            return 0

        monkeypatch.setattr(
            "pipeline.lib.build.dispatch_buildkit_build", fake_dispatch
        )

        import pipeline.lib.source_toggle as st
        real_restore = st.restore_baseline
        calls = {"n": 0}

        def flaky_restore(component_dir, translation_output):
            calls["n"] += 1
            # Calls run pre/post per algo: 1,2 = algo1; 3,4 = algo2. Fail on
            # algo2's post-build restore, the last one in the run.
            if calls["n"] == 4:
                raise _sub.CalledProcessError(
                    1, ["git", "checkout"], output=b"", stderr=b"simulated"
                )
            real_restore(component_dir, translation_output)

        monkeypatch.setattr(
            "pipeline.lib.source_toggle.restore_baseline", flaky_restore
        )

        rc = sim2real.main(["build", "--translation", thash, "--force-rebuild"])
        assert rc == 2
        # Both algorithms built — the failure is in the final cleanup only.
        assert len(dispatched) == 2
        err = capsys.readouterr().err
        assert "failed to restore baseline after build for algo2" in err
```

- [ ] **Step 6: Run it**

```bash
.venv/bin/python -m pytest pipeline/tests/test_sim2real.py -v -k \
  "finally_restore_failure_on_last_algo_still_exits_2"
```

Expected: PASS. If it fails on the call count, print `calls["n"]` from a temporary `print` to confirm the pre/post ordering for the fixture's two algorithms, and adjust the trip number — the ordering is what the test documents, so fix the number, not the assertion.

- [ ] **Step 7: Write the return-cancellation test**

Review Focus item 3: the one row of the issue's table where the old and new mechanisms genuinely differ in *how* they exit. The `try` body is returning 2 (digest write failed, line 2489) while the restore also fails. Old code: the `finally`'s `break` cancelled the return and the post-loop `return 2 if any_failure else 0` produced the 2. New code: the `return 2` proceeds. Same exit code, different path — pin it.

```python
    def test_digest_write_failure_and_failed_restore_still_exits_2(
        self, tmp_path, monkeypatch, capsys
    ):
        """A pending `return 2` must survive a failed restore.

        The try body returns 2 when the digest write fails; the finally then
        fails too. The old `break` inside the finally cancelled that return
        and let the post-loop expression produce the 2 instead. Either way
        the caller sees 2 and no later algorithm runs — assert the outcome,
        which is what the contract promises (issue #930).
        """
        import subprocess as _sub
        exp_root, src, thash = self._make_fixture(tmp_path, monkeypatch)

        dispatched = []
        monkeypatch.setattr(
            "pipeline.lib.build.probe_image_digest", lambda *a, **k: "sha256:x"
        )

        def fake_dispatch(*, image_ref, **_kw):
            dispatched.append(image_ref)
            return 0

        monkeypatch.setattr(
            "pipeline.lib.build.dispatch_buildkit_build", fake_dispatch
        )
        # The post-build digest write fails for every algorithm. Patching
        # the module attribute hits the `build.atomic_write_json(...)` call
        # at sim2real.py:2481 — the digest write inside the try. It does NOT
        # affect the module-level `_atomic_write_json` alias bound at import
        # (sim2real.py:30), nor the pre-build-probe write at 2401, which
        # --force-rebuild skips.
        monkeypatch.setattr(
            "pipeline.lib.build.atomic_write_json",
            lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
        )

        import pipeline.lib.source_toggle as st
        real_restore = st.restore_baseline
        calls = {"n": 0}

        def flaky_restore(component_dir, translation_output):
            calls["n"] += 1
            if calls["n"] == 2:
                raise _sub.CalledProcessError(
                    1, ["git", "checkout"], output=b"", stderr=b"simulated"
                )
            real_restore(component_dir, translation_output)

        monkeypatch.setattr(
            "pipeline.lib.source_toggle.restore_baseline", flaky_restore
        )

        rc = sim2real.main(["build", "--translation", thash, "--force-rebuild"])
        assert rc == 2
        # algo2 must not be attempted on an unknown tree state.
        assert len(dispatched) == 1
        err = capsys.readouterr().err
        assert "failed to record digest" in err
        assert "failed to restore baseline after build for algo1" in err
```

- [ ] **Step 8: Run it**

```bash
.venv/bin/python -m pytest pipeline/tests/test_sim2real.py -v -k \
  "digest_write_failure_and_failed_restore"
```

Expected: PASS. If `atomic_write_json` turns out to be called from somewhere else in the path, narrow the monkeypatch to raise only when the target path ends in `translation_output.json`.

- [ ] **Step 9: Commit**

```bash
git add pipeline/tests/test_sim2real.py
git commit -m "test(build): pin the last-algo and pending-return restore failures

Refs #930"
```

---

### Task 3: Make the warning fail CI

**Files:**
- Create: `pipeline/tests/test_no_syntax_warnings.py`

**Interfaces:**
- Consumes: `pipeline.lib.layout.repo_root() -> Path` (the repo-root helper `test_python_version.py` already uses for exactly this purpose).
- Produces: nothing other tasks consume.

Without this, the fix holds only until someone writes the construct again — nothing in CI fails on a `SyntaxWarning`. Note that `ruff --select B012` (`jump-statement-in-finally`) does **not** catch this shape: the `break` sits inside an `except` handler nested in the `finally`, and ruff only inspects the `finally` body directly. Verified both ways — B012 flags a bare `break` in a `finally` and passes the nested form. So the guard has to be CPython's own compiler.

- [ ] **Step 1: Write the test**

```python
"""Every tracked Python file must compile without warnings (issue #930).

A `SyntaxWarning` is invisible in CI — it does not fail a lint or a test
run — so the construct that produces one can be reintroduced freely. #930
was exactly that: a `break` inside a `finally` block warned on every
`sim2real.py` invocation under Python 3.14 for as long as it took someone
to read the noise and ask.

This is vacuously green on 3.12, which does not emit the warning (PEP 765
landed in 3.14). The CI matrix runs both legs, so the 3.14 leg is the one
that enforces it.
"""
import py_compile
import warnings

import pytest

from pipeline.lib import layout

REPO_ROOT = layout.repo_root()

# Directories holding this repo's own Python. Submodules (inference-sim,
# tektonc-data-collection, llm-d-benchmark) are deliberately excluded —
# their warnings are not ours to fix, and they are not on our CI gate.
OWN_PYTHON_ROOTS = ("pipeline", ".claude/skills")

SKIP_DIR_PARTS = {".venv", "__pycache__", "node_modules", ".git"}


def _own_python_files():
    files = [REPO_ROOT / "conftest.py"]
    for root in OWN_PYTHON_ROOTS:
        files.extend((REPO_ROOT / root).rglob("*.py"))
    return sorted(
        f for f in files
        if f.is_file() and not SKIP_DIR_PARTS & set(f.relative_to(REPO_ROOT).parts)
    )


def test_found_the_python_files():
    """A glob that silently matches nothing would make this suite vacuous."""
    files = _own_python_files()
    assert len(files) > 50
    assert REPO_ROOT / "pipeline" / "sim2real.py" in files


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
```

- [ ] **Step 2: Run it against the fixed tree**

```bash
.venv/bin/python -m pytest pipeline/tests/test_no_syntax_warnings.py -q
```

Expected: all pass (one case per file, ~145 cases).

- [ ] **Step 3: Verify the test actually catches the bug it is named for**

Write a throwaway file with the exact nested shape #930 had, confirm the test fails on it, then delete it. A guard nobody has seen fail is not a guard.

```bash
cat > pipeline/tests/_tmp_b012_probe.py <<'EOF'
def f(items):
    for i in items:
        try:
            pass
        finally:
            try:
                raise OSError
            except OSError:
                break
EOF
.venv/bin/python -m pytest pipeline/tests/test_no_syntax_warnings.py -q 2>&1 | tail -5
rm pipeline/tests/_tmp_b012_probe.py
```

Expected (on 3.14): one FAILED case naming `_tmp_b012_probe.py` and `'break' in a 'finally' block`. On 3.12 this step is inapplicable — note that and move on.

- [ ] **Step 4: Commit**

```bash
git add pipeline/tests/test_no_syntax_warnings.py
git commit -m "test: fail on any SyntaxWarning in this repo's own Python

ruff's B012 misses a jump inside an except handler nested in a finally,
which is the shape #930 had. CPython's compiler catches it; make that
the gate.

Refs #930"
```

---

### Task 4: Full verification

**Files:** none modified.

**Interfaces:** none.

- [ ] **Step 1: Lint exactly as CI does**

```bash
.venv/bin/ruff check pipeline/ .claude/skills/ --select F
```

Expected: `All checks passed!`

- [ ] **Step 2: Run the full suite exactly as CI does**

```bash
.venv/bin/python -m pytest pipeline/ \
  .claude/skills/sim2real-analyze/tests/ \
  .claude/skills/sim2real-bootstrap/tests/ \
  .claude/skills/sim2real-translate/tests/ \
  .claude/skills/sim2real-check/tests/ \
  --cov=pipeline --cov-report=term-missing --cov-fail-under=90 -q
```

Expected: all pass, coverage gate met. If coverage dropped below 90, the new parametrised test file inflates the denominator without covering `pipeline/` — check `term-missing` for which module regressed before assuming the gate is wrong.

- [ ] **Step 3: Confirm the original symptom is gone end to end**

The warning appeared on every subcommand, so reproduce with `--help`, which touches no cluster:

```bash
.venv/bin/python pipeline/sim2real.py --help 2>&1 | grep -i syntaxwarning || echo "no warning"
```

Expected: `no warning`.

- [ ] **Step 4: Confirm nothing leaked into the parent repo**

```bash
git status --short
git -C "$(git rev-parse --git-common-dir)/.." status --short -- pipeline/ docs/
```

Expected: the worktree shows only this branch's commits (clean tree); the parent shows no `pipeline/` modifications from this work.

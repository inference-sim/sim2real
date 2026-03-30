# Deploy Phase Re-run Control Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add interactive per-phase re-run prompts and a `--force-rerun` flag to `deploy.py` so operators are informed when a benchmark phase is already done and can choose to re-run it.

**Architecture:** Single-file change to `scripts/deploy.py`. Add `_clear_phase_state` helper, update the phase-skip logic in `stage_benchmarks` to prompt on TTY or honour `--force-rerun`, and wire the new flag through `build_parser` and `main`.

**Tech Stack:** Python 3.10+, stdlib only, pytest for tests.

**Spec:** `docs/superpowers/specs/2026-03-29-deploy-phase-rerun-design.md`

---

## Chunk 1: `_clear_phase_state` helper + tests

### Task 1: Add `_clear_phase_state` and its unit tests

**Files:**
- Modify: `scripts/deploy.py` — add helper after the existing pure-data helpers block (~line 76)
- Modify: `tests/test_deploy.py` — add test section

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_deploy.py`:

```python
# ── _clear_phase_state ──────────────────────────────────────────────

def test_clear_phase_state_removes_status_and_results_path(tmp_path):
    f = tmp_path / "benchmark_state.json"
    f.write_text(json.dumps({
        "phases": {
            "baseline": {"status": "done", "results_path": "/some/path", "started_at": "t"},
            "treatment": {"status": "done"},
        }
    }))
    deploy._clear_phase_state("baseline", f)
    state = json.loads(f.read_text())
    assert "status" not in state["phases"]["baseline"]
    assert "results_path" not in state["phases"]["baseline"]
    # Other keys preserved
    assert state["phases"]["baseline"]["started_at"] == "t"
    # Sibling phase untouched
    assert state["phases"]["treatment"]["status"] == "done"


def test_clear_phase_state_missing_phase_is_noop(tmp_path):
    f = tmp_path / "benchmark_state.json"
    original = {"phases": {"treatment": {"status": "done"}}}
    f.write_text(json.dumps(original))
    deploy._clear_phase_state("baseline", f)  # baseline not present
    assert json.loads(f.read_text()) == original


def test_clear_phase_state_missing_file_is_noop(tmp_path):
    f = tmp_path / "benchmark_state.json"
    # File does not exist — should not raise
    deploy._clear_phase_state("baseline", f)
    assert not f.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_deploy.py -k "clear_phase_state" -v
```

Expected: `AttributeError: module 'deploy' has no attribute '_clear_phase_state'`

- [ ] **Step 3: Implement `_clear_phase_state` in `scripts/deploy.py`**

Add after the `_merge_benchmark_into_validation` function (line 133, just before `# ── Run metadata`):

```python
def _clear_phase_state(phase: str, bench_state_file: Path) -> None:
    """Remove status and results_path for a phase so it will re-run.

    Preserves other keys in the phase entry. No-op if file or phase absent.
    """
    if not bench_state_file.exists():
        return
    state = json.loads(bench_state_file.read_text())
    phase_dict = state.get("phases", {}).get(phase, {})
    phase_dict.pop("status", None)
    phase_dict.pop("results_path", None)
    bench_state_file.write_text(json.dumps(state, indent=2))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_deploy.py -k "clear_phase_state" -v
```

Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/deploy.py tests/test_deploy.py
git commit -m "feat(deploy): add _clear_phase_state helper"
```

---

## Chunk 2: `--force-rerun` flag + phase-loop prompt

### Task 2: Add `--force-rerun` to the argument parser

**Files:**
- Modify: `scripts/deploy.py:69-73` (`build_parser`, argument block)

- [ ] **Step 1: Add the flag and update the epilog**

In `build_parser()`, after the `--pr` argument (line 72):

```python
p.add_argument("--force-rerun", action="store_true",
               help="Re-run already-done benchmark phases without prompting")
```

Update the epilog examples block to add:

```
  python scripts/deploy.py --skip-build-epp --force-rerun  # re-run all done phases
```

- [ ] **Step 2: Wire through `main()`**

In `main()`, update the `stage_benchmarks` call:

```python
# Before:
verdict = stage_benchmarks(run_dir, namespace, fast_iter)

# After:
verdict = stage_benchmarks(run_dir, namespace, fast_iter, args.force_rerun)
```

- [ ] **Step 3: Update `stage_benchmarks` signature**

```python
# Before:
def stage_benchmarks(run_dir: Path, namespace: str, fast_iter: bool) -> str:

# After:
def stage_benchmarks(run_dir: Path, namespace: str, fast_iter: bool, force_rerun: bool = False) -> str:
```

### Task 3: Replace silent-skip with prompt logic

**Files:**
- Modify: `scripts/deploy.py:598-603` (phase loop skip block)
- Modify: `tests/test_deploy.py` — add integration tests for prompt logic

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_deploy.py`. These test `_should_skip_phase` — a small extraction of the skip/rerun decision that we'll add alongside the loop change:

```python
# ── _should_skip_phase ───────────────────────────────────────────────

def test_should_skip_phase_force_rerun_returns_false(tmp_path):
    """force_rerun=True means never skip — always re-run."""
    f = tmp_path / "benchmark_state.json"
    f.write_text(json.dumps({"phases": {"baseline": {"status": "done"}}}))
    skip, reason = deploy._should_skip_phase("baseline", f, force_rerun=True, interactive=False)
    assert skip is False
    assert "force-rerun" in reason


def test_should_skip_phase_non_interactive_skips(tmp_path):
    """Non-interactive + no flag → skip."""
    f = tmp_path / "benchmark_state.json"
    f.write_text(json.dumps({"phases": {"baseline": {"status": "done"}}}))
    skip, reason = deploy._should_skip_phase("baseline", f, force_rerun=False, interactive=False)
    assert skip is True
    assert "non-interactive" in reason


def test_should_skip_phase_not_done_returns_false(tmp_path):
    """Phase not done → never skip regardless of flags."""
    f = tmp_path / "benchmark_state.json"
    f.write_text(json.dumps({"phases": {"baseline": {"status": "pending"}}}))
    skip, _ = deploy._should_skip_phase("baseline", f, force_rerun=False, interactive=False)
    assert skip is False


def test_should_skip_phase_missing_state_returns_false(tmp_path):
    """No state file → phase has never run, don't skip."""
    f = tmp_path / "benchmark_state.json"
    skip, _ = deploy._should_skip_phase("baseline", f, force_rerun=False, interactive=False)
    assert skip is False


def test_should_skip_phase_interactive_user_says_no(tmp_path, monkeypatch):
    """Interactive + user enters 'n' → skip."""
    f = tmp_path / "benchmark_state.json"
    f.write_text(json.dumps({"phases": {"baseline": {"status": "done"}}}))
    monkeypatch.setattr("builtins.input", lambda _: "n")
    skip, reason = deploy._should_skip_phase("baseline", f, force_rerun=False, interactive=True)
    assert skip is True


def test_should_skip_phase_interactive_user_says_yes(tmp_path, monkeypatch):
    """Interactive + user enters 'y' → re-run."""
    f = tmp_path / "benchmark_state.json"
    f.write_text(json.dumps({"phases": {"baseline": {"status": "done"}}}))
    monkeypatch.setattr("builtins.input", lambda _: "y")
    skip, _ = deploy._should_skip_phase("baseline", f, force_rerun=False, interactive=True)
    assert skip is False


def test_should_skip_phase_interactive_empty_enter_skips(tmp_path, monkeypatch):
    """Interactive + user hits Enter (empty) → default skip."""
    f = tmp_path / "benchmark_state.json"
    f.write_text(json.dumps({"phases": {"baseline": {"status": "done"}}}))
    monkeypatch.setattr("builtins.input", lambda _: "")
    skip, _ = deploy._should_skip_phase("baseline", f, force_rerun=False, interactive=True)
    assert skip is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_deploy.py -k "should_skip_phase" -v
```

Expected: `AttributeError: module 'deploy' has no attribute '_should_skip_phase'`

- [ ] **Step 3: Add `_should_skip_phase` helper and replace the silent-skip block in `stage_benchmarks`**

Add `_should_skip_phase` after `_clear_phase_state` (around line 148):

```python
def _should_skip_phase(phase: str, bench_state_file: Path,
                       force_rerun: bool, interactive: bool) -> tuple[bool, str]:
    """Return (should_skip, reason_message) for a benchmark phase.

    Returns (False, "") when the phase is not marked done.
    """
    if not bench_state_file.exists():
        return False, ""
    state = json.loads(bench_state_file.read_text())
    if state.get("phases", {}).get(phase, {}).get("status") != "done":
        return False, ""
    if force_rerun:
        return False, f"Phase {phase} already done — re-running (--force-rerun)"
    if interactive:
        answer = input(f"  Phase {phase} already done — re-run? [y/N]: ").strip().lower()
        if answer == "y":
            return False, ""
        return True, f"Skipping {phase}"
    return True, f"Phase {phase} already done — skipping (non-interactive)"
```

Then replace the silent-skip block in `stage_benchmarks` (lines 598–603):

```python
        # Skip if already done (resume support)
        if bench_state_file.exists():
            state = json.loads(bench_state_file.read_text())
            if state.get("phases", {}).get(phase, {}).get("status") == "done":
                info(f"Phase {phase} already done — skipping")
                continue
```

Replace with:

```python
        # Skip or re-run if already done
        skip, msg = _should_skip_phase(
            phase, bench_state_file,
            force_rerun=force_rerun,
            interactive=sys.stdin.isatty(),
        )
        if skip:
            info(msg)
            continue
        if msg:
            info(msg)
        _clear_phase_state(phase, bench_state_file)  # no-op if file absent
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_deploy.py -k "should_skip_phase" -v
```

Expected: 7 tests PASS.

- [ ] **Step 5: Run full test suite — no regressions**

```bash
python -m pytest tests/test_deploy.py -v
```

Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/deploy.py tests/test_deploy.py
git commit -m "feat(deploy): add --force-rerun flag and interactive phase re-run prompt"
```

---

## Chunk 3: Smoke-test the CLI surface

### Task 4: Verify argument parser accepts the new flag

**Files:** none (read-only check)

- [ ] **Step 1: Verify `--help` output includes the new flag**

```bash
python scripts/deploy.py --help
```

Expected output includes (text may wrap depending on terminal width):
```
  --force-rerun        Re-run already-done benchmark phases without prompting
```

And the epilog includes:
```
  python scripts/deploy.py --skip-build-epp --force-rerun  # re-run all done phases
```

- [ ] **Step 2: Verify unknown flags still error**

```bash
python scripts/deploy.py --rerun-baseline 2>&1 | head -1
```

Expected: `error: unrecognized arguments: --rerun-baseline`

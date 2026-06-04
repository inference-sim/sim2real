# Issue #191: deploy build — write epp_image and last_completed_step Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the `epp_image` and `stages.deploy.last_completed_step` fields in `run_metadata.json` after `deploy.py build` (and the build phase of `deploy.py run`) succeeds. The fields stopped being written when PR #189 routed through `_cmd_build`, which always passes `--image-ref` to `build-epp.sh` — short-circuiting that script's own metadata write.

**Architecture:** Factor the metadata write into a tiny helper `_write_build_metadata(run_dir, epp_image)` in `pipeline/deploy.py`. Call it from `_cmd_build` immediately before each non-error return ("built" and "current"). Skip on the "skip" return because `--skip-build` / no `component_image` does not represent build completion (the file may not even have prior build state to update). Use the canonical `treatment_ref = f"{registry}/{repo_name}:{run_name}"` as the `epp_image` value — same convention build-epp.sh used historically. The bash script's existing conditional in step 8 is intentional (caller-owned-metadata contract) and stays unchanged.

**Tech Stack:** Python (deploy.py, pytest).

**Closes:** #191

---

## File Structure

- Modify: `pipeline/deploy.py` — add `_write_build_metadata` helper + two call sites in `_cmd_build` (before each successful return).
- Modify: `pipeline/tests/test_deploy_build.py` — add unit test for the helper.

---

## Task 1: Add helper and integrate into `_cmd_build`

**Files:**
- Modify: `pipeline/deploy.py` (around lines 115 and 304)
- Test: `pipeline/tests/test_deploy_build.py`

**Acceptance criteria (from issue #191):**
- After `_cmd_build` returns `"built"` or `"current"`, `run_metadata.json` contains:
  - `epp_image == f"{registry}/{repo_name}:{run_name}"`
  - `stages.deploy.last_completed_step == "build"`
- After `_cmd_build` returns `"skip"`, the file is left untouched (no false positives).
- `run.py inspect` displays the deploy step again (verified via `run_manager.inspect_run` consumer).

- [ ] **Step 1: Write the failing test**

Append to `pipeline/tests/test_deploy_build.py`:

```python
class TestWriteBuildMetadata:
    """Unit tests for _write_build_metadata helper."""

    def test_writes_epp_image_and_last_completed_step(self, tmp_path):
        from pipeline.deploy import _write_build_metadata

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        meta = {"version": 1, "stages": {}}
        (run_dir / "run_metadata.json").write_text(json.dumps(meta))

        _write_build_metadata(run_dir, "ghcr.io/org/sched:r1")

        result = json.loads((run_dir / "run_metadata.json").read_text())
        assert result["epp_image"] == "ghcr.io/org/sched:r1"
        assert result["stages"]["deploy"]["last_completed_step"] == "build"

    def test_creates_stages_and_deploy_keys_when_missing(self, tmp_path):
        from pipeline.deploy import _write_build_metadata

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "run_metadata.json").write_text(json.dumps({"version": 1}))

        _write_build_metadata(run_dir, "img:tag")

        result = json.loads((run_dir / "run_metadata.json").read_text())
        assert result["stages"]["deploy"]["last_completed_step"] == "build"
        assert result["epp_image"] == "img:tag"

    def test_preserves_other_stage_keys(self, tmp_path):
        from pipeline.deploy import _write_build_metadata

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "run_metadata.json").write_text(json.dumps({
            "version": 1,
            "stages": {"setup": {"status": "ok"}, "deploy": {"status": "in_progress"}},
        }))

        _write_build_metadata(run_dir, "img:tag")

        result = json.loads((run_dir / "run_metadata.json").read_text())
        assert result["stages"]["setup"] == {"status": "ok"}
        assert result["stages"]["deploy"]["status"] == "in_progress"
        assert result["stages"]["deploy"]["last_completed_step"] == "build"

    def test_no_op_when_metadata_missing(self, tmp_path):
        from pipeline.deploy import _write_build_metadata

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        # No run_metadata.json — must not raise.
        _write_build_metadata(run_dir, "img:tag")
        assert not (run_dir / "run_metadata.json").exists()

    def test_no_op_when_metadata_unparseable(self, tmp_path):
        from pipeline.deploy import _write_build_metadata

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "run_metadata.json").write_text("not json {{{")

        _write_build_metadata(run_dir, "img:tag")

        # File is left as-is (no rewrite, no exception).
        assert (run_dir / "run_metadata.json").read_text() == "not json {{{"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest pipeline/tests/test_deploy_build.py::TestWriteBuildMetadata -v`
Expected: 5 failures with `ImportError: cannot import name '_write_build_metadata' from 'pipeline.deploy'`.

- [ ] **Step 3: Add the helper**

In `pipeline/deploy.py`, immediately before `def _cmd_build(...)` (i.e., after the `# ── Image build ────` comment around line 115), insert:

```python
def _write_build_metadata(run_dir: Path, epp_image: str) -> None:
    """Record a successful EPP build in run_metadata.json.

    Sets ``epp_image`` and ``stages.deploy.last_completed_step = "build"`` so
    ``run.py inspect`` (via ``run_manager.inspect_run``) shows the deploy
    progress. No-op if run_metadata.json is missing or unparseable — those
    cases are handled by the caller's own error paths.
    """
    meta_path = run_dir / "run_metadata.json"
    if not meta_path.exists():
        return
    try:
        meta = json.loads(meta_path.read_text())
    except json.JSONDecodeError:
        return
    meta["epp_image"] = epp_image
    meta.setdefault("stages", {}).setdefault("deploy", {})["last_completed_step"] = "build"
    meta_path.write_text(json.dumps(meta, indent=2))
```

- [ ] **Step 4: Run helper tests to verify they pass**

Run: `python -m pytest pipeline/tests/test_deploy_build.py::TestWriteBuildMetadata -v`
Expected: 5 passed.

- [ ] **Step 5: Wire helper into `_cmd_build`**

In `pipeline/deploy.py`, find the end of `_cmd_build`. The current tail is:

```python
        current_hash = compute_source_hash(source_dir)
        save_source_hash(run_dir, ref, current_hash)
        ok(f"Image built and hash recorded: {ref}")
        built_any = True

    return "built" if built_any else "current"
```

Replace with:

```python
        current_hash = compute_source_hash(source_dir)
        save_source_hash(run_dir, ref, current_hash)
        ok(f"Image built and hash recorded: {ref}")
        built_any = True

    treatment_ref = f"{registry}/{repo_name}:{run_name}"
    _write_build_metadata(run_dir, treatment_ref)
    return "built" if built_any else "current"
```

Also handle the early `"current"` return at the top of the function (around line 184–185):

```python
    if not to_build:
        return "current"
```

Replace with:

```python
    if not to_build:
        treatment_ref = f"{registry}/{repo_name}:{run_name}"
        _write_build_metadata(run_dir, treatment_ref)
        return "current"
```

The two `"skip"` returns (no `component_image`, or `--skip-build`) are intentionally not modified — those represent "no build was attempted", not "build completed".

- [ ] **Step 6: Run the full test suite**

Run: `python -m pytest pipeline/ .claude/skills/sim2real-analyze/tests/ .claude/skills/sim2real-translate/tests/`
Expected: all tests pass (was 919, should be 924 with the 5 new tests).

- [ ] **Step 7: Run lint**

Run: `ruff check pipeline/ .claude/skills/ --select F`
Expected: `All checks passed!`.

- [ ] **Step 8: Commit**

```bash
git add pipeline/deploy.py pipeline/tests/test_deploy_build.py docs/superpowers/plans/2026-06-04-issue-191-build-metadata-write.md
git commit -m "$(cat <<'EOF'
fix(deploy): write epp_image and last_completed_step after _cmd_build

PR #189 routed all builds through _cmd_build, which passes --image-ref
to build-epp.sh. That short-circuits the bash script's metadata write
(it gates on `[ -z "${IMAGE_REF}" ]`, intentionally — when callers pass
--image-ref the caller owns metadata). As a result, run_metadata.json
no longer received epp_image or stages.deploy.last_completed_step, and
`run.py inspect` showed an empty deploy step (run_manager.inspect_run
reads last_completed_step at line 197).

Restore the contract by writing the two fields from _cmd_build itself
on every successful return ("built" and "current"), using the canonical
treatment_ref pattern. The "skip" return is intentionally not updated.

Closes #191
EOF
)"
```

---

## Self-Review

**Spec coverage:**
- "Have `_cmd_build` write these fields after a successful build" ✓ (Steps 3, 5)
- `meta["epp_image"] = treatment_ref` ✓ (Step 5)
- `meta.setdefault("stages", {}).setdefault("deploy", {})["last_completed_step"] = "build"` ✓ (Step 3)
- Restores `run.py inspect` deploy step display ✓ (consumed by `run_manager.inspect_run` line 197 — no change needed there, only the producer)

**Placeholder scan:** None — every step has full code or full command.

**Type consistency:** Helper signature `_write_build_metadata(run_dir: Path, epp_image: str) -> None` is used identically in test (positional args) and call sites.

**Edge cases handled:**
- Missing `run_metadata.json` → no-op (test 4).
- Unparseable JSON → no-op (test 5). Caller already validates JSON earlier; double-decoding here is defensive.
- Existing `stages` / `deploy` dicts are preserved via `setdefault` (test 3).
- `"skip"` return (no component_image, or `--skip-build`) not touched — preserves prior metadata, no false claim of build completion.
- Multiple per-algorithm builds in one `_cmd_build` invocation: helper called once at end with canonical treatment_ref. Matches legacy behavior of build-epp.sh (which would have written this on the last build call).

**Cross-path parity:** Issue affects only the `_cmd_build` path in `pipeline/deploy.py`, which serves both `deploy.py build` and `deploy.py run`. Single fix covers both. No simulator (`sim/` `cmd/`) changes implicated.

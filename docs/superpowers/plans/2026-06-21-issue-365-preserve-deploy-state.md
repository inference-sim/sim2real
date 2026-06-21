# Issue #365: Preserve deploy-owned state on setup.py re-run

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `pipeline/setup.py` re-runs idempotent for deploy-owned state in `run_metadata.json` so subsequent `deploy.py run` calls skip rebuilding images whose source has not changed.

**Architecture:** Switch `step_config_output` from "build dict literal and overwrite" to "read existing JSON, update only setup-owned keys, `setdefault` the per-stage seeds, write back." Deploy-owned keys (`source_hashes`, `epp_image`, `stages.deploy.last_completed_step`) are left untouched. The pattern mirrors `pipeline/lib/ensure_image.save_source_hash`.

**Tech Stack:** Python 3.10+, pytest.

## Global Constraints

- All edits inside the worktree at `.claude/worktrees/issue-365-preserve-deploy-state/`. Verify any absolute path passed to Read/Edit/Write contains that substring.
- CI must pass: `ruff check pipeline/ .claude/skills/ --select F` and `python -m pytest pipeline/ .claude/skills/sim2real-analyze/tests/ .claude/skills/sim2real-bootstrap/tests/ .claude/skills/sim2real-translate/tests/ -v`.
- Existing test `pipeline/tests/test_setup_pipeline.py::TestSetupConfigJson` must keep passing (no regressions to setup_config.json output).
- Field-ownership model — keys grouped by which stage produces them:
  - **Setup-owned (overwrite OK):** `version`, `namespace`, `registry`, `repo_name`, `storage_class`, `is_openshift`, `container_runtime`, `created_at`, `pipeline_commit`, `component_image`, `stages.setup`.
  - **Per-stage seeds (setdefault only):** `stages.prepare`, `stages.deploy`, `stages.results`.
  - **Deploy-owned (must NOT touch):** `source_hashes`, `epp_image`, `stages.deploy.last_completed_step` (and any other deploy-introduced subkey under `stages.deploy`).

---

### Task 1: Failing test — re-running setup preserves deploy-owned fields

**Files:**
- Modify: `pipeline/tests/test_setup_pipeline.py` (add new `TestRunMetadataIdempotent` class at end)

**Interfaces:**
- Consumes: `pipeline.setup.step_config_output(cfg, run_dir, container_rt)` (existing); `_make_config()` helper already at module top.
- Produces: A test class that exercises the re-run path and asserts deploy-owned fields survive.

- [ ] **Step 1: Add the failing test class**

Append to `pipeline/tests/test_setup_pipeline.py`:

```python
class TestRunMetadataIdempotent:
    """Re-running setup must preserve deploy-owned fields in run_metadata.json (issue #365)."""

    def _run_setup(self, tmp_path):
        from pipeline.setup import step_config_output
        import pipeline.setup as setup_module

        original = setup_module.EXPERIMENT_ROOT
        setup_module.EXPERIMENT_ROOT = tmp_path
        try:
            run_dir = tmp_path / "workspace" / "runs" / "test-run"
            run_dir.mkdir(parents=True, exist_ok=True)
            cfg = _make_config()
            step_config_output(cfg, run_dir, "podman")
            return run_dir
        finally:
            setup_module.EXPERIMENT_ROOT = original

    def test_preserves_source_hashes_on_rerun(self, tmp_path):
        """Deploy-written source_hashes must survive a setup re-run."""
        run_dir = self._run_setup(tmp_path)
        meta_path = run_dir / "run_metadata.json"

        meta = json.loads(meta_path.read_text())
        meta["source_hashes"] = {"quay.io/test/llm-d-inference-scheduler:test-run": "abc123def456"}
        meta_path.write_text(json.dumps(meta))

        self._run_setup(tmp_path)

        meta2 = json.loads(meta_path.read_text())
        assert meta2.get("source_hashes") == {
            "quay.io/test/llm-d-inference-scheduler:test-run": "abc123def456"
        }

    def test_preserves_epp_image_on_rerun(self, tmp_path):
        """Deploy-written epp_image must survive a setup re-run."""
        run_dir = self._run_setup(tmp_path)
        meta_path = run_dir / "run_metadata.json"

        meta = json.loads(meta_path.read_text())
        meta["epp_image"] = "quay.io/test/llm-d-inference-scheduler:test-run"
        meta_path.write_text(json.dumps(meta))

        self._run_setup(tmp_path)

        meta2 = json.loads(meta_path.read_text())
        assert meta2.get("epp_image") == "quay.io/test/llm-d-inference-scheduler:test-run"

    def test_preserves_stages_deploy_last_completed_step(self, tmp_path):
        """stages.deploy.last_completed_step (deploy-owned) must survive a setup re-run."""
        run_dir = self._run_setup(tmp_path)
        meta_path = run_dir / "run_metadata.json"

        meta = json.loads(meta_path.read_text())
        meta.setdefault("stages", {}).setdefault("deploy", {})["last_completed_step"] = "build"
        meta_path.write_text(json.dumps(meta))

        self._run_setup(tmp_path)

        meta2 = json.loads(meta_path.read_text())
        assert meta2["stages"]["deploy"].get("last_completed_step") == "build"

    def test_refreshes_setup_owned_fields_on_rerun(self, tmp_path):
        """Setup-owned fields (registry, namespace) reflect the latest cfg on re-run."""
        from pipeline.setup import step_config_output
        import pipeline.setup as setup_module

        run_dir = self._run_setup(tmp_path)
        meta_path = run_dir / "run_metadata.json"

        original = setup_module.EXPERIMENT_ROOT
        setup_module.EXPERIMENT_ROOT = tmp_path
        try:
            cfg2 = _make_config(registry="quay.io/new-registry", namespace="new-ns")
            step_config_output(cfg2, run_dir, "docker")
        finally:
            setup_module.EXPERIMENT_ROOT = original

        meta2 = json.loads(meta_path.read_text())
        assert meta2["registry"] == "quay.io/new-registry"
        assert meta2["namespace"] == "new-ns"
        assert meta2["container_runtime"] == "docker"

    def test_first_run_creates_full_metadata(self, tmp_path):
        """First-run path (no existing file) still produces all setup-owned fields."""
        run_dir = self._run_setup(tmp_path)
        meta = json.loads((run_dir / "run_metadata.json").read_text())
        assert meta["version"] == 1
        assert meta["namespace"] == "test-ns"
        assert meta["registry"] == "quay.io/test"
        assert meta["component_image"] == "quay.io/test/llm-d-inference-scheduler:test-run"
        assert meta["stages"]["setup"]["status"] == "completed"
        assert meta["stages"]["prepare"] == {"status": "pending"}
        assert meta["stages"]["deploy"] == {"status": "pending"}
        assert meta["stages"]["results"] == {"status": "pending"}
```

- [ ] **Step 2: Run new tests; confirm they fail**

Run: `python -m pytest pipeline/tests/test_setup_pipeline.py::TestRunMetadataIdempotent -v`
Expected: `test_preserves_source_hashes_on_rerun`, `test_preserves_epp_image_on_rerun`, `test_preserves_stages_deploy_last_completed_step` fail (deploy-owned fields wiped on re-run). `test_refreshes_setup_owned_fields_on_rerun` and `test_first_run_creates_full_metadata` pass.

- [ ] **Step 3: Commit failing tests**

```bash
git add pipeline/tests/test_setup_pipeline.py
git commit -m "test: assert setup.py preserves deploy-owned run_metadata fields (#365)"
```

---

### Task 2: Implement read-modify-write in `step_config_output`

**Files:**
- Modify: `pipeline/setup.py:703-725` (the `run_metadata.json` write block in `step_config_output`)

**Interfaces:**
- Consumes: existing `cfg`, `run_dir`, `now_iso`, `commit`, `container_rt` locals already computed earlier in `step_config_output`.
- Produces: file at `run_dir / "run_metadata.json"` with setup-owned fields refreshed and deploy-owned fields preserved.

- [ ] **Step 1: Replace the metadata write block**

Open `pipeline/setup.py`. Locate the block starting at line 703 (the `# run_metadata.json (with version: 1 per spec)` comment) and ending at the `ok(f"Run metadata → {meta_path}")` line.

Replace with:

```python
    # run_metadata.json — read-modify-write so deploy-owned keys
    # (source_hashes, epp_image, stages.deploy.last_completed_step) survive
    # setup re-runs. See issue #365.
    meta_path = run_dir / "run_metadata.json"
    if meta_path.exists():
        try:
            existing = json.loads(meta_path.read_text())
        except json.JSONDecodeError:
            existing = {}
    else:
        existing = {}

    # Setup-owned fields (overwrite on every re-run).
    existing.update({
        "version": 1,
        "namespace": cfg.namespace,
        "registry": cfg.registry,
        "repo_name": cfg.repo_name,
        "storage_class": cfg.storage_class,
        "is_openshift": cfg.is_openshift,
        "container_runtime": container_rt,
        "created_at": now_iso,
        "pipeline_commit": commit,
    })
    if cfg.registry:
        existing["component_image"] = f"{cfg.registry}/{cfg.repo_name}:{cfg.run_name}"

    # stages: refresh the setup entry; seed the others only if absent so
    # later stages (deploy in particular) keep any subkeys they wrote.
    stages = existing.setdefault("stages", {})
    stages["setup"] = {
        "status": "completed",
        "completed_at": now_iso,
        "summary": f"Namespace {cfg.namespace} configured, "
                   f"PVCs created, Tekton tasks deployed",
    }
    stages.setdefault("prepare", {"status": "pending"})
    stages.setdefault("deploy",  {"status": "pending"})
    stages.setdefault("results", {"status": "pending"})

    meta_path.write_text(json.dumps(existing, indent=2))
    ok(f"Run metadata → {meta_path}")
```

- [ ] **Step 2: Run the new test class; confirm it now passes**

Run: `python -m pytest pipeline/tests/test_setup_pipeline.py::TestRunMetadataIdempotent -v`
Expected: All 5 tests PASS.

- [ ] **Step 3: Run full test_setup_pipeline.py for regressions**

Run: `python -m pytest pipeline/tests/test_setup_pipeline.py -v`
Expected: All tests PASS (existing pipeline_yaml / hf_secret_name / orchestrator_image tests still pass — those touch `setup_config.json`, not `run_metadata.json`).

- [ ] **Step 4: Commit the fix**

```bash
git add pipeline/setup.py
git commit -m "fix(setup): preserve deploy-owned run_metadata fields on re-run (#365)

step_config_output now reads existing run_metadata.json, updates only the
setup-owned keys (version, namespace, registry, repo_name, storage_class,
is_openshift, container_runtime, created_at, pipeline_commit,
component_image, stages.setup), and uses setdefault for stages.prepare /
stages.deploy / stages.results. Deploy-owned fields (source_hashes,
epp_image, stages.deploy.last_completed_step) survive setup re-runs, so
deploy.py can skip rebuilds when the component HEAD has not moved."
```

---

### Task 3: Full verification + sweep + PR

**Files:**
- No further code edits (unless the sweep finds stale references).

- [ ] **Step 1: Run full pipeline test suite**

Run: `python -m pytest pipeline/ -v`
Expected: All tests PASS.

- [ ] **Step 2: Run skill test suites listed in CI**

Run: `python -m pytest pipeline/ .claude/skills/sim2real-analyze/tests/ .claude/skills/sim2real-bootstrap/tests/ .claude/skills/sim2real-translate/tests/ -v`
Expected: All tests PASS.

- [ ] **Step 3: Run lint**

Run: `ruff check pipeline/ .claude/skills/ --select F`
Expected: no F-code errors.

- [ ] **Step 4: Sweep for stale references**

The diff touches `step_config_output` in `pipeline/setup.py` and adds tests in `pipeline/tests/test_setup_pipeline.py`. Schema of `run_metadata.json` is unchanged — only the *write semantics* changed. Therefore the relevant grep targets are:

```bash
grep -rn "run_metadata" docs/ README* pipeline/README.md .claude/skills/ 2>/dev/null
grep -rn "step_config_output" docs/ README* pipeline/README.md .claude/skills/ 2>/dev/null
grep -rn "source_hashes\|epp_image" docs/ README* pipeline/README.md .claude/skills/ 2>/dev/null
```

For each hit: decide stale (update in this PR) / accurate (leave) / unrelated (leave). Note the result in the PR body.

- [ ] **Step 5: Confirm path discipline — no parent-repo leakage**

```bash
git status
git -C ../../.. status
```

Worktree should show committed changes only. Parent repo should show only the pre-existing `M`/`??` entries from the session-start gitStatus.

- [ ] **Step 6: Push + create PR**

```bash
git push -u origin worktree-issue-365-preserve-deploy-state
gh pr create --title "fix(setup): preserve deploy-owned run_metadata fields on re-run" --body "$(cat <<'EOF'
Closes #365.

## Summary

`pipeline/setup.py` was overwriting `run_metadata.json` with a fresh dict on every re-run, wiping deploy-owned fields (`source_hashes`, `epp_image`, `stages.deploy.last_completed_step`). The next `deploy.py run` then saw no stored hash for any image and rebuilt every image even when the component repo's HEAD had not moved.

This change switches `step_config_output` to read-modify-write: setup-owned keys are refreshed; per-stage seeds use `setdefault`; deploy-owned keys are left untouched.

## Field ownership

- **Setup-owned (overwrite):** `version`, `namespace`, `registry`, `repo_name`, `storage_class`, `is_openshift`, `container_runtime`, `created_at`, `pipeline_commit`, `component_image`, `stages.setup`.
- **Per-stage seeds (setdefault only):** `stages.prepare`, `stages.deploy`, `stages.results`.
- **Deploy-owned (do not touch):** `source_hashes`, `epp_image`, `stages.deploy.last_completed_step`.

## Tests

New `TestRunMetadataIdempotent` class in `pipeline/tests/test_setup_pipeline.py` covers:

- `source_hashes` survives a setup re-run
- `epp_image` survives a setup re-run
- `stages.deploy.last_completed_step` survives a setup re-run
- Setup-owned fields (registry, namespace, container_runtime) are refreshed on re-run
- First-run path still produces all expected keys (no regression)

## Stale-reference sweep

Grepped `docs/`, `README*`, `pipeline/README.md`, and `.claude/skills/` for `run_metadata`, `step_config_output`, `source_hashes`, `epp_image`. Sweep notes in PR conversation.

## Test plan

- [x] `python -m pytest pipeline/ -v`
- [x] `python -m pytest .claude/skills/sim2real-analyze/tests/ .claude/skills/sim2real-bootstrap/tests/ .claude/skills/sim2real-translate/tests/ -v`
- [x] `ruff check pipeline/ .claude/skills/ --select F`
EOF
)"
```

If `gh pr create` returns `Resource not accessible by personal access token`, retry with `unset GITHUB_TOKEN GH_TOKEN; gh pr create ...`.

---

## Self-review

- **Spec coverage:** Issue #365 acceptance is "after the fix, `deploy.py run` (4) skips the rebuild after a setup re-run (3) when source is unchanged." Task 2 implements the read-modify-write. Task 1 tests assert the three deploy-owned fields are preserved — that is the necessary precondition for `image_needs_build` to return `False` on the unchanged path. `image_needs_build` already has unit tests in `test_ensure_image.py` covering the hash-match → False path; combined, the issue's acceptance is covered. ✓
- **Placeholder scan:** No TBDs, no "appropriate error handling," no "similar to Task N." Code blocks are complete. ✓
- **Type consistency:** `step_config_output(cfg, run_dir, container_rt)` signature unchanged. `now_iso`, `commit`, `container_rt` are pre-existing locals at the replacement site — verified in the read of `pipeline/setup.py:672-676` before plan was written. ✓

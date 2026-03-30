# Move EPP Build to Prepare Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move `stage_build_epp` (and its `_inject_image_reference` helper) from `scripts/deploy.py` to `scripts/prepare.py`, making deploy purely "run benchmarks + PR".

**Architecture:** The EPP build step is the terminal step of prepare — it runs after the outer retry loop exits successfully, before `write_outputs()`. Deploy's prerequisites check confirms the EPP image exists in `run_metadata.json` before proceeding to benchmarks. The `--skip-build-epp` flag on deploy is removed entirely; the existing `_should_skip`-style prompt in prepare handles the resume case.

**Tech Stack:** Python 3.10+, stdlib only. Tests use pytest.

---

## Chunk 1: Move `_inject_image_reference` to prepare.py and update tests

**Files:**
- Modify: `scripts/prepare.py` (add helper after module constants)
- Modify: `scripts/deploy.py` (keep the function for now — removed in Chunk 3)
- Create: `tests/test_prepare.py`

### Task 1: Write tests for `_inject_image_reference` in a new test file

- [ ] **Step 1: Create `tests/test_prepare.py` with the four `_inject_image_reference` tests**

```python
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import importlib

prepare = importlib.import_module("prepare")


# ── _inject_image_reference ─────────────────────────────────────────

def test_inject_image_sets_hub_name_and_tag():
    alg = {"stack": {"gaie": {"treatment": {"helmValues": {}}}}}
    result = prepare._inject_image_reference(alg, "quay.io/me", "my-repo", "run-2026-03-28")
    ie = result["stack"]["gaie"]["treatment"]["helmValues"]["inferenceExtension"]["image"]
    assert ie["hub"] == "quay.io/me"
    assert ie["name"] == "my-repo"
    assert ie["tag"] == "run-2026-03-28"


def test_inject_image_replaces_existing_image():
    alg = {"stack": {"gaie": {"treatment": {"helmValues": {
        "inferenceExtension": {"image": {"hub": "old.io/org/repo", "tag": "old-tag"}}
    }}}}}
    result = prepare._inject_image_reference(alg, "quay.io/me", "new-repo", "new-tag")
    ie = result["stack"]["gaie"]["treatment"]["helmValues"]["inferenceExtension"]["image"]
    assert ie == {"hub": "quay.io/me", "name": "new-repo", "tag": "new-tag"}


def test_inject_image_preserves_other_keys():
    alg = {"stack": {"gaie": {"treatment": {"helmValues": {"foo": "bar"}}}}}
    result = prepare._inject_image_reference(alg, "hub", "repo", "tag")
    assert result["stack"]["gaie"]["treatment"]["helmValues"]["foo"] == "bar"


def test_inject_image_creates_missing_nesting():
    alg = {}
    result = prepare._inject_image_reference(alg, "hub", "repo", "tag")
    ie = result["stack"]["gaie"]["treatment"]["helmValues"]["inferenceExtension"]["image"]
    assert ie["hub"] == "hub"
    assert ie["name"] == "repo"
    assert ie["tag"] == "tag"
```

- [ ] **Step 2: Run the new tests to confirm they fail (function not in prepare yet)**

```bash
cd /Users/jchen/go/src/inference-sim/sim2real
python -m pytest tests/test_prepare.py -v
```
Expected: 4 failures with `AttributeError: module 'prepare' has no attribute '_inject_image_reference'`

- [ ] **Step 3: Add `_inject_image_reference` and module-level constants to `prepare.py`**

After the `MODELS`/`DEV_MODELS` lines (around line 17), add:
```python
VENV_PYTHON = str(REPO_ROOT / ".venv/bin/python")
CLI = str(REPO_ROOT / "tools/transfer_cli.py")
```

Then add `_inject_image_reference` near the end of the "pure data helpers" section, just before the `_should_skip` function:

```python
def _inject_image_reference(alg_values: dict, hub: str, name: str, tag: str) -> dict:
    """Inject EPP image hub+name+tag into algorithm_values dict. Returns modified dict.

    Uses direct assignment (not update) to fully replace any prior image dict, ensuring
    hub, name, and tag are always consistent.
    """
    (alg_values
        .setdefault("stack", {})
        .setdefault("gaie", {})
        .setdefault("treatment", {})
        .setdefault("helmValues", {})
        .setdefault("inferenceExtension", {})
        ["image"]) = {"hub": hub, "name": name, "tag": tag}
    return alg_values
```

- [ ] **Step 4: Run the tests to confirm they pass**

```bash
python -m pytest tests/test_prepare.py -v
```
Expected: 4 PASSED

- [ ] **Step 5: Run the full test suite to confirm nothing broke**

```bash
python -m pytest tests/ -v
```
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add tests/test_prepare.py scripts/prepare.py
git commit -m "feat: add _inject_image_reference to prepare.py with tests"
```

---

## Chunk 2: Add `stage_build_epp` to prepare.py and wire it into `main()`

**Files:**
- Modify: `scripts/prepare.py` (add function + call in main)

### Task 2: Add `stage_build_epp` to prepare.py

- [ ] **Step 1: Add `stage_build_epp` to prepare.py**

Add after `_inject_image_reference` (and before `_should_skip`), still in the "pure data helpers / cluster steps" section. The function is identical to the one in deploy.py except it lives in prepare and uses prepare's module-level `VENV_PYTHON`/`CLI` constants.

Also insert this at the top of the function to handle the "already built" resume case — matching the `_should_skip` UX pattern:

```python
# ── Stage 5: Build EPP ─────────────────────────────────────────────────────────

def stage_build_epp(run_dir: Path, run_name: str, namespace: str) -> str:
    """Build EPP image on-cluster, update algorithm_values, re-merge, compile+apply pipelines.

    Asks to reuse if epp_image is already recorded in run_metadata.json.
    Returns the full image reference (e.g. quay.io/me/llm-d:run-name).
    """
    # Resume support: if EPP image already built, ask user to reuse
    meta_path = run_dir / "run_metadata.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        existing = meta.get("epp_image", "")
        if existing:
            print(f"\n  Existing EPP image: {existing}")
            while True:
                choice = input("  Reuse? [Y]es / [n]o rebuild: ").strip().lower()
                if choice in ("", "y", "yes"):
                    info(f"[skip] Build EPP — reusing {existing}")
                    return existing
                if choice in ("n", "no"):
                    break
                print("  Enter 'y' to reuse or 'n' to rebuild.")

    step(5, "Build EPP Image (in-cluster via BuildKit)")

    candidates = list(REPO_ROOT.glob(".claude/skills/sim2real-deploy/scripts/build-epp.sh"))
    if not candidates:
        err("build-epp.sh not found at .claude/skills/sim2real-deploy/scripts/build-epp.sh")
        sys.exit(1)
    build_script = candidates[0]

    result = run(
        ["bash", str(build_script),
         "--run-dir", str(run_dir),
         "--run-name", run_name,
         "--namespace", namespace],
        check=False,
        cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        err("EPP build failed — see output above")
        sys.exit(1)

    meta = json.loads(meta_path.read_text())
    full_image = meta.get("epp_image", "")
    if not full_image:
        err("build-epp.sh completed but epp_image not set in run_metadata.json")
        sys.exit(1)
    ok(f"EPP image: {full_image}")

    # Inject image reference into algorithm_values.yaml
    step("5a", "Injecting image reference into algorithm_values.yaml")
    import yaml
    alg_values_path = run_dir / "prepare_tekton" / "algorithm_values.yaml"
    alg_values = yaml.safe_load(alg_values_path.read_text())

    env_cfg = yaml.safe_load((REPO_ROOT / "config" / "env_defaults.yaml").read_text())
    build_cfg = (env_cfg.get("stack", {}).get("gaie", {})
                        .get("epp_image", {}).get("build", {}))
    epp_hub  = build_cfg.get("hub", "")
    epp_name = build_cfg.get("name", "")
    epp_tag  = full_image.rsplit(":", 1)[1] if ":" in full_image else run_name
    alg_values = _inject_image_reference(alg_values, epp_hub, epp_name, epp_tag)
    alg_values_path.write_text(yaml.dump(alg_values, default_flow_style=False, sort_keys=False))
    ok("algorithm_values.yaml updated")

    # Re-merge values
    step("5b", "Re-merging values")
    values_out = run_dir / "prepare_tekton" / "values.yaml"
    run([VENV_PYTHON, CLI, "merge-values",
         "--env", str(REPO_ROOT / "config" / "env_defaults.yaml"),
         "--algorithm", str(alg_values_path),
         "--out", str(values_out)],
        cwd=REPO_ROOT)
    ok("values.yaml re-merged")

    # Compile and apply Tekton pipeline YAMLs
    step("5c", "Compiling and applying Tekton pipelines")
    pipelines_dir = run_dir / "prepare_tekton" / "pipelines"
    pipelines_dir.mkdir(parents=True, exist_ok=True)
    for phase in ["noise", "baseline", "treatment"]:
        run([VENV_PYTHON, CLI, "compile-pipeline",
             "--template-dir", str(REPO_ROOT / "tektonc-data-collection/tektoncsample/sim2real"),
             "--values", str(values_out),
             "--phase", phase,
             "--out", str(pipelines_dir)],
            cwd=REPO_ROOT)
    for yaml_file in sorted(pipelines_dir.glob("*.yaml")):
        run(["kubectl", "apply", "-f", str(yaml_file), f"-n={namespace}"])
    ok("Tekton pipelines applied to cluster")

    update_run_metadata(run_dir, "prepare", last_completed_step="build_epp")
    return full_image
```

- [ ] **Step 2: Wire `stage_build_epp` into `main()` in prepare.py**

In `main()`, add the EPP build call BEFORE `write_outputs()`. Locate the lines:

```python
        if outer == MAX_OUTER:
            err("Final review still failing after max retries — HALT")
            sys.exit(1)

    write_outputs(run_dir, cfg, stage3_path)
```

Change to:

```python
        if outer == MAX_OUTER:
            err("Final review still failing after max retries — HALT")
            sys.exit(1)

    namespace = os.environ.get("NAMESPACE", cfg.get("namespace", ""))
    if not namespace:
        err("namespace not found in setup_config.json or NAMESPACE env var")
        sys.exit(1)
    stage_build_epp(run_dir, cfg["current_run"], namespace)

    write_outputs(run_dir, cfg, stage3_path)
```

- [ ] **Step 3: Fix the pre-existing bug in `main()` where `cfg["run_name"]` should be `cfg["current_run"]`**

Locate (around line 1453):
```python
    run_dir = REPO_ROOT / "workspace/runs" / cfg["run_name"]
```
Change to:
```python
    run_dir = REPO_ROOT / "workspace/runs" / cfg["current_run"]
```

- [ ] **Step 4: Update `write_outputs()` to include EPP image in the completion output**

In `write_outputs()`, update the metadata artifacts list and the printed artifact listing to reference the EPP image from `run_metadata.json`.

Change the `update_run_metadata` call's `summary` and find the print block that lists artifacts. After the scorer file line, add:

```python
    # Show EPP image if built
    meta_path = run_dir / "run_metadata.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        epp_image = meta.get("epp_image", "")
        if epp_image:
            print(f"  ✓  EPP image: {epp_image}")
```

Also update the summary string in `update_run_metadata`:
```python
    update_run_metadata(
        run_dir, "prepare",
        status="completed",
        completed_at=now,
        summary="Extract, translate, generate, build/test, AI review, and EPP build completed",
        ...
    )
```

And update the "Next:" line at the end of `write_outputs()`:
```python
    print("Next: python scripts/deploy.py  OR  /sim2real-deploy in Claude")
```
(no change needed here — this is already correct)

Note: `stage_build_epp` itself has no unit tests — it has cluster dependencies (kubectl, BuildKit) that require a live cluster to test. Only the pure helper `_inject_image_reference` is unit-tested, which is sufficient.

- [ ] **Step 5: Run the full test suite**

```bash
python -m pytest tests/ -v
```
Expected: all tests pass (no cluster ops touched by tests)

- [ ] **Step 6: Commit**

```bash
git add scripts/prepare.py
git commit -m "feat: move stage_build_epp to prepare.py, wire into main after final review"
```

---

## Chunk 3: Strip EPP build from deploy.py

**Files:**
- Modify: `scripts/deploy.py`
- Modify: `tests/test_deploy.py`

### Task 3: Update deploy.py

- [ ] **Step 1: Remove `_inject_image_reference` from deploy.py**

Delete the entire `_inject_image_reference` function (lines ~81–94 in the current file, under the comment `# ── Pure data helpers (unit-tested) ──────────────────────────────────────────`).

- [ ] **Step 2: Remove `stage_build_epp` from deploy.py**

Delete the entire `stage_build_epp` function (lines ~297–377, from the `# ── Stage 1: Build EPP ──` comment through `return full_image`).

- [ ] **Step 3: Remove `--skip-build-epp` from the argument parser**

Remove these lines from `build_parser()`:
```python
    p.add_argument("--skip-build-epp", action="store_true",
                   help="Skip EPP image build (use if already built this run)")
```

Also remove references to `--skip-build-epp` from the epilog:
- Remove the lines: `python scripts/deploy.py --skip-build-epp  # resume benchmarks (EPP already built)` and `python scripts/deploy.py --skip-build-epp --pr  # resume and create PR`
- Remove `python scripts/deploy.py --skip-build-epp --force-rerun  # re-run all done phases`
- Add a new example line: `python scripts/deploy.py --force-rerun  # re-run all done benchmark phases`

Updated epilog (replace the whole epilog string):
```python
        epilog="""
Examples:
  python scripts/deploy.py                  # benchmarks (no PR)
  python scripts/deploy.py --pr             # benchmarks + PR creation
  python scripts/deploy.py --force-rerun    # re-run already-done benchmark phases

Environment variables:
  NAMESPACE   Override namespace from workspace/setup_config.json
""",
```

- [ ] **Step 4: Update `check_prerequisites` in deploy.py**

The function currently:
1. Checks required Phase 1 artifact files
2. Verifies JSON readable
3. Checks AI review verdict
4. Checks equivalence gate (Suite A + C)
5. Verifies scorer file exists and builds (Go build) — **remove this**
6. Checks registry config (epp_image.build.hub) — **remove this**
7. Returns `(scorer_file, fast_iter)`

After the change:
- Remove the "Scorer file exists and builds" block (the `stage3 = ...`, `scorer_file = ...`, and `go build` subprocess call)
- Remove the registry configuration check block
- **Add** a check that `epp_image` is recorded in `run_metadata.json`
- Change the return to yield `(full_image, fast_iter)` instead of `(scorer_file, fast_iter)` — replacing scorer_file with full_image in the first position.

New check to add (replace the removed blocks, before `ok("All prerequisites satisfied")`):
```python
    # EPP image must be built (prepare.py builds it)
    meta_path = run_dir / "run_metadata.json"
    if not meta_path.exists():
        err("run_metadata.json not found — run python scripts/prepare.py first")
        sys.exit(1)
    meta = json.loads(meta_path.read_text())
    full_image = meta.get("epp_image", "")
    if not full_image:
        err("EPP image not built — run python scripts/prepare.py first")
        sys.exit(1)
    info(f"EPP image: {full_image}")

    # Read fast_iter from env_defaults
    try:
        import yaml
        cfg = yaml.safe_load((REPO_ROOT / "config" / "env_defaults.yaml").read_text())
        fast_iter = bool(cfg.get("pipeline", {}).get("fast_iteration", True))
    except Exception as e:
        err(f"Cannot read config/env_defaults.yaml: {e}")
        sys.exit(1)
```

Update the return statement and function signature:
```python
def check_prerequisites(run_dir: Path) -> tuple[str, bool]:
    """Verify Phase 2 prerequisites: Phase 1 artifacts + EPP image built.

    Returns (full_image, fast_iter).
    Exits 1 on any failure.
    """
    ...
    return full_image, fast_iter
```

- [ ] **Step 5: Update `main()` in deploy.py**

Replace the current EPP handling block:
```python
    scorer_file, fast_iter = check_prerequisites(run_dir)

    if not args.skip_build_epp:
        full_image = stage_build_epp(run_dir, run_name, namespace)
    else:
        meta = json.loads((run_dir / "run_metadata.json").read_text())
        full_image = meta.get("epp_image", "")
        if not full_image:
            err("--skip-build-epp set but no epp_image in run_metadata.json")
            sys.exit(1)
        info(f"Skipping EPP build. Using image: {full_image}")
```

With:
```python
    full_image, fast_iter = check_prerequisites(run_dir)
```

- [ ] **Step 6: Renumber stage steps in deploy.py**

The step annotations now start at Step 1 for benchmarks and Step 2 for PR.

Find `step(2, f"Cluster Benchmarks ...")` — change to `step(1, ...)`.
Find `step(3, "PR Creation")` — change to `step(2, "PR Creation")`.
Find `step(0, "Checking prerequisites")` in `check_prerequisites` — keep as Step 0.

- [ ] **Step 7: Update completion summary in `main()`**

Change:
```python
        summary="Build EPP, benchmarks completed",
```
To:
```python
        summary="Cluster benchmarks completed",
```

Also update the banner description string at the top of `build_parser()`:
```python
        description="sim2real deploy: Cluster Benchmarks → PR",
```

And update the module docstring at line 2:
```python
"""sim2real deploy — Cluster Benchmarks, PR."""
```

- [ ] **Step 8: Update `tests/test_deploy.py` — remove `_inject_image_reference` tests**

Delete the four `_inject_image_reference` test functions from `tests/test_deploy.py` (they now live in `tests/test_prepare.py`):
- `test_inject_image_sets_hub_name_and_tag`
- `test_inject_image_replaces_existing_image`
- `test_inject_image_preserves_other_keys`
- `test_inject_image_creates_missing_nesting`

- [ ] **Step 9: Run the full test suite**

```bash
python -m pytest tests/ -v
```
Expected: all remaining tests pass

- [ ] **Step 10: Commit**

```bash
git add scripts/deploy.py tests/test_deploy.py
git commit -m "feat: remove EPP build from deploy.py; deploy now starts at cluster benchmarks"
```

---

## Chunk 4: Update SKILL.md files

**Files:**
- Modify: `.claude/skills/sim2real-deploy/SKILL.md`
- Modify: `.claude/skills/sim2real-prepare/SKILL.md`

### Task 4: Update skill descriptions and steps

- [ ] **Step 1: Update sim2real-deploy SKILL.md description**

Change the frontmatter `description` block from:
```yaml
description: |
  Phase 2 of the sim2real transfer pipeline. Builds, tests, runs equivalence
  gate, builds EPP image (in-cluster via BuildKit), runs cluster benchmarks,
  and creates PRs. Use after /sim2real-prepare completes successfully.
```
To:
```yaml
description: |
  Phase 2 of the sim2real transfer pipeline. Runs cluster benchmarks and
  creates PRs. Use after /sim2real-prepare completes successfully (EPP image
  must already be built by prepare).
```

- [ ] **Step 2: Update sim2real-deploy SKILL.md prerequisites section**

In the prerequisites bash block, remove:
- The "Scorer file exists and builds" block (lines checking `SCORER_FILE` and running `go build`)
- The "Registry configuration" block (the python3 yaml check for `epp_image.build.hub`)

Add a new prerequisite check for EPP image:
```bash
# EPP image must be built by prepare
EPP_IMAGE=$(python3 -c "import json; print(json.load(open('${RUN_DIR}/run_metadata.json')).get('epp_image',''))")
[ -n "$EPP_IMAGE" ] || { echo "HALT: EPP image not built — run /sim2real-prepare first"; exit 1; }
echo "EPP image: $EPP_IMAGE"
```

- [ ] **Step 3: Remove Stage 1 (Build EPP Image) from sim2real-deploy SKILL.md**

Delete the entire "## Stage 1: Build EPP Image (In-Cluster via BuildKit)" section and all its content. The former Stage 2 (Cluster Benchmarks) becomes Stage 1, and Stage 3 (PR) becomes Stage 2.

Update all stage headings and `step(N, ...)` references accordingly.

- [ ] **Step 4: Update sim2real-prepare SKILL.md description**

Change the frontmatter `description`:
```yaml
description: |
  Phase 1 of the sim2real transfer pipeline. Extracts algorithm metadata,
  translates signals, generates scorer plugin, runs multi-model AI review,
  and builds the EPP image (in-cluster via BuildKit). Use after /sim2real-setup.
  Produces all artifacts needed for /sim2real-deploy.
```

- [ ] **Step 5: Add EPP build stage to sim2real-prepare SKILL.md**

At the end of the SKILL.md, before the completion section, add a "## Stage 5: Build EPP Image" section that references `python scripts/prepare.py` completing this automatically (since it's now part of the script). Keep it brief — the script handles it; the skill just needs to document that it happens.

```markdown
## Stage 5: Build EPP Image (In-Cluster via BuildKit)

`prepare.py` automatically runs this after Final Review passes. It:
- Builds the EPP image on-cluster using `build-epp.sh`
- Injects the image reference into `algorithm_values.yaml`
- Re-merges `values.yaml`
- Compiles and applies Tekton pipeline YAMLs

If `run_metadata.json` already contains `epp_image`, the user is prompted to reuse it.

### Artifact produced
- `run_metadata.json` updated with `epp_image: <hub>/<name>:<tag>`
- `prepare_tekton/algorithm_values.yaml` updated with image reference
- `prepare_tekton/values.yaml` re-merged
- `prepare_tekton/pipelines/*.yaml` applied to cluster
```

- [ ] **Step 6: Commit**

```bash
git add .claude/skills/sim2real-deploy/SKILL.md .claude/skills/sim2real-prepare/SKILL.md
git commit -m "docs: update skill descriptions for EPP build moving to prepare phase"
```

---

## Final verification

- [ ] **Run full test suite one last time**

```bash
python -m pytest tests/ -v
```
Expected: all tests pass

- [ ] **Verify deploy.py no longer references `stage_build_epp` or `skip_build_epp`**

```bash
grep -n "build_epp\|skip_build" scripts/deploy.py
```
Expected: no output

- [ ] **Verify prepare.py contains `stage_build_epp` and `_inject_image_reference`**

```bash
grep -n "def stage_build_epp\|def _inject_image_reference" scripts/prepare.py
```
Expected: both found

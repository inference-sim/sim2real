# Tekton Generation Move to Stage 5 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix issue #19 by moving `merge-values` + PipelineRun stub generation from Stage 3 to Stage 5, so re-running Stage 3 can never wipe the EPP image tag that Stage 4.5 produced.

**Architecture:** Stage 3 generates BLIS-only `algorithm_values.yaml`. Stage 4.75 (Build & Push, formerly 4.5 before PR #44) writes the built EPP tag to a new `workspace/epp_build_state.json` state file (not into `algorithm_values.yaml`). Stage 5 reads both files, runs `merge-values --epp-state`, and generates PipelineRun stubs in a new Step 0 before any validation.

**Tech Stack:** Python 3.10+ stdlib + PyYAML, pytest, JSON Schema (manual validator in `tools/schema_validator.py`)

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `tools/schemas/epp_build_state.schema.json` | **Create** | JSON Schema for new state file |
| `tools/transfer_cli.py` | Modify | Add `--epp-state` to `merge-values`; rewrite `cmd_build_push_epp` post-push |
| `tools/test_transfer_cli.py` | Modify | Tests for new `--epp-state` behavior and new `build-push-epp` output |
| `prompts/generate.md` | Modify | Remove Step 8 Parts D and E |
| `prompts/validate.md` | Modify | Add Step 0 (Tekton Assembly) before Prerequisites |
| `prompts/build-push.md` | Modify | Update between-stage validation and command docs (Stage 4.75 after PR #44) |
| `prompts/transfer.md` | Modify | Update Stage 4.75→5 gate (PR #44 renumbered Build & Push from 4.5 to 4.75) |
| `tools/schemas/stage3_output.schema.json` | Modify | Remove `tekton_artifacts` property |
| `CLAUDE.md` | Modify | Update CLI docs and architecture section |

---

## Task 1: Create `epp_build_state.schema.json`

**Files:**
- Create: `tools/schemas/epp_build_state.schema.json`
- Test: `tools/test_transfer_cli.py` (add to `TestValidateSchema` class or standalone)

- [ ] **Step 1: Write a failing test** that validates a well-formed state file passes schema

```python
# In tools/test_transfer_cli.py — add to a new TestEppBuildStateSchema class
import json, sys, subprocess
from pathlib import Path

TOOLS_DIR = Path(__file__).parent
REPO_ROOT = TOOLS_DIR.parent

class TestEppBuildStateSchema:
    def _run_validate(self, data: dict, tmp_path: Path):
        f = tmp_path / "epp_build_state.json"
        f.write_text(json.dumps(data))
        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "transfer_cli.py"),
             "validate-schema", str(f)],
            capture_output=True, text=True, cwd=str(REPO_ROOT)
        )
        return result.returncode, result.stdout, result.stderr

    def test_valid_state_passes(self, tmp_path):
        rc, out, err = self._run_validate({
            "hub": "ghcr.io/myorg",
            "name": "llm-d-inference-scheduler",
            "tag": "sim2real-abc12345",
            "platform": "linux/amd64",
            "pullPolicy": "Always",
        }, tmp_path)
        assert rc == 0, f"Expected 0, got {rc}. stderr: {err}"

    def test_missing_required_tag_fails(self, tmp_path):
        rc, _, err = self._run_validate(
            {"hub": "ghcr.io/myorg", "name": "llm-d-inference-scheduler"}, tmp_path
        )
        assert rc != 0

    def test_bad_tag_pattern_fails(self, tmp_path):
        rc, _, err = self._run_validate({
            "hub": "ghcr.io/myorg", "name": "x", "tag": "latest"
        }, tmp_path)
        assert rc != 0

    def test_extra_property_fails(self, tmp_path):
        rc, _, err = self._run_validate({
            "hub": "ghcr.io/myorg", "name": "x",
            "tag": "sim2real-abc12345", "unexpected": "field"
        }, tmp_path)
        assert rc != 0
```

- [ ] **Step 2: Run tests — expect FAIL** (schema file doesn't exist yet)

```bash
cd /Users/kalantar/projects/go.workspace/src/github.com/kalantar/sim2real
.venv/bin/python -m pytest tools/test_transfer_cli.py::TestEppBuildStateSchema -v
```
Expected: all 4 tests fail with "Schema not found"

- [ ] **Step 3: Create the schema file**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "EPP Build State",
  "description": "Written by build-push-epp (Stage 4.75). Records the built+pushed EPP image reference for consumption by merge-values --epp-state in Stage 5.",
  "type": "object",
  "required": ["hub", "name", "tag"],
  "additionalProperties": false,
  "properties": {
    "hub":        { "type": "string", "minLength": 1 },
    "name":       { "type": "string", "minLength": 1 },
    "tag":        { "type": "string", "pattern": "^sim2real-[0-9a-f]{8}$" },
    "platform":   { "type": "string" },
    "pullPolicy": { "type": "string", "enum": ["Always", "IfNotPresent", "Never"] }
  }
}
```

Save to: `tools/schemas/epp_build_state.schema.json`

- [ ] **Step 4: Run tests — expect PASS**

```bash
.venv/bin/python -m pytest tools/test_transfer_cli.py::TestEppBuildStateSchema -v
```
Expected: 4 tests pass

- [ ] **Step 5: Commit**

```bash
git add tools/schemas/epp_build_state.schema.json tools/test_transfer_cli.py
git commit -m "feat: add epp_build_state schema for Stage 4.75 EPP image tag persistence"
```

---

## Task 2: Add `--epp-state` to `merge-values`

**Files:**
- Modify: `tools/transfer_cli.py` — `cmd_merge_values` (line ~2177) and argparse (line ~2903)
- Test: `tools/test_transfer_cli.py` — add `TestMergeValuesEppState` class

- [ ] **Step 1: Write failing tests**

```python
# Add to tools/test_transfer_cli.py after TestMergeValuesMissingTestGaps

class TestMergeValuesEppState:
    """Tests for merge-values --epp-state flag."""

    def _write_yaml(self, path, data):
        import yaml
        path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))

    def _minimal_env(self) -> dict:
        return {
            "stack": {
                "gateway": {"helmValues": {"gateway": {"provider": "istio", "gatewayClassName": "istio"}}},
                "gaie": {
                    "epp_image": {
                        # upstream is required for _flatten_gaie_shared to inject any image at all
                        "upstream": {"hub": "ghcr.io/llm-d", "name": "llm-d-inference-scheduler", "tag": "latest", "pullPolicy": "IfNotPresent"},
                        # build.tag is intentionally absent — set by --epp-state in the tests that need it
                        "build":    {"hub": "ghcr.io/myorg", "name": "llm-d-inference-scheduler", "platform": "linux/amd64", "pullPolicy": "Always"},
                    }
                }
            }
        }

    def _minimal_alg(self) -> dict:
        return {
            "stack": {
                "model": {
                    "modelName": "Org/Model",
                    "helmValues": {
                        "modelArtifacts": {"name": "Org/Model", "uri": "pvc://pvc/m"},
                        "decode": {"replicas": 1, "containers": [{"image": "vllm/vllm-openai:v0.11.0"}]},
                    },
                },
                "gaie": {"treatment": {"helmValues": {"inferenceExtension": {"pluginsCustomConfig": {"k": "v"}}}}},
            },
            "observe": {"image": "ghcr.io/inference-sim/blis:v1.0.0", "workloads": [{"name": "w", "spec": "version: '1'"}]},
        }

    def test_epp_state_injects_treatment_tag(self, tmp_path):
        """With --epp-state, treatment phase gets the state file's hub/name/tag."""
        import yaml, json
        env_f = tmp_path / "env.yaml"; self._write_yaml(env_f, self._minimal_env())
        alg_f = tmp_path / "alg.yaml"; self._write_yaml(alg_f, self._minimal_alg())
        out_f = tmp_path / "out.yaml"
        state = {"hub": "ghcr.io/built", "name": "llm-d-inference-scheduler", "tag": "sim2real-deadbeef", "pullPolicy": "Always"}
        state_f = tmp_path / "epp_build_state.json"; state_f.write_text(json.dumps(state))

        rc, out, err = _run_cli(
            "merge-values",
            "--env", str(env_f), "--algorithm", str(alg_f),
            "--epp-state", str(state_f), "--out", str(out_f),
        )
        assert rc == 0, f"Expected 0, got {rc}. stderr: {err}"
        merged = yaml.safe_load(out_f.read_text())
        img = (merged.get("stack", {}).get("gaie", {})
               .get("treatment", {}).get("helmValues", {})
               .get("inferenceExtension", {}).get("image", {}))
        assert img.get("tag") == "sim2real-deadbeef", f"Expected tag sim2real-deadbeef, got: {img}"
        assert img.get("hub") == "ghcr.io/built"

    def test_epp_state_baseline_still_uses_upstream(self, tmp_path):
        """With --epp-state, baseline phase still gets the upstream image (not build)."""
        import yaml, json
        env_f = tmp_path / "env.yaml"; self._write_yaml(env_f, self._minimal_env())
        alg_f = tmp_path / "alg.yaml"; self._write_yaml(alg_f, self._minimal_alg())
        out_f = tmp_path / "out.yaml"
        state_f = tmp_path / "epp_build_state.json"
        state_f.write_text(json.dumps({"hub": "ghcr.io/built", "name": "llm-d-inference-scheduler", "tag": "sim2real-deadbeef"}))

        rc, out, err = _run_cli(
            "merge-values",
            "--env", str(env_f), "--algorithm", str(alg_f),
            "--epp-state", str(state_f), "--out", str(out_f),
        )
        assert rc == 0, err
        merged = yaml.safe_load(out_f.read_text())
        img = (merged.get("stack", {}).get("gaie", {})
               .get("baseline", {}).get("helmValues", {})
               .get("inferenceExtension", {}).get("image", {}))
        assert img.get("hub") == "ghcr.io/llm-d", f"Baseline should use upstream, got: {img}"
        assert img.get("tag") == "latest"

    def test_missing_epp_state_file_exits_2(self, tmp_path):
        """--epp-state pointing to nonexistent file → exit 2."""
        import yaml
        env_f = tmp_path / "env.yaml"; self._write_yaml(env_f, self._minimal_env())
        alg_f = tmp_path / "alg.yaml"; self._write_yaml(alg_f, self._minimal_alg())
        out_f = tmp_path / "out.yaml"
        rc, out, err = _run_cli(
            "merge-values",
            "--env", str(env_f), "--algorithm", str(alg_f),
            "--epp-state", str(tmp_path / "nonexistent.json"), "--out", str(out_f),
        )
        assert rc == 2, f"Expected exit 2, got {rc}"
        assert "ERROR" in err

    def test_malformed_epp_state_exits_2(self, tmp_path):
        """--epp-state pointing to malformed JSON → exit 2."""
        import yaml
        env_f = tmp_path / "env.yaml"; self._write_yaml(env_f, self._minimal_env())
        alg_f = tmp_path / "alg.yaml"; self._write_yaml(alg_f, self._minimal_alg())
        out_f = tmp_path / "out.yaml"
        state_f = tmp_path / "bad.json"; state_f.write_text("{not valid json")
        rc, out, err = _run_cli(
            "merge-values",
            "--env", str(env_f), "--algorithm", str(alg_f),
            "--epp-state", str(state_f), "--out", str(out_f),
        )
        assert rc == 2, f"Expected exit 2, got {rc}"
        assert "ERROR" in err

    def test_without_epp_state_behavior_unchanged(self, tmp_path):
        """Without --epp-state, merge-values behavior is identical to before.
        build.tag not set in env, so treatment falls back to upstream image."""
        import yaml
        env_f = tmp_path / "env.yaml"; self._write_yaml(env_f, self._minimal_env())
        alg_f = tmp_path / "alg.yaml"; self._write_yaml(alg_f, self._minimal_alg())
        out_f = tmp_path / "out.yaml"
        rc, out, err = _run_cli(
            "merge-values",
            "--env", str(env_f), "--algorithm", str(alg_f), "--out", str(out_f),
        )
        assert rc == 0, err
        merged = yaml.safe_load(out_f.read_text())
        # build.tag not set in env (and no --epp-state), so treatment falls back to upstream
        img = (merged.get("stack", {}).get("gaie", {})
               .get("treatment", {}).get("helmValues", {})
               .get("inferenceExtension", {}).get("image", {}))
        assert img.get("hub") == "ghcr.io/llm-d", f"Expected upstream hub, got: {img}"
        assert img.get("tag") == "latest"
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
.venv/bin/python -m pytest tools/test_transfer_cli.py::TestMergeValuesEppState -v
```
Expected: all 5 tests fail (unknown argument `--epp-state`)

- [ ] **Step 3: Add `--epp-state` to argparse** (in `transfer_cli.py` around line 2910)

Find the block:
```python
    p_mv.add_argument("--out", required=True,
                      help="Output path for merged values.yaml")
    p_mv.set_defaults(func=cmd_merge_values)
```

Add after `--out`:
```python
    p_mv.add_argument("--epp-state", dest="epp_state", default=None,
                      help="Optional path to epp_build_state.json; overrides "
                           "epp_image.build in env_defaults with the built tag")
```

- [ ] **Step 4: Add state file loading to `cmd_merge_values`** (after loading `env_data`, before `_deep_merge` at line ~2207)

After the `try/except` that loads `alg_data` (around line 2204), add:

```python
    # Optional: inject EPP build state into env_data to override epp_image.build tag
    epp_state_path = getattr(args, "epp_state", None)
    if epp_state_path:
        p = Path(epp_state_path)
        if not p.exists():
            print(f"ERROR: --epp-state file '{p}' not found.", file=sys.stderr)
            return 2
        try:
            epp_state = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError) as e:
            print(f"ERROR: failed to parse --epp-state file '{p}': {e}", file=sys.stderr)
            return 2
        # Patch env_data so _flatten_gaie_shared picks up the build tag
        build_cfg = (env_data
                     .setdefault("stack", {})
                     .setdefault("gaie", {})
                     .setdefault("epp_image", {})
                     .setdefault("build", {}))
        for key in ("hub", "name", "tag", "pullPolicy"):
            if key in epp_state:
                build_cfg[key] = epp_state[key]
```

Also add `import json` at the top of `cmd_merge_values` (it's not currently imported inside this function — check if it's a module-level import first; if `json` is already imported at module level, skip this).

- [ ] **Step 5: Run tests — expect PASS**

```bash
.venv/bin/python -m pytest tools/test_transfer_cli.py::TestMergeValuesEppState -v
```
Expected: 5 tests pass

- [ ] **Step 6: Run full test suite to confirm no regressions**

```bash
.venv/bin/python -m pytest tools/ -v
```
Expected: all tests pass

- [ ] **Step 7: Commit**

```bash
git add tools/transfer_cli.py tools/test_transfer_cli.py
git commit -m "feat: add --epp-state flag to merge-values for EPP tag injection"
```

---

## Task 3: Rewrite `cmd_build_push_epp` to write state file

**Files:**
- Modify: `tools/transfer_cli.py` — `cmd_build_push_epp` (line ~2270) and argparse (line ~2913)
- Test: `tools/test_transfer_cli.py` — add `TestBuildPushEppStateFile` class

- [ ] **Step 1: Write failing tests**

```python
# Add to tools/test_transfer_cli.py

class TestBuildPushEppStateFile:
    """build-push-epp now writes epp_build_state.json instead of modifying algorithm_values.yaml."""

    def _write_yaml(self, path, data):
        import yaml
        path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))

    def _minimal_env(self, hub="ghcr.io/myorg") -> dict:
        return {
            "stack": {"gaie": {"epp_image": {"build": {
                "hub": hub,
                "name": "llm-d-inference-scheduler",
                "platform": "linux/amd64",
                "pullPolicy": "Always",
            }}}}
        }

    def test_dry_run_writes_no_state_file(self, tmp_path):
        """--dry-run builds but does not push or write state file."""
        env_f = tmp_path / "env.yaml"; self._write_yaml(env_f, self._minimal_env())
        state_f = tmp_path / "epp_build_state.json"
        # We don't actually run the build in unit tests — test that the old
        # --values argument is no longer accepted
        rc, out, err = _run_cli(
            "build-push-epp",
            "--scheduler-dir", "llm-d-inference-scheduler",
            "--env", str(env_f),
            "--values", "workspace/tekton/algorithm_values.yaml",  # old arg — should fail
        )
        assert rc != 0, "Old --values argument should no longer be accepted"

    def test_new_out_arg_accepted_old_values_rejected(self, tmp_path):
        """--values and --merged-values no longer accepted; --out is the new interface."""
        env_f = tmp_path / "env.yaml"; self._write_yaml(env_f, self._minimal_env())

        # Old --values argument should be rejected
        rc_old, _, err_old = _run_cli(
            "build-push-epp",
            "--scheduler-dir", "llm-d-inference-scheduler",
            "--env", str(env_f),
            "--values", "workspace/tekton/algorithm_values.yaml",
        )
        assert rc_old != 0, "Old --values argument should be rejected after refactor"
        assert "unrecognized" in err_old.lower() or "error" in err_old.lower(), (
            f"Expected error about unrecognized --values, got: {err_old}"
        )

        # --out help text should appear (confirms argument is registered)
        rc_help, out_help, _ = _run_cli("build-push-epp", "--help")
        assert rc_help == 0
        assert "--out" in out_help, f"--out not in help output: {out_help}"
        assert "--values" not in out_help, f"Old --values still in help output: {out_help}"
```

Note: Full integration testing of `build-push-epp` requires a real build environment. These unit tests verify the CLI interface contract. The key behavioral test is that `algorithm_values.yaml` is NOT modified.

- [ ] **Step 2: Run tests — expect FAIL**

```bash
.venv/bin/python -m pytest tools/test_transfer_cli.py::TestBuildPushEppStateFile -v
```
Expected: `test_dry_run_writes_no_state_file` passes (old `--values` may still be accepted), `test_new_out_arg_accepted` fails (no `--out` arg yet)

- [ ] **Step 3: Update argparse for `build-push-epp`**

Find in `transfer_cli.py` around line 2913:
```python
    p_bpe.add_argument("--values", required=True,
                       help="Path to workspace/tekton/algorithm_values.yaml")
    p_bpe.add_argument("--merged-values", required=True, dest="merged_values",
                       help="Path to workspace/tekton/values.yaml (output of merge-values)")
```

Replace with:
```python
    p_bpe.add_argument("--out", default="workspace/epp_build_state.json",
                       help="Output path for epp_build_state.json (default: workspace/epp_build_state.json)")
```

- [ ] **Step 4: Rewrite `cmd_build_push_epp` post-push section**

In `cmd_build_push_epp` around line 2283, remove these lines (they reference deleted args):
```python
    algo_path = Path(args.values)
    merged_path = Path(args.merged_values)
```

Also remove the infra check for `algo_path.exists()` (lines 2296-2298).

Replace the entire block from line 2369 to 2416 (inject + re-merge + compile) with:

```python
    # --- Write EPP build state file ---
    state = {
        "hub": hub,
        "name": name,
        "tag": tag,
        "platform": platform,
        "pullPolicy": build_cfg.get("pullPolicy", "Always"),
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(state, indent=2))
    print(f"Wrote EPP build state to {out_path}: {image_ref}")

    print(f"Stage 4.75 complete. EPP image: {image_ref}")
    return 0
```

Add `import json` at the top of `cmd_build_push_epp` (if not already at module level). Check: `json` is used in the function for `json.dumps`. Verify module-level imports include `json` — if yes, the local import inside `cmd_build_push_epp` is not needed.

Also update the `--dry-run` branch (around line 2355-2357):
```python
    if dry_run:
        print(f"--dry-run: skipping push and state file write. Image built: {image_ref}")
        return 0
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
.venv/bin/python -m pytest tools/test_transfer_cli.py::TestBuildPushEppStateFile -v
```

- [ ] **Step 6: Run full test suite**

```bash
.venv/bin/python -m pytest tools/ -v
```
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add tools/transfer_cli.py tools/test_transfer_cli.py
git commit -m "feat: build-push-epp writes epp_build_state.json instead of modifying algorithm_values.yaml"
```

---

## Task 4: Update `prompts/generate.md` — remove Parts D and E

**Files:**
- Modify: `prompts/generate.md`

- [ ] **Step 1: Remove `merge-values` call from Part D and rename section header** (lines 325-342)

Find the section header and merge block:
```markdown
### Part D: Validate and merge

**Validate algorithm_values.yaml:**
...
**Merge to produce values.yaml:**
```bash
# halt_reason: merge_values_failure_stage3
.venv/bin/python tools/transfer_cli.py merge-values \
  --env config/env_defaults.yaml \
  --algorithm workspace/tekton/algorithm_values.yaml \
  --out workspace/tekton/values.yaml \
  || { echo "HALT: merge-values failed"; exit 1; }
```
```

1. Rename the section header from `### Part D: Validate and merge` to `### Part D: Validate`
2. Delete the entire `**Merge to produce values.yaml:**` block (keep only the schema validation block)

- [ ] **Step 2: Remove all of Part E** (lines 344-428)

Delete the entire `### Part E: Validate merged values.yaml` section, including:
- The values.yaml validation Python snippet
- The PipelineRun stub generation
- The `stage3_output.json` `tekton_artifacts` update block
- The final schema validate-schema call

- [ ] **Step 3: Update Step 8 Halt Conditions table**

Remove rows:
- `merge-values fails | merge_values_failure_stage3`
- `tekton_artifacts schema validation | tekton_artifacts_validation_failure_stage3`

- [ ] **Step 4: Update Expected Outputs section** (lines 448-461)

Remove from the bullet list:
- `workspace/tekton/values.yaml — merged Tekton pipeline values (env_defaults + algorithm_values)`
- `workspace/tekton/pipelinerun-{noise,baseline,treatment}.yaml — PipelineRun stubs`
- `tekton_artifacts: values_yaml path and pipeline_stubs array` from `workspace/stage3_output.json` description

Change the `stage3_output.json` description to:
```
- `workspace/stage3_output.json` — stage output artifact with:
  - `scorer_file`: path to generated scorer
  - `test_file`: path to generated test file
  - `register_file`: path to register.go
  - `scorer_type`: the TypedName type string
- `workspace/tekton/algorithm_values.yaml` — BLIS-derived Tekton values (generated by Step 8)
```

- [ ] **Step 5: Verify generate.md still reads correctly**

Read through Step 8 to confirm it ends cleanly after schema validation:
```
Part A → Part B → Part C → Part D (schema validate only) → done
```

- [ ] **Step 6: Commit**

```bash
git add prompts/generate.md
git commit -m "refactor: remove merge-values and PipelineRun generation from Stage 3 Step 8"
```

---

## Task 5: Update `prompts/validate.md` — add Step 0

> **PR #44 context:** `validate.md` was significantly restructured — Stage 5 no longer runs Suites A/B/C directly (they moved to new Stage 4.5 Equivalence Gate). Stage 5 now reads `workspace/equivalence_results.json` from Stage 4.5. The frontmatter already has `workspace/equivalence_results.json` in `inputs:`. The Prerequisites section now checks `equivalence_results.json` at lines 51-65. Our Step 0 insertion point (before `## Prerequisites`, line 22) is unchanged.

**Files:**
- Modify: `prompts/validate.md`

- [ ] **Step 1: Insert Step 0 before the Prerequisites section**

Insert the following immediately before `## Prerequisites` (line 22):

```markdown
## Step 0: Assemble Tekton Artifacts

Generate `workspace/tekton/values.yaml` and PipelineRun stubs from BLIS and EPP build state.
This step runs once at the start of every Stage 5 invocation and is **idempotent** — safe
to re-run on Stage 5 REENTER (exit-3) because `merge-values` is deterministic.
PipelineRun stubs (Step 0d) are also re-generated on every invocation — this is safe because
`$PIPELINERUN_NAME` remains a literal placeholder until `render-pipelinerun` at submit time.
Do not manually fill in stub fields between REENTER exits; Step 0d will overwrite them.

### Step 0a: Verify preconditions

```bash
# EPP build state must exist and be schema-valid
test -f workspace/epp_build_state.json \
  || { echo "HALT: workspace/epp_build_state.json missing — run Stage 4.75 (build-push-epp) first"; exit 1; }
.venv/bin/python tools/transfer_cli.py validate-schema workspace/epp_build_state.json \
  || { echo "HALT: epp_build_state.json schema validation failed"; exit 1; }

# BLIS algorithm values must exist and be schema-valid
.venv/bin/python tools/transfer_cli.py validate-schema workspace/tekton/algorithm_values.yaml \
  || { echo "HALT: algorithm_values.yaml missing or invalid — re-run Stage 3 Step 8 first"; exit 1; }
```

**HALT if any check fails.**

### Step 0b: Run merge-values

```bash
.venv/bin/python tools/transfer_cli.py merge-values \
  --env config/env_defaults.yaml \
  --algorithm workspace/tekton/algorithm_values.yaml \
  --epp-state workspace/epp_build_state.json \
  --out workspace/tekton/values.yaml \
  || { echo "HALT: merge-values failed"; exit 1; }
```

### Step 0c: Validate merged values.yaml

```bash
.venv/bin/python -c "
import yaml
v = yaml.safe_load(open('workspace/tekton/values.yaml'))
required = ['stack', 'observe']
for k in required:
    assert k in v, f'missing key: {k}'
assert 'image' in v['observe'], 'missing observe.image'
assert '<TAG>' not in v['observe']['image'], 'unresolved <TAG> in observe.image'
assert v['observe'].get('noise_runs'), 'missing observe.noise_runs'
wl = v['observe'].get('workloads', [])
assert len(wl) > 0, 'observe.workloads must be non-empty'
assert v['stack'].get('model', {}).get('modelName'), 'missing stack.model.modelName'
gw = v['stack'].get('gateway', {}).get('helmValues', {}).get('gateway', {})
assert gw.get('provider'), 'missing stack.gateway.helmValues.gateway.provider'
assert gw.get('gatewayClassName'), 'missing stack.gateway.helmValues.gateway.gatewayClassName'
for phase in ('baseline', 'treatment'):
    hv = v['stack'].get('gaie', {}).get(phase, {}).get('helmValues', {})
    assert hv, f'missing stack.gaie.{phase}.helmValues'
    pcc = hv.get('inferenceExtension', {}).get('pluginsCustomConfig', {})
    assert pcc, f'missing stack.gaie.{phase}.helmValues.inferenceExtension.pluginsCustomConfig'
# Treatment must have a custom EPP image tag (confirms Stage 4.75 ran)
treatment_img = (v.get('stack', {}).get('gaie', {}).get('treatment', {})
                 .get('helmValues', {}).get('inferenceExtension', {}).get('image', {}))
assert treatment_img.get('tag', '').startswith('sim2real-'), \
    f'treatment EPP tag does not look like a sim2real build: {treatment_img.get(\"tag\")!r}'
print('OK: merged values.yaml valid')
"
```

**HALT if any assertion fails.**

### Step 0d: Generate PipelineRun stubs

Generate `workspace/tekton/pipelinerun-{noise,baseline,treatment}.yaml`. Each stub:

```yaml
apiVersion: tekton.dev/v1
kind: PipelineRun
metadata:
  name: $PIPELINERUN_NAME
  namespace: $NAMESPACE
  labels:
    sim2real-phase: $PHASE
spec:
  pipelineRef:
    name: sim2real-<phase>
  taskRunTemplate:
    serviceAccountName: helm-installer
  params:
    - name: experimentId
      value: $PIPELINERUN_NAME
    - name: namespace
      value: $NAMESPACE
  workspaces:
    - name: model-cache
      persistentVolumeClaim:
        claimName: model-pvc
    - name: hf-credentials
      secret:
        secretName: hf-secret
    - name: data-storage
      persistentVolumeClaim:
        claimName: data-pvc
```

Write three files with `$PHASE` set to `noise`, `baseline`, and `treatment` respectively,
and `$PIPELINERUN_NAME` left as a literal placeholder (resolved by `render-pipelinerun` at submit time).

---
```

- [ ] **Step 2: Update the frontmatter `inputs:` list**

PR #44 already added `workspace/equivalence_results.json` to `inputs:`. Add the two new inputs our Step 0 requires:
```yaml
  - workspace/epp_build_state.json
  - workspace/tekton/algorithm_values.yaml
```

- [ ] **Step 3: Verify the file reads correctly**

Confirm the flow is: Step 0 → Prerequisites → Fast-Iteration Check → Step 1/Step 2...

- [ ] **Step 4: Commit**

```bash
git add prompts/validate.md
git commit -m "feat: add Stage 5 Step 0 to assemble tekton artifacts from algorithm_values + epp_build_state"
```

---

## Task 6: Update `prompts/build-push.md` — between-stage validation

> **PR #44 context:** `build-push.md` was renumbered from Stage 4.5 to Stage 4.75. The file structure is unchanged except for the stage number in the frontmatter/heading and a new equivalence gate prerequisite. The Step 2 invocation (lines 89-94) and between-stage validation (lines 116-130) still use the old `--values`/`--merged-values` interface — these are the blocks we change.

**Files:**
- Modify: `prompts/build-push.md`

- [ ] **Step 1: Update Step 2 command documentation** (lines 96-103)

Replace the numbered list describing what the command does:
```markdown
The command:
1. Reads `epp_image.build.{hub, name, platform}` from `config/env_defaults.yaml`
2. Generates a tag `sim2real-<git-sha>` from the `llm-d-inference-scheduler` HEAD commit
3. Builds the image (cross-compiles to `linux/amd64` by default — correct for target clusters even on arm64 Mac)
4. Pushes to `<hub>/llm-d-inference-scheduler:<tag>`
5. Injects the image reference into `workspace/tekton/algorithm_values.yaml` under `stack.gaie.treatment.helmValues.inferenceExtension.image`
6. Re-runs `merge-values` to regenerate `workspace/tekton/values.yaml`
7. Re-compiles all three phase pipeline YAMLs
```

With:
```markdown
The command:
1. Reads `epp_image.build.{hub, name, platform}` from `config/env_defaults.yaml`
2. Generates a tag `sim2real-<git-sha>` from the `llm-d-inference-scheduler` HEAD commit
3. Builds the image (cross-compiles to `linux/amd64` by default — correct for target clusters even on arm64 Mac)
4. Pushes to `<hub>/llm-d-inference-scheduler:<tag>`
5. Writes the image reference to `workspace/epp_build_state.json` (consumed by `merge-values --epp-state` in Stage 5 Step 0)
```

- [ ] **Step 2: Update the Step 2 CLI invocation** (lines 89-94)

Replace:
```bash
.venv/bin/python tools/transfer_cli.py build-push-epp \
  --scheduler-dir llm-d-inference-scheduler \
  --env config/env_defaults.yaml \
  --values workspace/tekton/algorithm_values.yaml \
  --merged-values workspace/tekton/values.yaml
```

With:
```bash
.venv/bin/python tools/transfer_cli.py build-push-epp \
  --scheduler-dir llm-d-inference-scheduler \
  --env config/env_defaults.yaml
```

- [ ] **Step 3: Update the dry-run invocation** (lines 108-113)

Replace:
```bash
.venv/bin/python tools/transfer_cli.py build-push-epp \
  --scheduler-dir llm-d-inference-scheduler \
  --env config/env_defaults.yaml \
  --values workspace/tekton/algorithm_values.yaml \
  --merged-values workspace/tekton/values.yaml \
  --dry-run
```

With:
```bash
.venv/bin/python tools/transfer_cli.py build-push-epp \
  --scheduler-dir llm-d-inference-scheduler \
  --env config/env_defaults.yaml \
  --dry-run
```

- [ ] **Step 4: Replace the between-stage validation block** (lines 116-130)

Replace the entire `## Between-stage Validation` block with:

```markdown
## Between-stage Validation

```bash
# Verify EPP build state file was written
test -f workspace/epp_build_state.json \
  || { echo "HALT: workspace/epp_build_state.json missing — did build-push-epp complete?"; exit 1; }

.venv/bin/python -c "
import json, sys
d = json.load(open('workspace/epp_build_state.json'))
if not d.get('hub') or not d.get('tag'):
    print('HALT: epp_build_state.json is missing hub or tag fields'); sys.exit(1)
if not d['tag'].startswith('sim2real-'):
    print(f'HALT: epp_build_state.json tag does not look like a sim2real build: {d[\"tag\"]!r}'); sys.exit(1)
print(f\"EPP image: {d['hub']}/{d['name']}:{d['tag']}\")
" || exit 1
```

**HALT if any validation fails.** Do not proceed to Stage 5.
```

- [ ] **Step 5: Commit**

```bash
git add prompts/build-push.md
git commit -m "refactor: build-push.md uses epp_build_state.json for between-stage validation"
```

---

## Task 7: Update `prompts/transfer.md` — Stage 4.75→5 gate

> **PR #44 context:** `transfer.md` was updated to include the new Stage 4.5 Equivalence Gate and renumber Build & Push to Stage 4.75. The Stage 4.75→5 between-stage validation is now at lines 196-212. It still checks `algorithm_values.yaml` — this is what we change.

**Files:**
- Modify: `prompts/transfer.md`

- [ ] **Step 1: Replace the between-stage validation block** (lines 196-212)

Find this block under `### Stage 4.75: Build & Push EPP Image`:
```markdown
**Between-stage validation:**

```bash
# Verify treatment image reference is set in algorithm_values.yaml
.venv/bin/python -c "
import yaml, sys
d = yaml.safe_load(open('workspace/tekton/algorithm_values.yaml'))
img = (d.get('stack',{}).get('gaie',{}).get('treatment',{})
        .get('helmValues',{}).get('inferenceExtension',{}).get('image',{}))
if not img.get('hub') or not img.get('tag'):
    print('HALT: treatment EPP image not set'); sys.exit(1)
print(f\"EPP image: {img['hub']}/{img['name']}:{img['tag']}\")
" || { echo "HALT: Stage 4.75 validation failed"; exit 1; }

# Verify values.yaml was regenerated
test -f workspace/tekton/values.yaml || { echo "HALT: workspace/tekton/values.yaml missing"; exit 1; }
```
```

Replace with:
```markdown
**Between-stage validation:**

```bash
# Verify EPP build state was written by build-push-epp
test -f workspace/epp_build_state.json \
  || { echo "HALT: workspace/epp_build_state.json missing — run Stage 4.75 first"; exit 1; }
.venv/bin/python -c "
import json, sys
d = json.load(open('workspace/epp_build_state.json'))
if not d.get('hub') or not d.get('tag'):
    print('HALT: treatment EPP image not set in epp_build_state.json'); sys.exit(1)
print(f\"EPP image: {d['hub']}/{d['name']}:{d['tag']}\")
" || { echo "HALT: Stage 4.75 validation failed"; exit 1; }
```
```

- [ ] **Step 2: Commit**

```bash
git add prompts/transfer.md
git commit -m "refactor: transfer.md Stage 4.75→5 gate checks epp_build_state.json"
```

---

## Task 8: Update `stage3_output.schema.json`

**Files:**
- Modify: `tools/schemas/stage3_output.schema.json`

> **Migration note:** The schema has `additionalProperties: false`. Removing `tekton_artifacts` from `properties` means any existing `workspace/stage3_output.json` that contains the key will fail schema validation. This is a pipeline-breaker for anyone mid-run. The migration gate below must run before the schema change is committed.

- [ ] **Step 1: Migration gate — verify no stale artifact exists**

```bash
# If workspace/stage3_output.json exists, check for stale tekton_artifacts key
if [ -f workspace/stage3_output.json ]; then
  .venv/bin/python -c "
import json, sys
d = json.load(open('workspace/stage3_output.json'))
if 'tekton_artifacts' in d:
    print('STOP: workspace/stage3_output.json contains stale tekton_artifacts key.')
    print('Action required: re-run Stage 3 to regenerate stage3_output.json without this key, then retry Task 8.')
    sys.exit(1)
print('OK: stage3_output.json is clean')
" || exit 1
fi
```

**STOP if the check fails.** Re-run Stage 3 (or manually remove the `tekton_artifacts` key from `workspace/stage3_output.json` if you are certain the stale artifact is acceptable), then retry.

- [ ] **Step 2: Remove `tekton_artifacts` property** (lines 29-45)

The current schema defines `tekton_artifacts` as optional (not in `required`). Remove it:
- Delete lines 29-45 (`"tekton_artifacts": { ... }`)
- Stage 3 no longer writes this key, so the schema should reject it

- [ ] **Step 3: Run schema validator tests**

```bash
.venv/bin/python -m pytest tools/test_schema_validator.py -v
.venv/bin/python -m pytest tools/ -v
```
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add tools/schemas/stage3_output.schema.json
git commit -m "refactor: remove tekton_artifacts from stage3_output schema (moved to Stage 5)"
```

---

## Task 9: Update `CLAUDE.md`

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update `merge-values` CLI example**

Find the current example (around the `merge-values` section). Add `--epp-state` with a note:
```bash
# Merge env_defaults + algorithm_values + EPP build state → values.yaml (Stage 5 Step 0)
python tools/transfer_cli.py merge-values \
  --env config/env_defaults.yaml \
  --algorithm workspace/tekton/algorithm_values.yaml \
  --epp-state workspace/epp_build_state.json \
  --out workspace/tekton/values.yaml
```

Add note: `# --epp-state is optional; when provided, overrides epp_image.build in env_defaults with the tag from Stage 4.75`

- [ ] **Step 2: Update `build-push-epp` CLI example**

Replace the old invocation (with `--values` and `--merged-values`) with:
```bash
python tools/transfer_cli.py build-push-epp \
  --scheduler-dir llm-d-inference-scheduler \
  --env config/env_defaults.yaml
# Writes workspace/epp_build_state.json with {hub, name, tag} for Stage 5 consumption
# --out workspace/epp_build_state.json  (default; override if needed)
```

- [ ] **Step 3: Update Two-Layer Tekton Config Architecture section**

Update to describe the three-input merge model:
- Layer 1: `config/env_defaults.yaml` (infrastructure defaults)
- Layer 2: `workspace/tekton/algorithm_values.yaml` (BLIS-derived values, generated by Stage 3 Step 8)
- Layer 3: `workspace/epp_build_state.json` (EPP build output, generated by Stage **4.75** — PR #44 renumbered Build & Push from 4.5 to 4.75) — injected via `--epp-state`

Note: `merge-values` now runs in Stage 5 Step 0 (not Stage 3 Step 8).

- [ ] **Step 4: Update "Fixing Pipeline Issues" section**

Add:
- `workspace/epp_build_state.json` is generated by Stage 4.75 (`prompts/build-push.md`) — re-run `build-push-epp` to regenerate

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for three-input merge-values and Stage 5 tekton assembly"
```

---

## Verification

After all tasks are complete, run this end-to-end smoke test:

```bash
# 1. Full test suite must pass
.venv/bin/python -m pytest tools/ -v

# 2. validate-schema on the new state file schema
echo '{"hub":"ghcr.io/kalantar","name":"llm-d-inference-scheduler","tag":"sim2real-abc12345","pullPolicy":"Always"}' \
  > /tmp/epp_build_state.json
.venv/bin/python tools/transfer_cli.py validate-schema /tmp/epp_build_state.json
# Expected: exit 0, JSON output with "status": "ok"

# 3. merge-values with --epp-state injects correct tag
.venv/bin/python tools/transfer_cli.py merge-values \
  --env config/env_defaults.yaml \
  --algorithm workspace/tekton/algorithm_values.yaml \
  --epp-state /tmp/epp_build_state.json \
  --out /tmp/merged_test.yaml
# Expected: exit 0
python3 -c "
import yaml
v = yaml.safe_load(open('/tmp/merged_test.yaml'))
tag = v['stack']['gaie']['treatment']['helmValues']['inferenceExtension']['image']['tag']
assert tag == 'sim2real-abc12345', f'Wrong tag: {tag}'
print('PASS: treatment tag injected correctly')
"

# 4. merge-values without --epp-state still works (backward compat)
.venv/bin/python tools/transfer_cli.py merge-values \
  --env config/env_defaults.yaml \
  --algorithm workspace/tekton/algorithm_values.yaml \
  --out /tmp/merged_no_state.yaml
# Expected: exit 0 (treatment falls back to upstream since build.tag not set)

# 5. build-push-epp no longer accepts --values
.venv/bin/python tools/transfer_cli.py build-push-epp --help
# Confirm: --values and --merged-values NOT in help output; --out IS present

# 6. generate.md no longer references merge-values
grep -n "merge-values" prompts/generate.md
# Expected: no matches

# 7. validate.md contains Step 0
grep -n "Step 0" prompts/validate.md
# Expected: match found

# 8. stage3_output.schema.json no longer has tekton_artifacts
grep "tekton_artifacts" tools/schemas/stage3_output.schema.json
# Expected: no matches
```

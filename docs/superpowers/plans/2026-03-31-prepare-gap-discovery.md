# Prepare Pipeline Gap Discovery Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add LLM-driven gap discovery to Extract and Translate stages so human reviewers see what the algorithm *doesn't* use.

**Architecture:** Expand LLM prompts to report unused attributes/capabilities alongside their primary task. New optional schema fields store the gaps. `human_gate` displays them when non-empty. No new files — all changes are additions to existing files.

**Tech Stack:** Python 3.10+, JSON Schema (draft 2020-12), pytest

**Spec:** `docs/superpowers/specs/2026-03-31-prepare-gap-discovery-design.md`

---

## File Structure

No new files created. All changes modify existing files:

| File | Responsibility |
|------|----------------|
| `tools/schemas/algorithm_summary.schema.json` | Add `unused_source_attributes` optional array |
| `tools/schemas/signal_coverage.schema.json` | Add `production_gaps` optional array |
| `scripts/lib/gates.py` | `human_gate` gains `gaps` parameter, displays before menu |
| `scripts/prepare.py` (~line 267) | Extract review prompt: add check #5 |
| `scripts/prepare.py` (~line 293) | Extract gate call: read gaps, pass to `human_gate` |
| `scripts/prepare.py` (~line 574) | Translate review prompt: add check #6 |
| `scripts/prepare.py` (~line 601) | Translate gate call: read gaps, pass to `human_gate` |
| `prompts/extract-full.md` (~line 66) | Add Section 5: report unused source attributes |
| `prompts/translate.md` (~line 157) | Add Step 9: report production gaps |
| `tests/test_gates.py` | Add gap display tests, fix stale LLM error test |

---

## Chunk 1: Schema + Gate Changes

### Task 1: Add `unused_source_attributes` to algorithm_summary schema

**Files:**
- Modify: `tools/schemas/algorithm_summary.schema.json:143` (before closing `}`)
- Test: `tests/test_gates.py` (schema validation added in Task 4)

- [ ] **Step 1: Add the field to the schema**

In `tools/schemas/algorithm_summary.schema.json`, add this property after `cross_file_deps` (before the final closing braces):

```json
    "unused_source_attributes": {
      "type": "array",
      "description": "Optional. Source-file attributes not referenced by the EVOLVE-BLOCK.",
      "items": {
        "type": "object",
        "required": ["name", "type", "location", "description"],
        "additionalProperties": false,
        "properties": {
          "name": { "type": "string" },
          "type": { "type": "string", "description": "Go type if determinable, otherwise 'unknown'" },
          "location": { "type": "string", "description": "file:line location in source" },
          "description": { "type": "string" }
        }
      }
    }
```

- [ ] **Step 2: Verify existing schema validation still works**

Run:
```bash
.venv/bin/python tools/transfer_cli.py validate-schema workspace/algorithm_summary.json
```
Expected: PASS (field is optional, existing artifacts don't have it)

- [ ] **Step 3: Commit**

```bash
git add tools/schemas/algorithm_summary.schema.json
git commit -m "schema: add optional unused_source_attributes to algorithm_summary"
```

---

### Task 2: Add `production_gaps` to signal_coverage schema

**Files:**
- Modify: `tools/schemas/signal_coverage.schema.json:106` (before closing `}`)

- [ ] **Step 1: Add the field to the schema**

In `tools/schemas/signal_coverage.schema.json`, add this property after `helper_translations` (before the final closing braces):

```json
    "production_gaps": {
      "type": "array",
      "description": "Optional. Production capabilities not mapped by this algorithm.",
      "items": {
        "type": "object",
        "required": ["name", "category", "production_location", "description"],
        "additionalProperties": false,
        "properties": {
          "name": { "type": "string", "description": "Capability name" },
          "category": {
            "type": "string",
            "enum": ["header", "metric", "crd_field", "plugin_type", "config_option"]
          },
          "production_location": { "type": "string", "description": "File path within llm-d-inference-scheduler" },
          "description": { "type": "string" }
        }
      }
    }
```

- [ ] **Step 2: Verify existing schema validation still works**

Run:
```bash
.venv/bin/python tools/transfer_cli.py validate-schema workspace/signal_coverage.json
```
Expected: PASS (field is optional)

- [ ] **Step 3: Commit**

```bash
git add tools/schemas/signal_coverage.schema.json
git commit -m "schema: add optional production_gaps to signal_coverage"
```

---

### Task 3: Add `gaps` parameter to `human_gate` and display logic

**Files:**
- Modify: `scripts/lib/gates.py:120-184`
- Test: `tests/test_gates.py`

- [ ] **Step 1: Write the failing test — gaps displayed when non-empty**

Add to `tests/test_gates.py`:

```python
def test_human_gate_displays_gaps(monkeypatch, capsys):
    """When gaps are non-empty, they are displayed before the gate menu."""
    monkeypatch.setattr("builtins.input", lambda _: "d")
    review = ReviewResult(passed=True, verdicts={})
    gaps = [
        {"name": "SessionID", "type": "string", "description": "Session affinity identifier"},
        {"name": "SLOClass", "type": "string", "description": "SLO tier classification"},
    ]
    result = human_gate(
        stage_name="Extract",
        artifact_paths=[Path("/tmp/test.json")],
        ai_review=review,
        context_for_chat=[],
        gaps=gaps,
    )
    captured = capsys.readouterr()
    assert "Sim-side gaps" in captured.out
    assert "SessionID" in captured.out
    assert "SLOClass" in captured.out
    assert result.approved is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
.venv/bin/python -m pytest tests/test_gates.py::test_human_gate_displays_gaps -v
```
Expected: FAIL — `human_gate() got an unexpected keyword argument 'gaps'`

- [ ] **Step 3: Write the failing test — gaps not displayed when None**

Add to `tests/test_gates.py`:

```python
def test_human_gate_no_gaps_when_none(monkeypatch, capsys):
    """When gaps is None, no gap section is displayed."""
    monkeypatch.setattr("builtins.input", lambda _: "d")
    review = ReviewResult(passed=True, verdicts={})
    result = human_gate(
        stage_name="Extract",
        artifact_paths=[Path("/tmp/test.json")],
        ai_review=review,
        context_for_chat=[],
        gaps=None,
    )
    captured = capsys.readouterr()
    assert "unused" not in captured.out.lower()
    assert result.approved is True
```

- [ ] **Step 4: Write the failing test — gaps not displayed when empty list**

Add to `tests/test_gates.py`:

```python
def test_human_gate_no_gaps_when_empty(monkeypatch, capsys):
    """When gaps is an empty list, no gap section is displayed."""
    monkeypatch.setattr("builtins.input", lambda _: "d")
    review = ReviewResult(passed=True, verdicts={})
    result = human_gate(
        stage_name="Extract",
        artifact_paths=[Path("/tmp/test.json")],
        ai_review=review,
        context_for_chat=[],
        gaps=[],
    )
    captured = capsys.readouterr()
    assert "unused" not in captured.out.lower()
    assert "production" not in captured.out.lower()
    assert result.approved is True
```

- [ ] **Step 5: Implement `gaps` parameter and display logic in `human_gate`**

In `scripts/lib/gates.py`, modify `human_gate`:

1. Add parameter to signature (after `repo_root`):
```python
def human_gate(
    stage_name: str,
    artifact_paths: list[Path],
    ai_review: ReviewResult,
    context_for_chat: list[Path],
    repo_root: Path | None = None,
    gaps: list[dict] | None = None,
) -> GateResult:
```

2. Add gap display after the AI review summary block (after the `print()` following `ai_review.summary_lines()`) and before the importance message:

```python
    # Display gaps if present
    if gaps:
        if stage_name == "Extract":
            print("  Sim-side gaps (attributes available but unused by algorithm):")
        else:
            print("  Production-side gaps (available in llm-d but unused by algorithm):")
        for g in gaps:
            name = g.get("name", "?")
            gtype = g.get("type") or g.get("category", "")
            desc = g.get("description", "")
            if gtype:
                print(f"    - {name} ({gtype}) — {desc}")
            else:
                print(f"    - {name} — {desc}")
        print()
        if stage_name == "Extract":
            print("  These source-file attributes exist but are NOT referenced by the EVOLVE-BLOCK.")
            print("  If any are important to the algorithm's behavior, the extraction may be incomplete.")
        else:
            print("  These production capabilities are NOT covered by this algorithm's translation.")
            print("  If any are critical to your use case, the generated plugin may be incomplete.")
        print()
```

- [ ] **Step 6: Run all gate tests**

Run:
```bash
.venv/bin/python -m pytest tests/test_gates.py -v
```
Expected: New gap tests PASS. The stale `test_review_artifacts_llm_error_treated_as_incomplete` test will FAIL (addressed in Task 4).

- [ ] **Step 7: Commit**

```bash
git add scripts/lib/gates.py tests/test_gates.py
git commit -m "feat(gates): add gaps display to human_gate"
```

---

### Task 4: Fix stale LLM error test and add schema validation tests

**Files:**
- Modify: `tests/test_gates.py`

- [ ] **Step 1: Fix the stale LLM error non-vote test**

The test `test_review_artifacts_llm_error_treated_as_incomplete` (line 46) asserts `result.passed is False`, but the implementation now treats LLM errors as non-votes (2 successful "complete" responses → passes). Fix:

```python
def test_review_artifacts_llm_error_is_non_vote():
    """LLM errors are non-votes — remaining successful reviews decide."""
    from lib.llm import LLMError
    mock_responses = {
        "Azure/gpt-4o": json.dumps({"verdict": "complete", "issues": [], "suggestions": []}),
        "GCP/gemini-2.5-flash": LLMError("timeout"),
        "aws/claude-opus-4-6": json.dumps({"verdict": "complete", "issues": [], "suggestions": []}),
    }
    with patch("lib.gates.call_models_parallel", return_value=mock_responses):
        result = review_artifacts(
            artifact_paths=[Path("/tmp/algo.json")],
            review_prompt="Review these artifacts",
            models=["Azure/gpt-4o", "GCP/gemini-2.5-flash", "aws/claude-opus-4-6"],
        )
    assert result.passed is True
    assert result.verdicts["GCP/gemini-2.5-flash"]["verdict"] == "error"
```

- [ ] **Step 2: Run all tests**

Run:
```bash
.venv/bin/python -m pytest tests/test_gates.py -v
```
Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_gates.py
git commit -m "fix(tests): update LLM error test to match non-vote behavior"
```

---

## Chunk 2: Prepare.py Integration + Prompt Changes

### Task 5: Wire gaps into Extract gate in `prepare.py`

**Files:**
- Modify: `scripts/prepare.py:267-300`

- [ ] **Step 1: Add check #5 to the extract review prompt**

In `scripts/prepare.py`, find the review_prompt string at ~line 267. Add a 5th check:

```python
    review_prompt = (
        "Review this algorithm_summary.json for completeness. Check that:\n"
        "1. All signals referenced in the EVOLVE-BLOCK are captured\n"
        "2. Signal types and access paths are correct\n"
        "3. Cross-file dependencies (type_refs, helper_refs) are captured if present\n"
        "4. The evolve_block_source line range matches the actual markers\n"
        "5. unused_source_attributes captures relevant attributes from the source file"
        " that the EVOLVE-BLOCK does not reference\n"
    )
```

- [ ] **Step 2: Read gaps from artifact and pass to `human_gate`**

Find the `human_gate` call at ~line 293. Before it, read the gap field from the artifact. Then pass it:

```python
        # Read gaps from artifact for display
        _artifact = json.loads(out.read_text())
        _extract_gaps = _artifact.get("unused_source_attributes")

        gate_result = human_gate(
            stage_name="Extract",
            artifact_paths=[out],
            ai_review=ai_review,
            context_for_chat=[source_file],
            repo_root=REPO_ROOT,
            gaps=_extract_gaps,
        )
```

(`json` is already imported at line 5 of `prepare.py`.)

- [ ] **Step 3: Commit**

```bash
git add scripts/prepare.py
git commit -m "feat(prepare): wire extract gaps into human gate"
```

---

### Task 6: Wire gaps into Translate gate in `prepare.py`

**Files:**
- Modify: `scripts/prepare.py:574-615`

- [ ] **Step 1: Add check #6 to the translate review prompt**

In `scripts/prepare.py`, find the review_prompt string at ~line 574. Add a 6th check:

```python
    review_prompt = (
        "Review this signal_coverage.json for completeness. Check that:\n"
        "1. All signals from algorithm_summary are mapped to production equivalents\n"
        "2. prod_access_path expressions are valid Go code paths\n"
        "3. fidelity_rating reflects actual mapping quality\n"
        "4. context_notes explain how each signal is used in the algorithm\n"
        "5. type_mappings and helper_translations are present if applicable\n"
        "6. production_gaps captures relevant production capabilities that are NOT"
        " mapped by this algorithm\n"
    )
```

- [ ] **Step 2: Read gaps from artifact and pass to `human_gate`**

Find the `human_gate` call at ~line 601. Before it, read the gap field from the artifact. Then pass it:

```python
        # Read gaps from artifact for display
        _artifact = json.loads(out.read_text())
        _translate_gaps = _artifact.get("production_gaps")

        gate_result = human_gate(
            stage_name="Translate",
            artifact_paths=[out],
            ai_review=ai_review,
            context_for_chat=[algo_summary_path, source_file],
            repo_root=REPO_ROOT,
            gaps=_translate_gaps,
        )
```

- [ ] **Step 3: Commit**

```bash
git add scripts/prepare.py
git commit -m "feat(prepare): wire translate gaps into human gate"
```

---

### Task 7: Update Extract LLM prompt

**Files:**
- Modify: `prompts/extract-full.md` (after Section 4, before `## Important`)

- [ ] **Step 1: Add Section 5 to the extract prompt**

Insert after line 65 (end of Section 4) and before `## Important` (line 67):

```markdown
### 5. Report Unused Source Attributes

Scan the full source file (the file containing the EVOLVE-BLOCK) for class attributes,
state variables, configuration parameters, and fields on key types that exist in the
source file but are NOT referenced within the EVOLVE-BLOCK.

**Scope:** Only scan the file that contains the EVOLVE-BLOCK. Do not scan other files
in the repository.

For each, report in the `unused_source_attributes` array:
- `name`: attribute or field name
- `type`: Go type if determinable, otherwise `"unknown"`
- `location`: `"file:line"` location in the source
- `description`: brief description of what it represents

If no unused attributes are found, omit the field or set it to an empty array.

```

- [ ] **Step 2: Commit**

```bash
git add prompts/extract-full.md
git commit -m "prompt(extract): add Section 5 — report unused source attributes"
```

---

### Task 8: Update Translate LLM prompt

**Files:**
- Modify: `prompts/translate.md` (after Step 8, before `## Halt Conditions`)

- [ ] **Step 1: Add Step 9 to the translate prompt**

Insert after line 156 (end of Step 8) and before `## Halt Conditions` (line 158):

```markdown
## Step 9: Report Production Gaps

As you navigated the production codebase (`llm-d-inference-scheduler/`) to map signals,
note any metrics fields, request headers, CRD spec fields, or plugin types you encountered
that are NOT needed by this algorithm.

**Scope:** Only report capabilities you encountered while navigating the production
codebase to complete the signal mapping. This is not an exhaustive audit — report what
you naturally discovered during your mapping work. Focus on the scorer plugin interface,
endpoint metrics, request metadata, and CRD spec fields.

For each, report in the `production_gaps` array:
- `name`: capability name
- `category`: one of `"header"`, `"metric"`, `"crd_field"`, `"plugin_type"`, `"config_option"`
- `production_location`: file path within `llm-d-inference-scheduler/`
- `description`: brief description of the capability

If no production gaps are found, omit the field or set it to an empty array.

```

- [ ] **Step 2: Commit**

```bash
git add prompts/translate.md
git commit -m "prompt(translate): add Step 9 — report production gaps"
```

---

### Task 9: Final verification

- [ ] **Step 1: Run all tests**

```bash
.venv/bin/python -m pytest tests/test_gates.py -v
```
Expected: ALL PASS

- [ ] **Step 2: Run schema validation on existing artifacts**

```bash
.venv/bin/python tools/transfer_cli.py validate-schema workspace/algorithm_summary.json
.venv/bin/python tools/transfer_cli.py validate-schema workspace/signal_coverage.json
```
Expected: Both PASS (new fields are optional)

- [ ] **Step 3: Commit any remaining changes**

If any fixups were needed, commit them.

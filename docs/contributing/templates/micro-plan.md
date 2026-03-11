You are operating inside a real repository with full code access,
including submodules that reference external systems.

You are tasked with producing a PR-SPECIFIC IMPLEMENTATION PLAN for a
cross-system pipeline project (sim2real). This plan combines:
1. Design rigor (behavioral contracts, cross-system validation)
2. Executable task breakdown (language-appropriate testing, bite-sized steps)

The source of work is a section in the approved Macro Plan:
  docs/plans/2026-03-06-sim2real-transfer-macro-plan-v3.md

This plan has TWO AUDIENCES:
1) A human reviewer who validates behavioral correctness
2) Automated agents (via executing-plans skill) who execute the tasks

The plan must be comprehensive enough that agents can implement WITHOUT
additional codebase exploration.

======================================================================
KEY DIFFERENCES FROM STANDARD MICRO PLAN TEMPLATE
======================================================================

This template adapts the standard micro-plan.md for sim2real's
mixed-language, artifact-heavy, cross-system nature:

1. LANGUAGE FLEXIBILITY: Tasks may be Python (CLI), Go (harness),
   Markdown (artifacts), JSON (schemas), or prompt templates.
   No single language assumed.

2. PR CATEGORIES: Each PR is categorized per pr-workflow.md
   (Artifact / Pipeline Stage / Validation / Integration).
   The category determines review depth and verification gate.

3. ARTIFACT TASKS: Pure documentation/artifact PRs (mapping docs,
   scorer templates, prompt templates) use an author-validate-commit
   cycle instead of TDD.

4. CROSS-SYSTEM AUDIT: Phase 0 checks submodule APIs, commit pins,
   and workspace artifact schema chains instead of Go struct
   construction sites.

5. SELF-AUDIT DIMENSIONS: Phase 8 uses the 6 sim2real self-audit
   dimensions from pr-workflow.md instead of inference-sim antipattern
   rules R1-R20.

======================================================================
DOCUMENT HEADER (REQUIRED)
======================================================================

Every plan MUST start with this exact header format:

```markdown
# [PR Title] Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** [One sentence a non-contributor could understand — what capability does this PR add?]

**The problem today:** [2-3 sentences explaining what's missing or broken without this PR.]

**What this PR adds:** [Numbered list of 2-4 concrete capabilities.]

**Why this matters:** [1-2 sentences connecting this PR to the broader pipeline vision.]

**Architecture:** [2-3 sentences about the technical approach — languages, components, integration points.]

**PR Category:** [Artifact | Pipeline Stage | Validation | Integration] (per docs/contributing/pr-workflow.md)

**Source:** Macro plan PR[N]: [section title] in docs/plans/2026-03-06-sim2real-transfer-macro-plan-v3.md

**Behavioral Contracts:** See Part 1, Section B below

---
```

======================================================================
PHASE 0 — CROSS-SYSTEM DEPENDENCY AUDIT (MANDATORY)
======================================================================

Before planning implementation, audit all cross-system dependencies:

1) SUBMODULE API VERIFICATION
   For each submodule referenced by this PR:
   - Run `git submodule status` and record commit hashes
   - Read the actual API signatures the PR depends on
   - Compare against what the macro plan documents
   - Flag any DEVIATION (API changed, method renamed, etc.)

2) WORKSPACE ARTIFACT CHAIN VERIFICATION
   For each workspace artifact this PR produces or consumes:
   - Identify the Writer stage and Reader stage(s) from the macro plan
   - Verify required fields match between writer output and reader input
   - Check that JSON schema files (tools/schemas/) match the field list

3) PREDECESSOR ARTIFACT VERIFICATION
   For each artifact this PR requires from a previous PR:
   - Verify it exists (if the predecessor PR has merged)
   - If predecessor hasn't merged, document the dependency explicitly
   - Check that file paths match what this PR will reference

4) COMMIT PIN VERIFICATION
   - Record current submodule HEADs
   - Compare against commit pins in mapping artifact (if it exists)
   - Flag stale pins as DEVIATION

List confirmed facts (with file:line citations or git output).
Flag anything from the macro plan that doesn't match current code
as a DEVIATION — these must be resolved before implementation begins.

======================================================================
OUTPUT FORMAT (STRICT)
======================================================================

--- PART 1: Design Validation (Human Review, target <120 lines) ---

A) Executive Summary (5-10 lines)
   - What this PR builds (plain language)
   - Where it fits in the pipeline (what comes before, what depends on it)
   - PR category and why
   - Any DEVIATION flags from Phase 0

B) Behavioral Contracts (Phase 1)
   - 3-15 named contracts (BC-1, BC-2, ...)
   - Format: GIVEN / WHEN / THEN / MECHANISM
   - Grouped: positive contracts, negative contracts, error handling
   - For artifact PRs: contracts describe artifact properties, not code behavior
     (e.g., "GIVEN the mapping artifact WHEN all signals are listed
     THEN each signal has type, metric path, staleness window, and fidelity rating")

C) Component Interaction (Phase 2)
   - Component diagram (text)
   - Cross-system data flow (source system → artifact → target system)
   - Workspace artifact production/consumption

D) Deviation Log (Phase 3)
   - Compare micro plan vs macro plan
   - Table: | Macro Plan Says | Micro Plan Does | Reason |

E) Review Guide (Phase 7-B)
   - The tricky part
   - What to scrutinize
   - What's safe to skim
   - Known debt

--- PART 2: Executable Implementation (Agent Execution) ---

F) Implementation Overview (Phase 4 summary)
   - Files to create/modify (one-line each)
   - Key decisions
   - Confirmation: no dead artifacts, all files consumed

G) Task Breakdown (Phase 4 detailed)
   - 4-12 tasks using appropriate task variant (see Phase 4 below)
   - Continuous execution (no pause points between tasks)
   - Each task uses the variant matching its deliverable type

H) Test Strategy (Phase 6)
   - Map contracts to tasks/tests
   - Per-language test approach
   - Cross-system invariant verification

I) Risk Analysis (Phase 7-A)
   - Risks with likelihood/impact/mitigation

--- PART 3: Quality Assurance ---

J) Sanity Checklist (Phase 8)
   - Pre-implementation verification
   - All items from Phase 8 template (sim2real self-audit dimensions)

--- APPENDIX: File-Level Implementation Details ---

K) Detailed specifications
   - Complete file contents for artifacts (.md, .json schemas)
   - Complete function signatures for code (.py, .go)
   - Prompt template full text
   - Any behavioral subtleties (file:line citations)

======================================================================
PHASE 1 — BEHAVIORAL CONTRACTS (Human-Reviewable)
======================================================================

This defines what this PR guarantees. Use named contracts (BC-1, BC-2, ...)
that can be referenced in tests, reviews, and future PRs.

For each contract:

  BC-N: <Name>
  - GIVEN <precondition>
  - WHEN <action>
  - THEN <observable outcome>
  - MECHANISM: <one sentence explaining how> (optional but recommended)

Group contracts into:

1) Behavioral Contracts (what MUST happen)
   - For code: normal operation, edge cases
   - For artifacts: required content, structural properties
   - For prompts: required sections, halt conditions

2) Negative Contracts (what MUST NOT happen)
   - Cross-system contract violations
   - Schema chain breaks
   - Stale commit pin references

3) Error Handling Contracts
   - CLI: exit code 0/1/2 semantics
   - Pipeline: halt conditions and user decision points
   - Artifacts: what happens when validation fails

TARGET: 3-15 contracts per PR. Artifact PRs may have as few as 3.

No vague wording. "Should" is banned — use "MUST" or "MUST NOT."

THEN CLAUSE QUALITY GATE:
Every THEN clause must describe OBSERVABLE BEHAVIOR or VERIFIABLE
PROPERTY, not internal structure.

Check each THEN clause against this filter:
- Does it reference an internal implementation detail? → Rewrite
  BAD:  "THEN the JSON has a `_version` field"
  GOOD: "THEN the schema validates successfully against the published schema"
- Is it verifiable by an external observer? → Keep
  GOOD: "THEN `transfer_cli.py validate-mapping` exits with code 0"
- Does it survive a refactor? → If renaming a function or reorganizing
  files would invalidate this THEN, it is structural

======================================================================
PHASE 2 — COMPONENT INTERACTION (Human-Reviewable)
======================================================================

Describe this PR's place in the cross-system pipeline.

1) Component Diagram (text-based)
   - This PR's component and its responsibility
   - Adjacent components (existing or new)
   - Data flow: source system → artifact → target system
   - Workspace artifact flow between pipeline stages

2) API / Artifact Contracts
   - For code: function signatures, CLI commands, JSON output schemas
   - For artifacts: required sections, field definitions
   - For prompts: prerequisite artifacts, halt conditions, outputs

3) Cross-System Dependencies
   - Submodule APIs this PR depends on (with commit pins)
   - Workspace artifacts consumed (with required fields)
   - Workspace artifacts produced (with field definitions)

4) Dead Artifact Check
   - For each file this PR creates, identify the consumer
   - If no consumer exists in this PR or a planned future PR, justify

TARGET: under 40 lines. PRs touching multiple cross-system boundaries
may go up to 60 lines with justification.

======================================================================
PHASE 3 — DEVIATION LOG
======================================================================

Compare this micro plan against the macro plan section for this PR.

For each difference:

| Macro Plan Says | Micro Plan Does | Reason |
|-----------------|-----------------|--------|

Categories of deviation:
- SIMPLIFICATION: Macro plan specified more than needed at this stage
- CORRECTION: Macro plan was wrong about existing code, API, or behavior
- DEFERRAL: Feature moved to a later PR (explain why)
- ADDITION: Something the macro plan missed
- API_CHANGE: Submodule API changed since macro plan was written

If there are zero deviations, state "No deviations from macro plan."

======================================================================
PHASE 4 — EXECUTABLE TASK BREAKDOWN
======================================================================

Break implementation into 4-12 tasks. Each task uses ONE of three
variants depending on the deliverable type.

**Execution is continuous** — all tasks run sequentially without pausing
for human input. Execution only stops on test failure, validation failure,
or build error.

----------------------------------------------------------------------
VARIANT A: CODE TASK (Python CLI or Go harness)
----------------------------------------------------------------------

### Task N: [Component/Feature Name]

**Contracts Implemented:** BC-X, BC-Y
**Language:** Python | Go

**Files:**
- Create: `exact/path/to/file.py`
- Modify: `exact/path/to/existing.py`
- Test: `exact/path/to/test_file.py`

**Step 1: Write failing test**

Context: [1-2 sentences]

```python
# Complete test code (Python example)
def test_component_scenario_behavior():
    """BC-X: [contract name]."""
    # GIVEN [precondition]
    # WHEN [action]
    # THEN [expected outcome]
    assert result == expected
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_file.py::test_component_scenario_behavior -v`
Expected: FAIL

**Step 3: Implement minimal code**

Context: [1-2 sentences]

```python
# Complete implementation
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_file.py::test_component_scenario_behavior -v`
Expected: PASS

**Step 5: Run full test suite for this PR category**

For Python: `python -m pytest tests/ -v`
For Go: `go test ./tools/harness/... -v && go build ./tools/harness/...`

**Step 6: Commit**

```bash
git add <files>
git commit -m "$(cat <<'EOF'
feat(tools): implement component (BC-X, BC-Y)

- Add component with behavior
- Implement contract BC-X: [brief]

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

----------------------------------------------------------------------
VARIANT B: ARTIFACT TASK (mapping docs, scorer templates, schemas)
----------------------------------------------------------------------

### Task N: [Artifact Name]

**Contracts Implemented:** BC-X, BC-Y

**Files:**
- Create: `docs/transfer/artifact_name.md`
- Create: `tools/schemas/artifact.schema.json` (if applicable)

**Step 1: Author artifact**

Context: [1-2 sentences explaining the artifact's purpose and consumers]

Complete artifact content in the Appendix (Section K). Reference it here.

**Step 2: Validate cross-references**

For mapping artifacts:
Run: `python tools/transfer_cli.py validate-mapping` (if CLI exists)
Or manual: Verify each signal name appears in both source and target systems.

For JSON schemas:
Run: `python tools/transfer_cli.py validate-schema --schema <schema> --artifact <artifact>`
Or manual: Verify required fields match the macro plan's workspace artifact table.

For scorer templates (.go.md):
Manual: Extract code blocks, compile against submodule HEAD:
```bash
# Extract and compile (specific commands depend on template structure)
go build ./tools/harness/...
```

**Step 3: Verify no dead artifacts**

For each file created, confirm at least one consumer exists:
- A test that reads it
- A later PR that references it
- The pipeline runtime

**Step 4: Commit**

```bash
git add <files>
git commit -m "$(cat <<'EOF'
docs(transfer): add artifact name (BC-X)

- Create artifact with [content summary]
- Validates against [consumer]

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

----------------------------------------------------------------------
VARIANT C: PROMPT TEMPLATE TASK
----------------------------------------------------------------------

### Task N: [Prompt Template Name]

**Contracts Implemented:** BC-X, BC-Y

**Files:**
- Create: `prompts/stage_name.md`

**Step 1: Author prompt template**

Context: [1-2 sentences explaining which pipeline stage this drives]

Complete prompt content in the Appendix (Section K). Reference it here.

**Step 2: Verify structural completeness**

Check that the prompt contains all 4 required sections:
- [ ] Prerequisites: which workspace artifacts must exist
- [ ] Validation steps: how to verify stage output
- [ ] Halt conditions: when to stop with explicit trigger and action
- [ ] Expected outputs: file names, JSON fields, format constraints

**Step 3: Verify predecessor artifact checks**

The prompt must instruct the LLM to validate predecessor artifacts
before reading them (schema check + file existence).

**Step 4: Commit**

```bash
git add <files>
git commit -m "$(cat <<'EOF'
docs(prompts): add stage_name prompt template (BC-X)

- Stage N prompt with prerequisites, validation, halt conditions
- Consumes: [predecessor artifacts]
- Produces: [output artifacts]

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

----------------------------------------------------------------------
TASK DESIGN RULES (ALL VARIANTS)
----------------------------------------------------------------------

1. **Each task implements 1-3 related contracts** — don't split single
   contracts across tasks, don't pack unrelated contracts together

2. **Complete content in every step** — no "add the mapping" or
   "implement the logic" without showing the exact content

3. **Exact commands with expected output** — agent should know if
   verification succeeded or failed

4. **Dependency ordering** — tasks must be ordered so each can build
   on previous completed work

5. **No dead artifacts** — every file, every schema, every prompt must
   be consumed by the end of this PR or a documented future PR

6. **Commit messages** — use conventional commits format with contract
   references (feat/fix/docs/test)

======================================================================
PHASE 5 — REMOVED (Merged into Phase 4 Task Verification)
======================================================================

Exercisability is proven by the task-level verification steps.

======================================================================
PHASE 6 — TEST STRATEGY
======================================================================

Map contracts to tasks and tests:

| Contract | Task | Test Type | Verification |
|----------|------|-----------|-------------|
| BC-1     | Task 1 | Unit (Python) | pytest test_extract.py |
| BC-2     | Task 2 | Artifact validation | validate-mapping CLI |
| BC-3     | Task 3 | Structural check | prompt has 4 sections |
| BC-4     | Task 4 | Unit (Go) | go test harness |
| ...      | ...    | ...       | ...         |

Test types:
- Unit (Python): pytest test for CLI command behavior
- Unit (Go): go test for harness/compilation behavior
- Artifact validation: CLI or manual validation of document properties
- Schema validation: JSON schema structural check
- Structural check: prompt template completeness verification
- Integration: cross-component or end-to-end pipeline test
- Compilation: go build / code block extraction and compilation

sim2real invariants (reference these where applicable):
- **Schema chain integrity:** For each workspace artifact, the writer's
  output fields are a superset of the reader's required fields
- **CLI exit code contract:** 0 = success, 1 = validation failure (halt),
  2 = infrastructure error (retry)
- **Signal name consistency:** Each signal name is identical across
  mapping artifact, JSON schemas, prompt templates, CLI code, and README
- **Prompt completeness:** Every prompt template has prerequisites,
  validation steps, halt conditions, and expected outputs
- **Workspace artifact ownership:** Each artifact has exactly one writer
  per pipeline run; no stage mutates another stage's artifact
- **Commit pin freshness:** Submodule HEAD matches documented commit pin

======================================================================
PHASE 7 — RISK ANALYSIS & REVIEW GUIDE
======================================================================

PART A: Risks

For each risk:
- Risk description
- Likelihood (low/medium/high)
- Impact (low/medium/high)
- Mitigation (specific test, validation step, or design choice)
- Which task mitigates the risk

PART B: Review Guide (for the human reviewer)

In 5-10 lines, tell the reviewer:

1) THE TRICKY PART: What's the most subtle or error-prone aspect?
   (For artifact PRs: which cross-references are most fragile?)
2) WHAT TO SCRUTINIZE: Which contract(s) are hardest to verify?
3) WHAT'S SAFE TO SKIM: Which parts are mechanical/boilerplate?
4) KNOWN DEBT: Any pre-existing issues encountered but not fixed?

======================================================================
PHASE 8 — SELF-AUDIT CHECKLIST
======================================================================

Before implementation, verify against the 6 sim2real self-audit
dimensions (from docs/contributing/pr-workflow.md):

**Dimension 1: Cross-system accuracy**
- [ ] All submodule API references match actual code
- [ ] Commit pins are current (`git submodule status` matches docs)
- [ ] No stale references to APIs that have been renamed or removed

**Dimension 2: Schema chain integrity**
- [ ] Each workspace artifact's output fields match consuming stage's input
- [ ] JSON schema files match the macro plan's workspace artifact table
- [ ] Writer → Reader chains traced and verified

**Dimension 3: Prompt completeness**
- [ ] Every prompt template specifies: prerequisites, validation steps,
      halt conditions, expected outputs
- [ ] Predecessor artifact checks are included in each prompt

**Dimension 4: CLI contract**
- [ ] All CLI commands produce documented JSON schemas
- [ ] Exit codes are consistent (0 = success, 1 = validation failure,
      2 = infrastructure error)
- [ ] Error messages are actionable

**Dimension 5: Artifact consistency**
- [ ] Signal names match across mapping artifact, schemas, prompts, CLI, README
- [ ] Field names match across workspace artifact schemas and code
- [ ] File paths referenced in documents exist or will be created

**Dimension 6: Dead artifact prevention**
- [ ] Every file created by this PR has an identified consumer
- [ ] No orphan schemas, no unreferenced prompts, no unused artifacts

**Additional checks:**
- [ ] PR category correctly identified (Artifact / Pipeline Stage /
      Validation / Integration)
- [ ] Verification gate matches PR category (per pr-workflow.md)
- [ ] No feature creep beyond macro plan scope
- [ ] Deviation log reviewed — no unresolved deviations
- [ ] Each task produces working, verifiable output (no scaffolding)
- [ ] Task dependencies are correctly ordered
- [ ] All contracts are mapped to specific tasks

======================================================================
APPENDIX — FILE-LEVEL IMPLEMENTATION DETAILS
======================================================================

This section has NO LENGTH LIMIT. It should contain everything needed
to implement the PR without further codebase exploration.

For each file to be created or modified, provide the appropriate detail:

**For Python files (`tools/*.py`, `tests/*.py`):**

```python
# Complete implementation with all imports, functions, classes
# Include docstrings for public functions
# Include type hints
```

**For Go files (`tools/harness/*.go`):**

```go
// Complete implementation with all imports, types, functions
// Include doc comments for exported symbols
```

**For Markdown artifacts (`docs/transfer/*.md`):**

Complete document content with all sections, tables, and references.

**For JSON schemas (`tools/schemas/*.schema.json`):**

```json
{
  "Complete JSON schema with all fields, types, required arrays"
}
```

**For prompt templates (`prompts/*.md`):**

Complete prompt text with all 4 required sections:
- Prerequisites
- Validation steps
- Halt conditions
- Expected outputs

**Key Implementation Notes per file:**
- Cross-system dependencies: [which submodule APIs are called?]
- Workspace artifacts: [which are read? which are written?]
- Error handling: [exit codes, halt conditions, error messages]

======================================================================
EXECUTION HANDOFF
======================================================================

After creating the plan, the workflow continues with:

**Option 1: Subagent-Driven Development (in current session)**
- Invoke superpowers:subagent-driven-development
- Fresh subagent per task
- Code review between tasks

**Option 2: Worktree with executing-plans (recommended for complex PRs)**
- Create isolated worktree (superpowers:using-git-worktrees)
- Invoke superpowers:executing-plans with this plan
- Continuous execution (stops only on failure)
- Invoke superpowers:finishing-a-development-branch when complete

======================================================================
QUALITY BAR
======================================================================

This plan must:
- Survive expert review (behavioral contracts are sound)
- Survive cross-system scrutiny (API refs verified, schema chains valid)
- Eliminate dead artifacts (all files consumed)
- Reduce implementation bugs (tests, validation, verification gates)
- Stay strictly within macro plan scope (deviations justified)
- Match PR category verification gate (per pr-workflow.md)
- Enable automated execution (complete content, exact commands)
- Map every contract to a task (traceability)

======================================================================
VERIFICATION GATES (per PR category)
======================================================================

After all tasks complete, run the verification gate matching the
PR category (from docs/contributing/pr-workflow.md):

**Artifact PRs:**
```bash
python tools/transfer_cli.py validate-mapping  # if mapping exists
python tools/transfer_cli.py validate-schema --schema <schema> --artifact <artifact>
# Manual: extract code blocks from .go.md, compile against submodule HEAD
```

**Pipeline Stage PRs:**
```bash
python -m pytest tests/
go build ./tools/harness/...  # if harness code exists
# Manual: verify prompt template YAML front-matter
```

**Validation PRs:**
```bash
python -m pytest tests/
go test ./tools/harness/...
go build ./tools/harness/...
```

**Integration PRs:**
```bash
python tools/transfer_cli.py extract routing/
python -m pytest tests/
go test ./tools/harness/...
go build ./tools/harness/...
```

======================================================================

Think carefully.
Inspect deeply.
Verify cross-system dependencies.
Break into executable tasks.
Verify every step.
Direct the reviewer's attention wisely.

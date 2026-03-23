# PR6: Stage 6 (PR Creation) + Pipeline Self-Verification + Calibration

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Complete the sim-to-production transfer pipeline by adding the Stage 6 prompt (PR creation), a CLI command for safe calibration log append, and a known-answer test that verifies the harness framework end-to-end with a synthetic algorithm.

**The problem today:** `prompts/pr.md` does not exist. Running Stage 5 to completion leaves the operator with no guided path for pushing branches, creating PRs in llm-d repos, or recording calibration data. The calibration log has a template but no safe append mechanism. There is no end-to-end regression test for the harness framework itself.

**What this PR adds:**
1. `prompts/pr.md` — Stage 6 prompt template: prerequisites, branch push, PR creation via `gh`, calibration log append, halt conditions.
2. `transfer_cli.py append-calibration-log` — atomic append with corruption detection (entry count + byte-exact verification).
3. `tools/harness/known_answer_test.go` + `tools/harness/testdata/known_answer_expected.json` — known-answer test: synthetic algorithm (`score = queue_depth / 100.0`) verified against hand-computed fixture.
4. CLAUDE.md + `docs/transfer/README.md` updated to mark PR6 complete and document deliverables.

**Why this matters:** Without Stage 6, the pipeline cannot create PRs even after a PASS verdict — the validated scorer stays on a local branch. PR6 closes the loop: validated scorer → PR in llm-d repos → calibration entry.

**Architecture:** Prompt-driven (Stage 6 prompt), Python CLI (append-calibration-log), Go harness unit test. Stage 6 reads `workspace/validation_results.json` and `workspace/transfer_evidence.md` (written by PR5), then orchestrates `gh pr create` for both llm-d submodule working copies. The known-answer test is a pure unit test with no workspace dependencies.

**PR Category:** Integration (per `docs/contributing/pr-workflow.md`) — touches prompt templates, Python CLI, Go harness, and documentation; end-to-end pipeline closure.

**Source:** Macro plan PR6: "Stage 6 (PR creation) + pipeline self-verification + calibration" in `docs/plans/2026-03-06-sim2real-transfer-macro-plan-v3.md`

**Behavioral Contracts:** See Part 1, Section B below

---

## PHASE 0 — Cross-System Dependency Audit

### Submodule Status

```
 319d1b701ab92fd37458d2f2b4106c0ff2c428d6 inference-sim (v0.6.12-6-g319d1b7)
 111c6ff8e4995268c570dab2193a69112f2e9202 llm-d-benchmark (v0.5.0-1-g111c6ff)
 4cd7046e2cf9121be6cdc2fc0815dbeeba721c9f llm-d-inference-scheduler (v0.4.0-rc.1-128-g4cd7046)
+ee6e27c0b0ce8dc4f8fd8ece0865ff8112be3e7e tektonc-data-collection (heads/main)
```

### Workspace Artifact Chain Verification

PR6 consumes but does not write new workspace artifacts:

| Artifact | Writer | PR6 Reads? | Required Fields |
|----------|--------|-----------|-----------------|
| `workspace/algorithm_summary.json` | Stage 1 CLI | Yes | `algorithm_name`, `overall_verdict` (via validation_results), `evolve_block_source` |
| `workspace/signal_coverage.json` | Stage 2 prompt | No (indirectly via evidence) | — |
| `workspace/validation_results.json` | Stage 5 prompt | Yes | `overall_verdict`, `suite_a`, `suite_b`, `suite_c`, `benchmark`, `noise_cv` |
| `workspace/transfer_evidence.md` | Stage 5 `generate-evidence` | Yes | body text |
| `workspace/stage3_output.json` | Stage 3 prompt | No | — (scorer already committed in submodule working copy by Stage 4) |

Schema files:
- `tools/schemas/validation_results.schema.json` — already exists (PR5). ✅
- `tools/schemas/algorithm_summary.schema.json` — already exists (PR1). ✅

### Predecessor Artifact Verification

- `prompts/validate.md` (PR5) — exists. `validate.md` Step 7 references `prompts/pr.md` and correctly notes it is a PR6 deliverable. ✅
- `docs/transfer/calibration_log.md` — exists with template (PR5). Schema documented in file. ✅
- `tools/transfer_cli.py generate-evidence` — exists and functional (PR5). PR6 adds `append-calibration-log` as a companion command. ✅

### Commit Pin Verification

Macro plan Section G documented `b9a4a82e...` for llm-d-inference-scheduler; submodule has since advanced to `4cd7046e2cf9121be6cdc2fc0815dbeeba721c9f` (v0.4.0-rc.1-128-g4cd7046). The mapping artifact (PR1) has not changed; no re-verification of signal names required — only the recorded pin is updated here. ✅

### DEVIATIONS

| # | Macro Plan Says | Actual | Category |
|---|-----------------|--------|----------|
| D1 | "Smoke test: trivial algorithm runs through all 6 stages" | PR6 implements a Go unit test (`TestKnownAnswer`) using a `syntheticAlgorithm` struct — not a full interactive 6-stage run. A full smoke run requires cluster access and is performed manually post-merge. | SIMPLIFICATION |
| D2 | Stage 6 appends calibration log (prompt handles append) | PR6 adds `append-calibration-log` CLI command for the append — the prompt instructs the operator to call this command rather than editing the log manually. | ADDITION — safer than manual LLM edit |
| D3 | Macro plan lists no `append-calibration-log` CLI command | `append-calibration-log` added to support the corruption detection requirements in macro plan Section D.7. | ADDITION — derived from macro plan requirements |

---

## PART 1: Design Validation

### A) Executive Summary

PR6 is the final PR in the sim2real build phase. It adds:
- **`prompts/pr.md`** — Stage 6 prompt that reads `workspace/validation_results.json`, checks overall_verdict, pushes scored branches to both llm-d submodule working copies, creates PRs via `gh pr create`, and appends a calibration log entry via CLI.
- **`transfer_cli.py append-calibration-log`** — Reads `workspace/algorithm_summary.json` and `workspace/validation_results.json`, builds a calibration entry, appends to `docs/transfer/calibration_log.md`, and verifies corruption-free append (count before/after + byte-exact last-entry check).
- **Known-answer test** — `TestKnownAnswer` in `tools/harness/known_answer_test.go` uses a `syntheticAlgorithm` (score = QueueDepth/100) and verifies per-endpoint scores against a committed fixture.
- **Documentation** — CLAUDE.md pipeline table and `docs/transfer/README.md` updated.

**What comes before:** PR5 delivers `prompts/validate.md`, Suite A/B/C harness, and `workspace/validation_results.json`. PR5's Step 7 already references `prompts/pr.md` as a PR6 deliverable.

**What depends on this:** Nothing in the build phase. At runtime, pipeline users invoke `prompts/pr.md` to complete a transfer.

**DEVIATION flags:** D1 (smoke test is a Go unit test, not a full interactive run), D2/D3 (calibration log append via CLI command, not manual LLM edit).

---

### B) Behavioral Contracts

#### Positive Contracts

**BC-1: PASS verdict gates PR creation**
- GIVEN `workspace/validation_results.json` with `overall_verdict: "PASS"`
- WHEN Stage 6 prompt prerequisites are verified
- THEN Stage 6 proceeds to branch push and PR creation
- MECHANISM: prompts/pr.md Step 1 reads and checks `overall_verdict` before any git or gh operations

**BC-2: INCONCLUSIVE requires operator_notes**
- GIVEN `workspace/validation_results.json` with `overall_verdict: "INCONCLUSIVE"`
- WHEN `operator_notes` field is present and non-empty
- THEN Stage 6 proceeds to branch push and PR creation with a warning in the PR description
- MECHANISM: prompts/pr.md Step 1 checks `operator_notes` if verdict is INCONCLUSIVE; halts if absent

**BC-3: Known-answer test: per-endpoint score accuracy**
- GIVEN a `syntheticAlgorithm` with `score = float64(snap.QueueDepth) / 100.0` and 3 test tuples with QueueDepth values `[0, 50, 100]`, `[25, 75]`, `[33, 67]`
- WHEN `RunTuples(syntheticAlg, tuples)` executes
- THEN each endpoint's score in `SimScores` matches the corresponding value in `testdata/known_answer_expected.json` within 1e-6 absolute tolerance per endpoint
- MECHANISM: `TestKnownAnswer` loads fixture, runs `RunTuples`, iterates per-endpoint comparison

**BC-4: Known-answer fixture is reviewer-verifiable**
- GIVEN `testdata/known_answer_expected.json`
- WHEN a reviewer applies `score = QueueDepth / 100.0` to the minimum (0), maximum (100), and median (50) queue depth values in the fixture
- THEN computed scores 0.0, 1.0, 0.5 exactly match the committed fixture values
- MECHANISM: fixture values are exact IEEE-754 fractions for QueueDepth 0, 50, 100 (multiples of 50); 25/75 are also exact (0.25 = 1/4, 0.75 = 3/4). Values 0.33 and 0.67 are not exact binary fractions but differ from `float64(33)/100.0` by < 1e-15, well within the 1e-6 tolerance.

**BC-5: Calibration log append is corruption-safe**
- GIVEN `docs/transfer/calibration_log.md` exists (with N existing entries) and workspace artifacts are valid
- WHEN `transfer_cli.py append-calibration-log` runs
- THEN entry count increases by exactly 1 AND the last entry byte-exactly matches the appended content; exit 0
- MECHANISM: CLI counts `### Transfer:` occurrences before append, appends, re-reads, verifies count+1 and last-entry match

**BC-6: Branch already exists — timestamped fallback**
- GIVEN a branch `transfer/<algorithm_name>` already exists on the remote
- WHEN Stage 6 attempts to push
- THEN Stage 6 warns the operator and uses `transfer/<algorithm_name>-<YYYYMMDD-HHMMSS>` as the branch name
- MECHANISM: prompts/pr.md Step 3 checks `git ls-remote` before push; if exists, appends timestamp suffix

**BC-7: Partial failure recovery**
- GIVEN branch is pushed successfully to remote AND calibration log is appended (Step 4)
- WHEN `gh pr create` fails (Step 5)
- THEN Stage 6 halts, reports the pushed branch name and last `gh` error, and instructs the operator to create the PR manually or retry `gh pr create`; the calibration log entry already exists — do not re-run append
- MECHANISM: prompts/pr.md captures branch name before attempting pr create; reports it on failure

**BC-8: Stage 6 prompt structural completeness**
- GIVEN `prompts/pr.md`
- WHEN checking for required prompt sections
- THEN the file contains: Prerequisites, Validation steps, Halt conditions, Expected outputs; and calibration append (Step 4) precedes gh pr create (Step 5)
- MECHANISM: Variant C structural check in Task 4 (Step 2)

#### Negative Contracts

**BC-9: FAIL verdict blocks PR creation**
- GIVEN `workspace/validation_results.json` with `overall_verdict: "FAIL"`
- WHEN Stage 6 prompt prerequisites are verified
- THEN Stage 6 halts with message "HALT: overall_verdict is FAIL — do not create PRs. Document failure and stop."
- MECHANISM: prompts/pr.md Step 1 reads overall_verdict; FAIL is an explicit halt

**BC-10: gh auth failure halts before git operations**
- GIVEN `gh auth` is not configured
- WHEN Stage 6 runs `gh auth status`
- THEN Stage 6 halts with "HALT: gh auth check failed — run 'gh auth login' and retry"
- MECHANISM: prompts/pr.md Step 2 runs `gh auth status` before any git push or gh pr create

**BC-11: Calibration log append fails on workspace artifact absence**
- GIVEN `workspace/algorithm_summary.json` or `workspace/validation_results.json` is absent
- WHEN `transfer_cli.py append-calibration-log` runs
- THEN exits with code 2 and an error message naming the missing file
- MECHANISM: CLI checks file existence before any reads or appends

#### Error Handling Contracts

**BC-12: append-calibration-log exit code semantics**
- GIVEN `append-calibration-log` runs
- WHEN it exits
- THEN exit 0 = success (entry appended and verified); exit 1 = corruption detected (count mismatch or last-entry mismatch — operator must inspect and repair); exit 2 = infrastructure error (missing workspace artifact, unreadable calibration log, write failure)
- MECHANISM: three distinct error paths with separate exit codes

---

### C) Component Interaction

```
[workspace/validation_results.json] ──→ prompts/pr.md (Stage 6)
[workspace/algorithm_summary.json]  ──→     │
[workspace/transfer_evidence.md]    ──→     │
                                            ↓
                                [gh auth status]
                                [git push transfer/<name> → llm-d-inference-scheduler fork]
                                            │
                                            ↓
                              transfer_cli.py append-calibration-log   ← Step 4 (before PRs)
                                            │
                                            ↓
                              docs/transfer/calibration_log.md (append-only)
                                            │
                                            ↓
                                [gh pr create → llm-d-inference-scheduler]
                                [git push transfer/<name> → llm-d-benchmark fork (if applicable)]
                                [gh pr create → llm-d-benchmark (if applicable)]
                                            │
                                            ↓
                              Output: PR URLs + calibration entry
```

**Workspace artifacts produced by PR6 at runtime:** None. `calibration_log.md` is a repo file, not a workspace artifact.

**Dead artifact check:**
- `prompts/pr.md` — consumed by pipeline operators at Stage 5→6 transition
- `transfer_cli.py append-calibration-log` — consumed by `prompts/pr.md`
- `tools/harness/known_answer_test.go` — consumed by `go test ./tools/harness/...`
- `tools/harness/testdata/known_answer_expected.json` — consumed by `TestKnownAnswer`

---

### D) Deviation Log

| Macro Plan Says | Micro Plan Does | Category |
|-----------------|-----------------|----------|
| "Smoke test: trivial algorithm runs through all 6 stages, each stage produces output in expected format" | `TestKnownAnswer` tests the harness framework with a synthetic algorithm — a unit test, not an interactive 6-stage run. Full smoke run is a manual post-merge activity requiring cluster access. | SIMPLIFICATION |
| Stage 6 prompt handles calibration log append directly | `append-calibration-log` CLI command does the append; the prompt instructs the operator to call it | ADDITION — the macro plan's corruption detection requirements (count before/after + byte-exact) are too complex for a prompt-level instruction |
| No `append-calibration-log` command listed | New CLI subcommand added | ADDITION (derived from macro plan Section D.7 calibration log requirements) |

---

### E) Review Guide

**THE TRICKY PART:** The `append-calibration-log` corruption detection uses `### Transfer:` as an entry delimiter. If anyone has manually added a line matching `### Transfer:` to the calibration log (e.g., in a comment), the count will be off. Verify the `### Transfer:` sentinel appears exactly once per entry in the existing log.

**WHAT TO SCRUTINIZE:**
- BC-5 (calibration log corruption detection): verify the before-count, append, after-count, and last-entry comparison are all in the correct order with no short-circuit
- BC-9 (FAIL blocks PR creation): check the prompt's Step 1 verdict check covers all three values (PASS/FAIL/INCONCLUSIVE) explicitly — a missing `elif` could let FAIL fall through
- BC-3 (known-answer score precision): the fixture uses integer queue depths divisible by 100, giving exact floats. Verify no floating-point arithmetic introduces error > 1e-6

**WHAT'S SAFE TO SKIM:** CLAUDE.md and README updates (mechanical documentation). The `gh` auth / PR creation steps in the prompt (standard `gh` CLI usage, not novel logic).

**KNOWN DEBT:** None. Step 6 of `prompts/pr.md` (K.1) fully handles the conditional llm-d-benchmark PR via `if ! git -C llm-d-benchmark diff --quiet HEAD; then ... else echo "No benchmark config changes — skipping llm-d-benchmark PR." fi`.

## PART 2: Executable Implementation

### F) Implementation Overview

| File | Action | Responsibility |
|------|--------|----------------|
| `tools/harness/testdata/known_answer_expected.json` | Create | Known-answer score fixture for 3 test tuples |
| `tools/harness/known_answer_test.go` | Create | `TestKnownAnswer` + `syntheticAlgorithm` |
| `tools/transfer_cli.py` | Modify | Add `append-calibration-log` command + argparse entry |
| `tools/test_transfer_cli.py` | Modify | Add tests for `append-calibration-log` |
| `prompts/pr.md` | Create | Stage 6 prompt: prerequisites, PR creation, calibration append |
| `CLAUDE.md` | Modify | Mark PR6 complete in pipeline table |
| `docs/transfer/README.md` | Modify | Add PR6 deliverables section |

**Key decisions:**
- Known-answer test has no build tag (no workspace dependencies, runs in standard `go test ./tools/harness/...`)
- `append-calibration-log` uses `### Transfer:` as the entry sentinel (matches existing log format)
- `prompts/pr.md` calls `append-calibration-log` via CLI — not manual LLM editing

**Dead artifact check:** All files above have identified consumers (see Section C).

---

### G) Task Breakdown

---

### Task 1: Known-Answer Score Fixture

**Contracts Implemented:** BC-3, BC-4
**Type:** Artifact (Variant B)

**Files:**
- Create: `tools/harness/testdata/known_answer_expected.json`

**Step 1: Author fixture**

The fixture encodes expected scores for 3 test tuples under `score = QueueDepth / 100.0`. Integer queue depths divisible by 100 give exact IEEE-754 floats — no rounding error.

Reviewer verification (3 representative values):
- Minimum: ep-ka-0 QueueDepth=0 → 0/100 = 0.0 ✓
- Maximum: ep-ka-2 QueueDepth=100 → 100/100 = 1.0 ✓
- Median:  ep-ka-1 QueueDepth=50 → 50/100 = 0.5 ✓

Create `tools/harness/testdata/known_answer_expected.json`:

```json
[
  {"ep-ka-0": 0.0, "ep-ka-1": 0.5, "ep-ka-2": 1.0},
  {"ep-ka-3": 0.25, "ep-ka-4": 0.75},
  {"ep-ka-5": 0.33, "ep-ka-6": 0.67}
]
```

**Step 2: Verify no dead artifacts**

`TestKnownAnswer` (Task 2) reads this file via `os.ReadFile("testdata/known_answer_expected.json")`. Consumer confirmed.

**Step 3: Commit**

```bash
git add tools/harness/testdata/known_answer_expected.json
git commit -m "$(cat <<'EOF'
test(harness): add known-answer score fixture (BC-3, BC-4)

- 3 tuples with QueueDepth in {0,25,33,50,67,75,100}
- Scores = QueueDepth/100.0; exact floats at integer/100 boundaries
- Reviewer-verifiable: min=0.0, max=1.0, median=0.5

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Known-Answer Test

**Contracts Implemented:** BC-3, BC-4
**Language:** Go

**Files:**
- Create: `tools/harness/known_answer_test.go`

**Step 1: Write and run the test**

Create `tools/harness/known_answer_test.go`:

```go
package harness

import (
	"encoding/json"
	"math"
	"os"
	"testing"

	sim "github.com/inference-sim/inference-sim/sim"
)

// syntheticAlgorithm implements Algorithm with score = QueueDepth / maxQueueDepth.
// Used for the known-answer test only.
type syntheticAlgorithm struct{}

func (a *syntheticAlgorithm) Route(req *sim.Request, state *sim.RouterState) sim.RoutingDecision {
	const maxQueueDepth = 100.0
	scores := make(map[string]float64, len(state.Snapshots))
	for _, snap := range state.Snapshots {
		scores[snap.ID] = float64(snap.QueueDepth) / maxQueueDepth
	}
	if len(state.Snapshots) == 0 {
		return sim.RoutingDecision{Reason: "no-endpoints"}
	}
	bestID := state.Snapshots[0].ID
	bestScore := scores[bestID]
	for _, snap := range state.Snapshots[1:] {
		if scores[snap.ID] > bestScore {
			bestScore = scores[snap.ID]
			bestID = snap.ID
		}
	}
	return sim.NewRoutingDecisionWithScores(bestID, "synthetic", scores)
}

// TestKnownAnswer verifies BC-3 and BC-4: RunTuples produces scores matching
// testdata/known_answer_expected.json within 1e-6 absolute tolerance per endpoint.
func TestKnownAnswer(t *testing.T) {
	tuples := []TestTuple{
		{
			Request: sim.Request{ID: "known-req-0"},
			State: sim.RouterState{Snapshots: []sim.RoutingSnapshot{
				{ID: "ep-ka-0", QueueDepth: 0},
				{ID: "ep-ka-1", QueueDepth: 50},
				{ID: "ep-ka-2", QueueDepth: 100},
			}},
		},
		{
			Request: sim.Request{ID: "known-req-1"},
			State: sim.RouterState{Snapshots: []sim.RoutingSnapshot{
				{ID: "ep-ka-3", QueueDepth: 25},
				{ID: "ep-ka-4", QueueDepth: 75},
			}},
		},
		{
			Request: sim.Request{ID: "known-req-2"},
			State: sim.RouterState{Snapshots: []sim.RoutingSnapshot{
				{ID: "ep-ka-5", QueueDepth: 33},
				{ID: "ep-ka-6", QueueDepth: 67},
			}},
		},
	}

	data, err := os.ReadFile("testdata/known_answer_expected.json")
	if err != nil {
		t.Fatalf("read fixture: %v", err)
	}
	var expected []map[string]float64
	if err := json.Unmarshal(data, &expected); err != nil {
		t.Fatalf("parse fixture: %v", err)
	}
	if len(expected) != len(tuples) {
		t.Fatalf("fixture has %d entries, want %d", len(expected), len(tuples))
	}

	alg := &syntheticAlgorithm{}
	results := RunTuples(alg, tuples)

	const tol = 1e-6
	for i, result := range results {
		if result.Error != nil {
			t.Errorf("tuple %d: unexpected error: %v", i, result.Error)
			continue
		}
		for epID, wantScore := range expected[i] {
			gotScore, ok := result.SimScores[epID]
			if !ok {
				t.Errorf("tuple %d: missing score for endpoint %q", i, epID)
				continue
			}
			if diff := math.Abs(gotScore - wantScore); diff > tol {
				t.Errorf("tuple %d endpoint %q: got %.10f, want %.10f (diff=%.2e > tol=1e-6)",
					i, epID, gotScore, wantScore, diff)
			}
		}
	}
}
```

**Step 2: Run test**

```bash
go test ./tools/harness/... -run TestKnownAnswer -v
```

Expected: PASS

**Step 3: Run full Go harness test suite**

```bash
go test ./tools/harness/... -v
go build ./tools/harness/...
```

Expected: All existing tests PASS, TestKnownAnswer PASS, build succeeds.

**Step 4: Commit**

```bash
git add tools/harness/known_answer_test.go
git commit -m "$(cat <<'EOF'
test(harness): add TestKnownAnswer synthetic algorithm test (BC-3, BC-4)

- syntheticAlgorithm: score = QueueDepth/100.0
- 3 test tuples; scores verified against testdata/known_answer_expected.json
- Per-endpoint tolerance: 1e-6 absolute
- Runs without workspace artifacts or build tags

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: append-calibration-log CLI Command

**Contracts Implemented:** BC-5, BC-11, BC-12
**Language:** Python

**Files:**
- Modify: `tools/transfer_cli.py`
- Modify: `tools/test_transfer_cli.py`

**Step 1: Write failing tests**

Add the `TestAppendCalibrationLog` class to `tools/test_transfer_cli.py`. Full test code is in the Appendix, Section K.2.

**Step 2: Run failing tests**

```bash
python -m pytest tools/test_transfer_cli.py::TestAppendCalibrationLog -v
```

Expected: FAIL — `append-calibration-log` subcommand not yet registered.

**Step 3: Implement append-calibration-log**

Add `cmd_append_calibration_log` function and argparse entry to `tools/transfer_cli.py`. Full implementation is in the Appendix, Section K.3.

**Step 4: Run tests to verify they pass**

```bash
python -m pytest tools/test_transfer_cli.py::TestAppendCalibrationLog -v
```

Expected: All 6 tests PASS.

**Step 5: Run full Python test suite**

```bash
python -m pytest tools/ -v
```

Expected: All tests PASS.

**Step 6: Commit**

```bash
git add tools/transfer_cli.py tools/test_transfer_cli.py
git commit -m "$(cat <<'EOF'
feat(tools): add append-calibration-log command (BC-5, BC-11, BC-12)

- Reads algorithm_summary.json + validation_results.json from workspace
- Appends YAML entry under ### Transfer: sentinel
- Corruption detection: count before/after + byte-exact last-entry check
- Exit 0 = success, 1 = corruption, 2 = infrastructure error

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Stage 6 Prompt Template

**Contracts Implemented:** BC-1, BC-2, BC-6, BC-7, BC-8, BC-9, BC-10
**Type:** Prompt Template (Variant C)

**Files:**
- Create: `prompts/pr.md`

**Step 1: Author prompt template**

Create `prompts/pr.md` with the full content from Appendix Section K.1.

**Step 2: Verify structural completeness**

Check that `prompts/pr.md` contains all 4 required sections:
- [ ] Prerequisites: schema validation commands for workspace artifacts (`validation_results.json`, `algorithm_summary.json`), `gh auth status`
- [ ] Validation steps: verdict check, branch existence check, calibration append before PR creation, pr create success confirmation
- [ ] Halt conditions: FAIL verdict, INCONCLUSIVE without operator_notes, gh auth failure, calibration append failure, pr create failure
- [ ] Expected outputs: PR URLs for llm-d-inference-scheduler (and llm-d-benchmark if applicable), calibration log entry

**Step 3: Verify predecessor artifact checks**

Confirm `prompts/pr.md` Prerequisites block includes:
```bash
.venv/bin/python tools/transfer_cli.py validate-schema workspace/validation_results.json
.venv/bin/python tools/transfer_cli.py validate-schema workspace/algorithm_summary.json
```
(Note: `stage3_output.json` is NOT validated — the scorer file is already in the submodule working copy from Stage 4 and is not consumed by Stage 6.)

**Step 4: Commit**

```bash
git add prompts/pr.md
git commit -m "$(cat <<'EOF'
docs(prompts): add Stage 6 pr.md prompt template (BC-1, BC-2, BC-6–BC-10)

- Prerequisites: verdict check, schema validation, gh auth
- Steps: branch push, gh pr create for llm-d repos, append-calibration-log
- Halt: FAIL verdict, INCONCLUSIVE without operator_notes, gh auth failure
- BC-6: branch-exists fallback uses timestamped suffix
- BC-7: partial failure reports pushed branch name for manual recovery

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Documentation Updates

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/transfer/README.md`

**Step 1: Update CLAUDE.md pipeline table**

Change the PR6 row from `Not started` to `Complete`:

```markdown
| PR6 | Stage 6 + self-verification + calibration | Complete |
```

**Step 2: Update docs/transfer/README.md**

Append a `### PR6 Deliverables` section to `docs/transfer/README.md`:

```markdown
### PR6 Deliverables

- **Stage 6 prompt:** `prompts/pr.md` — prerequisites check, branch push, `gh pr create` for llm-d repos, `append-calibration-log`.
- **append-calibration-log CLI:** `tools/transfer_cli.py append-calibration-log` — atomic append with corruption detection.
- **Known-answer test:** `tools/harness/known_answer_test.go::TestKnownAnswer` — synthetic algorithm (score = QueueDepth/100.0) against fixture `tools/harness/testdata/known_answer_expected.json`.

**Stage 6 invocation (after Stage 5 PASS):**
```bash
# Open prompts/pr.md and follow the steps.
# The prompt will call:
.venv/bin/python tools/transfer_cli.py append-calibration-log
```
```

**Step 3: Commit**

```bash
git add CLAUDE.md docs/transfer/README.md
git commit -m "$(cat <<'EOF'
docs: mark PR6 complete; add PR6 deliverables to README

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Verification Gate (Integration PR)

```bash
# Python tests
python -m pytest tools/ -v

# Go tests (includes TestKnownAnswer)
go test ./tools/harness/... -v

# Go build
go build ./tools/harness/...

# Smoke: extract pipeline still works
.venv/bin/python tools/transfer_cli.py extract blis_router/best/
.venv/bin/python tools/transfer_cli.py validate-mapping
```

Expected: All PASS, build succeeds, extract exits 0.


---

## PART 3: Quality Assurance

### H) Test Strategy

| Contract | Task | Test Type | Verification |
|----------|------|-----------|-------------|
| BC-1 (PASS verdict gates PR) | Task 4 | Structural check (prompt) | Step 1 of pr.md reads overall_verdict before any git op |
| BC-2 (INCONCLUSIVE needs operator_notes) | Task 4 | Structural check (prompt) | Step 1 explicitly checks operator_notes |
| BC-3 (known-answer score accuracy) | Task 2 | Unit (Go) | `go test ./tools/harness/... -run TestKnownAnswer` |
| BC-4 (fixture reviewer-verifiable) | Task 1 | Artifact validation | Manual: verify 0.0, 1.0, 0.5 match formula for QueueDepth 0, 100, 50 |
| BC-5 (calibration append corruption-safe) | Task 3 | Unit (Python) | `pytest TestAppendCalibrationLog::test_appends_entry_*` |
| BC-6 (branch-exists → timestamp suffix) | Task 4 | Structural check (prompt) | pr.md Step 3 documents `git ls-remote` + suffix logic |
| BC-7 (partial failure recovery) | Task 4 | Structural check (prompt) | pr.md Step 4 (calibration append) precedes Step 5 (gh pr create); branch name captured before pr create |
| BC-8 (prompt structural completeness) | Task 4 | Structural check | Verify 4 required sections present |
| BC-9 (FAIL blocks PR creation) | Task 4 | Structural check (prompt) | pr.md Step 1 explicit FAIL halt |
| BC-10 (gh auth failure halts) | Task 4 | Structural check (prompt) | pr.md Step 2 runs `gh auth status` first |
| BC-11 (missing artifacts → exit 2) | Task 3 | Unit (Python) | `pytest TestAppendCalibrationLog::test_missing_*` |
| BC-12 (exit code semantics) | Task 3 | Unit (Python) | `pytest TestAppendCalibrationLog` exit code assertions |

**sim2real invariants checked:**
- Schema chain integrity: PR6 consumes but does not write new workspace artifacts; existing schemas cover all consumed fields ✅
- CLI exit code contract: `append-calibration-log` uses 0/1/2 semantics ✅
- Prompt completeness: `prompts/pr.md` has all 4 required sections ✅
- Dead artifact prevention: every new file has an identified consumer ✅

---

### I) Risk Analysis

| Risk | Likelihood | Impact | Mitigation | Task |
|------|-----------|--------|-----------|------|
| `### Transfer:` count corrupted if log has stale content matching the sentinel | Low | Medium | Review guide flags this; implementer checks existing log for false positives | Task 3 |
| `gh` CLI version differences in branch/PR creation flags | Low | Medium | `prompts/pr.md` uses only stable `gh pr create` flags (`--title`, `--body`, `--base`, `--head`) | Task 4 |
| `syntheticAlgorithm` first-endpoint tie-breaking differs from fixture assumption | Low | Low | All QueueDepth values in 3 tuples are distinct — no ties possible | Task 2 |
| `ep-ka-5` score 0.33 has floating-point precision concern (33/100 = 0.33 exactly in IEEE-754? No — it's 0.33000000000000001... but diff from 0.33 is ~1e-17, well within 1e-6) | Low | None | 33/100 in float64 = 0.33 representation; diff from literal 0.33 in JSON is < 1e-15 < 1e-6 tol ✅ | Task 1/2 |
| CLAUDE.md pipeline table format differs from expected | Very low | Very low | Read file before editing; preserve existing table format | Task 5 |

---

### J) Sanity Checklist (Self-Audit)

**Dimension 1: Cross-system accuracy**
- [ ] All submodule API references match actual code (submodule status verified: inference-sim 319d1b7, llm-d-inference-scheduler 4cd7046)
- [ ] Commit pins are current (no mapping artifact updated in PR6 — pins unchanged from PR5)
- [ ] No stale references to APIs that have been renamed

**Dimension 2: Schema chain integrity**
- [ ] PR6 does not write new workspace artifacts → no new schema chain to verify
- [ ] `append-calibration-log` reads `validation_results.json` fields that exist in `validation_results.schema.json` (`overall_verdict`, `suite_a.kendall_tau`, `suite_b.rank_stability_tau`, `suite_c.passed`, `suite_c.deterministic`, `suite_c.max_pile_on_ratio`, `benchmark.mechanism_check_verdict`, `benchmark.t_eff`, `noise_cv`)
- [ ] Writer → Reader chain for calibration log: Stage 5 (validation_results.json) → Stage 6 prompt → append-calibration-log → calibration_log.md ✅

**Dimension 3: Prompt completeness**
- [ ] `prompts/pr.md` specifies prerequisites (validation_results.json, transfer_evidence.md, algorithm_summary.json, gh auth; stage3_output.json is NOT a prereq — scorer already in submodule working copy)
- [ ] `prompts/pr.md` specifies validation steps (verdict check, schema validation, branch push success)
- [ ] `prompts/pr.md` specifies halt conditions (FAIL verdict, INCONCLUSIVE without operator_notes, gh auth failure, calibration append failure, pr create failure)
- [ ] `prompts/pr.md` specifies expected outputs (PR URLs, calibration log entry)
- [ ] Predecessor artifact checks included (`validate-schema` commands)

**Dimension 4: CLI contract**
- [ ] `append-calibration-log` exit 0 = success, 1 = corruption, 2 = infrastructure error
- [ ] Error messages are actionable (name the missing file, identify the corruption type)

**Dimension 5: Artifact consistency**
- [ ] Calibration log sentinel `### Transfer:` matches format in existing `docs/transfer/calibration_log.md`
- [ ] `pipeline_commit` in calibration entry: `algorithm_summary.json` has no `pipeline_commit` field; CLI falls back to `git rev-parse HEAD` — this is correct and expected
- [ ] CLAUDE.md pipeline table format preserved

**Dimension 6: Dead artifact prevention**
- [ ] `prompts/pr.md` — consumed by pipeline operators
- [ ] `append-calibration-log` — called by `prompts/pr.md`
- [ ] `known_answer_test.go` — consumed by `go test ./tools/harness/...`
- [ ] `known_answer_expected.json` — consumed by `TestKnownAnswer`
- [ ] CLAUDE.md, README.md updates — consumed by pipeline operators

**Additional checks:**
- [ ] PR category correctly identified: Integration ✅
- [ ] Verification gate matches PR category: Integration gate (python tests + go tests + go build + smoke extract) ✅
- [ ] No feature creep beyond macro plan scope
- [ ] Deviation log reviewed — D1, D2, D3 are justified and harmless
- [ ] Task dependencies correctly ordered: Task 1 before Task 2 (fixture before test), Task 3 before Task 4 (CLI before prompt)
- [ ] All contracts mapped to tasks ✅


---

## APPENDIX: File-Level Implementation Details

### K.1 — prompts/pr.md (complete)

```markdown
---
stage: 6
name: pr
description: "Stage 6 — Create PRs in llm-d repos, append calibration log entry"
inputs:
  - workspace/validation_results.json
  - workspace/transfer_evidence.md
  - workspace/algorithm_summary.json
outputs:
  - llm-d-inference-scheduler PR URL
  - docs/transfer/calibration_log.md entry
---

# Stage 6: PR Creation

You are running Stage 6 of the sim-to-production transfer pipeline. This stage
creates PRs in the llm-d target repositories and records a calibration log entry.

## Prerequisites

Verify all predecessor artifacts exist and are valid:

```bash
.venv/bin/python tools/transfer_cli.py validate-schema workspace/validation_results.json \
  || { echo "HALT: validation_results.json missing or invalid"; exit 1; }
.venv/bin/python tools/transfer_cli.py validate-schema workspace/algorithm_summary.json \
  || { echo "HALT: algorithm_summary.json missing or invalid"; exit 1; }
test -s workspace/transfer_evidence.md \
  || { echo "HALT: workspace/transfer_evidence.md missing or empty — run generate-evidence first"; exit 1; }
```

**HALT if any command exits non-zero.**

## Step 1: Check Overall Verdict

```bash
VERDICT=$(.venv/bin/python -c "import json; print(json.load(open('workspace/validation_results.json'))['overall_verdict'])")
echo "overall_verdict: $VERDICT"

if [ "$VERDICT" = "FAIL" ]; then
  echo "HALT: overall_verdict is FAIL — do not create PRs. Document failure and stop."
  exit 1
fi

if [ "$VERDICT" = "INCONCLUSIVE" ]; then
  OPERATOR_NOTES=$(.venv/bin/python -c "
import json
v = json.load(open('workspace/validation_results.json'))
print(v.get('operator_notes', '').strip())
" 2>/dev/null)
  if [ -z "$OPERATOR_NOTES" ]; then
    echo "HALT: overall_verdict is INCONCLUSIVE but operator_notes is absent or empty."
    echo "Set operator_notes in workspace/validation_results.json with rationale before proceeding."
    exit 1
  fi
  echo "WARN: Proceeding with INCONCLUSIVE verdict under operator sign-off: $OPERATOR_NOTES"
elif [ "$VERDICT" != "PASS" ]; then
  echo "HALT: unexpected overall_verdict '$VERDICT' — expected PASS, FAIL, or INCONCLUSIVE."
  exit 1
fi
```

**HALT if FAIL. HALT if INCONCLUSIVE without operator_notes. HALT if verdict is not a known value.**

## Step 2: Check gh Auth

```bash
gh auth status \
  || { echo "HALT: gh auth check failed — run 'gh auth login' and retry Stage 6."; exit 1; }
```

**HALT if gh auth status exits non-zero.**

## Step 3: Push Branch to llm-d-inference-scheduler

```bash
ALG_NAME=$(.venv/bin/python -c "import json; print(json.load(open('workspace/algorithm_summary.json'))['algorithm_name'])")
BRANCH="transfer/${ALG_NAME}"

# Check if branch already exists on remote
cd llm-d-inference-scheduler
REMOTE_URL=$(git remote get-url origin 2>/dev/null || git remote get-url upstream 2>/dev/null || echo "")
if git ls-remote --exit-code --heads origin "$BRANCH" 2>/dev/null; then
  TIMESTAMP=$(date +%Y%m%d-%H%M%S)
  BRANCH="${BRANCH}-${TIMESTAMP}"
  echo "WARN: branch already exists — using timestamped branch: $BRANCH"
fi

git checkout -b "$BRANCH" 2>/dev/null || git checkout "$BRANCH"
git push origin "$BRANCH" \
  || { echo "HALT: git push failed for branch $BRANCH in llm-d-inference-scheduler"; cd ..; exit 1; }
echo "Pushed branch: $BRANCH to llm-d-inference-scheduler"
cd ..
```

**HALT if git push fails. Record $BRANCH for PR creation.**

## Step 4: Append Calibration Log Entry

Append the calibration entry **before** creating PRs. If the append fails, no PRs have been created yet — safe to halt and investigate.

```bash
.venv/bin/python tools/transfer_cli.py append-calibration-log \
  --workspace workspace/ \
  --calibration-log docs/transfer/calibration_log.md \
  || { echo "HALT: append-calibration-log failed — inspect docs/transfer/calibration_log.md before proceeding"; exit 1; }
```

**HALT if exit non-zero. Fix calibration log before continuing to PR creation.**

## Step 5: Create PR in llm-d-inference-scheduler

```bash
SUITE_A_TAU=$(.venv/bin/python -c "import json; print(json.load(open('workspace/validation_results.json'))['suite_a']['kendall_tau'])")
SUITE_C_PASS=$(.venv/bin/python -c "import json; print(str(json.load(open('workspace/validation_results.json'))['suite_c']['passed']).lower())")
MECH=$(.venv/bin/python -c "import json; print(json.load(open('workspace/validation_results.json'))['benchmark']['mechanism_check_verdict'])")

# Write body to a temp file to avoid shell quoting issues with Markdown content
# (transfer_evidence.md may contain double quotes, backslashes, or $ signs).
PR_BODY_FILE=$(mktemp)
cat << EOF > "$PR_BODY_FILE"
## Summary

Sim-to-production transfer: \`${ALG_NAME}\`

**Validation:**
- Suite A Kendall-tau: \`${SUITE_A_TAU}\` (threshold: 0.8)
- Suite C concurrent safety: \`${SUITE_C_PASS}\`
- Mechanism check: \`${MECH}\`
- Overall verdict: \`${VERDICT}\`

## Evidence

$(cat workspace/transfer_evidence.md)

## Rollback

To disable: in EndpointPickerConfig, find the \`plugins\` entry with \`type: blis-weighted-scorer\` (or by the explicit \`name:\` you used when adding it in Stage 4) and set \`parameters.enabled: false\`.
EOF

cd llm-d-inference-scheduler
gh pr create \
  --title "feat(scorer): add ${ALG_NAME} sim-to-production scorer plugin" \
  --base main \
  --head "$BRANCH" \
  --body-file "$PR_BODY_FILE" \
  || { PUSH_BRANCH="$BRANCH"; rm -f "$PR_BODY_FILE"; echo "HALT: gh pr create failed for llm-d-inference-scheduler. Branch '$PUSH_BRANCH' is already pushed and calibration entry is already appended — do NOT re-run Stage 6 from Step 1. Create the PR manually or retry only 'gh pr create --head $PUSH_BRANCH ...' from this step."; cd ..; exit 1; }
rm -f "$PR_BODY_FILE"

SCHEDULER_PR_URL=$(gh pr view --json url -q .url)
echo "Created PR: $SCHEDULER_PR_URL"
cd ..
```

**HALT if gh pr create fails — report the pushed branch name for manual recovery.**

## Step 6: llm-d-benchmark PR (conditional)

If this transfer involved benchmark config changes in the llm-d-benchmark submodule, push a branch and create a PR there too. **If no benchmark config changes exist, skip this step.**

```bash
# Check for uncommitted changes in llm-d-benchmark
if ! git -C llm-d-benchmark diff --quiet HEAD; then
  BENCH_BRANCH="transfer/${ALG_NAME}"
  if git -C llm-d-benchmark ls-remote --exit-code --heads origin "$BENCH_BRANCH" 2>/dev/null; then
    TIMESTAMP=$(date +%Y%m%d-%H%M%S)
    BENCH_BRANCH="${BENCH_BRANCH}-${TIMESTAMP}"
  fi
  git -C llm-d-benchmark checkout -b "$BENCH_BRANCH"
  git -C llm-d-benchmark push origin "$BENCH_BRANCH" \
    || { echo "HALT: git push failed for llm-d-benchmark branch $BENCH_BRANCH"; exit 1; }
  cd llm-d-benchmark
  gh pr create \
    --title "feat(benchmark): add ${ALG_NAME} benchmark configs" \
    --base main \
    --head "$BENCH_BRANCH" \
    --body "Benchmark configs for sim-to-production transfer: \`${ALG_NAME}\`. See llm-d-inference-scheduler PR: ${SCHEDULER_PR_URL}" \
    || { echo "HALT: gh pr create failed for llm-d-benchmark. Branch '$BENCH_BRANCH' is pushed."; cd ..; exit 1; }
  BENCHMARK_PR_URL=$(gh pr view --json url -q .url)
  echo "Created benchmark PR: $BENCHMARK_PR_URL"
  cd ..
else
  echo "No benchmark config changes — skipping llm-d-benchmark PR."
  BENCHMARK_PR_URL="(none)"
fi
```

## Halt Conditions Summary

| Condition | Trigger | Action |
|-----------|---------|--------|
| Prerequisite artifact missing/invalid | validate-schema exits non-zero | HALT: "Stage N prerequisite missing" |
| overall_verdict == FAIL | Step 1 check | HALT: "Do not create PRs" |
| INCONCLUSIVE without operator_notes | Step 1 check | HALT: "Add operator_notes to validation_results.json" |
| gh auth not configured | Step 2 | HALT: "Run gh auth login" |
| git push fails | Step 3 or 6 | HALT: report branch name |
| append-calibration-log fails | Step 4 | HALT: "Inspect calibration_log.md before continuing" |
| gh pr create fails | Step 5 or 6 | HALT: report pushed branch for manual recovery |

## Expected Outputs

- `docs/transfer/calibration_log.md` entry: appended by Step 4
- `llm-d-inference-scheduler` PR URL: printed by Step 5
- `llm-d-benchmark` PR URL (or "none"): printed by Step 6

> **Note:** Stage 6 ends the interactive pipeline session. The generated scorer is now under review by llm-d maintainers. If they request changes, re-run Stages 3→4 to address feedback and push an updated branch.
```


---

### K.2 — TestAppendCalibrationLog tests (tools/test_transfer_cli.py)

Add this class to `tools/test_transfer_cli.py`. Note: helper functions `_run_cli`, `_write_algorithm_summary`, `_write_validation_results`, `_write_calibration_log` may already exist in the file — check and add only what's missing.

```python
class TestAppendCalibrationLog:
    def test_appends_entry_to_empty_log(self, tmp_path):
        """BC-5: happy path — appends entry when log has 0 existing entries."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        _write_algorithm_summary(ws)
        _write_validation_results(ws)
        cal = tmp_path / "calibration_log.md"
        _write_calibration_log(cal, n_entries=0)
        rc, out, err = _run_cli(
            "append-calibration-log",
            "--workspace", str(ws),
            "--calibration-log", str(cal),
        )
        assert rc == 0, f"exit {rc}: {err}"
        content = cal.read_text()
        assert content.count("### Transfer:") == 1
        assert "test_algo" in content

    def test_appends_entry_to_existing_log(self, tmp_path):
        """BC-5: appends entry when log already has N entries."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        _write_algorithm_summary(ws, "second_algo")
        _write_validation_results(ws)
        cal = tmp_path / "calibration_log.md"
        _write_calibration_log(cal, n_entries=2)
        rc, out, err = _run_cli(
            "append-calibration-log",
            "--workspace", str(ws),
            "--calibration-log", str(cal),
        )
        assert rc == 0, f"exit {rc}: {err}"
        assert cal.read_text().count("### Transfer:") == 3

    def test_missing_algorithm_summary_exits_2(self, tmp_path):
        """BC-11: exits 2 when algorithm_summary.json is absent."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        _write_validation_results(ws)
        cal = tmp_path / "calibration_log.md"
        _write_calibration_log(cal)
        rc, out, err = _run_cli(
            "append-calibration-log",
            "--workspace", str(ws),
            "--calibration-log", str(cal),
        )
        assert rc == 2, f"expected exit 2, got {rc}"
        assert "algorithm_summary.json" in err

    def test_missing_validation_results_exits_2(self, tmp_path):
        """BC-11: exits 2 when validation_results.json is absent."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        _write_algorithm_summary(ws)
        cal = tmp_path / "calibration_log.md"
        _write_calibration_log(cal)
        rc, out, err = _run_cli(
            "append-calibration-log",
            "--workspace", str(ws),
            "--calibration-log", str(cal),
        )
        assert rc == 2, f"expected exit 2, got {rc}"
        assert "validation_results.json" in err

    def test_corruption_detected_exits_1(self, tmp_path, monkeypatch):
        """BC-12: exit 1 when count mismatch detected after append.

        Patches Path.read_text to return extra content on the post-append read,
        simulating a concurrent write that injected an extra ### Transfer: sentinel
        between the CLI's append and its verification read.
        Calls cmd_append_calibration_log directly (not via subprocess) so the
        monkeypatch takes effect in-process.

        Note: the CLI appends via open("a")/f.write(), not Path.write_text, so
        patching write_text has no effect. Patching read_text on the second call
        is the correct injection point for the post-append verification read.
        """
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        import transfer_cli  # the actual module under test

        ws = tmp_path / "workspace"
        ws.mkdir()
        _write_algorithm_summary(ws)
        _write_validation_results(ws)
        cal = tmp_path / "calibration_log.md"
        _write_calibration_log(cal, n_entries=1)

        # Patch Path.read_text to inject an extra ### Transfer: sentinel on the
        # second read of the calibration log (the post-append verification read).
        # First read = before_text (count_before). Second read = after_text (count_after).
        real_read = Path.read_text
        read_call_count = [0]

        def injecting_read(path_self, *args, **kwargs):
            content = real_read(path_self, *args, **kwargs)
            if str(path_self) == str(cal):
                read_call_count[0] += 1
                if read_call_count[0] >= 2:
                    # Simulate concurrent write: inject extra sentinel so
                    # count_after != count_before + 1, triggering exit 1.
                    content = content + "\n### Transfer: injected\n```yaml\n```\n"
            return content

        monkeypatch.setattr(Path, "read_text", injecting_read)

        import argparse
        args = argparse.Namespace(workspace=str(ws), calibration_log=str(cal))
        rc = transfer_cli.cmd_append_calibration_log(args)
        assert rc == 1, f"expected exit 1 (corruption detected), got {rc}"

    def test_inconclusive_verdict_recorded(self, tmp_path):
        """BC-12/recording: INCONCLUSIVE overall_verdict is written to the calibration log.

        Note: operator_notes enforcement (BC-2) is a prompt-level contract in prompts/pr.md
        Step 1, not a CLI-level contract. append-calibration-log records whatever verdict
        is present without validating it.
        """
        ws = tmp_path / "workspace"
        ws.mkdir()
        _write_algorithm_summary(ws)
        _write_validation_results(ws, verdict="INCONCLUSIVE")
        cal = tmp_path / "calibration_log.md"
        _write_calibration_log(cal)
        rc, out, err = _run_cli(
            "append-calibration-log",
            "--workspace", str(ws),
            "--calibration-log", str(cal),
        )
        assert rc == 0, f"exit {rc}: {err}"
        assert "INCONCLUSIVE" in cal.read_text()
```

Helper functions to add near top of test file (if not present):

```python
import json
import subprocess
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).parent


def _run_cli(*args, cwd=None):
    """Run transfer_cli.py; return (exit_code, stdout, stderr)."""
    cmd = [sys.executable, str(TOOLS_DIR / "transfer_cli.py")] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd or TOOLS_DIR.parent)
    return result.returncode, result.stdout, result.stderr


def _write_algorithm_summary(ws: Path, algorithm_name: str = "test_algo") -> None:
    # Note: pipeline_commit is NOT a field in algorithm_summary.json schema.
    # The CLI reads algorithm_name; pipeline_commit falls back to git rev-parse HEAD.
    (ws / "algorithm_summary.json").write_text(json.dumps({
        "algorithm_name": algorithm_name,
        "evolve_block_source": "blis_router/best/best_program.go:1-5",
        "evolve_block_content_hash": "abc123",
        "signals": [], "composite_signals": [],
        "metrics": {"combined_score": 0.0},
        "scope_validation_passed": True,
        "mapping_artifact_version": "1.0",
        "fidelity_checked": True,
    }))


def _write_validation_results(ws: Path, verdict: str = "PASS") -> None:
    data = {
        "suite_a": {"passed": True, "kendall_tau": 0.92, "max_abs_error": 0.01, "tuple_count": 200},
        "suite_b": {"passed": True, "rank_stability_tau": 0.95,
                    "threshold_crossing_pct": 0.0, "informational_only": True},
        "suite_c": {"passed": True, "deterministic": True, "max_pile_on_ratio": 1.1},
        "benchmark": {
            "passed": True, "mechanism_check_verdict": "PASS", "t_eff": 0.05,
            "workload_classification": [
                {"workload": "wl-a", "classification": "matched",
                 "improvement": 0.12, "matched_signals": ["queue_depth"]}
            ],
            "specificity_notes": [],
        },
        "overall_verdict": verdict,
        "noise_cv": 0.03,
    }
    if verdict == "INCONCLUSIVE":
        data["operator_notes"] = "Improvement marginally below T_eff; operator approves"
    (ws / "validation_results.json").write_text(json.dumps(data))


def _write_calibration_log(cal: Path, n_entries: int = 0) -> None:
    header = ("# Transfer Pipeline Calibration Log\n\nAppend-only.\n\n"
              "## Entries\n\n<!-- Stage 6 appends entries below this line -->\n")
    entries = "".join(
        f"\n### Transfer: prior_algo_{i}\n```yaml\ntransfer_date: 2026-01-0{i+1}\n```\n"
        for i in range(n_entries)
    )
    cal.write_text(header + entries)
```

---

### K.3 — cmd_append_calibration_log (tools/transfer_cli.py)

Add this function before `main()`:

```python
def cmd_append_calibration_log(args: "argparse.Namespace") -> int:
    """Append a per-transfer calibration entry to docs/transfer/calibration_log.md.

    Exit 0 = success; 1 = corruption detected; 2 = infrastructure error.
    """
    import json
    from datetime import date
    from pathlib import Path
    import subprocess

    ws = Path(args.workspace)
    cal_path = Path(args.calibration_log)

    alg_path = ws / "algorithm_summary.json"
    val_path = ws / "validation_results.json"

    for p, label in [(alg_path, "algorithm_summary.json"),
                     (val_path, "validation_results.json")]:
        if not p.exists():
            print(f"ERROR: append-calibration-log requires '{p}' — {label} not found.",
                  file=sys.stderr)
            return 2

    try:
        alg = json.loads(alg_path.read_text())
        val = json.loads(val_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"ERROR: cannot read workspace artifacts: {e}", file=sys.stderr)
        return 2

    pipeline_commit = alg.get("pipeline_commit", "")
    if not pipeline_commit:
        try:
            pipeline_commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], text=True).strip()
        except Exception:
            pipeline_commit = "unknown"

    alg_name = alg.get("algorithm_name", "unknown")
    overall = val.get("overall_verdict", "UNKNOWN")
    suite_a = val.get("suite_a", {})
    suite_b = val.get("suite_b", {})
    suite_c = val.get("suite_c", {})
    bench = val.get("benchmark", {})

    matched_improvement = 0.0
    for wc in bench.get("workload_classification", []):
        if wc.get("classification") == "matched":
            matched_improvement = max(matched_improvement, wc.get("improvement", 0.0))

    entry = (
        f"\n### Transfer: {alg_name}\n"
        f"```yaml\n"
        f"transfer_date: {date.today().isoformat()}\n"
        f"algorithm_name: {alg_name}\n"
        f"pipeline_commit: {pipeline_commit}\n"
        f"single_run_provisional: true\n"
        f"suite_a_results:\n"
        f"  kendall_tau: {suite_a.get('kendall_tau', 0.0)}\n"
        f"  max_abs_error: {suite_a.get('max_abs_error', 0.0)}\n"
        f"suite_b_results:\n"
        f"  rank_stability_tau: {suite_b.get('rank_stability_tau', 0.0)}\n"
        f"  threshold_crossing_pct: {suite_b.get('threshold_crossing_pct', 0.0)}\n"
        f"  informational_only: true\n"
        f"suite_c_results:\n"
        f"  deterministic: {str(suite_c.get('deterministic', False)).lower()}\n"
        f"  max_pile_on_ratio: {suite_c.get('max_pile_on_ratio', 0.0)}\n"
        f"benchmark_results:\n"
        f"  mechanism_check_verdict: {bench.get('mechanism_check_verdict', 'UNKNOWN')}\n"
        f"  t_eff: {bench.get('t_eff', 0.0)}\n"
        f"  matched_improvement: {matched_improvement}\n"
        f"noise_cv: {val.get('noise_cv', 0.0)}\n"
        f"overall_verdict: {overall}\n"
        f"threshold_adjustments: []\n"
        f"```\n"
    )

    if not cal_path.exists():
        cal_path.parent.mkdir(parents=True, exist_ok=True)
        cal_path.write_text(
            "# Transfer Pipeline Calibration Log\n\nAppend-only: do not modify existing entries.\n\n"
            "## Entries\n\n<!-- Stage 6 appends entries below this line -->\n"
        )

    try:
        before_text = cal_path.read_text()
    except OSError as e:
        print(f"ERROR: cannot read calibration log: {e}", file=sys.stderr)
        return 2
    count_before = before_text.count("### Transfer:")

    try:
        with cal_path.open("a") as f:
            f.write(entry)
    except OSError as e:
        print(f"ERROR: cannot write to calibration log: {e}", file=sys.stderr)
        return 2

    try:
        after_text = cal_path.read_text()
    except OSError as e:
        print(f"ERROR: cannot re-read calibration log after append: {e}", file=sys.stderr)
        return 2  # infrastructure error, not corruption
    count_after = after_text.count("### Transfer:")

    if count_after != count_before + 1:
        print(
            f"ERROR: calibration log corruption — count changed from {count_before} "
            f"to {count_after} (expected {count_before + 1}). "
            f"Inspect {cal_path} and repair from git history.",
            file=sys.stderr,
        )
        return 1

    if not after_text.endswith(entry):
        print(
            "ERROR: calibration log corruption — last entry does not match appended content. "
            f"Inspect {cal_path} and repair from git history.",
            file=sys.stderr,
        )
        return 1

    print(f"OK: calibration entry appended for '{alg_name}' "
          f"(overall_verdict={overall}, entry {count_after} of log).")
    return 0
```

Add argparse registration in `main()` after `p_ge.set_defaults(func=cmd_generate_evidence)`:

```python
    p_acl = subparsers.add_parser(
        "append-calibration-log",
        help="Append a calibration entry to docs/transfer/calibration_log.md",
    )
    p_acl.add_argument("--workspace", default="workspace/",
                       help="Path to workspace directory")
    p_acl.add_argument("--calibration-log", default="docs/transfer/calibration_log.md",
                       help="Path to calibration log")
    p_acl.set_defaults(func=cmd_append_calibration_log)
```


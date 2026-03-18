# Update sim2real Transfer Pipeline to Use blis_router Input

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Migrate the sim2real transfer pipeline to use the new `blis_router/` input directory structure, replacing the old `routing/` directory, and update the evolved algorithm harness to match the new EVOLVE-BLOCK logic.

**The problem today:** The pipeline's `extract` command hard-codes `best_program.py` and expects a Python wrapper file, but the new evolved algorithm is stored as a pure Go file at `blis_router/best/best_program.go`. The Go harness (`evolved_algorithm.go`) implements the old algorithm's penalty logic (cubic load penalty, CacheHitRate affinity, hard load cutoffs), not the new algorithm's logic (adaptive prefix-affinity decay, subtractive KV penalty at 0.9 threshold, inflight tiebreaker). All prompt templates, tests, and docs still reference `routing/` paths that no longer contain the active algorithm.

**What this PR adds:**
1. `extract` command accepts `blis_router/best/` and reads `best_program.go` (pure Go) instead of `best_program.py` (Python wrapper).
2. Go harness `evolvedAlgorithm` implements the new EVOLVE-BLOCK logic (adaptive prefix-affinity decay, subtractive KV penalty at > 0.9, inflight tiebreaker).
3. All prompt templates updated to reference new input paths (`blis_router/best/`, `blis_router/workloads/`).
4. Golden signal list updated to reflect signals in the new algorithm: `{InFlightRequests, KVUtilization}`.

**Why this matters:** The transfer pipeline must run against the actual evolved algorithm. With the old `routing/` files and old harness logic, Suite A and Suite B/C tests measure the wrong algorithm; the `extract` command fails on the new input; and prompts guide the LLM to files that no longer exist.

**Architecture:** The Python `transfer_cli.py` CLI is updated to read `best_program.go` (file-extension-agnostic: the extract logic searches for EVOLVE-BLOCK markers regardless of language). The Go harness `evolved_algorithm.go` is rewritten to implement the new two-stage EVOLVE-BLOCK: base WeightedScoring + adaptive weight decay + KV subtractive penalty + inflight tiebreaker. Prompt templates are updated in-place.

**PR Category:** Validation (per docs/contributing/pr-workflow.md) — harness Go code rewrite + prompt updates. Includes Perspective 4 (Code correctness) because evolved_algorithm.go is fully rewritten; Validation category is required when Go harness tests (`go test ./tools/harness/...`) are part of the verification gate.

**Source:** Not a macro plan PR; this is a retroactive input-source migration required because the evolutionary search moved from `routing/` to `blis_router/`.

**Behavioral Contracts:** See Part 1, Section B below

---

## PART 1: Design Validation

### A) Executive Summary

This PR migrates the sim2real transfer pipeline from `routing/` to `blis_router/best/` as the input directory, and from the old Python-wrapped EVOLVE-BLOCK to the new pure-Go EVOLVE-BLOCK. The new algorithm uses only `InFlightRequests` and `KVUtilization` directly (no CacheHitRate, SessionID, or EffectiveLoad method call), and applies fundamentally different scoring logic: adaptive prefix-affinity weight decay, a subtractive KV penalty at >0.9 (not >0.82 multiplicative), and a small inflight tiebreaker. Workload YAML files moved from `routing/workload_v2_*.yaml` to `blis_router/workloads/*.yaml` with new names (`glia_40qps`, `prefix_heavy`).

**PR category:** Validation — 5 perspectives: cross-system contracts (1), artifact consistency (2), prompt completeness (3), code correctness (4), plan structural validation (5). Perspective 4 (Code correctness) is included because evolved_algorithm.go is fully rewritten; per docs/contributing/pr-workflow.md, adding Perspective 4 places this PR in the Validation category (full convergence, max 3 rounds).

**DEVIATION flags from Phase 0:**
- DEV-1: `blis_router/best/best_program_info.json` has `"language": "python"` which is incorrect (the program is Go). This field is not consumed by the pipeline and is noted but not corrected in this PR.
- DEV-2: The new `best_program_info.json` uses different metric key names (`glia_40qps_e2e_ms`, `prefix_heavy_e2e_ms`) than the old file (`cache_warmup_e2e_ms`, etc.). The `extract` command passes the whole `metrics` dict through unchanged; downstream stages must be aware of the new key names when the time comes.
- DEV-3: The harness `evolved_algorithm.go` intentionally omits the adaptive prefix-affinity decay technique listed in the EVOLVE-BLOCK. In Suite A canonical tuples `InputTokens=nil` causes all prefix-affinity scores to be 0.0, so the decay branch never fires and the omission does not affect tau. Documented in Section D (Deviation Log) and Section E (Review Guide). Only techniques 2 (KV penalty) and 3 (inflight tiebreaker) are implemented.

### B) Behavioral Contracts

**Positive Contracts:**

BC-1: Extract reads new input files
- GIVEN `blis_router/best/` directory contains `best_program.go` and `best_program_info.json`
- WHEN `python tools/transfer_cli.py extract blis_router/best/` is run
- THEN exit code 0, `workspace/algorithm_summary.json` written with `signal_count >= 2`, `scope_validation_passed: true`
- MECHANISM: `cmd_extract` opens `best_program.go` (not `.py`) and calls `_extract_evolve_block` which finds markers by string search regardless of language.

BC-2: New golden signal list
- GIVEN the EVOLVE-BLOCK in `blis_router/best/best_program.go`
- WHEN `extract` is run
- THEN `signals` array contains exactly `{InFlightRequests, KVUtilization}` and `composite_signals` is empty (no EffectiveLoad call)
- MECHANISM: `_extract_signals` regex finds `snap.InFlightRequests` and `snap.KVUtilization`; no `EffectiveLoad()` call present.

BC-3: KV penalty fires at > 0.9 (not > 0.82)
- GIVEN a routing state with one endpoint at KVUtilization = 0.95 and another at 0.50
- WHEN `evolvedAlgorithm.Route()` is called
- THEN the high-KV endpoint scores strictly lower than the low-KV endpoint
- MECHANISM: new EVOLVE-BLOCK applies `scores[id] -= 0.5*(KVUtil-0.9)/0.1` for KVUtil > 0.9.

BC-4: KV penalty does NOT fire at exactly 0.9
- GIVEN two endpoints both at KVUtilization = 0.9, equal InFlightRequests
- WHEN `evolvedAlgorithm.Route()` is called
- THEN both endpoints receive equal scores
- MECHANISM: condition is strictly `> 0.9`; at exactly 0.9, penalty term is 0.0.

BC-5: Inflight tiebreaker favors lower InFlightRequests
- GIVEN two endpoints with identical base scores and KV utilization, one with InFlightRequests=0, one with InFlightRequests=5
- WHEN `evolvedAlgorithm.Route()` is called
- THEN the endpoint with InFlightRequests=0 receives a higher score
- MECHANISM: `0.01/(1+0) = 0.01 > 0.01/(1+5) ≈ 0.0017`.

BC-6: evolve_block_source references new Go file
- GIVEN extract runs against `blis_router/best/`
- WHEN `workspace/algorithm_summary.json` is written
- THEN `evolve_block_source` contains `blis_router/best/best_program.go:N-M`
- MECHANISM: `_relative_source_path(routing_dir)` + `"best_program.go"` string.

BC-7: Cross-language hash consistency maintained
- GIVEN `workspace/algorithm_summary.json` produced by Python extract
- WHEN Go `LoadAlgorithm` reads the source file and recomputes the SHA-256
- THEN Go hash equals Python hash
- MECHANISM: both compute SHA-256 of the exact byte content of the EVOLVE-BLOCK lines (CRLF-normalized).

BC-8: Prompts reference new input paths and skip guards
- GIVEN `prompts/extract.md`, `prompts/transfer.md`, `prompts/generate.md`, `prompts/validate.md`, `prompts/translate.md`
- WHEN the LLM reads these prompts
- THEN all pre-flight checks and artifact references point to `blis_router/best/best_program.go` and `blis_router/workloads/*.yaml`; AND `prompts/translate.md` Step 4 contains the F-10 skip guard for empty `composite_signals`
- MECHANISM: in-place text updates to prompt files; skip guard verified by grep in Task 6 Step 4.

**Negative Contracts:**

BC-9: extract MUST fail on `routing/` (old directory, missing best_program.go)
- GIVEN `routing/` directory (which has `best_program.py` not `.go`)
- WHEN `extract routing/` is run
- THEN exit code 2 with error "best_program.go not found in <routing_dir>"
- NOTE: This is acceptable breakage — `routing/` is the old input and is no longer supported.

BC-10: Old algorithm logic MUST NOT remain in harness
- GIVEN `tools/harness/evolved_algorithm.go`
- WHEN tests run
- THEN no reference to cubic load penalty (`loadDelta*loadDelta*loadDelta`), no CacheHitRate access, no SessionID access, no hard load cutoffs (>7.0 or >4.5)
- MECHANISM: `evolved_algorithm.go` is fully rewritten.

**Error Handling Contracts:**

BC-11: best_program.go missing → exit 2
- GIVEN `blis_router/best/` exists but `best_program.go` is absent
- WHEN `extract blis_router/best/` is run
- THEN exit code 2, error message contains "best_program.go not found"

BC-12: Stale hash detection still works with .go source
- GIVEN `algorithm_summary.json` with hash from `blis_router/best/best_program.go`
- WHEN `best_program.go` is modified after extraction
- THEN `LoadAlgorithm` returns error containing "hash mismatch"
- MECHANISM: `TestStaleHashAbortsParsing` verifies this with temp files.

### C) Component Interaction

```
blis_router/best/
  best_program.go         ← EVOLVE-BLOCK source (new)
  best_program_info.json  ← metrics + iteration info (new)
blis_router/workloads/
  workload_glia_40qps.yaml        ← workload for validate stage (new)
  workload_glia_prefix_heavy.yaml ← workload for validate stage (new)
         │
         ▼ (Stage 1: extract)
tools/transfer_cli.py  [cmd_extract]
  reads best_program.go, extracts EVOLVE-BLOCK, computes SHA-256
         │
         ▼ writes
workspace/algorithm_summary.json
  evolve_block_source: "blis_router/best/best_program.go:N-M"
  signals: [InFlightRequests, KVUtilization]
  composite_signals: []
         │
         ├── (Go harness: LoadAlgorithm verifies hash)
         │       tools/harness/harness.go
         │       tools/harness/evolved_algorithm.go [REWRITTEN]
         │
         └── (Stage 2-6: downstream prompt stages)
               prompts/extract.md   [UPDATED paths]
               prompts/transfer.md  [UPDATED paths]
               prompts/generate.md  [UPDATED ref]
               prompts/validate.md  [UPDATED workload glob]
```

**Workspace artifact chain:** `algorithm_summary.json` writer is Stage 1 (`extract`). Readers are: Stage 2 (`translate`), `LoadAlgorithm` in harness. No schema field changes — `signals` array format is unchanged.

**Dead artifact check:** No new files created. All modified files have existing consumers.

### D) Deviation Log

| Macro Plan Says | Micro Plan Does | Reason |
|-----------------|-----------------|--------|
| N/A — no macro plan section for this migration | Migrate from routing/ to blis_router/best/ | Evolutionary search infrastructure changed input location |
| (Not documented) extract uses best_program.py | extract uses best_program.go | New algorithm stored as pure Go, not Python wrapper |
| prompts/validate.md: routing/workload_v2_*.yaml | blis_router/workloads/*.yaml | Workloads renamed and relocated in new experiment |
| harness evolved_algorithm: KV penalty > 0.82 multiplicative | new EVOLVE-BLOCK: KV penalty > 0.9 subtractive | New algorithm has different scoring logic |
| EXPECTED_SIGNALS = {QueueDepth, BatchSize, InFlightRequests, KVUtilization, CacheHitRate, SessionID} | EXPECTED_SIGNALS = {InFlightRequests, KVUtilization} | New EVOLVE-BLOCK does not access these signals directly |
| DEV-1: blis_router/best/best_program_info.json should have "language": "go" | File has "language": "python" — not corrected | Informational metadata field not consumed by pipeline; intentional known debt |
| DEV-2: metric keys should match previous pattern (cache_warmup_e2e_ms etc.) | New file uses different keys (glia_40qps_e2e_ms, prefix_heavy_e2e_ms) | extract passes metrics dict through unchanged; downstream stages must handle new key names |
| evolved_algorithm.go should implement all EVOLVE-BLOCK techniques | Adaptive prefix-affinity decay (technique 1) is omitted | decay branch never fires in canonical Suite A tuples (InputTokens=nil → prefix score 0.0 ≤ 0.1); simplification achieves tau > 0.8 |
| blis_to_llmd_mapping.md should reflect all actively translated signals | QueueDepth row removed from mapping artifact (Task 8 Step 3) despite active translation in evolved_scorer.go line 96 (`QueueDepth: m.WaitingQueueSize`) | QueueDepth is not in the new EVOLVE-BLOCK signal set `{InFlightRequests, KVUtilization}`; leaving it in the mapping causes `validate-mapping` to report it as "extra" and exit 1; removal is required for CI compliance. **DEV-4: A reader of the mapping artifact will not see the QueueDepth translation; the ground truth is evolved_scorer.go line 96.** |

### E) Review Guide

**THE TRICKY PART:** The `evolved_algorithm.go` rewrite. The new EVOLVE-BLOCK uses `ws.scorers` directly (getting per-scorer per-instance scores), but the harness only has access to `WeightedScoring.Route()` as a black box. In Suite A canonical tuples, `InputTokens=nil` means prefix-affinity scores all 0.0 for all instances, so the adaptive decay branch (`bestPrefixScore > 0.1`) never fires. This means the harness implementation can safely skip the adaptive decay logic and still achieve tau > 0.8 in Suite A. This simplification MUST be documented clearly in the code comment.

**WHAT TO SCRUTINIZE:** BC-3 and BC-4 (KV penalty boundary). The old threshold was 0.82 (multiplicative), the new is 0.9 (subtractive). Test data in `harness_test.go` must use 0.95 (not 0.90) to trigger the penalty in single-endpoint and boundary tests.

**WHAT'S SAFE TO SKIM:** File path string replacements in prompts and docs — these are mechanical find-replace operations.

**KNOWN DEBT:** `blis_router/best/best_program_info.json` has `"language": "python"` which is inaccurate. Not corrected here — it's informational metadata not consumed by the pipeline.

---

## PART 2: Executable Implementation

### F) Implementation Overview

Files to modify (no new files created):

| File | Change |
|------|--------|
| `tools/transfer_cli.py` | `best_program.py` → `best_program.go` in cmd_extract (lines ~366-401, 494) |
| `tools/test_transfer_cli.py` | ROUTING_DIR, EXPECTED_SIGNALS, EXPECTED_COMPOSITES, all `best_program.py` refs |
| `tools/harness/evolved_algorithm.go` | Full rewrite of Route() method for new EVOLVE-BLOCK |
| `tools/harness/harness_test.go` | Path strings, KV boundary values in 4 tests |
| `tools/harness/evolved_scorer.go` | Remove dead SessionID header reading; update comment |
| `tools/harness/harness.go` | Update stale `.py` comment at line 73 to reference new `.go` file format |
| `prompts/extract.md` | File path strings + CLI command |
| `prompts/transfer.md` | Table row + pre-flight check |
| `prompts/generate.md` | EVOLVE-BLOCK source reference |
| `prompts/validate.md` | Workload glob + HALT message + classification rule |
| `CLAUDE.md` | CLI example + pipeline status |
| `docs/manual-testing-guide.md` | PR1 and PR5 test commands |
| `docs/transfer/blis_to_llmd_mapping.md` | Remove rows for signals not in new EVOLVE-BLOCK (QueueDepth, BatchSize, CacheHitRate, EffectiveLoad, SessionID) so `validate-mapping` does not report extra signals |
| `tools/schemas/algorithm_summary.schema.json` | Update `evolve_block_source` pattern from `.py`-only to extension-agnostic (`.[a-zA-Z]+`) so `.go` paths pass schema validation; update description to remove Python-only parsing instructions |
| `prompts/translate.md` | Add skip guard to Step 4 F-10 Double-Counting Detection: skip entirely when `composite_signals` is empty (which it is for the new blis_router EVOLVE-BLOCK) |

**Key decisions:**
1. `extract` input is `blis_router/best/` (the directory containing both files), not `blis_router/` (the package root).
2. Adaptive prefix-affinity decay is omitted from `evolved_algorithm.go` — it requires access to individual scorer scores which the harness doesn't expose, and it never fires in canonical Suite A tuples.
3. `evolved_scorer.go` keeps QueueDepth translation (base WeightedScoring uses EffectiveLoad which includes QueueDepth); removes only the SessionID translation.

**Confirmation:** All modified files have existing consumers. No dead artifacts introduced. `docs/transfer/blis_to_llmd_mapping.md` must be updated alongside signal set changes to keep `validate-mapping` from exiting 1 on extra signals.

### G) Task Breakdown

---

#### Task 1: Update transfer_cli.py for Go input file

**Contracts Implemented:** BC-1, BC-2, BC-6, BC-9, BC-11
**Language:** Python

**Files:**
- Modify: `tools/transfer_cli.py`
- Modify: `tools/schemas/algorithm_summary.schema.json`

**Step 1: Write failing tests**

Two existing tests will fail after the change and serve as our failing tests:
- `test_extract_source_path_in_summary` (verifies `best_program.py` is in source path)
- `test_extracted_signals_match_golden_list` (checks old signal set)

Run to confirm they currently pass:
```bash
.venv/bin/python -m pytest tools/test_transfer_cli.py::TestExtractBasicBehavior::test_extract_includes_metrics tools/test_transfer_cli.py::TestGoldenSignalList::test_extracted_signals_match_golden_list -v
```
Expected: PASS (they're green now)

**Step 2: Implement changes in transfer_cli.py**

Find and update `cmd_extract`:

Line ~363: Change
```python
            "Usage: python tools/transfer_cli.py extract --strict routing/"
```
To:
```python
            "Usage: python tools/transfer_cli.py extract --strict blis_router/best/"
```
(This is the usage hint in the CI guard error message — it must match the new input path so developers running in CI receive correct instructions.)

Line ~366: Change
```python
    program_py = routing_dir / "best_program.py"
    if not program_py.exists():
        return _output("error", 2, errors=[f"best_program.py not found in {routing_dir}"])
```
To:
```python
    program_go = routing_dir / "best_program.go"
    if not program_go.exists():
        return _output("error", 2, errors=[f"best_program.go not found in {routing_dir}"])
```

Line ~377: Change
```python
        if program_py.stat().st_size > MAX_FILE_SIZE:
            return _output("error", 2, errors=[
                f"best_program.py exceeds {MAX_FILE_SIZE} bytes — refusing to read."])
```
To:
```python
        if program_go.stat().st_size > MAX_FILE_SIZE:
            return _output("error", 2, errors=[
                f"best_program.go exceeds {MAX_FILE_SIZE} bytes — refusing to read."])
```

Line ~387: Change
```python
        source = program_py.read_text()
    except OSError as e:
        return _output("error", 2, errors=[f"Failed to read {program_py}: {e}"])
    block, line_range, block_error = _extract_evolve_block(source)
    if block is None:
        _ERROR_MESSAGES = {
            "no_markers": "Neither EVOLVE-BLOCK-START nor EVOLVE-BLOCK-END markers found in best_program.py.",
            "end_without_start": "EVOLVE-BLOCK-END found without EVOLVE-BLOCK-START in best_program.py.",
            "start_without_end": "EVOLVE-BLOCK-START found but no EVOLVE-BLOCK-END in best_program.py.",
            "inverted_markers": "EVOLVE-BLOCK-END appears before EVOLVE-BLOCK-START in best_program.py (inverted markers).",
        }
```
To:
```python
        source = program_go.read_text()
    except OSError as e:
        return _output("error", 2, errors=[f"Failed to read {program_go}: {e}"])
    block, line_range, block_error = _extract_evolve_block(source)
    if block is None:
        _ERROR_MESSAGES = {
            "no_markers": "Neither EVOLVE-BLOCK-START nor EVOLVE-BLOCK-END markers found in best_program.go.",
            "end_without_start": "EVOLVE-BLOCK-END found without EVOLVE-BLOCK-START in best_program.go.",
            "start_without_end": "EVOLVE-BLOCK-START found but no EVOLVE-BLOCK-END in best_program.go.",
            "inverted_markers": "EVOLVE-BLOCK-END appears before EVOLVE-BLOCK-START in best_program.go (inverted markers).",
        }
```

Line ~400: Change
```python
        error_msg = _ERROR_MESSAGES.get(block_error,
            "EVOLVE-BLOCK markers not found or malformed in best_program.py.")
```
To:
```python
        error_msg = _ERROR_MESSAGES.get(block_error,
            "EVOLVE-BLOCK markers not found or malformed in best_program.go.")
```

Line ~494: Change
```python
        "evolve_block_source": f"{_relative_source_path(routing_dir)}best_program.py:{line_range}",
```
To:
```python
        "evolve_block_source": f"{_relative_source_path(routing_dir)}best_program.go:{line_range}",
```

**Step 3: Run extract against new input to verify**

```bash
.venv/bin/python tools/transfer_cli.py extract blis_router/best/
```
Expected: exit code 0, JSON output with `status: "ok"`, `signal_count >= 2`.

Also verify it fails on old input:
```bash
.venv/bin/python tools/transfer_cli.py extract routing/ 2>&1; echo "Exit: $?"
```
Expected: exit code 2, error contains "best_program.go not found".

**Step 4: Update tools/schemas/algorithm_summary.schema.json**

The `evolve_block_source` pattern must accept `.go` extensions. Verify the pattern is already extension-agnostic:
```bash
grep '"pattern"' tools/schemas/algorithm_summary.schema.json | head -2
```
Expected: pattern contains `[a-zA-Z]+` (not `.py`-only). If not already updated, change the `evolve_block_source` pattern from `\\.py` to `\\.[a-zA-Z]+`.

Also update the `evolve_block_content_hash` description to remove Python-specific language. The description currently reads "raw Python file lines from EVOLVE-BLOCK-START through EVOLVE-BLOCK-END". Change "raw Python file lines" to "raw source file lines" so the description applies to both `.py` and `.go` source files.

Verify:
```bash
grep "evolve_block_content_hash" -A3 tools/schemas/algorithm_summary.schema.json
```
Expected: description says "raw source file lines" (not "Python").

**Step 5: Commit**

**⚠ Broken intermediate state note:** After this commit, `TestCrossLanguageHashConsistency` in `harness_test.go` will fail if Go tests are run before Task 4 completes. The test's skip guard checks for `routing/best_program.py` (which still exists), so the skip does NOT fire; then extract runs against `routing/` and exits 2 (best_program.go not found). This is fixed in Task 4 Step 2c. Do not run Go harness tests between Tasks 1 and 4.

```bash
git add tools/transfer_cli.py tools/schemas/algorithm_summary.schema.json
git commit -m "$(cat <<'EOF'
feat(tools): update extract to read best_program.go from blis_router/best/

- cmd_extract now opens best_program.go instead of best_program.py
- evolve_block_source in algorithm_summary.json references .go file
- error messages updated to reference new file name (BC-1, BC-6, BC-11)
- schema: evolve_block_source pattern accepts any extension (.[a-zA-Z]+)
- schema: evolve_block_content_hash description updated to language-neutral wording

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

#### Task 2: Update Python tests (routing dir, file names, golden signals)

**Contracts Implemented:** BC-2
**Language:** Python

**Files:**
- Modify: `tools/test_transfer_cli.py`

**Step 1: Write failing test**

The golden signal list test will fail because `ROUTING_DIR` still points to `routing/` and `EXPECTED_SIGNALS` is the old set. Confirm the test's current state:
```bash
.venv/bin/python -m pytest tools/test_transfer_cli.py::TestGoldenSignalList -v
```
**⚠ Expected state after Task 1:** FAIL — Task 1 changed `cmd_extract` to look for `best_program.go`; running extract against `routing/` (which only has `best_program.py`) exits 2 with "best_program.go not found". The test fails at extract execution, not at signal comparison. This is expected and will be fixed by updating `ROUTING_DIR` in Step 2 below.

**Step 2: Update test_transfer_cli.py**

Line 11: Change
```python
ROUTING_DIR = REPO_ROOT / "routing"
```
To:
```python
ROUTING_DIR = REPO_ROOT / "blis_router" / "best"
```

Lines 520-527: Change `EXPECTED_SIGNALS` and `EXPECTED_COMPOSITES`:
```python
    EXPECTED_SIGNALS = {
        "QueueDepth", "BatchSize", "InFlightRequests",
        "KVUtilization", "CacheHitRate", "SessionID",
    }

    EXPECTED_COMPOSITES = {
        "EffectiveLoad": {"QueueDepth", "BatchSize", "InFlightRequests"},
    }
```
To:
```python
    EXPECTED_SIGNALS = {
        "InFlightRequests",
        "KVUtilization",
    }

    EXPECTED_COMPOSITES = {}
```

All occurrences of `best_program.py` in test helper code (file copies, inline writes, error message assertions): replace with `best_program.go`.

Use this approach: search for all `best_program.py` in the file and replace with `best_program.go`. There are approximately 25 occurrences.

Key locations:
- Line 92: `source = (ROUTING_DIR / "best_program.py").read_text()` → `best_program.go`
- Line 118, 119: shutil.copy2 calls in temp dir setup → `best_program.go`
- Line 125: `(tmpdir / "best_program.py").write_text(src)` → `best_program.go`
- Lines 147, 163, 176, 210, 236, 261, 281, 318, 337, 356 etc: all inline test `best_program.py` writes → `best_program.go`
- Lines 300, 378, 398, 839, 874 etc: shutil.copy2 from ROUTING_DIR → `best_program.go`

Also add a new test function `test_extract_missing_go_file_exits_2` for BC-11 (missing `best_program.go` → exit 2). Add this after the existing `test_extract_missing_info_json_exits_2` test:

```python
def test_extract_missing_go_file_exits_2(self, tmp_path):
    """BC-11: extract exits 2 when best_program.go is absent from the routing dir."""
    # Provide best_program_info.json but NOT best_program.go
    info = tmp_path / "best_program_info.json"
    info.write_text('{"language": "go", "metrics": {}}')
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "transfer_cli.py"), "extract", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 2, f"expected exit 2, got {result.returncode}"
    assert "best_program.go not found" in result.stdout or "best_program.go not found" in result.stderr, \
        f"expected 'best_program.go not found' in output; stdout={result.stdout!r}"
```

Note: `test_extract_missing_info_json_exits_2` covers the missing `best_program_info.json` code path (different from BC-11); BC-11 requires this dedicated test for the missing `best_program.go` path.

**Step 3: Run updated tests**

```bash
.venv/bin/python -m pytest tools/test_transfer_cli.py -v --tb=short 2>&1 | tail -30
```
Expected: All tests pass including the new `test_extract_missing_go_file_exits_2`. If any test fails because it still references `best_program.py` as an expected error message string (e.g. asserting the error message contains "best_program.py"), update those assertion strings to `best_program.go`.

**Step 4: Run full Python test suite**

```bash
.venv/bin/python -m pytest tools/ -v 2>&1 | tail -20
```
Expected: All tests pass.

**Step 5: Commit**

```bash
git add tools/test_transfer_cli.py
git commit -m "$(cat <<'EOF'
test(tools): update ROUTING_DIR to blis_router/best/, golden signals to new set

- ROUTING_DIR → blis_router/best/
- EXPECTED_SIGNALS → {InFlightRequests, KVUtilization} (BC-2)
- EXPECTED_COMPOSITES → {} (no EffectiveLoad in new EVOLVE-BLOCK)
- All best_program.py references → best_program.go
- Add test_extract_missing_go_file_exits_2 for BC-11

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

#### Task 3: Rewrite evolved_algorithm.go for new EVOLVE-BLOCK logic

**Contracts Implemented:** BC-3, BC-4, BC-5, BC-10
**Language:** Go

**Files:**
- Modify: `tools/harness/evolved_algorithm.go`

**Step 0: Verify required API surface in inference-sim submodule**

These checks MUST pass before writing any code in Step 2. The implementation calls `baseDecision.Scores` and `sim.NewRoutingDecisionWithScores` — if either is absent the code will not compile.

```bash
grep -n "Scores" inference-sim/sim/routing.go
```
Expected: a line containing `Scores` as a field of `RoutingDecision` (e.g., `Scores map[string]float64`). If absent, **HALT** and report that the `sim.RoutingDecision` struct lacks the `Scores` field — the implementation cannot proceed without this API.

```bash
grep -n "NewRoutingDecisionWithScores" inference-sim/sim/routing.go
```
Expected: a function definition line containing `NewRoutingDecisionWithScores`. If absent, **HALT** and report that `sim.NewRoutingDecisionWithScores` does not exist in the submodule — the implementation cannot proceed without this constructor.

**Step 1: Write failing tests**

The existing tests `TestEvolvedAlgorithmSingleEndpoint` and `TestEvolvedAlgorithmKVPenaltyBoundary` will be updated in Task 4. First, add a new test that captures the new KV penalty boundary. But first verify the old tests currently pass:
```bash
go test ./tools/harness/... -run "TestEvolvedAlgorithm" -v
```
Expected: Tests pass with old algorithm.

**Step 2: Rewrite evolved_algorithm.go**

Replace the full file content:

```go
package harness

import (
	sim "github.com/inference-sim/inference-sim/sim"
)

// evolvedAlgorithm implements Algorithm using the EVOLVE-BLOCK logic from
// blis_router/best/best_program.go (WeightedScoring.Route EVOLVE-BLOCK-START to
// EVOLVE-BLOCK-END).
//
// The new EVOLVE-BLOCK adds three techniques on top of the base WeightedScoring:
//   1. Adaptive prefix-affinity decay: when the best prefix-cached instance is
//      overloaded, decay its weight by 1/(1 + 0.6*load_delta).
//   2. KV pressure penalty (subtractive): scores[id] -= 0.5*(KVUtil-0.9)/0.1
//      when KVUtilization > 0.9. Fires at >0.9, NOT at exactly 0.9.
//   3. Fresh load tiebreaker: scores[id] += 0.01/(1+InFlightRequests).
//
// HARNESS SIMPLIFICATION: The adaptive prefix-affinity decay (technique 1) is
// omitted from this implementation. In Suite A canonical tuples, sim.Request.InputTokens
// is nil, causing all prefix-affinity scores to be 0.0 (totalBlocks==0 → no match).
// Since bestPrefixScore is always 0.0 ≤ 0.1, the decay branch never fires.
//
// IMPORTANT: sim.NewRoutingPolicy("weighted", ...) returns a WeightedScoring
// whose Route() method IS the full EVOLVE-BLOCK, including techniques 2
// (KV pressure penalty) and 3 (inflight tiebreaker). Delegating to a.base.Route()
// therefore runs all three techniques in one call. The scores returned in
// baseDecision.Scores already have the KV penalty subtracted and the tiebreaker
// added — do NOT re-apply them after calling a.base.Route().
//
// NOTE: This implementation does NOT use CacheHitRate, SessionID, or
// EffectiveLoad() directly (none are accessed in the new EVOLVE-BLOCK).
// DO NOT modify without re-running evolutionary optimization against
// blis_router/best/best_program.go.
type evolvedAlgorithm struct {
	base sim.RoutingPolicy
}

// newEvolvedAlgorithm creates an evolvedAlgorithm with inference-sim's default scorer
// configuration (prefix-affinity:3, queue-depth:2, kv-utilization:2, blockSize=64).
// blockSize=64 matches the default used in inference-sim cluster simulations.
func newEvolvedAlgorithm() *evolvedAlgorithm {
	return &evolvedAlgorithm{
		base: sim.NewRoutingPolicy("weighted", sim.DefaultScorerConfigs(), 64, nil),
	}
}

// Route implements Algorithm. It runs the EVOLVE-BLOCK logic by delegating to
// a.base (sim.NewRoutingPolicy("weighted", ...)), whose Route() method IS the
// full EVOLVE-BLOCK including:
//  1. Adaptive prefix-affinity decay (never fires in Suite A; InputTokens=nil).
//  2. KV pressure penalty: subtract 0.5*(KVUtil-0.9)/0.1 when KVUtil > 0.9.
//  3. Fresh load tiebreaker: add 0.01/(1+InFlightRequests).
// The returned baseDecision.Scores already have all three techniques applied.
// The argmax here selects the winner and relabels the decision as "evolved".
//
// WARNING — observer-callback / prefix-affinity history poisoning:
// The call to a.base.Route() below fires WeightedScoring's internal observer
// callbacks, which record the final post-EVOLVE-BLOCK routing decision (the
// argmax after KV penalty and tiebreaker are applied) in the prefix-affinity
// scorer's session history — this is the actual evolved target, not a stale
// base-only argmax. In Suite A canonical tuples sim.Request.InputTokens is nil,
// so all prefix-affinity scores are 0.0 (totalBlocks==0 → no match) and the
// observer records no preference; the callback is harmless in that case.
// However, future test authors who construct requests with non-nil InputTokens
// must be aware: if the KV pressure penalty changes the argmax relative to a
// purely prefix-affinity-driven choice, the observer records the KV-adjusted
// target, which then influences prefix-affinity scores for subsequent requests
// in the same session. This is correct behavior (the observer records the actual
// routing target), but it can cause surprising prefix-affinity score drift in
// multi-request session tests.
func (a *evolvedAlgorithm) Route(req *sim.Request, state *sim.RouterState) sim.RoutingDecision {
	snapshots := state.Snapshots
	if len(snapshots) == 0 {
		panic("evolvedAlgorithm.Route: empty snapshots")
	}

	// Step 1: Delegate to base WeightedScoring, which IS the full EVOLVE-BLOCK.
	// baseDecision.Scores already contains scores with KV penalty (technique 2)
	// and inflight tiebreaker (technique 3) applied — do NOT re-apply them.
	baseDecision := a.base.Route(req, state)

	// Step 2: Argmax over the already-final scores — select instance with highest
	// score (first wins on tie). Relabel the decision as "evolved".
	scores := baseDecision.Scores
	bestScore := scores[snapshots[0].ID]
	bestIdx := 0
	for i, snap := range snapshots[1:] {
		if scores[snap.ID] > bestScore {
			bestScore = scores[snap.ID]
			bestIdx = i + 1
		}
	}

	return sim.NewRoutingDecisionWithScores(snapshots[bestIdx].ID, "evolved", scores)
}
```

**Step 3: Build to verify compilation**

```bash
go build ./tools/harness/...
```
Expected: clean build, no errors.

**Step 4: Run harness unit tests (before updating test boundaries)**

```bash
go test ./tools/harness/... -run "TestEvolvedAlgorithm" -v 2>&1
```
Expected: Both `TestEvolvedAlgorithmSingleEndpoint` and `TestEvolvedAlgorithmKVPenaltyBoundary` will **PASS** — this is expected and does NOT mean the rewrite is wrong. Here is why:

- `TestEvolvedAlgorithmSingleEndpoint`: The test uses KVUtil=0.90. After rewrite, the penalty condition is `KVUtil > 0.9` (strictly). At 0.90, `0.90 > 0.9` is false — the penalty does NOT fire. Base WeightedScoring + tiebreaker produces a score ≈ 0.324, which satisfies both existing assertions (`score > 0.0` and `score < 1.0`). **The test passes, but it is no longer testing the KV penalty.** Task 4 Step 2e updates the test to use KVUtil=0.95 (> 0.9) so the penalty actually fires.
- `TestEvolvedAlgorithmKVPenaltyBoundary`: Both endpoints are at KVUtil=0.82, which is below both the old threshold (0.82 > 0.82 = false) and the new threshold (0.82 > 0.9 = false). Equal scores are produced before and after the rewrite. **The test passes, but is testing the wrong boundary.** Task 4 Step 2f updates the test to use KVUtil=0.9 to test the new boundary.

⚠️ **If you see PASS for these two tests after Task 3, that is correct behavior — do not second-guess your rewrite.** The tests will be updated in Task 4 to test the correct threshold values.

Note: `TestLoadAlgorithmReturnsEvolved` is expected to PASS after Task 3 because the base WeightedScoring kv-utilization scorer computes `1 - KVUtilization`, giving 0.10 for high-kv=0.90 vs 0.50 for low-kv=0.50. The base scorer already differentiates the endpoints, so the assertion `high-kv score >= low-kv score` passes. Task 4 sub-step 2d still updates this test to use KVUtil=0.95 (instead of 0.90) so that the KV *penalty* (not just the base scorer) is also exercised; that change does not break the test.

Do NOT update boundary values yet; they are updated in Task 4.

**Step 5: Commit**

```bash
git add tools/harness/evolved_algorithm.go
git commit -m "$(cat <<'EOF'
feat(harness): rewrite evolvedAlgorithm for new EVOLVE-BLOCK (blis_router)

- Replace old cubic load penalty + CacheHitRate + SessionID logic
- Implement new KV penalty: subtractive at >0.9 (not multiplicative at >0.82)
- Implement inflight tiebreaker: +0.01/(1+InFlightRequests)
- Skip adaptive prefix-affinity decay (never fires in canonical Suite A tuples)
- Contracts: BC-3, BC-4, BC-5, BC-10

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

#### Task 4: Update harness tests for new algorithm and new paths

**Contracts Implemented:** BC-3, BC-4, BC-7, BC-12
**Language:** Go

**Files:**
- Modify: `tools/harness/harness_test.go`

**Step 1: Verify current test state**

```bash
go test ./tools/harness/... -run "TestEvolvedAlgorithmSingleEndpoint|TestEvolvedAlgorithmKVPenaltyBoundary" -v 2>&1
```
Expected: Both tests **PASS** (see Task 3 Step 4 for explanation). This is correct — the tests are testing stale threshold values (0.90 and 0.82 respectively) that happen to still pass after the rewrite, but they no longer exercise the KV penalty. Steps 2e and 2f below update the tests to use the correct threshold values (0.95 and 0.9) so they actually validate the new algorithm behavior.

**Step 2: Update harness_test.go**

**2a. TestStaleHashAbortsParsing (line ~66-71, 84):**

Change:
```go
sourceDir := filepath.Join(repoRoot, "routing")
...
sourcePath := filepath.Join(sourceDir, "best_program.py")
...
"evolve_block_source": "routing/best_program.py:2-4",
```
To:
```go
sourceDir := filepath.Join(repoRoot, "blis_router", "best")
...
sourcePath := filepath.Join(sourceDir, "best_program.go")
...
"evolve_block_source": "blis_router/best/best_program.go:2-4",
```

**2b. TestUnknownSignalTypeRejection (line ~189):**

Change:
```go
"evolve_block_source": "routing/best_program.py:1-1",
```
To:
```go
"evolve_block_source": "blis_router/best/best_program.go:1-1",
```

**2c. TestCrossLanguageHashConsistency (lines ~224-232):**

Change:
```go
// Use the actual routing/best_program.py and run extract
routingDir := filepath.Join(repoRoot, "routing")
if _, err := os.Stat(filepath.Join(routingDir, "best_program.py")); err != nil {
    t.Skip("requires routing/best_program.py")
}

// Run extract to get the Python-computed hash.
cmd := exec.Command(venvPython, filepath.Join(repoRoot, "tools", "transfer_cli.py"), "extract", routingDir)
```
To:
```go
// Use the actual blis_router/best/best_program.go and run extract
routingDir := filepath.Join(repoRoot, "blis_router", "best")
if _, err := os.Stat(filepath.Join(routingDir, "best_program.go")); err != nil {
    t.Skip("requires blis_router/best/best_program.go")
}

// Run extract to get the Python-computed hash.
cmd := exec.Command(venvPython, filepath.Join(repoRoot, "tools", "transfer_cli.py"), "extract", routingDir)
```

**2d. TestLoadAlgorithmReturnsEvolved (lines ~360-374):**

The high-load test remains valid. Update the KV penalty test to use 0.95 (triggers penalty) not 0.90 (does not trigger with new >0.9 condition):

Change:
```go
// BC-3: KV pressure penalty fires when KVUtilization > 0.82
kvState := sim.RouterState{
    Snapshots: []sim.RoutingSnapshot{
        {ID: "high-kv", QueueDepth: 0, KVUtilization: 0.90},
        {ID: "low-kv",  QueueDepth: 0, KVUtilization: 0.50},
    },
}
```
To:
```go
// BC-3: KV pressure penalty fires when KVUtilization > 0.9 (new threshold)
kvState := sim.RouterState{
    Snapshots: []sim.RoutingSnapshot{
        {ID: "high-kv", QueueDepth: 0, KVUtilization: 0.95},
        {ID: "low-kv",  QueueDepth: 0, KVUtilization: 0.50},
    },
}
```

**2e. TestEvolvedAlgorithmSingleEndpoint (lines ~383-402):**

Change the test to use KVUtilization = 0.95 (> 0.9) to trigger the penalty:
```go
state := sim.RouterState{
    Snapshots: []sim.RoutingSnapshot{
        {ID: "solo", QueueDepth: 0, InFlightRequests: 0, KVUtilization: 0.90},
    },
}
```
To:
```go
state := sim.RouterState{
    Snapshots: []sim.RoutingSnapshot{
        {ID: "solo", QueueDepth: 0, InFlightRequests: 0, KVUtilization: 0.95},
    },
}
```

Also replace the stale comment and assertions with a comment block and two new assertions:
```go
// KV penalty fires: scores[id] -= 0.5*(0.95-0.9)/0.1 = 0.25 (subtractive).
// Base WeightedScoring score at KVUtil=0.95 ≈ 0.324; tiebreaker adds 0.01/(1+0)=0.01.
// Unpenalized total ≈ 0.334; penalized ≈ 0.334 - 0.25 = 0.084.
// Assert score < 0.3 (strictly below the unpenalized base ~0.334) to verify the penalty
// actually fired — score < 1.0 alone is vacuously true with or without the penalty.
// NOTE: do NOT assert score > 0.0; subtractive penalty can produce negative scores.
if score >= 0.3 {
    t.Errorf("expected score < 0.3 (KV penalty fired: 0.5*(0.95-0.9)/0.1=0.25 subtracted from base ~0.334), got %f", score)
}
if decision.TargetInstance != "solo" {
    t.Errorf("expected TargetInstance='solo' (only endpoint), got %q", decision.TargetInstance)
}
```

Remove the old `score <= 0.0` assertion at line ~394 (subtractive penalty can produce negative scores when base score < 0.25, which is valid; argmax still selects the sole endpoint correctly). Use `score >= 0.3` as the discriminating assertion: at KVUtil=0.95 the penalty term 0.5*(0.95-0.9)/0.1 = 0.25 is subtracted from the unpenalized base score ~0.334, yielding ~0.084 < 0.3. A bug omitting the penalty would leave the score at ~0.334 ≥ 0.3, causing the assertion to fail and detecting the regression. `score < 1.0` alone is vacuously true with or without the penalty (base WeightedScoring scores are always < 1.0). Add `TargetInstance == "solo"` to verify the routing decision is correct.

**2f. TestEvolvedAlgorithmKVPenaltyBoundary (lines ~410-426):**

Update the boundary value to the new threshold (0.9, not 0.82):
```go
state := sim.RouterState{
    Snapshots: []sim.RoutingSnapshot{
        {ID: "ep-0", QueueDepth: 0, InFlightRequests: 0, KVUtilization: 0.82},
        {ID: "ep-1", QueueDepth: 0, InFlightRequests: 0, KVUtilization: 0.82},
    },
}
```
To:
```go
state := sim.RouterState{
    Snapshots: []sim.RoutingSnapshot{
        {ID: "ep-0", QueueDepth: 0, InFlightRequests: 0, KVUtilization: 0.9},
        {ID: "ep-1", QueueDepth: 0, InFlightRequests: 0, KVUtilization: 0.9},
    },
}
```
And update the test comment from "exactly KV=0.82 (boundary)" to "exactly KV=0.9 (boundary)".

**2g. TestLoadAlgorithmErrorPaths — source file and summary path stubs (lines ~540-591):**

Change all occurrences of:
```go
sourceDir := filepath.Join(repoRoot, "routing")
...
os.WriteFile(filepath.Join(sourceDir, "best_program.py"), ...)
...
"evolve_block_source": "routing/best_program.py:..."
```
To:
```go
sourceDir := filepath.Join(repoRoot, "blis_router", "best")
...
os.WriteFile(filepath.Join(sourceDir, "best_program.go"), ...)
...
"evolve_block_source": "blis_router/best/best_program.go:..."
```

Also change the **no-colon** error-path stub (the `"invalid source format (no colon)"` test case,
which uses a bare path with no colon suffix and therefore is NOT matched by the `:...` pattern
above):
```go
"evolve_block_source": "routing/best_program.py"
```
To:
```go
"evolve_block_source": "blis_router/best/best_program.go"
```
This test case validates that `LoadAlgorithm` rejects a path that has no `file:line-line` colon
separator; keeping the old `.py` path would leave a stale reference to the pre-migration source
location in the updated test file.

**2h. TestEvolvedAlgorithmInflightTiebreaker — new test for BC-5 (add after 2g):**

Add a new test function to `harness_test.go` that verifies the inflight tiebreaker in isolation:

```go
// TestEvolvedAlgorithmInflightTiebreaker verifies BC-5: the 0.01/(1+InFlightRequests)
// tiebreaker favors the endpoint with fewer in-flight requests when all other scoring
// factors are equal.
//
// ISOLATION DESIGN: InFlightRequests feeds into EffectiveLoad (QueueDepth + BatchSize +
// InFlightRequests), which the base WeightedScoring load-balance scorer uses. Setting
// InFlightRequests=0 vs 5 with QueueDepth=0 for both would make EffectiveLoad 0 vs 5,
// causing large base score differences that mask the tiebreaker. To isolate the
// tiebreaker, we compensate with QueueDepth so EffectiveLoad is equal:
//   idle:  QueueDepth=5, InFlightRequests=0 → EffectiveLoad=5
//   busy:  QueueDepth=0, InFlightRequests=5 → EffectiveLoad=5
// Equal EffectiveLoad → equal base scores from load-balance scorer.
// Equal KV=0.0 → equal base scores from kv-utilization scorer.
// InputTokens=nil → prefix-affinity scores 0.0 for both.
// Only the tiebreaker term (0.01/(1+InFlightRequests)) then differentiates the scores.
func TestEvolvedAlgorithmInflightTiebreaker(t *testing.T) {
    alg := newEvolvedAlgorithm()
    state := sim.RouterState{
        Snapshots: []sim.RoutingSnapshot{
            // idle: QueueDepth=5, InFlightRequests=0 → EffectiveLoad=5 (equal to busy)
            {ID: "idle",  QueueDepth: 5, InFlightRequests: 0, KVUtilization: 0.0},
            // busy: QueueDepth=0, InFlightRequests=5 → EffectiveLoad=5 (equal to idle)
            {ID: "busy",  QueueDepth: 0, InFlightRequests: 5, KVUtilization: 0.0},
        },
    }
    decision := alg.Route(&sim.Request{}, &state)
    scores := decision.Scores
    if scores["idle"] <= scores["busy"] {
        t.Errorf("expected idle (InFlight=0) score > busy (InFlight=5) score; got idle=%f busy=%f",
            scores["idle"], scores["busy"])
    }
    // Verify tiebreaker magnitudes: 0.01/(1+0)=0.01 vs 0.01/(1+5)≈0.00167.
    // With equal base scores, the diff should be close to the tiebreaker delta alone.
    expectedIdle := 0.01 / (1.0 + 0)
    expectedBusy := 0.01 / (1.0 + 5)
    if diff := scores["idle"] - scores["busy"]; diff < (expectedIdle-expectedBusy)*0.99 {
        t.Errorf("tiebreaker delta too small: got %f, expected ~%f", diff, expectedIdle-expectedBusy)
    }
}
```

**2i. TestEvolvedScorerScoresCorrectly — update stale comments (lines ~486, ~494, ~515):**

In `TestEvolvedScorerScoresCorrectly`, update three stale comments:

First, the function-level comment at line ~486. Change:
```go
// BC-4: metric translation; BC-5: session header.
```
To:
```go
// Verifies metric translation: WaitingQueueSize/RunningRequestsSize/KVCacheUsagePercent → sim fields.
// Also verifies Score() handles a request with session headers without error.
```
(BC-4 now means "KV penalty does NOT fire at exactly 0.9" per Section B; BC-5 now means "inflight tiebreaker". The old labels do not apply to this test function.)

Second, the inline comment at line ~494. Change:
```go
// EffectiveLoad=8 → hard penalty (load>7)
```
To:
```go
// higher load → lower base score from WeightedScoring
```

Third, the inline comment at line ~515. Change:
```go
// BC-5: session header extraction
```
To:
```go
// Verify Score() handles a request with session headers (no error expected).
// Note: evolved_scorer.go no longer extracts SessionID from headers (removed in Task 5).
// This sub-block tests that the scorer is robust to irrelevant headers.
```
(The test assertion here only verifies `len(scoresWithSess) != 2` — it passes whether or not SessionID is extracted. After Task 5 removes header extraction, this test still passes.)

(The test assertions `heavy score < light score` and `len(scoresWithSess) != 2` still pass — the base WeightedScoring queue-depth and load-balance scorers ensure the lighter endpoint scores higher. Only the comments are stale; no assertion changes are needed.)

**Step 3: Run all harness tests**

```bash
go test ./tools/harness/... -race -timeout 120s -v 2>&1 | tail -40
```
Expected: All tests pass including:
- `TestStaleHashAbortsParsing` ✓
- `TestUnknownSignalTypeRejection` ✓
- `TestEvolvedAlgorithmSingleEndpoint` ✓
- `TestEvolvedAlgorithmKVPenaltyBoundary` ✓
- `TestEvolvedAlgorithmInflightTiebreaker` ✓
- `TestEvolvedScorerScoresCorrectly` ✓
- `TestCrossLanguageHashConsistency` (skips if venv missing; runs if venv present) ✓

**Step 4: Build**

```bash
go build ./tools/harness/...
```
Expected: clean.

**Step 5: Commit**

```bash
git add tools/harness/harness_test.go
git commit -m "$(cat <<'EOF'
test(harness): update tests for new algorithm and blis_router input paths

- All evolve_block_source strings: routing/best_program.py → blis_router/best/best_program.go
- TestEvolvedAlgorithmSingleEndpoint: KVUtil 0.90→0.95 (penalty fires at >0.9)
- TestEvolvedAlgorithmKVPenaltyBoundary: boundary 0.82→0.9
- TestLoadAlgorithmReturnsEvolved: KV test uses 0.95 not 0.90
- TestCrossLanguageHashConsistency: uses blis_router/best/best_program.go
- TestEvolvedAlgorithmInflightTiebreaker: new test for BC-5 (inflight tiebreaker)
- Contracts: BC-3, BC-4, BC-5, BC-7, BC-12

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

#### Task 5: Update evolved_scorer.go (remove dead SessionID translation)

**Contracts Implemented:** BC-10
**Language:** Go

**Files:**
- Modify: `tools/harness/evolved_scorer.go`

**Step 1: Update evolved_scorer.go**

The new EVOLVE-BLOCK does not read `req.SessionID`, so the header extraction is dead code. Remove it.

In the `Score` method, remove these lines (~113-116):
```go
    if req.Headers != nil {
        simReq.SessionID = req.Headers[sessionTokenHeader]
    }
```

Also remove the `sessionTokenHeader` constant (~18):
```go
const (
    EvolvedScorerType = "evolved-scorer"
    // sessionTokenHeader is the request header key for session affinity.
    // Matches session_affinity.go in llm-d-inference-scheduler.
    sessionTokenHeader = "x-session-token"
)
```
Simplify to:
```go
const EvolvedScorerType = "evolved-scorer"
```

Update the struct comment (~28-34) to remove CacheHitRate and SessionID:
```go
// Signal translation (from workspace/signal_coverage.json and mapping artifact):
//   - endpoint.GetMetrics().WaitingQueueSize    → sim.RoutingSnapshot.QueueDepth
//   - endpoint.GetMetrics().RunningRequestsSize → sim.RoutingSnapshot.InFlightRequests
//     (F-10 single-count: BatchSize intentionally omitted — defaults to 0; only InFlightRequests maps to RunningRequestsSize)
//   - NormalizeKVUtilization(KVCacheUsagePercent) → sim.RoutingSnapshot.KVUtilization
//   - CacheHitRate: 0.0 (zero fallback — no production field available)
//   - request.Headers["x-session-token"] → sim.Request.SessionID
```
To:
```go
// Signal translation (from workspace/signal_coverage.json and mapping artifact):
//   - endpoint.GetMetrics().WaitingQueueSize    → sim.RoutingSnapshot.QueueDepth
//     (used by base WeightedScoring via EffectiveLoad; not in EVOLVE-BLOCK directly)
//   - endpoint.GetMetrics().RunningRequestsSize → sim.RoutingSnapshot.InFlightRequests
//     (F-10 single-count: BatchSize intentionally omitted — defaults to 0)
//   - NormalizeKVUtilization(KVCacheUsagePercent) → sim.RoutingSnapshot.KVUtilization
//   Note: CacheHitRate and SessionID are not used by the new EVOLVE-BLOCK.
```

**Step 1b: Update stale comment in harness.go (line 73)**

`tools/harness/harness.go` line 73 has a comment referencing `.py` extension:
```go
// Parse source path and line range (format: "path/to/file.py:START-END")
```
Change to:
```go
// Parse source path and line range (format: "path/to/file.go:START-END")
```
(The format is file-extension-agnostic at runtime; the comment should reflect the new canonical `.go` input.)

**Step 2: Verify build and tests pass**

```bash
go build ./tools/harness/... && go test ./tools/harness/... -race -timeout 120s -v 2>&1 | grep -E "PASS|FAIL|ok|---"
```
Expected: All tests pass, clean build.

**Step 3: Commit**

```bash
git add tools/harness/evolved_scorer.go tools/harness/harness.go
git commit -m "$(cat <<'EOF'
refactor(harness): remove dead SessionID translation from evolved_scorer; update harness.go comment

- New EVOLVE-BLOCK does not read req.SessionID (BC-10)
- Remove sessionTokenHeader constant and header extraction
- Update signal translation comment to reflect new algorithm signals
- harness.go line 73: update format comment from .py to .go

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

#### Task 6: Update prompts (extract.md, transfer.md, generate.md, translate.md)

**Contracts Implemented:** BC-8
**Language:** Markdown (prompt templates)

**Files:**
- Modify: `prompts/extract.md`
- Modify: `prompts/transfer.md`
- Modify: `prompts/generate.md`
- Modify: `prompts/translate.md`

**Step 1: Update prompts/extract.md**

Find and replace all path references:

```
routing/best_program.py  →  blis_router/best/best_program.go
routing/best_program_info.json  →  blis_router/best/best_program_info.json
```

Line ~19: Change
```bash
test -f routing/best_program.py || { echo "HALT: missing routing/best_program.py"; exit 1; }
test -f routing/best_program_info.json || { echo "HALT: missing routing/best_program_info.json"; exit 1; }
```
To:
```bash
test -f blis_router/best/best_program.go || { echo "HALT: missing blis_router/best/best_program.go"; exit 1; }
test -f blis_router/best/best_program_info.json || { echo "HALT: missing blis_router/best/best_program_info.json"; exit 1; }
```

Line ~38: Change
```bash
.venv/bin/python tools/transfer_cli.py extract ${CI:+--strict} routing/
```
To:
```bash
.venv/bin/python tools/transfer_cli.py extract ${CI:+--strict} blis_router/best/
```

Line ~109: Change
```
  - `evolve_block_source`: path with line range (e.g., `routing/best_program.py:171-242`)
```
To:
```
  - `evolve_block_source`: path with line range (e.g., `blis_router/best/best_program.go:177-258`)
```

**Step 2: Update prompts/transfer.md**

Line ~17 (table): Change
```
| 1     | Extract   | `prompts/extract.md`  | `routing/best_program.py`, `routing/best_program_info.json` | `workspace/algorithm_summary.json` |
```
To:
```
| 1     | Extract   | `prompts/extract.md`  | `blis_router/best/best_program.go`, `blis_router/best/best_program_info.json` | `workspace/algorithm_summary.json` |
```

Lines ~33-34 (pre-flight checks): Change
```bash
test -f routing/best_program.py || { echo "HALT: missing routing input best_program.py"; exit 1; }
test -f routing/best_program_info.json || { echo "HALT: missing routing input best_program_info.json"; exit 1; }
```
To:
```bash
test -f blis_router/best/best_program.go || { echo "HALT: missing blis_router input best_program.go"; exit 1; }
test -f blis_router/best/best_program_info.json || { echo "HALT: missing blis_router input best_program_info.json"; exit 1; }
```

**Step 3: Verify prompts/generate.md (already updated)**

Line ~93 already reads:
```
Read the EVOLVE-BLOCK from `blis_router/best/best_program.go` and identify:
```
This was updated in a prior round. **No edit required.** Verify the current content is correct:
```bash
grep -n "EVOLVE-BLOCK" prompts/generate.md | head -5
```
Expected: line ~93 references `blis_router/best/best_program.go`. If the old `routing/best_program.py` string is still present, apply the find-replace now; otherwise proceed to Step 4.

**Step 4: Update prompts/translate.md (F-10 skip guard)**

Verify the skip guard at Step 4 is already present on disk:
```bash
grep -n "skip this step entirely" prompts/translate.md
```
Expected: matches line ~97 containing "Skip this step entirely if `composite_signals` in `workspace/algorithm_summary.json` is empty."

If the line is absent, add it: at the top of `## Step 4: F-10 Double-Counting Detection`, insert:

```
**Skip this step entirely if `composite_signals` in `workspace/algorithm_summary.json` is empty.** An empty `composite_signals: []` means the EVOLVE-BLOCK contains no composite method calls (e.g., no `EffectiveLoad()`), so there is no composite to check for double-counting. For the current blis_router EVOLVE-BLOCK, `composite_signals: []` — proceed directly to Step 5.
```

This skip guard (F-10) ensures the translate stage does not attempt double-counting detection when the new EVOLVE-BLOCK has no composite signals.

**Step 5: Verify structural completeness of each prompt**

For each modified prompt, confirm the 4 required sections are still present:
- [ ] `prompts/extract.md`: Prerequisites, Validation steps, Halt conditions, Expected outputs — confirm all present after edit
- [ ] `prompts/transfer.md`: Pre-flight check block, stage table, stage instructions — confirm all present
- [ ] `prompts/generate.md`: Instructions reference correct input file — confirm
- [ ] `prompts/translate.md`: Step 4 F-10 skip guard present — confirm with grep above

**Step 6: Commit**

```bash
git add prompts/extract.md prompts/transfer.md prompts/generate.md prompts/translate.md
git commit -m "$(cat <<'EOF'
docs(prompts): update extract/transfer/generate/translate to use blis_router/best/ input

- All routing/best_program.py refs → blis_router/best/best_program.go
- All routing/best_program_info.json refs → blis_router/best/best_program_info.json
- CLI command: extract blis_router/best/ (BC-8)
- translate.md Step 4: add F-10 skip guard for empty composite_signals

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

#### Task 7: Update prompts/validate.md (workload paths)

**Contracts Implemented:** BC-8
**Language:** Markdown (prompt template)

**Files:**
- Modify: `prompts/validate.md`

**Step 1: Update workload glob and halt message**

Line ~142: Change
```bash
ls routing/workload_v2_*.yaml
```
To:
```bash
ls blis_router/workloads/*.yaml
```

Line ~145: Change
```
**HALT if no files match the glob.** Message: "HALT: No routing/workload_v2_*.yaml files found — cannot classify workloads as matched/unmatched. Ensure routing artifacts are present."
```
To:
```
**HALT if no files match the glob.** Message: "HALT: No blis_router/workloads/*.yaml files found — cannot classify workloads as matched/unmatched. Ensure blis_router artifacts are present."
```

Line ~149: Change
```
> A workload is **matched** if the signals exercised by the workload (per `routing/workload_v2_*.yaml` parameter ranges) overlap with at least one signal listed in `workspace/signal_coverage.json` `signals[]` that has `mapped == true` (equivalently, `prod_name` is non-null). A workload is **unmatched** if none of its exercised signals are mapped.
```
To:
```
> A workload is **matched** if the signals exercised by the workload (per `blis_router/workloads/*.yaml` parameter ranges) overlap with at least one signal listed in `workspace/signal_coverage.json` `signals[]` that has `mapped == true` (equivalently, `prod_name` is non-null). A workload is **unmatched** if none of its exercised signals are mapped.
```

**Step 2: Verify prompt completeness**

```bash
grep -c "HALT" prompts/validate.md
```
Expected: ≥ 3 (halt conditions preserved).

Verify the 4 required sections are present:
```bash
grep -E "^## Step [0-9]" prompts/validate.md
```
Expected: Steps 1-6 listed.

**Step 3: Commit**

```bash
git add prompts/validate.md
git commit -m "$(cat <<'EOF'
docs(prompts): update validate.md workload glob to blis_router/workloads/

- Prerequisite check: ls blis_router/workloads/*.yaml
- HALT message updated
- Workload classification rule updated (BC-8)

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

#### Task 8: Update docs (CLAUDE.md, manual-testing-guide.md)

**Contracts Implemented:** BC-8 (documentation consistency)
**Language:** Markdown

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/manual-testing-guide.md`

**Step 1: Update CLAUDE.md**

Find the CLI example section. Change:
```bash
# Extract algorithm metadata
python tools/transfer_cli.py extract routing/

# Extract with strict fidelity checks (recommended for CI)
python tools/transfer_cli.py extract --strict routing/
```
To:
```bash
# Extract algorithm metadata
python tools/transfer_cli.py extract blis_router/best/

# Extract with strict fidelity checks (recommended for CI)
python tools/transfer_cli.py extract --strict blis_router/best/
```

**Step 2: Update docs/manual-testing-guide.md**

**DO NOT remove or modify sections marked "DO NOT REMOVE NOTES".**

In section "## PR1 — Transfer Infrastructure + Mapping Artifact + CLI Extract":

Update all occurrences of `routing/` to the new paths:
- `routing/` (extract command argument) → `blis_router/best/`

Line ~15: Change
```bash
.venv/bin/python tools/transfer_cli.py extract routing/
```
To:
```bash
.venv/bin/python tools/transfer_cli.py extract blis_router/best/
```

Line ~26-30 (1.2 Extract — schema validation round-trip): No change needed (schema validation command doesn't reference routing/).

Line ~34: Change
```bash
.venv/bin/python tools/transfer_cli.py extract --strict routing/
```
To:
```bash
.venv/bin/python tools/transfer_cli.py extract --strict blis_router/best/
```

Lines ~43-45 (1.4 — tampered input negative test): Change
```bash
cp -r routing/ /tmp/routing_broken/
sed -i '' 's/EVOLVE-BLOCK-START/BROKEN/' /tmp/routing_broken/best_program.py
.venv/bin/python tools/transfer_cli.py extract /tmp/routing_broken/
```
To:
```bash
cp -r blis_router/best/ /tmp/routing_broken/
sed -i '' 's/EVOLVE-BLOCK-START/BROKEN/' /tmp/routing_broken/best_program.go
.venv/bin/python tools/transfer_cli.py extract /tmp/routing_broken/
```

Lines ~69-72 (1.6 — mapping spot-check): Update comment about `routing/` to reference `blis_router/best/`.

Lines ~78-82 (1.7 — content hash stability): Change
```bash
.venv/bin/python tools/transfer_cli.py extract routing/
...
.venv/bin/python tools/transfer_cli.py extract routing/
```
To:
```bash
.venv/bin/python tools/transfer_cli.py extract blis_router/best/
...
.venv/bin/python tools/transfer_cli.py extract blis_router/best/
```

In the Cross-PR Regression Checks section (~574): Change
```bash
.venv/bin/python tools/transfer_cli.py extract routing/
```
To:
```bash
.venv/bin/python tools/transfer_cli.py extract blis_router/best/
```

In section "## PR5 — Validation Pipeline (Stage 5)":

Lines ~141-145 (5.3 workload prerequisite check): This is in `prompts/validate.md` not `manual-testing-guide.md`. The manual testing guide section 5.3 says "go test -tags suitea" — no routing path change needed.

**Step 3: Update docs/transfer/blis_to_llmd_mapping.md (signal rows)**

The `validate-mapping` command (`transfer_cli.py:718`) computes `extra = mapping_signals − known_names`. `known_names` is derived at runtime from `workspace/algorithm_summary.json` (written by Task 1 Step 3's `extract blis_router/best/` run); it is NOT derived from `EXPECTED_SIGNALS` in `test_transfer_cli.py` (that constant is a pytest-only fixture with no effect on `validate-mapping`'s runtime behavior). After Task 1's extract run produces `algorithm_summary.json` with `signals = [InFlightRequests, KVUtilization]`, `known_names = {InFlightRequests, KVUtilization}` and any mapping rows for other signals become "extra", causing exit 1 (breaking CI and Stage 2 translate workflow).

Open `docs/transfer/blis_to_llmd_mapping.md` and **remove** (do NOT mark as deprecated) the rows for signals no longer present in the new EVOLVE-BLOCK: `QueueDepth`, `BatchSize`, `CacheHitRate`, `EffectiveLoad` (composite), and `SessionID`. Retain only the rows for `InFlightRequests` and `KVUtilization`.

⚠️ **Do not use a "deprecated" marker as an alternative to removal.** Deprecated rows are still parsed as mapping rows by `validate-mapping`; leaving them in the table causes `validate-mapping` to report extra signals and exit 1, breaking the verification step below. Only physical row deletion satisfies the exit-0 requirement. (The QueueDepth gap is documented in Section D Deviation Log as DEV-4.)

After editing, verify `validate-mapping` exits 0:
```bash
.venv/bin/python tools/transfer_cli.py validate-mapping
```
Expected: exit 0, no "extra signals" warning.

**Step 4: Verify no stale routing/ references remain in docs or prompts**

```bash
grep -rn "routing/" CLAUDE.md docs/manual-testing-guide.md prompts/
```
Expected: Zero results (all replaced). If any remain, they are either part of "DO NOT REMOVE" notes or legitimate cross-references — examine each individually.

**Step 5: Commit**

```bash
git add CLAUDE.md docs/manual-testing-guide.md docs/transfer/blis_to_llmd_mapping.md
git commit -m "$(cat <<'EOF'
docs: update CLAUDE.md, manual testing guide, and mapping artifact for blis_router input

- CLI examples: extract routing/ → extract blis_router/best/
- PR1 test commands updated
- DO NOT REMOVE sections preserved unchanged
- blis_to_llmd_mapping.md: remove rows for signals absent from new EVOLVE-BLOCK
  (QueueDepth, BatchSize, CacheHitRate, EffectiveLoad, SessionID) so validate-mapping
  exits 0 after signal set reduction to {InFlightRequests, KVUtilization}

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### H) Test Strategy

| Contract | Task | Test Type | Verification |
|----------|------|-----------|-------------|
| BC-1 | Task 1 | Integration (Python) | `extract blis_router/best/` exits 0 |
| BC-2 | Task 2 | Unit (Python) | `TestGoldenSignalList` with updated EXPECTED_SIGNALS |
| BC-3 | Task 3+4 | Unit (Go) | `TestLoadAlgorithmReturnsEvolved` KV=0.95 prefers low-KV endpoint |
| BC-4 | Task 4 | Unit (Go) | `TestEvolvedAlgorithmKVPenaltyBoundary` at KV=0.9 |
| BC-5 | Task 4 | Unit (Go) | Add new test `TestEvolvedAlgorithmInflightTiebreaker`: two endpoints with identical base scores and KV=0.0, one with InFlightRequests=0 one with InFlightRequests=5 — verify InFlightRequests=0 endpoint scores higher. Note: `TestEquivalenceTrivial` uses trivialAlgorithm (not evolvedAlgorithm) and does not isolate the tiebreaker |
| BC-6 | Task 1 | Integration (Python) | `algorithm_summary.json` has `blis_router/best/best_program.go` in evolve_block_source |
| BC-7 | Task 4 | Integration (cross-lang) | `TestCrossLanguageHashConsistency` |
| BC-8 | Tasks 6-8 | Structural check | grep for old paths returns 0 results in prompts/ and docs (CLAUDE.md, manual-testing-guide.md); grep confirms F-10 skip guard present in prompts/translate.md Step 4 |
| BC-9 | Task 1 | Negative (Python) | `extract routing/` exits 2 |
| BC-10 | Task 3 | Code review | No cubic/CacheHitRate/SessionID in evolved_algorithm.go |
| BC-11 | Task 2 | Unit (Python) | Add new test `test_extract_missing_go_file_exits_2` — **note:** existing `test_extract_missing_info_json_exits_2` covers missing metadata JSON (different code path); BC-11 requires a dedicated test for missing `best_program.go` |
| BC-12 | Task 4 | Unit (Go) | `TestStaleHashAbortsParsing` with new .go file path |

### I) Risk Analysis

| Risk | Likelihood | Impact | Mitigation | Task |
|------|-----------|--------|------------|------|
| Harness Suite A tau drops below 0.8 due to simplified algorithm | Medium | High | The adaptive decay never fires in canonical tuples (InputTokens=nil → prefix score 0.0); verified by analysis. If tau < 0.8 run Suite A and investigate. | Task 3 |
| test_transfer_cli.py has ~25 `best_program.py` references; some missed | Medium | Medium | Step 3 validation: run full test suite; any missed reference fails immediately | Task 2 |
| KV penalty direction: subtractive penalty can make scores negative | Low | Low | Not a bug — argmax still works with negative scores; `bestScore := -1e9` initial value handles this. Verified by new algorithm structure. | Task 3 |
| `routing/` references in other docs not covered by this PR | Low | Low | grep check at end of Task 8 catches these | Task 8 |
| `base.Route()` fires observer callbacks with BASE routing target, not evolved target | Low | Low | In Suite A canonical tuples InputTokens=nil → prefix score 0.0 for all instances, so the prefix-affinity scorer's observer records no preference; the base and evolved targets coincide for nil-InputTokens requests. However, future test authors using non-nil InputTokens should be aware that `base.Route()` internally records the base decision in prefix-affinity history — if the evolved algorithm's KV penalty changes the final argmax, the prefix-affinity scorer's history is poisoned for that session. Document in evolved_algorithm.go code comments. | Task 3 |

---

## PART 3: Quality Assurance

### J) Sanity Checklist

**Dimension 1: Cross-system accuracy**
- [x] `blis_router/best/best_program.go` contains EVOLVE-BLOCK-START and EVOLVE-BLOCK-END markers (verified by reading the file)
- [x] `blis_router/best/best_program_info.json` has `metrics.combined_score` (verified: 11.46)
- [x] `blis_router/workloads/` contains `workload_glia_40qps.yaml` and `workload_glia_prefix_heavy.yaml` (verified by ls)
- [x] Commit pins: not changed — no submodule API changes in this PR
- [ ] **MUST VERIFY before Task 3:** `sim.RoutingDecision` has a `Scores` field (`map[string]float64`) — confirm with `grep -n "Scores" inference-sim/sim/routing.go` (enforced in Task 3 Step 0)
- [ ] **MUST VERIFY before Task 3:** `sim.NewRoutingDecisionWithScores(id string, algorithm string, scores map[string]float64) sim.RoutingDecision` exists — confirm with `grep -n "NewRoutingDecisionWithScores" inference-sim/sim/routing.go` (enforced in Task 3 Step 0)

**Dimension 2: Schema chain integrity**
- [x] `algorithm_summary.schema.json` `evolve_block_source` pattern updated from `.py`-only to extension-agnostic (`.[a-zA-Z]+`) — required so `.go` paths (e.g., `blis_router/best/best_program.go:N-M`) pass schema validation. Without this fix, `validate-schema` rejects the new path, blocking Stage 2 (translate.md line 20 HALTs on schema failure) and failing TestRoundTrip and the K.5 pytest gate. `tools/schemas/algorithm_summary.schema.json` is in Section F modification list. `signals` array format and all other schema fields are unchanged.
- [x] `tools/test_schema_validator.py` `_valid_summary()` fixture uses `routing/best_program.py:171-242` which still matches the extension-agnostic pattern (`.[a-zA-Z]+`) — no update needed to this test file.
- [x] No new workspace artifacts introduced

**Dimension 3: Prompt completeness**
- [x] `prompts/extract.md`: 4 sections confirmed present (prerequisites = pre-flight check, validation = Step 2-3, halt conditions = HALT messages, outputs = Step 4 schema)
- [x] `prompts/transfer.md`: stage table + pre-flight + stage instructions
- [x] `prompts/validate.md`: Steps 1-6 + HALT conditions

**Dimension 4: CLI contract**
- [x] `extract` exit codes unchanged: 0=success, 1=validation failure, 2=infrastructure error
- [x] Error message for missing file updated to reference `best_program.go`

**Dimension 5: Artifact consistency**
- [x] Signal names `InFlightRequests` and `KVUtilization` are consistent across: EVOLVE-BLOCK source, `_extract_signals` regex, `ROUTING_SNAPSHOT_FIELDS`, golden list in tests
- [x] `evolve_block_source` path format (`path:start-end`) unchanged — `LoadAlgorithm` parsing logic unchanged

**Dimension 6: Dead artifact prevention**
- [x] No new files created — all changes are modifications to existing files
- [x] `routing/` directory and files remain on disk (not deleted) — other tests or scripts may reference them
- [x] `docs/transfer/blis_to_llmd_mapping.md` updated to remove rows for signals absent from new EVOLVE-BLOCK — `validate-mapping` will exit 0 after the signal set reduction

**Additional checks:**
- [x] PR category: Validation (correct — harness Go code rewrite + prompt updates; Perspective 4 Code correctness included per docs/contributing/pr-workflow.md; `go test ./tools/harness/...` is part of the gate per Validation category requirements)
- [x] Verification gate: `python -m pytest tools/ -v && go test ./tools/harness/... -race -timeout 120s -v && go build ./tools/harness/...`
- [x] No feature creep — strictly input path migration + algorithm update to match new source
- [x] Deviation log reviewed — DEV-1 (language metadata) noted but intentionally not corrected
- [x] All contracts mapped to tasks (see Test Strategy table)

---

## Appendix K: Key Implementation Details

### K.1 Signal extraction from new EVOLVE-BLOCK

The new `blis_router/best/best_program.go` EVOLVE-BLOCK (lines 177-258 approximately) accesses:
- `snap.InFlightRequests` — 4 occurrences (minInflight loop, cachedLoad fetch, tiebreaker)
- `snap.KVUtilization` — 1 occurrence (KV penalty condition)
- `snap.ID` — multiple (map lookup key, excluded from signals by `_IDENTIFIER_FIELDS`)
- `allDimScores[i][snap.ID]` — indirect score access via scorer function results (not a field access)
- `ws.scorers`, `ws.weights`, `ws.rng` — internal WeightedScoring state (not snap fields)

The `_extract_signals` regex `snap\.InFlightRequests` and `snap\.KVUtilization` will find both correctly.

### K.2 New EVOLVE-BLOCK exact line range

The new EVOLVE-BLOCK in `blis_router/best/best_program.go` spans lines 177-258 (based on reading the file). After running `extract blis_router/best/`, the `evolve_block_source` will show the actual line numbers.

### K.3 evolved_algorithm.go argmax edge case

The original algorithm uses `bestScore := -1e9` as initial value. The new implementation uses:
```go
bestScore := scores[snapshots[0].ID]
bestIdx := 0
for i, snap := range snapshots[1:] {
    if scores[snap.ID] > bestScore {
        bestScore = scores[snap.ID]
        bestIdx = i + 1
    }
}
```
Note the `i + 1` adjustment when iterating over `snapshots[1:]`. This is correct.

Alternatively, use the `-1e9` initial value to match the original algorithm's tie-breaking behavior:
```go
bestScore := -1e9
bestIdx := 0
for i, snap := range snapshots {
    if scores[snap.ID] > bestScore {
        bestScore = scores[snap.ID]
        bestIdx = i
    }
}
```
Both forms are functionally equivalent given the `len(snapshots) >= 1` panic guard. The `-1e9` form (Form 2 above) is an alternative equivalent implementation that more closely mirrors the original EVOLVE-BLOCK argmax style; either form is acceptable.

### K.4 TestEvolvedScorerScoresCorrectly — does it still pass?

The test creates `heavy` (WaitingQueueSize=5, RunningRequestsSize=3, KV=50%) and `light` (lower load, KV=20%). The old algorithm had a "hard penalty" for EffectiveLoad>7. The new algorithm doesn't have this. However, the base WeightedScoring (queue-depth + load-balance scorers) will still prefer the lighter endpoint. The inflight tiebreaker adds `0.01/(1+RunningRequestsSize)`. Both mechanisms ensure `light` scores higher. The test assertion `assert heavy score < light score` should still pass; update the comment from "EffectiveLoad=8 → hard penalty (load>7)" to "higher load → lower base score from WeightedScoring".

### K.5 Verification gate command

After all tasks complete:
```bash
# Python tests
.venv/bin/python -m pytest tools/ -v

# Go harness build and tests
go build ./tools/harness/...
go test ./tools/harness/... -race -timeout 120s -v

# Manual: run extract against new input
.venv/bin/python tools/transfer_cli.py extract blis_router/best/
cat workspace/algorithm_summary.json | python -m json.tool | grep -E "signal|source|hash"
```

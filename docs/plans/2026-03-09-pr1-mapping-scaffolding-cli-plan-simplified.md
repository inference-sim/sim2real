# PR1: Mapping Artifact + Project Scaffolding + CLI Extract (Simplified)

> Readable summary of the full plan at `2026-03-09-pr1-mapping-scaffolding-cli-plan.md`.

## Goal

Establish the foundation for the sim-to-production transfer pipeline: a signal mapping document, directory structure, JSON schemas, and a Python CLI that extracts algorithm metadata from simulation artifacts.

## What This PR Produces

| Deliverable | Path | Purpose |
|-------------|------|---------|
| Signal mapping artifact | `docs/transfer/blis_to_llmd_mapping.md` | Documents how each sim routing signal maps to production equivalents (types, fidelity, staleness) |
| CLI extraction tool | `tools/transfer_cli.py` | Three commands: `extract`, `validate-mapping`, `validate-schema` |
| JSON Schema | `tools/schemas/algorithm_summary.schema.json` | Defines the `workspace/algorithm_summary.json` contract consumed by all downstream PRs |
| Schema validator | `tools/schema_validator.py` | Lightweight stdlib-only JSON Schema validator (~100 lines) |
| Project scaffolding | `docs/transfer/`, `tools/`, `workspace/`, `.gitignore`, `CLAUDE.md` | Directory structure and documentation |
| R6 resolution | Documented in `docs/transfer/README.md` | `LoadEvolvedBlock` API doesn't exist; shim deferred to PR3 |

## Architecture

- **Python >= 3.9, stdlib only** (no pip dependencies)
- **Regex-based EVOLVE-BLOCK parsing** (Go code embedded in Python string -- not AST-parseable)
- **Exit codes:** 0 = success, 1 = validation failure, 2 = infrastructure error
- **`workspace/`** directory is gitignored; holds inter-stage JSON artifacts

## CLI Commands

```bash
python tools/transfer_cli.py extract routing/           # Parse EVOLVE-BLOCK, produce algorithm_summary.json
python tools/transfer_cli.py extract --strict routing/   # Same, but requires mapping artifact (for CI)
python tools/transfer_cli.py validate-mapping            # Check mapping completeness against extracted signals
python tools/transfer_cli.py validate-schema <file>      # Validate workspace JSON against schema
```

**Two-output design for `extract`:**
1. **File artifact** (`workspace/algorithm_summary.json`) -- the pipeline contract, schema-validated. Downstream stages consume this.
2. **Stdout JSON** -- operational metadata for humans/CI. Do NOT consume in downstream stages.

## Signals Extracted from EVOLVE-BLOCK

The macro plan listed 5 candidate signals; the actual EVOLVE-BLOCK uses 6:

| Signal | Go Type | Access Pattern | Source |
|--------|---------|----------------|--------|
| QueueDepth | int | `snap.QueueDepth` | via EffectiveLoad() |
| BatchSize | int | `snap.BatchSize` | via EffectiveLoad() |
| InFlightRequests | int | `snap.InFlightRequests` | via EffectiveLoad() |
| KVUtilization | float64 | `snap.KVUtilization` | direct |
| CacheHitRate | float64 | `snap.CacheHitRate` | direct |
| SessionID | bool | `req.SessionID != ""` | request-level |

**Composite:** `EffectiveLoad() = QueueDepth + BatchSize + InFlightRequests`

## Key Behavioral Contracts

| ID | Contract | Enforcement |
|----|----------|-------------|
| BC-1 | `extract` produces valid `algorithm_summary.json` with all required fields | `TestExtract::test_extract_produces_valid_summary` |
| BC-2 | `extract` identifies all signals from EVOLVE-BLOCK | `TestExtract::test_extract_identifies_signals` + golden-file test |
| BC-3 | `validate-mapping` checks bidirectional signal completeness | `TestValidateMapping::test_validate_mapping_passes_with_complete_mapping` |
| BC-4 | `validate-schema` enforces structure (required fields, types, enums) | `TestValidateArtifact` suite + `TestValidateSchema` integration |
| BC-5 | Scope validation rejects non-routing algorithms | `TestExtract::test_extract_scope_validation_*` |
| BC-6 | Low-fidelity signals halt the pipeline (exit 1) | `TestFidelityHalt::test_low_fidelity_signal_halts_extract` |
| BC-7 | No external dependencies (stdlib only) | Code review |
| BC-8 | Missing input files or markers -> exit 2 | `TestExtract::test_extract_missing_directory_exits_2` |
| BC-11 | Content hash enables drift detection for PR3 | `TestHashDriftDetection::test_hash_detects_source_modification` |

### `--strict` Mode (Required for CI)

- Requires mapping artifact to exist (fails if absent)
- All fidelity checks mandatory
- When `CI` env var is set, extract **fails** (exit 1) if `--strict` is not passed

**Bootstrap sequence:** Run `extract` without `--strict` first to discover signals -> create mapping artifact -> then `--strict` works.

## Signal Mapping Summary

| Sim Signal | Production Equivalent | Fidelity |
|------------|----------------------|----------|
| QueueDepth | `endpoint.GetMetrics().WaitingQueueSize` | high |
| BatchSize | `endpoint.GetMetrics().RunningQueueSize` (approximate) | medium |
| InFlightRequests | `endpoint.GetMetrics().RunningRequestCount` (approximate) | medium |
| KVUtilization | `endpoint.GetMetrics().KVCacheUsagePercent` (needs /100 normalization) | high |
| CacheHitRate | Prefix cache hit ratio from engine metrics | medium *(provisional)* |
| SessionID | Request header `x-session-id` | high |

**Fidelity scale:** high (same computation, R^2 >= 0.99), medium (equivalent but different source, R^2 >= 0.80), low (proxy signal, pipeline halts).

## Task Breakdown

### Task 1: Project Scaffolding + .gitignore
- Create `docs/transfer/README.md` (with R6 resolution)
- Create `tools/__init__.py`
- Add `workspace/` to `.gitignore`
- Verify `LoadEvolvedBlock` doesn't exist in inference-sim

### Task 2: JSON Schema + Lightweight Validator
- Create `tools/schemas/algorithm_summary.schema.json`
- Create `tools/schema_validator.py` (stdlib-only, handles: required fields, types, enums, nested objects, arrays, minItems/maxItems, additionalProperties, patterns)
- Create `tools/test_schema_validator.py`
- **Contracts:** BC-4, BC-10

### Task 3: CLI Framework + Extract Command
- Pre-flight: verify routing artifacts exist, check EVOLVE-BLOCK markers, verify EffectiveLoad() expansion
- Create `tools/transfer_cli.py` with `extract` command (+ `validate-mapping` and `validate-schema` stubs)
- Create `tools/test_transfer_cli.py` with extract tests, golden-file signal test, source sync verification
- **Contracts:** BC-1, BC-2, BC-5, BC-7, BC-8

### Task 4: Validate-Mapping Command
- Wire up `validate-mapping`: parse mapping Markdown table, cross-reference against `algorithm_summary.json`
- Tests for complete mapping, missing artifact, missing summary, placeholder hash rejection
- **Contracts:** BC-3, BC-9

### Task 5: Mapping Artifact
- Create `docs/transfer/blis_to_llmd_mapping.md` with signal table, composite signals, fidelity ratings, scorer interface reference
- Replace placeholder commit hash with actual `llm-d-inference-scheduler` HEAD
- Run extract + validate-mapping to verify consistency (iterate up to 3 rounds if needed)
- **Contracts:** BC-3 (completes), BC-6

### Task 6: Validate-Schema Integration + Additional Tests
- Integration tests for `validate-schema` via CLI
- Fidelity halt tests (BC-6)
- CI strict enforcement tests
- Hash drift detection tests (BC-11)
- Round-trip test: extract -> validate-schema
- Determinism test: same input -> same output

### Task 7: CLAUDE.md + CI + Final Documentation
- Create `CLAUDE.md` with project overview, CLI commands, pipeline status
- Create `.github/workflows/test.yml` (checks out inference-sim submodule, runs tests, enforces `--strict`)
- Update `docs/transfer/README.md`

## Key Design Decisions

1. **Algorithm logic is out of scope.** PR1 captures *what signals the algorithm reads*. PR3 captures *what the algorithm does with them* (scoring weights, penalties, thresholds).

2. **PR1-to-PR3 contract:** PR1 provides `evolve_block_source` (file + line range), `evolve_block_content_hash` (SHA-256), and `signals[]`. PR3 must verify the hash, re-parse the EVOLVE-BLOCK for behavioral logic, and produce a Go scorer shim.

3. **Fidelity as a hard gate.** Low-fidelity signals halt the pipeline (BC-6). This is conservative by design -- safer to halt than propagate bad signals. PR5 can revise ratings after empirical validation.

4. **KVUtilization normalization.** Sim uses 0.0-1.0; production uses 0-100. PR3 must divide by 100. The `normalization_note` field on the signal captures this.

5. **Overwrite semantics.** `extract` always overwrites `workspace/algorithm_summary.json`. The workspace directory is gitignored and designed for transient pipeline artifacts.

## Risks

| Risk | Mitigation |
|------|-----------|
| Signal regex misses a field access pattern | Golden-file test + manual review |
| Mapping fidelity ratings are subjective | Conservative ratings; empirical validation in PR5 |
| R6 shim approach infeasible | Fallback: submit API PR to inference-sim |
| Submodule commit hash stale by PR3 | Staleness detection via mapping_artifact_version field |

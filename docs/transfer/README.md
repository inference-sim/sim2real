# Sim-to-Production Transfer Pipeline

Pipeline for transferring simulation-discovered routing algorithms to production
llm-d-inference-scheduler scorer plugins.

## Status

Under construction. See `docs/plans/2026-03-06-sim2real-transfer-macro-plan-v3.md`.

## Directory Layout

- `docs/transfer/` — Mapping artifacts, scorer template, calibration log
- `tools/` — Python CLI + Go test harness (PR3)
- `tools/schemas/` — JSON Schema files for workspace artifact validation
- `prompts/` — Pipeline stage prompt templates (PR3)
- `workspace/` — Inter-stage JSON artifacts (gitignored)

## R6 Resolution: LoadEvolvedBlock API

**Status:** UNVERIFIED — `LoadEvolvedBlock` does not exist in inference-sim.

**Decision:** Use shim approach (option a). The Go test harness (`tools/harness/`, PR3)
will directly instantiate `WeightedScoring` from parsed algorithm parameters rather than
relying on a dedicated `LoadEvolvedBlock` API. This keeps all sim2real PRs independent
of inference-sim changes.

**Rationale:** The evolved algorithm's EVOLVE-BLOCK modifies the `Route()` method body
of `WeightedScoring`. A shim can reconstruct this by:
1. Parsing the evolved scoring/penalty logic from the EVOLVE-BLOCK
2. Creating a `WeightedScoring` instance with standard scorers
3. Wrapping it with the evolved penalty logic as a post-scoring step

This avoids requiring an inference-sim API PR (option b) which would block PR3.

**Shim acceptance criteria (PR3):** The shim in `tools/harness/evolved_scorer.go` MUST:
1. Implement `scheduling.Scorer` interface (Score method with correct signature)
2. Accept all signals from `algorithm_summary.json` `signals[]` as input
3. Reproduce the scoring/penalty logic from the EVOLVE-BLOCK
4. Verify `evolve_block_content_hash` before parsing (BC-11)
5. Pass unit tests comparing shim output against simulation output for reference inputs

## Cross-PR Contracts (PR3 Obligations)

R5-F-6 fix: Machine-readable list of PR3 gates with severity levels for cross-PR discoverability.

PR3 MUST implement and pass these gates before merging:

1. **`test_stale_hash_aborts_parsing`** — CRITICAL. PR3 must include a test that runs extract, modifies the EVOLVE-BLOCK source, attempts to parse using the stale summary, and asserts parsing aborts with a drift detection error. (See BC-11.)
2. **KVUtilization normalization test** — CRITICAL. PR3 must include a unit test verifying that production `KVCacheUsagePercent` values (0-100) are divided by 100 before being passed to the scorer.
3. **Unknown-type signal rejection** — IMPORTANT. PR3 must verify that signals with type `"unknown"` are rejected or handled explicitly, not silently passed through to the scorer.

## Prompt Template Contract (PR3)

PR3 prompt templates MUST consume these PR1 artifacts:
- **Input:** `workspace/algorithm_summary.json` (signal metadata, EVOLVE-BLOCK location, content hash)
- **Input:** `docs/transfer/blis_to_llmd_mapping.md` (signal mappings, fidelity ratings, normalization notes)
- **Output:** Generated Go source implementing `scheduling.Scorer`

**Prompt template requirements:**
1. Each prompt MUST include the signal list from `algorithm_summary.json` `signals[]`
2. Each prompt MUST include normalization notes from the mapping artifact for any signal where sim/prod units differ (e.g., KVUtilization: divide prod value by 100)
3. Each prompt MUST reference `evolve_block_source` to locate the algorithm logic
4. Generated code MUST be validated by `validate-schema` before downstream consumption

## Algorithm Logic Boundary

PR1 captures *what signals the algorithm reads* (signal metadata: names, types, access paths, normalization). PR3 captures *what the algorithm does with those signals* (behavioral logic). Concretely, "algorithm logic" means:
1. Scoring weights applied to each signal
2. Penalty functions (e.g., low-KV penalty)
3. Decision thresholds (e.g., score cutoffs)
4. Composite signal computations (e.g., EffectiveLoad formula)
5. Conditional branching logic (e.g., SessionID affinity check)

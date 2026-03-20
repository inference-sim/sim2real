# CLAUDE.md — sim2real

## Project Overview

sim2real is a pipeline for transferring simulation-discovered routing algorithms from
inference-sim to production llm-d-inference-scheduler scorer plugins.

## Repository Structure

- `blis_router/` — Input artifacts from evolutionary optimization (EVOLVE-BLOCK, metrics, workloads)
- `docs/transfer/` — Mapping artifacts, scorer template, calibration log
- `docs/plans/` — Design docs and implementation plans
- `tools/` — Python CLI (`transfer_cli.py`) and Go test harness (PR3)
- `tools/schemas/` — JSON Schema files for workspace artifact validation
- `prompts/` — Pipeline stage prompt templates (PR3+)
- `workspace/` — Inter-stage JSON artifacts (gitignored, not committed)

## Submodules

- `inference-sim/` — Discrete-event LLM inference simulator (source of evolved algorithms)
- `llm-d-inference-scheduler/` — Production scheduler with scorer plugin system (target)
- `llm-d-benchmark/` — Benchmark harness for cluster-level validation (target)

## Transfer Pipeline

6-stage prompt-driven pipeline:
1. **Extract** — Parse EVOLVE-BLOCK, produce algorithm_summary.json
2. **Translate** — Map sim signals to production equivalents
3. **Generate** — LLM produces scorer plugin code
4. **Test** — Build + test with retry logic
5. **Validate** — 3-suite equivalence + cluster benchmarks
6. **PR** — Create PRs in target repos

## CLI Commands

```bash
# Extract algorithm metadata
python tools/transfer_cli.py extract blis_router/best/

# Extract with strict fidelity checks (recommended for CI)
python tools/transfer_cli.py extract --strict blis_router/best/

# Validate mapping artifact completeness
# NOTE: commit hash check only verifies presence, not currency vs submodule HEAD;
# CI workflow performs the real hash comparison
python tools/transfer_cli.py validate-mapping

# Validate workspace artifact against JSON Schema
python tools/transfer_cli.py validate-schema workspace/algorithm_summary.json

# Classify go build/test output into error classes (stdin)
# Exit 0 = clean, 1 = error found, 2 = CLI infrastructure error
echo "<go build/test output>" | python tools/transfer_cli.py test-status

# noise-characterize removed — superseded by noise pipeline in Stage 5

# Compute mechanism check from noise/baseline/treatment results
python tools/transfer_cli.py benchmark \
  --noise workspace/noise_results.json \
  --baseline workspace/baseline_results.json \
  --treatment workspace/treatment_results.json \
  --signal-coverage workspace/signal_coverage.json \
  --workloads-dir blis_router/workloads/ \
  --out workspace/benchmark_output.json
```

## Important: Artifact Consumption

The `extract` command produces **two** JSON outputs:
1. **File artifact** (`workspace/algorithm_summary.json`) — the pipeline contract, validated by schema. **All downstream stages MUST consume this file.**
2. **Stdout JSON** — operational metadata for human/CI feedback. **Do NOT consume stdout in downstream stages.**

**Artifact existence on failure:** The `extract` command has three distinct exit-code-1 failure modes with different artifact postconditions:
- **Fidelity failure** (low-fidelity signal detected): Exits **before** writing the artifact file. `workspace/algorithm_summary.json` will **not exist** on disk.
- **Strict-mode minimum-signal failure** (`--strict` with fewer than 2 signals): Exits **before** writing the artifact file. `workspace/algorithm_summary.json` will **not exist** on disk.
- **Scope failure** (out-of-scope pattern detected): Exits **after** writing the artifact file. `workspace/algorithm_summary.json` **will exist** on disk (with `scope_validation_passed: false`).

Downstream stages MUST use the exit code (not file existence) as the success signal. A stale artifact from a prior successful run may remain on disk after any pre-write failure (fidelity failure or strict-mode minimum-signal failure).

## Development

- Python >= 3.10, stdlib only (no external dependencies for most subcommands)
- Tests: `python -m pytest tools/ -v`
- Lint: `ruff check tools/` (if installed)

## Notes

- `preflight` and `benchmark` import PyYAML directly and require it to be installed.
  `compile-pipeline` invokes `tektonc-data-collection/tektonc/tektonc.py` via subprocess; that tool requires `jinja2` and `PyYAML`.
  All of these are installed via `pip install -r requirements.txt`.
  The stdlib-only constraint does not apply to these subcommands.
- `benchmark` exit codes: 0 = PASS or INCONCLUSIVE (pipeline should proceed); 1 = FAIL (no matched improvement ≥ T_eff); 2 = ERROR (all workloads skipped due to name mismatch or no matched classifications) or infrastructure failure (missing/malformed input files). Operators must always parse `mechanism_check_verdict` from the output JSON to distinguish PASS from INCONCLUSIVE.
- Stage 5 validate.md exit codes (shell script level, not CLI):
  - `0` = complete (all phases done, all suites passed)
  - `1` = error/halt (context mismatch, ordering violation, suite failure)
  - `2` = infrastructure error (missing artifact, parse failure)
  - `3` = REENTER (noise phase not yet done; operator must jump to Step 5 and re-enter validate.md after noise completes). Automated harnesses must NOT treat exit 3 as a generic failure — it is a planned re-entry pause.

## Pipeline Status

| PR | Description | Status |
|----|-------------|--------|
| PR1 | Mapping artifact + scaffolding + CLI extract | Complete |
| PR2 | Scorer template artifact | Complete |
| PR3 | Prompt templates (Stages 1-3) + Go harness | Complete |
| PR4 | Stage 4 prompt + test retry logic | Complete |
| PR5 | Validation pipeline (Stage 5) | In progress |
| PR6 | Stage 6 + self-verification + calibration | Not started |

## Cross-PR Notes

When implementing PR3, read `docs/transfer/README.md` § Cross-PR Contracts first.

# CLAUDE.md — sim2real

## Project Overview

sim2real is a pipeline for transferring simulation-discovered routing algorithms from
inference-sim to production llm-d-inference-scheduler scorer plugins.

## Repository Structure

- `routing/` — Input artifacts from evolutionary optimization (EVOLVE-BLOCK, metrics, workloads)
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
python tools/transfer_cli.py extract routing/

# Extract with strict fidelity checks (recommended for CI)
python tools/transfer_cli.py extract --strict routing/

# Validate mapping artifact completeness
python tools/transfer_cli.py validate-mapping

# Validate workspace artifact against JSON Schema
python tools/transfer_cli.py validate-schema workspace/algorithm_summary.json
```

## Important: Artifact Consumption

The `extract` command produces **two** JSON outputs:
1. **File artifact** (`workspace/algorithm_summary.json`) — the pipeline contract, validated by schema. **All downstream stages MUST consume this file.**
2. **Stdout JSON** — operational metadata for human/CI feedback. **Do NOT consume stdout in downstream stages.**

## Development

- Python >= 3.9, stdlib only (no external dependencies)
- Tests: `python -m pytest tools/ -v`
- Lint: `ruff check tools/` (if installed)

## Pipeline Status

| PR | Description | Status |
|----|-------------|--------|
| PR1 | Mapping artifact + scaffolding + CLI extract | In progress |
| PR2 | Scorer template artifact | Not started |
| PR3 | Prompt templates (Stages 1-3) + Go harness | Not started |
| PR4 | Stage 4 prompt + test retry logic | Not started |
| PR5 | Validation pipeline (Stage 5) | Not started |
| PR6 | Stage 6 + self-verification + calibration | Not started |

## Cross-PR Notes

When implementing PR3, read `docs/transfer/README.md` § Cross-PR Contracts first.

# Contributing

Welcome to the sim2real contributor guide. This section covers engineering standards, development workflows, and extension recipes for building the sim-to-production algorithm transfer pipeline.

## Quick Start

```bash
# Python CLI tools
python tools/transfer_cli.py extract routing/
python tools/transfer_cli.py validate-mapping docs/transfer/routing/mapping-artifact.md
python -m pytest tests/

# Go test harness
go test ./tools/harness/...
go build ./tools/harness/...
```

Python tests and CLI tools require a project-local virtual environment (`.venv/`). Go commands require Go 1.21+.

## Your First Contribution

The lightest extension type is **adding a new signal mapping** to the mapping artifact. See [Extension Recipes](extension-recipes.md) for a step-by-step guide.

## Development Workflows

| Workflow | When to Use |
|----------|-------------|
| [PR Workflow](pr-workflow.md) | Every PR: worktree → plan → review → implement → audit → commit |
| [Design Process](design-process.md) | New features that introduce cross-system boundaries |
| [Macro Planning](macro-planning.md) | Multi-PR features requiring decomposition |
| [Transfer Validation](transfer-validation.md) | Validating that a transfer preserves algorithm behavior |
| [Convergence Protocol](convergence.md) | Review gate used by all workflows above |
| [Troubleshooting](troubleshooting.md) | Common failure modes and fixes for pipeline operators |

## Extension Recipes

[Extension Recipes](extension-recipes.md) — Step-by-step guides for adding transfer types, signal mappings, validation suites, target systems, workspace artifacts, and scorer template updates.

## Standards

| Document | Covers |
|----------|--------|
| [Antipattern Rules (R1-R10)](standards/rules.md) | 10 rules derived from 6 self-audit dimensions |
| [Pipeline Invariants (INV-1-8)](standards/invariants.md) | Properties that must always hold across pipeline stages |
| [Engineering Principles](standards/principles.md) | Cross-system separation, artifact-driven pipeline, mixed-language conventions |
| [Transfer Validation Standards](standards/experiments.md) | Validation categories, design rules, calibration procedures |

## Templates

| Template | Purpose |
|----------|---------|
| [Design Guidelines](templates/design-guidelines.md) | Cross-system pipeline foundations, component model, extension framework |
| [Macro Plan](templates/macro-plan.md) | Cross-system pipeline macro plan structure |
| [Micro Plan](templates/micro-plan.md) | Cross-system pipeline micro plan structure |
| [Transfer Validation](templates/transfer-validation.md) | Transfer validation results structure |

## Project Architecture

sim2real is a **mixed-language, artifact-driven pipeline** project:

- **Python CLI** (`tools/transfer_cli.py`) — Mechanical tasks: extract algorithms, validate mappings, run benchmarks
- **Go test harness** (`tools/harness/`) — Equivalence testing using inference-sim as a submodule
- **Markdown prompts** (`prompts/`) — LLM instructions for each pipeline stage
- **Mapping artifacts** (`docs/transfer/`) — Bridge between simulation and production concepts
- **JSON schemas** (`tools/schemas/`) — Contract definitions for workspace artifacts

The pipeline has 6 stages: **Extract → Translate → Generate → Test → Validate → PR**. Each stage reads predecessor artifacts and writes its own outputs to `workspace/`.

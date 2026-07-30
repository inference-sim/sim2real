# Contributing to sim2real

Thank you for your interest in contributing to sim2real!

sim2real is a pipeline for taking simulation-discovered algorithms from
[inference-sim](https://github.com/inference-sim/inference-sim) into production serving systems.
The aim is a general, reproducible process for promoting an algorithm found in simulation to a
real deployment — it is not tied to any single production target. In practice the pipeline is
currently developed and validated against [llm-d-router](https://github.com/llm-d/llm-d-router)
(as scorer/EPP plugins), which serves as the reference target, but the process is designed to
generalize to other targets.

---

## Getting Started

### Prerequisites

Before contributing, ensure you have:

- **Python 3.10+** (CI runs 3.14)
- **kubectl** — configured to reach a test cluster
- **Tekton Pipelines** installed on the cluster
- **Claude Code CLI** (`claude`) — for AI-assisted development (optional but recommended)
- **Git** with submodule support

### Setup

```bash
# Clone with submodules
git clone --recurse-submodules https://github.com/inference-sim/sim2real.git
cd sim2real

# Install Python dependencies
pip install -r requirements.txt

# Run the test suite
pytest -v
```

### Running Tests

The full test suite runs in CI on every PR. To run locally:

```bash
# Run all tests
pytest -v

# Run with coverage report
pytest --cov=pipeline --cov-report=term-missing

# Run a specific test file
pytest tests/test_deploy_helpers.py -v
```

The project enforces **≥90% test coverage** (`--cov-fail-under=90`). PRs that drop coverage below this threshold will fail CI.

---

## How to Contribute

### Finding Issues

- **Good first issues**: Look for the [`good first issue`](https://github.com/inference-sim/sim2real/labels/good%20first%20issue) label
- **Documentation**: Issues labeled [`documentation`](https://github.com/inference-sim/sim2real/labels/documentation) are great entry points

### Opening Issues

Before opening a new issue:
1. Search existing issues to avoid duplicates
2. Check if the issue is labeled `holdfromhive` (operator-deferred, not ready for community work)

For bugs, include:
- Command that failed
- Full error output
- Python version and OS

### Pull Request Workflow

1. **Fork the repository** and create a branch from `main`
2. **Write your code** — keep changes focused on a single concern
3. **Add tests** — new features and bug fixes should include tests
4. **Update docs** — update `CLAUDE.md` if you change pipeline behavior or add new subcommands
5. **Run tests locally** — `pytest -v` must pass
6. **Open a PR** targeting `main`

#### Branch naming

Follow the existing conventions:
```
fix/<short-description>         # Bug fixes
feat/<short-description>        # New features
docs/<short-description>        # Documentation only
ci/<short-description>          # CI/workflow changes
refactor/<short-description>    # Code refactoring
```

### PR Checklist

Before submitting:
- [ ] Tests pass locally (`pytest -v`)
- [ ] Coverage not reduced (check with `pytest --cov=pipeline`)
- [ ] `CLAUDE.md` updated if pipeline behavior changed
- [ ] Commit message follows [conventional commits](https://www.conventionalcommits.org/)
- [ ] Signed-off-by line present (`git commit -s`)

---

## Code Style

- **Python**: PEP 8, 4-space indentation, type hints encouraged
- **Imports**: stdlib → third-party → local, alphabetically within each group
- **Error handling**: prefer explicit error messages over bare exceptions
- **No dead code**: remove unused imports and variables before submitting

---

## Architecture Overview

See [CLAUDE.md](CLAUDE.md) and [pipeline/README.md](pipeline/README.md) for the sim2real pipeline architecture, entry points, and artifact schemas.

Key modules:
```
pipeline/
  deploy.py          # Main orchestrator CLI (~3,800 lines — decomposition in progress)
  setup.py           # Workspace setup
  sim2real.py        # Translation/assembly CLI
  cluster.py         # Cluster provisioning
  lib/               # Shared library modules (proc.py, errors.py, slicer.py, assemble_run.py, ...)
  rbac/              # RBAC manifests
```

---

## Community

- **Issues**: [github.com/inference-sim/sim2real/issues](https://github.com/inference-sim/sim2real/issues)

---

## License

Apache 2.0 — see [LICENSE](LICENSE) for details.

By contributing, you agree that your contributions will be licensed under the Apache 2.0 License.

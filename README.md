# sim2real

Pipeline for transferring simulation-discovered routing algorithms from [inference-sim](inference-sim/) to production [llm-d-inference-scheduler](llm-d-inference-scheduler/) scorer plugins.

## Quick Start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Run stages using prompt templates in `prompts/`. Use `prompts/transfer.md` as the orchestrator, or execute stages individually:

| Stage | Prompt | Output |
|-------|--------|--------|
| 1 Extract | `prompts/extract.md` | `workspace/algorithm_summary.json` |
| 2 Translate | `prompts/translate.md` | `workspace/signal_coverage.json` |
| 3 Generate | `prompts/generate.md` | Go scorer plugin source |
| 4 Test | `prompts/test.md` | Build + test pass |
| 5 Validate | `prompts/validate.md` | `workspace/validation_results.json` |
| 6 PR | `prompts/pr.md` | PRs in llm-d repos + calibration log |

**Input:** Evolved algorithm in `blis_router/best/` (EVOLVE-BLOCK from inference-sim evolutionary optimization).

## Tests

```bash
python -m pytest tools/ -v
```

See `CLAUDE.md` for CLI reference, artifact contracts, and exit code semantics.

## Stage 5 Prerequisites (Cluster Benchmarks)

Stage 5 (`prompts/validate.md`) submits Tekton pipelines to a production cluster. Before running it, verify:

**Environment variable**
```bash
export NAMESPACE=<your-namespace>   # Kubernetes namespace where pipelines run
```

**Python setup**
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt     # includes PyYAML and jinja2, required by Stage 5
```

**Cluster setup**

| Requirement | Verify with |
|-------------|-------------|
| `kubectl` context points to the correct cluster | `kubectl config current-context` |
| `tkn` (Tekton CLI) installed | `tkn version` |
| Tekton operator installed on cluster | `kubectl get pods -n tekton-pipelines` |
| PVC named `data-pvc` exists in `$NAMESPACE` | `kubectl get pvc data-pvc -n $NAMESPACE` |

See `blis_router/CLUSTER.md` for the exact hardware and cluster configuration required to reproduce the simulation environment.

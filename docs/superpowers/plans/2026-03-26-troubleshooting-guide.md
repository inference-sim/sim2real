# Troubleshooting Guide Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create `docs/contributing/troubleshooting.md` covering 10 common pipeline failure modes, and link it from `README.md` and `docs/contributing/index.md`.

**Architecture:** Documentation-only. Three files touched: one new file created, two existing files edited. No code changes, no tests. Verification is grep- and content-inspection-based.

**Tech Stack:** Markdown

**Spec:** `docs/superpowers/specs/2026-03-26-troubleshooting-guide-design.md`

---

## Files Modified

| File | Change |
|------|--------|
| `docs/contributing/troubleshooting.md` | Create — full troubleshooting guide |
| `README.md` | Add one link line after line 34 |
| `docs/contributing/index.md` | Add Troubleshooting row to Development Workflows table |

---

## Task 1: Create `docs/contributing/troubleshooting.md`

**Files:**
- Create: `docs/contributing/troubleshooting.md`

This is a documentation-only task. No tests to write. Verification is reading the file to confirm all 10 items are present and correctly formatted.

- [ ] **Step 1: Create the file with this exact content**

```markdown
# Troubleshooting

Common failure modes when running the sim2real transfer pipeline. Each entry lists the **symptom**, **cause**, and **fix**.

---

## Stage 4.5: Build & Push EPP Image

### EPP image not public on ghcr.io

**Symptom:** Cluster pods stuck in `ImagePullBackOff` after Stage 4.5 completes successfully.

**Cause:** Packages pushed to ghcr.io are **private by default**. The cluster cannot pull the image without credentials.

**Fix:** Make the package public:
1. Go to `github.com/<your-org>` → **Packages** → `llm-d-inference-scheduler`
2. Click **Package settings**
3. Scroll to **Danger Zone** → **Change visibility** → **Public**

---

### Registry auth failure

**Symptom:** `podman push` or `docker push` fails with HTTP 401 or 403.

**Cause:** Not logged in to ghcr.io, or the GitHub Personal Access Token (PAT) is expired or missing the `write:packages` scope.

**Fix:** Create a PAT with `write:packages` scope at `github.com/settings/tokens`, then log in:

```bash
echo $GITHUB_PAT | podman login ghcr.io -u <your-github-username> --password-stdin
# or: echo $GITHUB_PAT | docker login ghcr.io -u <your-github-username> --password-stdin
```

---

### Docker Hub rate limit pulling base image

**Symptom:** EPP image build fails during the base image pull step with HTTP 429 or a message like "toomanyrequests: You have reached your pull rate limit."

**Cause:** Docker Hub enforces anonymous pull rate limits. This is common on shared networks and CI environments. The failing step is typically pulling a base image like `python:3.12-slim` at the start of the container build.

**Fix:** Log in to Docker Hub (even a free account resets the rate limit):

```bash
podman login docker.io
# or: docker login
```

---

### Hub placeholder not replaced

**Symptom:** Stage 4.5 halts with `epp_image.build.hub not set` or the pushed image tag contains `REPLACE_ME`.

**Cause:** `config/env_defaults.yaml` still has the placeholder value for `stack.gaie.epp_image.build.hub`.

**Fix:** Edit `config/env_defaults.yaml` and set your registry:

```yaml
stack:
  gaie:
    epp_image:
      build:
        hub: ghcr.io/<your-org>   # e.g. ghcr.io/kalantar
```

---

## Stage 5: Cluster Benchmarks

### Exit code 3 is not an error

**Symptom:** Stage 5 exits with code 3. Automated tooling or the operator treats this as a failure.

**Cause:** Exit 3 means **REENTER** — the noise characterization phase has not yet completed. This is a planned pause built into the two-pass Stage 5 flow, not an error.

**Fix:** Complete the noise phase (Steps 5a–5b in `prompts/validate.md`), then re-run `prompts/validate.md` from Step 1. This is a fresh invocation of the prompt, not a continuation of the previous session.

---

### Wrong kubectl context

**Symptom:** Tekton PipelineRuns are submitted to an unexpected cluster, or pods do not appear in `$NAMESPACE` at all.

**Cause:** `kubectl` is pointing at a different cluster context.

**Fix:**

```bash
# Check current context
kubectl config current-context

# Switch to the correct context
kubectl config use-context <correct-context-name>
```

---

### PVC not found

**Symptom:** Stage 5 halts with "PVC data-pvc not found" or a Tekton PipelineRun fails immediately with a volume mount error.

**Cause:** The `data-pvc` PersistentVolumeClaim does not exist in `$NAMESPACE`.

**Fix:** Create the PVC following your cluster setup instructions, then verify:

```bash
kubectl get pvc data-pvc -n $NAMESPACE
```

---

## Stage 5: Comparison Results

### Comparison table shows all N/A

**Symptom:** The `compare` subcommand produces a table where every metric cell shows `N/A`. The command exits 0. `WARN:` lines appear on stderr.

**Cause:** Metric values in the results JSON are stored as strings (`"123.4"`) instead of numbers (`123.4`). The tool catches the resulting `TypeError` when computing deltas, substitutes N/A, and emits a warning to stderr — but does not fail.

**Fix:**

1. Check stderr for `WARN:` lines — they identify which workload and metric are affected.
2. Inspect the raw results files to confirm values are strings:
   ```bash
   python3 -c "import json; d=json.load(open('workspace/baseline_results.json')); print(list(d.values())[0])"
   ```
3. If values are strings, the benchmark pipeline serialized them incorrectly. Re-run the affected pipeline phase to regenerate the results files.

---

## Config Gotchas

### `fast_iteration: "false"` quoted string

**Symptom:** Stage 5 halts immediately with:
```
ERROR: pipeline.fast_iteration must be a boolean, got str: 'false'
```
Exit code 2 (infrastructure error).

**Cause:** `pipeline.fast_iteration: "false"` in `config/env_defaults.yaml` — the quoted string fails the boolean type guard and causes an immediate halt before any stage logic runs.

**Fix:** Use an unquoted YAML boolean:

```yaml
pipeline:
  fast_iteration: false   # correct — unquoted boolean
  # fast_iteration: "false"  # wrong — this is a string
```

---

## Identifying Failing Pods

Use these commands when cluster benchmarks stall or produce unexpected results.

### List all pods and spot non-Running states

```bash
kubectl get pods -n $NAMESPACE
```

Look for pods in `Pending`, `CrashLoopBackOff`, `Error`, `OOMKilled`, or `ImagePullBackOff` states.

### Check vllm pod logs

```bash
# Find the vllm pod (model serving)
kubectl get pods -n $NAMESPACE -l app=vllm   # adjust label selector for your deployment

# Stream recent logs
kubectl logs -n $NAMESPACE <vllm-pod-name> -c vllm --tail=100
```

Common issues: GPU OOM (`CUDA out of memory`), model load failure, readiness probe timeout.

### Check EPP pod logs

```bash
# Find the EPP pod (inference scheduler / endpoint picker)
kubectl get pods -n $NAMESPACE -l app=llm-d-inference-scheduler   # adjust as needed

# Stream recent logs
kubectl logs -n $NAMESPACE <epp-pod-name> --tail=100
```

Common issues: `ImagePullBackOff` (image not public — see [EPP image not public](#epp-image-not-public-on-ghcrio)), scorer plugin panic, config parse error.

### Describe a pod to see events

```bash
kubectl describe pod -n $NAMESPACE <pod-name>
```

The **Events** section at the bottom shows pull failures, OOM kills, failed probes, and scheduling errors.

### Common pod failure states

| State | Likely Cause | First Step |
|-------|-------------|------------|
| `ImagePullBackOff` | Image not public or wrong tag | Check image visibility; `kubectl describe pod` for the exact image URL |
| `CrashLoopBackOff` | Container starts then exits | `kubectl logs --previous -n $NAMESPACE <pod>` to see last crash output |
| `OOMKilled` | Insufficient memory | Check GPU/CPU memory requests vs model size |
| `Pending` | No schedulable node | `kubectl describe pod` → Events for scheduling failure details |
| `Error` | Process exited non-zero | `kubectl logs` for stderr output |

---

## General

### Stale workspace artifacts

**Symptom:** A file exists in `workspace/` but the stage that produced it failed on a prior run.

**Cause:** Some failure modes exit *after* writing the artifact (for example, `extract` writes `algorithm_summary.json` before detecting an out-of-scope pattern). A stale file from an earlier successful run may also remain on disk after a subsequent failure.

**Rule:** Always use the **exit code** as the success signal, not file existence. Re-run the stage if in doubt.

**Corollary:** Never fix a `workspace/` file directly. Fix the source (prompt or CLI tool) and regenerate — direct edits are silently overwritten the next time the stage runs.
```

- [ ] **Step 2: Verify all 10 issues are present**

```bash
grep -c "^###" docs/contributing/troubleshooting.md
```

Expected: `15` (10 issue headings + 5 pod-debugging sub-headings: list pods, vllm logs, EPP logs, describe pod, failure states table)

Also spot-check key phrases:
```bash
grep "not public" docs/contributing/troubleshooting.md
grep "Exit code 3" docs/contributing/troubleshooting.md
grep "fast_iteration" docs/contributing/troubleshooting.md
grep "N/A" docs/contributing/troubleshooting.md
grep "stale" docs/contributing/troubleshooting.md -i
```

Each should return at least one match.

- [ ] **Step 3: Commit**

```bash
git add docs/contributing/troubleshooting.md
git commit -m "docs: add troubleshooting guide"
```

---

## Task 2: Link from `README.md`

**Files:**
- Modify: `README.md:33-35`

- [ ] **Step 1: Add the link line**

Current content around line 33-35 of `README.md`:
```markdown
See `CLAUDE.md` for CLI reference, artifact contracts, and exit code semantics.

## Stage 4.5 Prerequisites (Build & Push EPP Image)
```

Replace with:
```markdown
See `CLAUDE.md` for CLI reference, artifact contracts, and exit code semantics.

> For help when things go wrong, see [Troubleshooting](docs/contributing/troubleshooting.md).

## Stage 4.5 Prerequisites (Build & Push EPP Image)
```

- [ ] **Step 2: Verify**

```bash
grep "Troubleshooting" README.md
```

Expected output:
```
> For help when things go wrong, see [Troubleshooting](docs/contributing/troubleshooting.md).
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs(README): link to troubleshooting guide"
```

---

## Task 3: Add entry to `docs/contributing/index.md`

**Files:**
- Modify: `docs/contributing/index.md:28-33`

- [ ] **Step 1: Add Troubleshooting row to the Development Workflows table**

Current Development Workflows table (lines 24-32):
```markdown
## Development Workflows

| Workflow | When to Use |
|----------|-------------|
| [PR Workflow](pr-workflow.md) | Every PR: worktree → plan → review → implement → audit → commit |
| [Design Process](design-process.md) | New features that introduce cross-system boundaries |
| [Macro Planning](macro-planning.md) | Multi-PR features requiring decomposition |
| [Transfer Validation](transfer-validation.md) | Validating that a transfer preserves algorithm behavior |
| [Convergence Protocol](convergence.md) | Review gate used by all workflows above |
```

Replace with:
```markdown
## Development Workflows

| Workflow | When to Use |
|----------|-------------|
| [PR Workflow](pr-workflow.md) | Every PR: worktree → plan → review → implement → audit → commit |
| [Design Process](design-process.md) | New features that introduce cross-system boundaries |
| [Macro Planning](macro-planning.md) | Multi-PR features requiring decomposition |
| [Transfer Validation](transfer-validation.md) | Validating that a transfer preserves algorithm behavior |
| [Convergence Protocol](convergence.md) | Review gate used by all workflows above |
| [Troubleshooting](troubleshooting.md) | Common failure modes and fixes for pipeline operators |
```

- [ ] **Step 2: Verify**

```bash
grep "Troubleshooting" docs/contributing/index.md
```

Expected output:
```
| [Troubleshooting](troubleshooting.md) | Common failure modes and fixes for pipeline operators |
```

- [ ] **Step 3: Commit**

```bash
git add docs/contributing/index.md
git commit -m "docs(contributing): add troubleshooting guide to index"
```

---

## Task 4: Final verification

- [ ] **Step 1: All three files updated**

```bash
git log --oneline -3
```

Expected: three commits — troubleshooting guide, README link, index entry.

- [ ] **Step 2: Links are not broken**

```bash
# Verify the file the README links to exists
test -f docs/contributing/troubleshooting.md && echo "PASS" || echo "FAIL: file missing"

# Verify the file the index links to exists (same file, relative path)
test -f docs/contributing/troubleshooting.md && echo "PASS" || echo "FAIL"
```

Expected: both print `PASS`.

- [ ] **Step 3: Tests still pass (no regressions)**

```bash
.venv/bin/python -m pytest tools/ -q 2>&1 | tail -3
```

Expected: `243 passed, 4 skipped` (or similar — no new failures).

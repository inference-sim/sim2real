# Design: Troubleshooting Guide

**Date:** 2026-03-26

## Goal

Create `docs/contributing/troubleshooting.md` — a practical operator-facing guide covering the most common failure modes encountered when running the sim2real transfer pipeline. Link it from the root `README.md`.

Content is sourced from past Claude sessions, embedded codebase comments, and the manual testing guide. No new functionality — documentation only.

## Target File

`docs/contributing/troubleshooting.md`

## README.md Change

Add one line after the `See \`CLAUDE.md\` for CLI reference...` line (currently line 34), before the `## Stage 4.5 Prerequisites` section:

```markdown
> For help when things go wrong, see [Troubleshooting](docs/contributing/troubleshooting.md).
```

## docs/contributing/index.md Change

Add a row to the Development Workflows table:

```markdown
| [Troubleshooting](troubleshooting.md) | Common failure modes and fixes for pipeline operators |
```

## Content Structure

Each entry follows the pattern: **Symptom → Cause → Fix**.

Sections are ordered by pipeline stage so operators can find issues in context.

---

### Section 1: Stage 4.5 — Build & Push EPP Image

**1.1 EPP image not public on ghcr.io**
- Symptom: Cluster pods stuck in `ImagePullBackOff` after Stage 4.5 succeeds
- Cause: ghcr.io packages are **private by default**; the cluster cannot pull without credentials
- Fix: GitHub UI → your org/account → Packages → `llm-d-inference-scheduler` → Package settings → Change visibility → Public

**1.2 Registry auth failure**
- Symptom: `podman push` or `docker push` fails with 401/403
- Cause: Not logged in, or PAT expired / missing `write:packages` scope
- Fix: Create a GitHub PAT with `write:packages` scope; `echo $GITHUB_PAT | podman login ghcr.io -u <username> --password-stdin`

**1.3 Docker Hub rate limit pulling base image**
- Symptom: Build fails during `FROM python:3.12-slim` or similar base image pull with 429 or "too many requests"
- Cause: Docker Hub anonymous pull rate limit; common on CI and shared networks
- Fix: `podman login docker.io` (even free account resets the limit)

**1.4 Hub placeholder not replaced**
- Symptom: Stage 4.5 halts with "epp_image.build.hub not set" or image tag contains `REPLACE_ME`
- Cause: `config/env_defaults.yaml` still has the placeholder value
- Fix: Set `stack.gaie.epp_image.build.hub: ghcr.io/<your-org>` in `config/env_defaults.yaml`

---

### Section 2: Stage 5 — Cluster Benchmarks

**2.1 Exit code 3 is not an error**
- Symptom: Stage 5 exits 3; operator treats it as a failure
- Cause: Exit 3 = REENTER — the noise characterization phase is not yet complete; this is a planned pause, not an error
- Fix: Complete the noise phase (Steps 5a–5b in `prompts/validate.md`), then re-run `prompts/validate.md` from the beginning (re-entering the prompt, not continuing the previous session)

**2.2 Wrong kubectl context**
- Symptom: Pipeline submits to wrong cluster; pods appear in unexpected namespace or not at all
- Cause: `kubectl` context points to a different cluster
- Fix: `kubectl config current-context` to verify; `kubectl config use-context <correct-context>` to switch

**2.3 PVC not found**
- Symptom: Stage 5 halts with "PVC data-pvc not found" or Tekton PipelineRun fails immediately
- Cause: `data-pvc` PersistentVolumeClaim does not exist in `$NAMESPACE`
- Fix: Create the PVC per cluster setup instructions; verify with `kubectl get pvc data-pvc -n $NAMESPACE`

---

### Section 3: Stage 5 — Comparison Results

**3.1 Comparison table shows all N/A**
- Symptom: `compare` subcommand produces a table where every metric cell is `N/A`; exits 0; WARN lines appear on stderr
- Cause: Metric values in the results JSON are stored as strings (`"123.4"`) instead of numbers (`123.4`); the tool catches the resulting `TypeError` and substitutes N/A, emitting a warning to stderr
- Fix: Check stderr output for `WARN:` lines identifying the affected workload and metric. Then inspect the raw results files (`workspace/baseline_results.json`, `workspace/treatment_results.json`) and confirm metric values are JSON numbers, not strings. If they are strings, the issue is in how the benchmark pipeline serialized results — re-run the affected pipeline phase.

---

### Section 4: Config Gotchas

**4.1 `fast_iteration: "false"` quoted string**
- Symptom: Stage 5 halts immediately with `ERROR: pipeline.fast_iteration must be a boolean, got str: 'false'` (exit 2)
- Cause: `pipeline.fast_iteration: "false"` — a quoted string fails the type guard (`isinstance(val, bool)`) and causes an infrastructure error before any stage logic runs
- Fix: Use an unquoted boolean: `pipeline.fast_iteration: false`

---

### Section 5: Identifying Failing Pods

**5.1 List all pods and spot non-Running states**
```bash
kubectl get pods -n $NAMESPACE
```
Look for pods in `Pending`, `CrashLoopBackOff`, `Error`, `OOMKilled`, or `ImagePullBackOff` states.

**5.2 Identify the vllm pod and check logs**
```bash
# Find the vllm pod (model serving)
kubectl get pods -n $NAMESPACE -l app=vllm   # adjust label selector as needed

# Stream logs
kubectl logs -n $NAMESPACE <vllm-pod-name> -c vllm --tail=100
```
Common issues: GPU OOM (`CUDA out of memory`), model load failure, readiness probe failing.

**5.3 Identify the EPP pod and check logs**
```bash
# Find the EPP pod (inference scheduler / endpoint picker)
kubectl get pods -n $NAMESPACE -l app=llm-d-inference-scheduler  # adjust as needed

# Stream logs
kubectl logs -n $NAMESPACE <epp-pod-name> --tail=100
```
Common issues: `ImagePullBackOff` (image not public — see 1.1), scorer plugin panic, config parse error.

**5.4 Describe a pod to see events**
```bash
kubectl describe pod -n $NAMESPACE <pod-name>
```
The `Events` section at the bottom shows `ImagePullBackOff`, `OOMKilled`, failed liveness/readiness probes, and scheduling failures.

**5.5 Common pod failure states**

| State | Likely Cause | First Step |
|-------|-------------|------------|
| `ImagePullBackOff` | Image not public or wrong tag | Check image visibility (§1.1); verify tag with `kubectl describe` |
| `CrashLoopBackOff` | Container starts then crashes | `kubectl logs --previous` to see last crash output |
| `OOMKilled` | Insufficient memory | Check GPU/CPU memory requests; check vllm model size vs GPU capacity |
| `Pending` | No schedulable node | `kubectl describe pod` → Events for scheduling failure reason |
| `Error` | Process exited non-zero | `kubectl logs` for stderr output |

---

### Section 6: General

**6.1 Stale workspace artifacts**
- Symptom: A workspace file exists on disk but the stage that produced it failed on a prior run
- Cause: Some failure modes exit *after* writing the artifact (e.g., scope failure in `extract`); a stale file from a successful prior run may also remain
- Rule: **Always use the exit code as the success signal, not file existence.** Re-run the stage if in doubt.
- Corollary: Never fix a `workspace/` file directly — fix the source (prompt or CLI tool) and regenerate.

## Out of Scope

- KIND / local dev cluster setup issues
- Go test infrastructure (pre-built images, `CONTAINER_RUNTIME`, sidecar e2e exclusion)
- New CLI subcommands or features

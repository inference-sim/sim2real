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

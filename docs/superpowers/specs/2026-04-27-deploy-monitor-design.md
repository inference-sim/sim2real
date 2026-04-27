# Deploy Monitor Design

**Date:** 2026-04-27
**Status:** Draft

---

## 1. Problem Statement

`deploy.py run` orchestrates parallel PipelineRun execution across namespace slots and
handles timeout/retry logic, but it does not inspect *why* a deployment fails. The most
common failure mode is vLLM pods not starting — due to OOMKilled, ImagePullBackOff, node
affinity mismatches, resource quota exhaustion, or configuration errors. Diagnosing these
requires manually running `kubectl describe`, reading events, and interpreting logs.

The monitor is a standalone script that runs alongside `deploy.py run`, watches all active
slots, diagnoses problems automatically, takes safe remedial actions, and writes a report
for review when the run completes.

---

## 2. Goals

1. Detect pod failures and stuck deployments across all active namespace slots
2. Auto-remediate transient failures (tier 1) without human intervention
3. Emit rules-based suggestions for known failure signatures (tier 2)
4. Call the Anthropic API to diagnose novel or ambiguous failures (tier 3)
5. Write all findings to a human-readable `health_report.md`
6. Terminate naturally when `deploy.py run` finishes

### Out of scope

- Integration into `deploy.py run` as a child process (deferred; easy to add later)
- Monitoring gateway or HTTPRoute resources (first version focuses on pod health)
- Persistent state across monitor restarts (in-memory remediation counters only)

---

## 3. Architecture

### Lifecycle

`pipeline/monitor.py` is a standalone script started manually alongside `deploy.py run`.
It reads `progress.json` to discover active namespace slots, polls those namespaces for
pod health and events, and terminates when all pairs have left `running` state (all done,
failed, timed-out, or collected). Also exits on SIGINT/SIGTERM.

Promoting to a child process spawned automatically by `deploy.py run` is a future option
requiring only a handful of lines in `_cmd_run` — the monitor script itself does not change.

### Modules

| File | Purpose |
|---|---|
| `pipeline/monitor.py` | CLI entry point, main poll loop, Anthropic API calls |
| `pipeline/lib/health.py` | kubectl-based detection, per-pod remediation logic |

### Coordination

The only coordination point with `deploy.py run` is `progress.json`. The monitor reads it
each poll cycle to find pairs with `status: running` and their assigned namespaces. No
shared memory, no IPC.

The `experimentId` needed for pod label queries is read from the PipelineRun YAML in
`cluster/wl-*/pipelinerun-*.yaml` — the same files used by `deploy.py`.

---

## 4. Signal Sources

For each active namespace slot, the monitor collects from four sources per poll cycle:

1. **Pod states** — `kubectl get pods -n <ns> -l modelLabel=sim2real-<experimentId>`
   for vLLM pods; equivalent label query for the EPP pod. Gives fast signals:
   `Pending`, `CrashLoopBackOff`, `OOMKilled`, `ImagePullBackOff`, `Evicted`.

2. **Kubernetes events** — `kubectl get events -n <ns> --sort-by=lastTimestamp`,
   filtered to the last 10 minutes. Surfaces the reason behind a pod state:
   `FailedScheduling`, `Failed to pull image`, `OOMKilling`, `Evicted`.

3. **Pod logs** — fetched only when a problem is detected, not on every poll.
   - Current container: `kubectl logs <pod> --tail=200`
   - Prior crash: `kubectl logs <pod> --previous --tail=100` (when restart count > 0)

4. **PipelineRun task status** — `kubectl get pipelinerun <name> -n <ns> -o json`
   to identify which Tekton task is currently executing and whether any task has a
   failure condition. Tells the monitor where in the pipeline a problem is occurring
   so it can focus pod queries.

---

## 5. Detection and Remediation

Three tiers, evaluated in order per detected anomaly.

### Tier 1 — Safe auto-remediate

Act automatically and log the action to stdout. No tier-2 suggestion emitted.

| Signal | Action | Escalation |
|---|---|---|
| `Evicted` pod | `kubectl delete pod <name> -n <ns>` | Never — eviction is always transient |
| `OOMKilled` pod | `kubectl delete pod <name> -n <ns>` | After 2 consecutive OOM kills on the same pod, stop deleting and escalate to tier 2/3 |

The monitor tracks a per-pod remediation counter in memory (keyed by pod name). The
counter resets if the pod reaches `Running`/`Ready` between failures.

### Tier 2 — Rules-based suggest

No action taken. Emit a specific, actionable suggestion.

| Signal | Diagnosis | Suggestion |
|---|---|---|
| `ImagePullBackOff` / `ErrImagePull` | Wrong image tag or registry auth failure | Print the image ref that failed; point to `env_defaults.yaml` `vllm_image` or `epp_image.build.tag` |
| Pod `Pending` + `FailedScheduling: 0/N nodes available` + GPU label | No nodes match affinity | Print the `nodeAffinity` block from the PipelineRun config; list nodes with the matching GPU label |
| Pod `Pending` + `exceeded quota` event | Resource quota exhausted | Print the quota and current usage |
| `OOMKilled` after tier-1 exhaustion (attempt 3) | Persistent memory pressure | Suggest reducing `--gpu-memory-utilization`, `--max-model-len`, or replica count |
| Startup probe failure (pod `Running` but not `Ready` for > failureThreshold × periodSeconds) | Probe timeout too short for model load time | Suggest increasing `failureThreshold` in `env_defaults.yaml` |

### Tier 3 — Claude API diagnosis

Triggered when tier-2 rules do not match, or when a failure is ambiguous (e.g.
`CrashLoopBackOff` with no matching event, or persistent `Pending` > 10 minutes with
no clear event reason).

The monitor bundles `kubectl describe pod`, recent namespace events, current logs, and
previous-container logs into a prompt and calls the Anthropic API (model:
`claude-haiku-4-5-20251001` for cost; upgradeable via flag). The response is written
verbatim to `health_report.md` under the finding, with a one-line headline on stdout.

---

## 6. Output

### Stdout

Uses the same color convention as `deploy.py` (`[INFO]`, `[OK]`, `[WARN]`, `[ERROR]`).
One line per event:

```
[WARN]  kalantar-0 / wl-chatbot-mid-treatment: OOMKilled (attempt 1/2) → deleted pod
[WARN]  kalantar-0 / wl-chatbot-mid-treatment: OOMKilled (attempt 2/2) → deleted pod
[ERROR] kalantar-0 / wl-chatbot-mid-treatment: OOMKilled (attempt 3) — escalating to API diagnosis
[INFO]  kalantar-0 / wl-chatbot-mid-treatment: diagnosis written to health_report.md
```

Tier-3 API findings get a short headline on stdout; full reasoning goes to the file only.

### `health_report.md`

Written to `workspace/runs/<run>/health_report.md`. The file is regenerated on each
poll cycle that produces findings, so the summary at the top always reflects the full
history. Monitor restarts do not lose prior findings — the monitor reads the existing
file on startup and preserves prior entries. Each finding is a timestamped markdown entry:

```markdown
## 2026-04-27 14:32:11  kalantar-0 / wl-chatbot-mid-treatment

**Signal:** OOMKilled (attempt 3 — escalating)
**Pod:** sim2real-ac-decode-0
**Action taken:** none

**Diagnosis (Claude):**
Your decode pod is requesting 4 replicas × 1 H100 with --gpu-memory-utilization=0.95
and --max-model-len=40960. At this KV cache size the activation memory alone exceeds
the remaining 5% headroom during prefill bursts. Reduce to 0.85 or lower max-model-len
to 32768.

**Suggested fix:**
  env_defaults.yaml → stack.model.vllm_args: --gpu-memory-utilization=0.85
```

A summary block at the top of the file shows total findings by severity and by namespace,
kept current by the regenerate-on-write approach.

---

## 7. CLI

```
python pipeline/monitor.py [OPTIONS]

Options:
  --experiment-root PATH   Root of the experiment repo (default: cwd)
  --run NAME               Run name (default: current_run from setup_config.json)
  --interval SECONDS       Poll interval (default: 30)
  --log-lines N            Tail depth for pod logs sent to API (default: 200)
```

No subcommands. No `--dry-run` in this version (deferred).

---

## 8. Future Work

- **Child process integration:** `deploy.py run` spawns `monitor.py` automatically;
  adds `--no-monitor` escape hatch. Monitor script is unchanged.
- **Gateway / HTTPRoute monitoring:** Extend signal sources to cover Istio gateway
  and HTTPRoute readiness.
- **`--dry-run`:** Detect and diagnose without taking any tier-1 actions.
- **Configurable API model:** `--api-model` flag to use a more capable model for
  difficult failures.

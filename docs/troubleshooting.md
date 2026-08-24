# Troubleshooting

Experiment-config issues operators encounter when running the sim2real pipeline. For pipeline-level recovery operations (rerun failed pairs, stop orchestration, clean up cluster artifacts, wipe data), see [`pipeline/README.md` § Troubleshooting](../pipeline/README.md#troubleshooting).

## Framework defaults overlay

The workarounds below are applied automatically by `sim2real assemble` via a defaults overlay. `sim2real-bootstrap` copies framework templates into `<experiment-root>/baselines/defaults/` at bootstrap time, and `pipeline/lib/assemble_run.py` deep-merges enabled fragments under each baseline (precedence: defaults → experiment baseline → registered overlay). Treatment scenarios inherit transitively through the resolved baseline.

The fragments shipped today:

| Fragment stem | What it adds | Universally correct? |
|---------------|--------------|----------------------|
| `epp-verbosity` | `router.epp.verbosity: "3"` — VERBOSE, the floor for `/sim2real-check` §5c.6 | yes |
| `epponly` | `gateway.className: epponly` plus sizing and args for the Envoy sidecar **inside the EPP pod** (`router.proxy`) | no — a topology decision |
| `externally-managed-gateway` | `gateway.externallyManaged: true` — tells the chart to assume the gateway (Istio, AgentGateway, etc.) is managed externally and skip in-chart gateway provisioning | no — topology-dependent |
| `model-pvc-size` | `storage.modelPvc.size: 1Ti` (framework default 300Gi cannot hold a 70B-class model as `hf download` fetches it) | no — sized for a 70B-class model |
| `preserve-request-id` | `EnvoyFilter` that preserves the external request-id | no — matches nothing under epponly / externally-managed, and needs Istio CRDs |
| `routing-proxy-resources` | `routing.proxy.resources.requests` set to `memory: 16Gi`, `cpu: 4` — the nixl routing sidecar **beside each model server** (chart leaves it unset) | yes |
| `tokenizer-sidecar` | `router.tokenizer.enabled: true` + HF_TOKEN / HF_HOME env, so a `token-producer` plugin's `localhost:8000` resolves | no — only when the arms declare that plugin |
| `vllm-keepalive` | `VLLM_HTTP_TIMEOUT_KEEP_ALIVE=120` on prefill, above the routing sidecar's 90s idle timeout | yes, until the llm-d-benchmark pin advances (#838) |
| `vllm-logging` | `vllmCommon.flags.disableUvicornAccessLog: false` — restores vLLM's own default on **both** roles | yes |

`epponly` and `routing-proxy-resources` size **different** sidecars — `router.proxy` is the Envoy inside the EPP pod, `routing.proxy` is the nixl sidecar next to each model server. They are not duplicates. Each fragment's header comment carries the full reasoning and cites.

Making emission conditional for the "no" rows, rather than leaving it to the operator to notice, is tracked as [#840](https://github.com/inference-sim/sim2real/issues/840). Detecting a fragment that has no effect on the resolved scenario is [#841](https://github.com/inference-sim/sim2real/issues/841).

**Opt out** by listing fragment stems under `defaults.disable` in `transfer.yaml`:

```yaml
defaults:
  disable:
    - vllm-logging
```

**Per-experiment customization** is done by editing the file in `<experiment-root>/baselines/defaults/<stem>.yaml` directly — the experiment's copy is what gets merged.

**Removing a workaround framework-wide** (e.g., upstream fix landed): delete the file from `.claude/skills/sim2real-bootstrap/templates/defaults/`. New bootstraps stop including it; existing experiments keep their copy until removed.

The remainder of this document keeps the original snippets so operators can hand-apply or hand-edit them when needed.

## EPP does not start (missing RBAC for `llm-d.ai`)

The EPP fails because it does not have permission to inspect `inferenceobjectives.llm-d.ai` resources. This can happen if the `llm-d.ai` CRDs are loaded in your cluster and you are using the `main` branch of `llm-d-router`.

> **Note:** PR #28 in `tektonc-data-collection` added `llm-d.ai` RBAC to the *collector* role used by the data-collection Tekton tasks. That does not cover the EPP itself — the EPP runs under its own ServiceAccount (`${model.idLabel}-gaie-epp`). The workaround below injects a `Role` + `RoleBinding` for that ServiceAccount via the scenario YAML.
>
> **Note:** The `llm-d-rbac` RBAC fragment was removed from the shipped framework defaults (`.claude/skills/sim2real-bootstrap/templates/defaults/`) — it is no longer auto-applied by `sim2real assemble`. Apply the snippet below manually to your `baselines/*.yaml` if you encounter this error.

Add this to your `baselines/*.yaml`:

```yaml
  extraObjects:
    - apiVersion: rbac.authorization.k8s.io/v1
      kind: Role
      metadata:
        name: ${model.idLabel}-gaie-epp-llm-d
      rules:
      - apiGroups: ["llm-d.ai"]
        resources: ["inferenceobjectives", "inferencemodelrewrites"]
        verbs: ["get", "watch", "list"]
    - apiVersion: rbac.authorization.k8s.io/v1
      kind: RoleBinding
      metadata:
        name: ${model.idLabel}-gaie-epp-llm-d
      subjects:
      - kind: ServiceAccount
        name: ${model.idLabel}-gaie-epp
      roleRef:
        apiGroup: rbac.authorization.k8s.io
        kind: Role
        name: ${model.idLabel}-gaie-epp-llm-d
```

## Inspect the shared data PVC directly

When `deploy.py collect` returns something unexpected (missing files, empty phases, an unfamiliar layout), you can browse the raw contents of a slot's results volume by hand. `data-pvc` is the per-namespace results volume that pipeline tasks write into and `deploy.py collect` reads from — each namespace slot has its own.

Save the manifest below as `data-pvc-explorer.yaml`, replacing `<namespace>` with your slot namespace, then apply it. The pod mounts `/data` **read-only** and runs as a **non-root** user, so it can browse but never mutate results:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: data-pvc-explorer
  namespace: <namespace>
spec:
  containers:
    - name: shell
      image: alpine:3.20@sha256:b89d9c93e9ed3597455c90a0b88a8bbb5cb7188438f70953fede212a0c4394e0
      command: ["sh", "-c", "sleep infinity"]
      volumeMounts:
        - name: data-storage
          mountPath: /data
          readOnly: true
      securityContext:
        runAsUser: 1000
        runAsNonRoot: true
        allowPrivilegeEscalation: false
        readOnlyRootFilesystem: true
        capabilities:
          drop: ["ALL"]
      resources:
        limits:   {cpu: "200m", memory: "128Mi"}
        requests: {cpu: "50m",  memory: "32Mi"}
  volumes:
    - name: data-storage
      persistentVolumeClaim:
        claimName: data-pvc
  restartPolicy: Never
```

```bash
NAMESPACE=<your-slot-namespace>
kubectl apply  -n "$NAMESPACE" -f data-pvc-explorer.yaml
kubectl wait   -n "$NAMESPACE" --for=condition=Ready pod/data-pvc-explorer --timeout=60s
kubectl exec   -n "$NAMESPACE" data-pvc-explorer -- ls -la /data
# ... browse /data ...
kubectl delete -n "$NAMESPACE" pod/data-pvc-explorer
```

> **Note:** If `ls /data` returns permission errors, the PVC's files are owned by a UID other than `1000` and are not world-readable. Add `fsGroup: <gid>` under a pod-level `spec.securityContext` to match the files' group, or adjust `runAsUser` to the owning UID.

## Increasing logging verbosity

### EPP

Add to the `scenario` in `baselines/*.yaml`:

```yaml
router:
  epp:
    verbosity: "3"
```

**The scale is named constants, not raw numbers** (`llm-d-router pkg/common/observability/logging/const.go:20-23`, v0.9.0):

| value | constant | when to use it |
|-------|----------|----------------|
| `"1"` | — | the llm-d-benchmark default; below `DEFAULT`, so the EPP is near-silent |
| `"2"` | `DEFAULT` | |
| `"3"` | `VERBOSE` | **what `epp-verbosity` sets.** The floor for `/sim2real-check` §5c.6 |
| `"4"` | `DEBUG` | escalation for prefill-selection problems — the P/D decider explains itself here |
| `"5"` | `TRACE` | only for a specific TRACE call site you have already identified |

> **Note:** The `epp-verbosity` framework defaults fragment applies `"3"` automatically — it is enabled by default for all bootstrapped experiments. `/sim2real-check` §5c.6 (InferenceObjective resolution at runtime) reads `objectiveKey` off `Request handled` lines, which are attached under `V(logging.VERBOSE)` — so `"3"` is exactly the floor at which that check runs, and anything lower makes it **INAPPLICABLE**. Going above `"3"` costs CPU on every request, since the EPP is in the request path; prefer `"4"` over `"5"` when you need more, as it keeps the decider diagnostics while dropping every TRACE site.

### vLLM

Add to the `scenario` in `baselines/*.yaml`:

```yaml
vllmCommon:
  flags:
    disableUvicornAccessLog: false
```

> **Note:** Use this typed key, not `**.vllm.additionalFlags: [--no-disable-uvicorn-access-log]`. `additionalFlags` is a list of scalars, so `pipeline/lib/values.py:_merge_lists` has each later layer *replace* it rather than append — a flag set from the defaults overlay is discarded by `baselines/<name>.yaml` and again by the registered overlay. The typed key also applies to **both** roles; `decode.vllm.loggingLevel: INFO` was additionally a no-op, since `INFO` is already the framework default (`defaults.yaml:51`).

## Correlate requests with pods (and hence nodes)

Add to baseline files:

```yaml
  extraObjects:
    - apiVersion: networking.istio.io/v1alpha3
      kind: EnvoyFilter
      metadata:
        name: preserve-external-request-id
      spec:
        workloadSelector:
          labels:
            gateway.networking.k8s.io/gateway-name: infra-llmdbench-inference-gateway
        configPatches:
          - applyTo: NETWORK_FILTER
            match:
              context: GATEWAY
              listener:
                filterChain:
                  filter:
                    name: envoy.filters.network.http_connection_manager
            patch:
              operation: MERGE
              value:
                typed_config:
                  "@type": type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager
                  preserve_external_request_id: true
```

# Troubleshooting

Experiment-config issues operators encounter when running the sim2real pipeline. For pipeline-level recovery operations (rerun failed pairs, stop orchestration, clean up cluster artifacts, wipe data), see [`pipeline/README.md` § Troubleshooting](../pipeline/README.md#troubleshooting).

## Framework defaults overlay

The workarounds below are applied automatically by `sim2real assemble` via a defaults overlay. `sim2real-bootstrap` copies framework templates into `<experiment-root>/baselines/defaults/` at bootstrap time, and `pipeline/lib/assemble_run.py` deep-merges enabled fragments under each baseline (precedence: defaults → experiment baseline → registered overlay). Treatment scenarios inherit transitively through the resolved baseline.

The fragments shipped today:

| Fragment stem | What it adds |
|---------------|--------------|
| `epp-verbosity` | `router.epp.verbosity: "5"` |
| `externally-managed-gateway` | `gateway.externallyManaged: true` — tells the chart to assume the gateway (Istio, AgentGateway, etc.) is managed externally and skip in-chart gateway provisioning |
| `preserve-request-id` | `EnvoyFilter` that preserves the external request-id |
| `routing-proxy-resources` | `routing.proxy.resources.requests` set to `memory: 16Gi`, `cpu: 4` (chart leaves it unset) |
| `vllm-logging` | `vllm.additionalFlags: [--no-disable-uvicorn-access-log]` + `loggingLevel: INFO` |

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
    verbosity: "5"
```

> **Note:** The `epp-verbosity` framework defaults fragment applies this automatically — it is enabled by default for all bootstrapped experiments. The `/sim2real-check` §4.2 InferenceObjectives check is **INAPPLICABLE** when EPP verbosity is below `-v=3`; `verbosity: "5"` (V(5) = VERBOSE) satisfies this requirement.

### vLLM

Add to `**.vllm` in `baselines/*.yaml`:

```yaml
additionalFlags:
- --no-disable-uvicorn-access-log
loggingLevel: INFO
```

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

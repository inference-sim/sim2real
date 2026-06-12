# Troubleshooting

Experiment-config issues operators encounter when running the sim2real pipeline. For pipeline-level recovery operations (rerun failed pairs, stop orchestration, clean up cluster artifacts, wipe data), see [`pipeline/README.md` § Troubleshooting](../pipeline/README.md#troubleshooting).

## Framework defaults overlay

The workarounds below are applied automatically by `prepare.py` Phase 4 via a defaults overlay. `sim2real-bootstrap` copies framework templates into `<experiment-root>/baselines/defaults/` at bootstrap time, and `assemble.py` deep-merges enabled fragments under each baseline (precedence: defaults → experiment baseline → skill overlay). Treatment scenarios inherit transitively through the resolved baseline.

The fragments shipped today:

| Fragment stem | What it adds |
|---------------|--------------|
| `llm-d-rbac` | EPP `Role` + `RoleBinding` for `inferenceobjectives.llm-d.ai` |
| `preserve-request-id` | `EnvoyFilter` that preserves the external request-id |
| `epp-verbosity` | `inferenceExtension.verbosity: "5"` |
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

## Increasing logging verbosity

### EPP

Add to the `scenario` in `baselines/*.yaml`:

```yaml
  inferenceExtension:
    verbosity: "5"
```

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

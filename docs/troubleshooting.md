# Troubleshooting

Experiment-config issues operators encounter when running the sim2real pipeline. For pipeline-level recovery operations (rerun failed pairs, stop orchestration, clean up cluster artifacts, wipe data), see [`pipeline/README.md` § Troubleshooting](../pipeline/README.md#troubleshooting).

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

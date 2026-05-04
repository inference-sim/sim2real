"""Tekton PipelineRun generation for sim2real."""
import yaml


def _apply_workspace_bindings(ws_names: list, bindings: dict) -> list:
    """Map workspace names to their PVC/secret bindings.

    Falls back to a PVC claim named after the workspace for any unmapped name.
    """
    return [
        {"name": name, **bindings.get(name, {"persistentVolumeClaim": {"claimName": name}})}
        for name in ws_names
    ]


def make_pipelinerun_scenario(
    phase: str,
    workload: dict,
    run_name: str,
    namespace: str,
    pipeline_name: str,
    scenario_content: str,
    workspace_bindings: dict | None = None,
) -> dict:
    """Generate a PipelineRun with resolved scenario content.

    Replaces gaieConfig + inferenceObjectives with a single scenarioContent
    param containing the fully resolved llmdbenchmark scenario YAML.
    """
    wl_name = workload.get("name", workload.get("workload_name", "unknown"))
    safe_name = wl_name.replace("_", "-")
    pr_name = f"{phase}-{safe_name}-{run_name}"

    wl_spec = {k: v for k, v in workload.items() if k != "workload_name"}
    wl_spec_str = yaml.dump(wl_spec, default_flow_style=True).strip()

    spec: dict = {
        "pipelineRef": {"name": pipeline_name},
        "params": [
            {"name": "experimentId",      "value": run_name},
            {"name": "runName",           "value": run_name},
            {"name": "namespace",         "value": namespace},
            {"name": "phase",             "value": phase},
            {"name": "scenarioContent",   "value": scenario_content},
            {"name": "workloadName",      "value": wl_name},
            {"name": "workloadSpec",      "value": wl_spec_str},
        ],
        "timeouts": {"pipeline": "4h"},
    }

    if workspace_bindings is not None:
        ws_names = list(workspace_bindings.keys())
        spec["workspaces"] = _apply_workspace_bindings(ws_names, workspace_bindings)

    return {
        "apiVersion": "tekton.dev/v1",
        "kind": "PipelineRun",
        "metadata": {"name": pr_name, "namespace": namespace},
        "spec": spec,
    }



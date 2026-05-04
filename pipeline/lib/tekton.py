"""Tekton pipeline compilation for sim2real."""
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def compile_pipeline(
    template_dir: Path,
    values_path: Path,
    phase: str,
    out_dir: Path,
    run_name: str = "",
    tektonc_dir: Path | None = None,
) -> bool:
    """Compile a Tekton PipelineRun YAML for the given phase.

    Augments values with synthetic keys (phase, gaie_config,
    inference_objectives), writes to a tempfile, and invokes
    tektonc.py as a subprocess.

    Returns True on success, False on any failure (missing tektonc,
    subprocess exit non-zero, timeout, values parse error).
    Caller is responsible for writing a stub file on False.
    """
    if not template_dir.is_dir():
        return False
    if not values_path.exists():
        return False

    # Prefer unified pipeline.yaml.j2; fall back to per-phase {phase}-pipeline.yaml.j2
    unified_template = template_dir / "pipeline.yaml.j2"
    phase_template = template_dir / f"{phase}-pipeline.yaml.j2"
    if unified_template.exists():
        template_file = unified_template
    elif phase_template.exists():
        template_file = phase_template
    else:
        return False

    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / (f"sim2real-{run_name}.yaml" if run_name else f"{phase}-pipeline.yaml")

    tektonc_base = Path(tektonc_dir) if tektonc_dir else REPO_ROOT / "tektonc-data-collection"
    tektonc = tektonc_base / "tektonc" / "tektonc.py"
    if not tektonc.exists():
        return False

    # Load values and inject phase-specific synthetic keys
    try:
        values = yaml.safe_load(values_path.read_text()) or {}
    except Exception:
        return False

    values["run_name"] = run_name if run_name else phase

    # Write augmented values to a temp file for tektonc
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    try:
        yaml.dump(values, tmp, default_flow_style=False, allow_unicode=True)
        tmp.flush()
        tmp.close()

        try:
            r = subprocess.run(
                [sys.executable, str(tektonc),
                 "-t", str(template_file),
                 "-f", tmp.name,
                 "-o", str(out_file)],
                capture_output=True, text=True, shell=False, timeout=120
            )
        except subprocess.TimeoutExpired:
            return False
        except OSError:
            return False
        if r.returncode != 0:
            if r.stderr:
                print(f"[tektonc] {r.stderr.strip()}", file=sys.stderr)
            return False
    finally:
        Path(tmp.name).unlink(missing_ok=True)

    return True


def _apply_workspace_bindings(ws_names: list, bindings: dict) -> list:
    """Map workspace names to their PVC/secret bindings.

    Falls back to a PVC claim named after the workspace for any unmapped name.
    """
    return [
        {"name": name, **bindings.get(name, {"persistentVolumeClaim": {"claimName": name}})}
        for name in ws_names
    ]


def make_pipelinerun(phase: str, workload: dict, run_name: str, namespace: str,
                     pipeline_name: str = "",
                     compiled_pipeline: dict | None = None,
                     workspace_bindings: dict | None = None) -> dict:
    """Generate a Tekton PipelineRun resource for a single workload."""
    wl_name = workload.get("name", workload.get("workload_name", "unknown"))
    safe_name = wl_name.replace("_", "-")
    pr_name = f"{phase}-{safe_name}-{run_name}"

    # Exclude injected housekeeping key from the workload spec passed to the pipeline
    wl_spec = {k: v for k, v in workload.items() if k != "workload_name"}
    wl_spec_str = yaml.dump(wl_spec, default_flow_style=True).strip()

    spec: dict = {
        "pipelineRef": {
            "name": pipeline_name or f"{phase}-pipeline"
        },
        "params": [
            {"name": "experimentId", "value": run_name},
            {"name": "runName", "value": run_name},
            {"name": "namespace", "value": namespace},
            {"name": "workloadName", "value": wl_name},
            {"name": "workloadSpec", "value": wl_spec_str},
        ],
    }
    if workspace_bindings is not None:
        # Derive workspace names from the compiled pipeline; fall back to binding keys
        if compiled_pipeline:
            ws_names = [ws["name"] for ws in compiled_pipeline.get("spec", {}).get("workspaces", [])]
        else:
            ws_names = list(workspace_bindings.keys())
        spec["workspaces"] = _apply_workspace_bindings(ws_names, workspace_bindings)

    spec["timeouts"] = {"pipeline": "4h"}

    return {
        "apiVersion": "tekton.dev/v1",
        "kind": "PipelineRun",
        "metadata": {
            "name": pr_name,
            "namespace": namespace,
        },
        "spec": spec,
    }



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



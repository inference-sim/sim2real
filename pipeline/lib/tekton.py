"""Tekton PipelineRun generation for sim2real."""
import hashlib
import json

import yaml

_SPEC_BASE_DIR = "/workspace/source/llm-d-benchmark"
_SCENARIO_FILE_PATH = "/tmp/llmdbench-config/scenario.yaml"

# Per-pipelineTask timeout overrides. These ride on top of the pipeline-level
# 4h ceiling and catch a stuck task earlier so the slot frees up. Values must
# stay below spec.timeouts.pipeline below; Tekton rejects taskRunSpecs entries
# whose timeout exceeds the enclosing pipeline timeout.
_TASK_TIMEOUTS: dict[str, str] = {
    "stream-epp-logs": "2h",
    "stream-gpu-stats": "2h",
    "run-workload-blis-observe-binary": "90m",
}


# Canonical shape of resultsDir in pipeline.yaml. Every task that writes into
# resultsDir threads this exact template. build_results_dir() renders it with
# concrete values for callers that construct the path locally (e.g. tests).
# Kept in one place so pipeline.yaml drift is caught by test_pipeline_yaml.py.
RESULTS_DIR_TEMPLATE = (
    "$(params.runName)/$(params.phase)/$(params.workloadName)/i$(params.replica)"
)


def build_results_dir(run: str, phase: str, workload: str, replica) -> str:
    """Return the canonical resultsDir path for a (run, phase, workload, replica)
    tuple. Callers supply either concrete strings/ints or Tekton param
    references — both round-trip through the same template.
    """
    return f"{run}/{phase}/{workload}/i{replica}"


_DNS_SUBDOMAIN_MAX = 253


def validate_pipelinerun_name(name: str) -> None:
    """Raise ValueError if ``name`` exceeds the RFC 1123 DNS subdomain limit
    (253 chars). PipelineRun.metadata.name is a DNS subdomain, so Tekton
    rejects longer names at admission. Called at construction time so
    assemble surfaces the failure before any dispatch attempt.
    """
    if len(name) > _DNS_SUBDOMAIN_MAX:
        raise ValueError(
            f"PipelineRun name {name!r} is {len(name)} chars, exceeds the "
            f"{_DNS_SUBDOMAIN_MAX}-char DNS subdomain limit"
        )


def _default_spec_content(base_dir: str = _SPEC_BASE_DIR,
                          scenario_file: str = _SCENARIO_FILE_PATH) -> str:
    """Return the llmdbenchmark spec content string with PVC paths."""
    return (
        f"base_dir: {base_dir}\n"
        f"\n"
        f"values_file:\n"
        f"  path: {base_dir}/config/templates/values/defaults.yaml\n"
        f"\n"
        f"template_dir:\n"
        f"  path: {base_dir}/config/templates/jinja\n"
        f"\n"
        f"scenario_file:\n"
        f"  path: {scenario_file}\n"
    )


def _apply_workspace_bindings(ws_names: list, bindings: dict) -> list:
    """Map workspace names to their PVC/secret bindings.

    Falls back to a PVC claim named after the workspace for any unmapped name.
    """
    return [
        {"name": name, **bindings.get(name, {"persistentVolumeClaim": {"claimName": name}})}
        for name in ws_names
    ]


_OBSERVE_PARAM_ORDER = (
    "maxConcurrency", "timeout", "warmupRequests", "prewarmDuration", "extraArgs",
)


def is_trace_workload(workload: dict) -> bool:
    """Return True iff ``workload`` declares a non-empty ``trace`` mapping.

    A trace workload sources its request stream from a recorded trace
    (prepare-trace + a session pool) rather than a generative WorkloadSpec.
    Any workload without a non-empty ``trace`` block is generative and flows
    through the existing ``workloadSpec`` path unchanged.
    """
    trace = workload.get("trace")
    return isinstance(trace, dict) and bool(trace)


def trace_path(wl_name: str, trace: dict) -> str:
    """Return the deterministic relative path ``traces/<safe_wl_name>-<sha12>``
    for a trace descriptor.

    ``<sha12>`` is the first 12 hex chars of sha256 over a CANONICAL JSON
    serialization of ``trace`` (``sort_keys=True``, no whitespace) so the path
    is STABLE across runs for identical descriptors and CHANGES when the
    descriptor changes. ``<safe_wl_name>`` is the workload name with ``_`` → ``-``.
    """
    canonical = json.dumps(trace, sort_keys=True, separators=(",", ":")).encode("utf-8")
    sha12 = hashlib.sha256(canonical).hexdigest()[:12]
    safe = wl_name.replace("_", "-")
    return f"traces/{safe}-{sha12}"


def make_pipelinerun_scenario(
    phase: str,
    workload: dict,
    run_name: str,
    namespace: str,
    pipeline_name: str,
    scenario_content: str,
    workspace_bindings: dict | None = None,
    spec_content: str | None = None,
    benchmark_git_commit: str = "",
    benchmark_git_repo_url: str = "",
    blis_git_commit: str = "",
    blis_git_repo_url: str = "",
    model: str = "",
    observe: dict | None = None,
    iteration: int = 1,
) -> dict:
    """Generate a PipelineRun with resolved scenario content."""
    if spec_content is None:
        spec_content = _default_spec_content()
    wl_name = workload.get("name", workload.get("workload_name", "unknown"))
    safe_name = wl_name.replace("_", "-")
    safe_phase = phase.replace("_", "-")
    pr_name = f"{safe_phase}-{safe_name}-{run_name}-i{iteration}"
    validate_pipelinerun_name(pr_name)

    # A trace workload emits a locked set of trace params (traceSpec/tracePath/
    # session counts) and an EMPTY workloadSpec — observe must NOT source
    # requests from a generative WorkloadSpec in trace mode. Generative
    # workloads keep the historical workloadSpec path byte-for-byte unchanged.
    trace_mode = is_trace_workload(workload)
    if trace_mode:
        trace = workload["trace"]
        wl_spec_str = ""
        trace_spec_str = yaml.dump(trace, default_flow_style=True).strip()
        t_path = trace_path(wl_name, trace)
        pool = trace.get("pool", {})
        concurrent_sessions = str(pool.get("concurrent_sessions"))
        total_sessions = str(pool.get("total_sessions"))
    else:
        wl_spec = {k: v for k, v in workload.items() if k != "workload_name"}
        wl_spec_str = yaml.dump(wl_spec, default_flow_style=True).strip()

    params: list[dict] = [
        {"name": "experimentId",      "value": run_name},
        {"name": "runName",           "value": run_name},
        {"name": "namespace",         "value": namespace},
        {"name": "phase",             "value": phase},
        {"name": "scenarioContent",   "value": scenario_content},
        {"name": "specContent",       "value": spec_content},
        {"name": "workloadName",      "value": wl_name},
        {"name": "workloadSpec",      "value": wl_spec_str},
        {"name": "benchmarkGitRepoUrl", "value": benchmark_git_repo_url},
        {"name": "benchmarkGitCommit", "value": benchmark_git_commit},
        {"name": "blisGitRepoUrl",   "value": blis_git_repo_url},
        {"name": "blisGitCommit",     "value": blis_git_commit},
        {"name": "model",            "value": model},
        {"name": "replica",          "value": str(iteration)},
    ]
    if trace_mode:
        # Trace-only params, adjacent to workloadSpec. Generative workloads
        # deliberately do NOT emit these so their param list stays identical
        # to prior releases.
        params += [
            {"name": "traceSpec",          "value": trace_spec_str},
            {"name": "tracePath",          "value": t_path},
            {"name": "concurrentSessions", "value": concurrent_sessions},
            {"name": "totalSessions",      "value": total_sessions},
        ]
    if observe:
        # Emit only specified keys; omitted ones fall through to Pipeline-level
        # defaults declared in pipeline/pipeline.yaml. Tekton params are strings.
        for k in _OBSERVE_PARAM_ORDER:
            if k in observe:
                params.append({"name": k, "value": str(observe[k])})

    spec: dict = {
        "pipelineRef": {"name": pipeline_name},
        "taskRunTemplate": {"serviceAccountName": "helm-installer"},
        "params": params,
        "timeouts": {"pipeline": "4h"},
        "taskRunSpecs": [
            {"pipelineTaskName": name, "timeout": dur}
            for name, dur in _TASK_TIMEOUTS.items()
        ],
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



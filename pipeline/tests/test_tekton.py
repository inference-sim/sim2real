"""Tests for Tekton PipelineRun generation."""

from pipeline.lib.tekton import make_pipelinerun_scenario


# ── Tests for make_pipelinerun_scenario ──────────────────────────────────────

_WORKSPACE_BINDINGS = {
    "data-storage":   {"persistentVolumeClaim": {"claimName": "data-pvc"}},
    "source":         {"persistentVolumeClaim": {"claimName": "source-pvc"}},
}

def test_make_pipelinerun_scenario_name():
    pr = make_pipelinerun_scenario(
        phase="baseline", workload={"name": "wl-smoke"}, run_name="ac",
        namespace="kalantar-0", pipeline_name="sim2real-ac",
        scenario_content="scenario: []",
        workspace_bindings=_WORKSPACE_BINDINGS,
    )
    assert pr["metadata"]["name"] == "baseline-wl-smoke-ac-i1"
    assert pr["metadata"]["namespace"] == "kalantar-0"


def test_make_pipelinerun_scenario_params():
    pr = make_pipelinerun_scenario(
        phase="treatment", workload={"name": "chatbot-mid"}, run_name="ac",
        namespace="ns", pipeline_name="sim2real-ac",
        scenario_content="scenario:\n- name: test\n",
        workspace_bindings=_WORKSPACE_BINDINGS,
    )
    params = {p["name"]: p["value"] for p in pr["spec"]["params"]}
    assert params["phase"] == "treatment"
    assert params["scenarioContent"] == "scenario:\n- name: test\n"
    assert params["workloadName"] == "chatbot-mid"
    assert "gaieConfig" not in params
    assert "inferenceObjectives" not in params


def test_make_pipelinerun_scenario_spec_content_default():
    pr = make_pipelinerun_scenario(
        phase="baseline", workload={"name": "wl"}, run_name="r",
        namespace="ns", pipeline_name="sim2real-r",
        scenario_content="{}",
        workspace_bindings=_WORKSPACE_BINDINGS,
    )
    params = {p["name"]: p["value"] for p in pr["spec"]["params"]}
    assert "specContent" in params
    spec = params["specContent"]
    assert "/workspace/source/llm-d-benchmark" in spec
    assert "/tmp/llmdbench-config/scenario.yaml" in spec
    assert "values_file:" in spec
    assert "template_dir:" in spec


def test_make_pipelinerun_scenario_spec_content_custom():
    from pipeline.lib.tekton import make_pipelinerun_scenario
    custom_spec = "base_dir: /custom\nscenario_file:\n  path: /custom/scenario.yaml\n"
    pr = make_pipelinerun_scenario(
        phase="baseline", workload={"name": "wl"}, run_name="r",
        namespace="ns", pipeline_name="sim2real-r",
        scenario_content="{}",
        workspace_bindings=_WORKSPACE_BINDINGS,
        spec_content=custom_spec,
    )
    params = {p["name"]: p["value"] for p in pr["spec"]["params"]}
    assert params["specContent"] == custom_spec


def test_make_pipelinerun_scenario_service_account():
    """PipelineRun pins taskRunTemplate.serviceAccountName so TaskRuns do not
    fall back to the namespace `default` SA."""
    pr = make_pipelinerun_scenario(
        phase="baseline", workload={"name": "wl"}, run_name="r",
        namespace="ns", pipeline_name="sim2real-r",
        scenario_content="{}",
        workspace_bindings=_WORKSPACE_BINDINGS,
    )
    assert pr["spec"]["taskRunTemplate"]["serviceAccountName"] == "helm-installer"


def test_make_pipelinerun_scenario_task_run_specs():
    """taskRunSpecs entries gate per-task timeouts on the long-running tasks."""
    from pipeline.lib.tekton import _TASK_TIMEOUTS
    pr = make_pipelinerun_scenario(
        phase="baseline", workload={"name": "wl"}, run_name="r",
        namespace="ns", pipeline_name="sim2real-r",
        scenario_content="{}",
        workspace_bindings=_WORKSPACE_BINDINGS,
    )
    specs = pr["spec"]["taskRunSpecs"]
    by_task = {s["pipelineTaskName"]: s["timeout"] for s in specs}
    assert by_task == _TASK_TIMEOUTS
    # The override must not collide with the global serviceAccountName or the
    # pipeline-level timeout — both stay where they are.
    assert pr["spec"]["taskRunTemplate"]["serviceAccountName"] == "helm-installer"
    assert pr["spec"]["timeouts"] == {"pipeline": "4h"}


def test_make_pipelinerun_scenario_workspace_bindings():
    pr = make_pipelinerun_scenario(
        phase="baseline", workload={"name": "wl"}, run_name="r",
        namespace="ns", pipeline_name="sim2real-r",
        scenario_content="{}",
        workspace_bindings=_WORKSPACE_BINDINGS,
    )
    ws_names = {ws["name"] for ws in pr["spec"]["workspaces"]}
    assert "source" in ws_names
    assert "data-storage" in ws_names
    assert "model-cache" not in ws_names
    assert "hf-credentials" not in ws_names


# ── Tests for phase name sanitization ─────────────────────────────────────────


def test_phase_name_in_pipelinerun():
    """Custom phase names appear in PipelineRun metadata and params."""
    pr = make_pipelinerun_scenario(
        phase="b1",
        workload={"name": "wl-smoke"},
        run_name="test-run",
        namespace="ns-0",
        pipeline_name="sim2real",
        scenario_content="scenario: []",
    )
    assert pr["metadata"]["name"] == "b1-wl-smoke-test-run-i1"
    params = {p["name"]: p["value"] for p in pr["spec"]["params"]}
    assert params["phase"] == "b1"


def test_phase_underscore_sanitized_in_name():
    """Underscores in phase names are converted to hyphens in PipelineRun name."""
    pr = make_pipelinerun_scenario(
        phase="my_phase",
        workload={"name": "wl-smoke"},
        run_name="test-run",
        namespace="ns-0",
        pipeline_name="sim2real",
        scenario_content="scenario: []",
    )
    assert pr["metadata"]["name"] == "my-phase-wl-smoke-test-run-i1"
    params = {p["name"]: p["value"] for p in pr["spec"]["params"]}
    assert params["phase"] == "my_phase"


def test_default_spec_content():
    """PipelineRun includes default spec content when none provided."""
    pr = make_pipelinerun_scenario(
        phase="baseline",
        workload={"name": "wl-smoke"},
        run_name="run-1",
        namespace="ns-0",
        pipeline_name="sim2real",
        scenario_content="scenario: []",
    )
    params = {p["name"]: p["value"] for p in pr["spec"]["params"]}
    assert "defaults.yaml" in params["specContent"]


def test_workspace_bindings():
    """Workspace bindings are applied when provided."""
    pr = make_pipelinerun_scenario(
        phase="baseline",
        workload={"name": "wl-smoke"},
        run_name="run-1",
        namespace="ns-0",
        pipeline_name="sim2real",
        scenario_content="scenario: []",
        workspace_bindings={"data-storage": {"persistentVolumeClaim": {"claimName": "my-pvc"}}},
    )
    assert "workspaces" in pr["spec"]
    ws = pr["spec"]["workspaces"]
    assert ws[0]["name"] == "data-storage"
    assert ws[0]["persistentVolumeClaim"]["claimName"] == "my-pvc"


# ── observe dict → PipelineRun params ─────────────────────────────────────────

_OBSERVE_KEYS = ("maxConcurrency", "timeout", "warmupRequests", "prewarmDuration", "extraArgs")


def _names(pr):
    return [p["name"] for p in pr["spec"]["params"]]


def test_observe_absent_emits_no_observe_params():
    """observe=None (or absent) leaves the PipelineRun param list at its base set."""
    pr = make_pipelinerun_scenario(
        phase="baseline", workload={"name": "wl"}, run_name="r",
        namespace="ns", pipeline_name="sim2real-r",
        scenario_content="{}",
    )
    names = _names(pr)
    for k in _OBSERVE_KEYS:
        assert k not in names, f"absent observe leaked {k}"


def test_observe_empty_dict_emits_no_observe_params():
    """observe={} (the manifest default when section is absent) emits nothing."""
    pr = make_pipelinerun_scenario(
        phase="baseline", workload={"name": "wl"}, run_name="r",
        namespace="ns", pipeline_name="sim2real-r",
        scenario_content="{}",
        observe={},
    )
    names = _names(pr)
    for k in _OBSERVE_KEYS:
        assert k not in names


def test_observe_partial_emits_only_specified_keys():
    """Omitted keys are left for Tekton to fall through to Pipeline-level defaults."""
    pr = make_pipelinerun_scenario(
        phase="baseline", workload={"name": "wl"}, run_name="r",
        namespace="ns", pipeline_name="sim2real-r",
        scenario_content="{}",
        observe={"timeout": 3600, "maxConcurrency": 5000},
    )
    params = {p["name"]: p["value"] for p in pr["spec"]["params"]}
    assert params["timeout"] == "3600"
    assert params["maxConcurrency"] == "5000"
    for k in ("warmupRequests", "prewarmDuration", "extraArgs"):
        assert k not in params


def test_observe_full_dict_emits_all_keys_as_strings():
    """All five keys flow through; values are coerced to strings for Tekton."""
    pr = make_pipelinerun_scenario(
        phase="baseline", workload={"name": "wl"}, run_name="r",
        namespace="ns", pipeline_name="sim2real-r",
        scenario_content="{}",
        observe={
            "maxConcurrency": 5000,
            "timeout": 3600,
            "warmupRequests": 25,
            "prewarmDuration": "30s",
            "extraArgs": "--foo bar",
        },
    )
    params = {p["name"]: p["value"] for p in pr["spec"]["params"]}
    assert params["maxConcurrency"] == "5000"
    assert params["timeout"] == "3600"
    assert params["warmupRequests"] == "25"
    assert params["prewarmDuration"] == "30s"
    assert params["extraArgs"] == "--foo bar"


def test_make_pipelinerun_scenario_iteration_default_is_one():
    """When iteration is not passed, default is 1 and name gets '-i1' suffix."""
    pr = make_pipelinerun_scenario(
        phase="baseline", workload={"name": "wl"}, run_name="r",
        namespace="ns", pipeline_name="sim2real",
        scenario_content="scenario: []",
    )
    assert pr["metadata"]["name"] == "baseline-wl-r-i1"


def test_make_pipelinerun_scenario_iteration_explicit():
    """Explicit iteration=N produces '-i<N>' suffix on metadata.name."""
    pr = make_pipelinerun_scenario(
        phase="baseline", workload={"name": "wl"}, run_name="r",
        namespace="ns", pipeline_name="sim2real",
        scenario_content="scenario: []",
        iteration=5,
    )
    assert pr["metadata"]["name"] == "baseline-wl-r-i5"


# ── Tests for build_results_dir + RESULTS_DIR_TEMPLATE ──────────────────────

from pipeline.lib.tekton import build_results_dir, RESULTS_DIR_TEMPLATE


def test_build_results_dir_returns_slash_joined_path():
    assert build_results_dir("run1", "baseline", "chatbot-mid", 1) == "run1/baseline/chatbot-mid/i1"


def test_build_results_dir_accepts_int_replica():
    assert build_results_dir("r", "p", "w", 3) == "r/p/w/i3"


def test_build_results_dir_accepts_str_replica():
    """String replica must produce the same output — used for pipeline.yaml templating."""
    assert build_results_dir("r", "p", "w", "3") == "r/p/w/i3"


def test_results_dir_template_shape():
    """The pipeline.yaml template must be exactly the shape build_results_dir
    would produce if each segment were a Tekton param reference."""
    assert RESULTS_DIR_TEMPLATE == "$(params.runName)/$(params.phase)/$(params.workloadName)/i$(params.replica)"


def test_build_results_dir_matches_template_when_params_substituted():
    """build_results_dir with Tekton-param strings reproduces the template
    verbatim. This is the invariant test_pipeline_yaml.py will assert against
    every resultsDir value in pipeline.yaml."""
    template = build_results_dir(
        "$(params.runName)", "$(params.phase)",
        "$(params.workloadName)", "$(params.replica)",
    )
    assert template == RESULTS_DIR_TEMPLATE

"""Tests for pipeline.lib.tekton module."""


# ── Tests for make_pipelinerun_scenario ──────────────────────────────────────

_WORKSPACE_BINDINGS_PARALLEL = {
    "model-cache":    {"persistentVolumeClaim": {"claimName": "model-pvc"}},
    "data-storage":   {"persistentVolumeClaim": {"claimName": "data-pvc"}},
    "hf-credentials": {"secret": {"secretName": "hf-secret"}},
    "source":         {"persistentVolumeClaim": {"claimName": "source-pvc"}},
}

def test_make_pipelinerun_scenario_name():
    from pipeline.lib.tekton import make_pipelinerun_scenario
    pr = make_pipelinerun_scenario(
        phase="baseline", workload={"name": "wl-smoke"}, run_name="ac",
        namespace="kalantar-0", pipeline_name="sim2real-ac",
        scenario_content="scenario: []",
        workspace_bindings=_WORKSPACE_BINDINGS_PARALLEL,
    )
    assert pr["metadata"]["name"] == "baseline-wl-smoke-ac"
    assert pr["metadata"]["namespace"] == "kalantar-0"


def test_make_pipelinerun_scenario_params():
    from pipeline.lib.tekton import make_pipelinerun_scenario
    pr = make_pipelinerun_scenario(
        phase="treatment", workload={"name": "chatbot-mid"}, run_name="ac",
        namespace="ns", pipeline_name="sim2real-ac",
        scenario_content="scenario:\n- name: test\n",
        workspace_bindings=_WORKSPACE_BINDINGS_PARALLEL,
    )
    params = {p["name"]: p["value"] for p in pr["spec"]["params"]}
    assert params["phase"] == "treatment"
    assert params["scenarioContent"] == "scenario:\n- name: test\n"
    assert params["workloadName"] == "chatbot-mid"
    assert "gaieConfig" not in params
    assert "inferenceObjectives" not in params


def test_make_pipelinerun_scenario_spec_content_default():
    from pipeline.lib.tekton import make_pipelinerun_scenario
    pr = make_pipelinerun_scenario(
        phase="baseline", workload={"name": "wl"}, run_name="r",
        namespace="ns", pipeline_name="sim2real-r",
        scenario_content="{}",
        workspace_bindings=_WORKSPACE_BINDINGS_PARALLEL,
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
        workspace_bindings=_WORKSPACE_BINDINGS_PARALLEL,
        spec_content=custom_spec,
    )
    params = {p["name"]: p["value"] for p in pr["spec"]["params"]}
    assert params["specContent"] == custom_spec


def test_make_pipelinerun_scenario_workspace_bindings():
    from pipeline.lib.tekton import make_pipelinerun_scenario
    pr = make_pipelinerun_scenario(
        phase="baseline", workload={"name": "wl"}, run_name="r",
        namespace="ns", pipeline_name="sim2real-r",
        scenario_content="{}",
        workspace_bindings=_WORKSPACE_BINDINGS_PARALLEL,
    )
    ws_names = {ws["name"] for ws in pr["spec"]["workspaces"]}
    assert "model-cache" in ws_names
    assert "data-storage" in ws_names

"""Tests for pipeline manifest override in prepare.py and deploy.py."""
from pipeline.lib.tekton import make_pipelinerun_scenario


def test_pipelinerun_uses_custom_pipeline_name():
    """make_pipelinerun_scenario embeds the pipeline_name in pipelineRef."""
    pr = make_pipelinerun_scenario(
        phase="baseline",
        workload={"name": "share-gpt", "num_requests": 10},
        run_name="run-001",
        namespace="sim2real-slot-0",
        pipeline_name="custom-pipeline",
        scenario_content="kind: scenario",
    )
    assert pr["spec"]["pipelineRef"]["name"] == "custom-pipeline"


def test_pipelinerun_uses_default_pipeline_name():
    """Default pipeline name is 'sim2real'."""
    pr = make_pipelinerun_scenario(
        phase="treatment",
        workload={"name": "share-gpt", "num_requests": 10},
        run_name="run-001",
        namespace="sim2real-slot-0",
        pipeline_name="sim2real",
        scenario_content="kind: scenario",
    )
    assert pr["spec"]["pipelineRef"]["name"] == "sim2real"

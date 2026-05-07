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


def test_pipeline_yaml_resolves_relative_to_repo_root(tmp_path):
    """pipeline.yaml from manifest is resolved relative to REPO_ROOT."""
    custom_yaml = tmp_path / "custom" / "my-pipeline.yaml"
    custom_yaml.parent.mkdir(parents=True)
    custom_yaml.write_text("apiVersion: tekton.dev/v1\nkind: Pipeline\n")

    manifest = {"pipeline": {"yaml": "custom/my-pipeline.yaml"}}
    yaml_path = manifest.get("pipeline", {}).get("yaml", "pipeline/pipeline.yaml")
    resolved = tmp_path / yaml_path
    assert resolved.exists()
    assert resolved == custom_yaml

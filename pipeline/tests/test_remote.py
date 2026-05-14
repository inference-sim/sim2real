"""Tests for remote run support (ConfigMap packing)."""

import json

import pytest

from pipeline.lib.remote import CONFIGMAP_NAME, build_run_inputs_configmap


@pytest.fixture()
def workspace(tmp_path):
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    run_dir = workspace_dir / "runs" / "test-run"
    run_dir.mkdir(parents=True)
    (run_dir / "cluster").mkdir()
    return workspace_dir, run_dir


def _write_defaults(workspace_dir, run_dir):
    setup = {"namespace": "ns", "pipeline_name": "p"}
    (workspace_dir / "setup_config.json").write_text(json.dumps(setup))
    meta = {"run_name": "test-run"}
    (run_dir / "run_metadata.json").write_text(json.dumps(meta))


def test_setup_config_packed(workspace):
    workspace_dir, run_dir = workspace
    _write_defaults(workspace_dir, run_dir)
    cm = build_run_inputs_configmap(
        run_dir=run_dir, workspace_dir=workspace_dir,
        namespace="ns", run_name="test-run",
    )
    assert cm["apiVersion"] == "v1"
    assert cm["kind"] == "ConfigMap"
    assert cm["metadata"]["name"] == CONFIGMAP_NAME
    assert cm["metadata"]["namespace"] == "ns"
    assert json.loads(cm["data"]["setup_config.json"]) == {
        "namespace": "ns", "pipeline_name": "p",
    }


def test_run_metadata_packed(workspace):
    workspace_dir, run_dir = workspace
    _write_defaults(workspace_dir, run_dir)
    cm = build_run_inputs_configmap(
        run_dir=run_dir, workspace_dir=workspace_dir,
        namespace="ns", run_name="test-run",
    )
    assert json.loads(cm["data"]["run_metadata.json"]) == {"run_name": "test-run"}


def test_cluster_yamls_packed(workspace):
    workspace_dir, run_dir = workspace
    _write_defaults(workspace_dir, run_dir)
    (run_dir / "cluster" / "baseline.yaml").write_text("base: true\n")
    (run_dir / "cluster" / "treatment.yaml").write_text("treat: true\n")
    cm = build_run_inputs_configmap(
        run_dir=run_dir, workspace_dir=workspace_dir,
        namespace="ns", run_name="test-run",
    )
    assert cm["data"]["cluster--baseline.yaml"] == "base: true\n"
    assert cm["data"]["cluster--treatment.yaml"] == "treat: true\n"


def test_missing_setup_config(workspace):
    workspace_dir, run_dir = workspace
    (run_dir / "run_metadata.json").write_text("{}")
    with pytest.raises(FileNotFoundError, match="setup_config.json"):
        build_run_inputs_configmap(
            run_dir=run_dir, workspace_dir=workspace_dir,
            namespace="ns", run_name="test-run",
        )


def test_missing_run_metadata(workspace):
    workspace_dir, run_dir = workspace
    (workspace_dir / "setup_config.json").write_text("{}")
    with pytest.raises(FileNotFoundError, match="run_metadata.json"):
        build_run_inputs_configmap(
            run_dir=run_dir, workspace_dir=workspace_dir,
            namespace="ns", run_name="test-run",
        )

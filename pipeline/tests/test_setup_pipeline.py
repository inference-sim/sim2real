"""Tests for setup.py Pipeline YAML application in step_tekton."""
import json
from unittest.mock import patch, call


def _make_config(**overrides):
    """Build a minimal SetupConfig with defaults for testing."""
    from pipeline.setup import SetupConfig
    defaults = dict(
        namespace="test-ns",
        namespaces=["test-ns"],
        registry="quay.io/test",
        repo_name="llm-d-inference-scheduler",
        run_name="test-run",
        hf_token="hf_xxx",
        github_token="gh_xxx",
        registry_user="user",
        registry_token="token",
        storage_class="standard",
        is_openshift=False,
        no_cluster=False,
        pipeline_yaml=None,
    )
    defaults.update(overrides)
    return SetupConfig(**defaults)


class TestStepTektonPipelineApply:
    """step_tekton applies Pipeline YAML to namespace."""

    @patch("pipeline.setup.run")
    def test_applies_default_pipeline_yaml(self, mock_run, tmp_path):
        """step_tekton applies pipeline/pipeline.yaml (default) after steps/tasks."""
        from pipeline.setup import step_tekton, REPO_ROOT

        cfg = _make_config()
        # Mock the kubectl pods check to succeed
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "pod Running"

        step_tekton(cfg)

        # Find the call that applies the pipeline YAML
        default_pipeline_path = REPO_ROOT / "pipeline" / "pipeline.yaml"
        pipeline_apply_call = call(
            ["kubectl", "apply", "-f", str(default_pipeline_path), "-n=test-ns"]
        )
        assert pipeline_apply_call in mock_run.call_args_list

    @patch("pipeline.setup.run")
    def test_applies_custom_pipeline_yaml(self, mock_run, tmp_path):
        """step_tekton uses custom pipeline_yaml path when set on config."""
        from pipeline.setup import step_tekton

        custom_path = str(tmp_path / "custom-pipeline.yaml")
        (tmp_path / "custom-pipeline.yaml").write_text("apiVersion: tekton.dev/v1\n")
        cfg = _make_config(pipeline_yaml=custom_path)

        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "pod Running"

        step_tekton(cfg)

        pipeline_apply_call = call(
            ["kubectl", "apply", "-f", custom_path, "-n=test-ns"]
        )
        assert pipeline_apply_call in mock_run.call_args_list

    @patch("pipeline.setup.run")
    def test_skipped_when_no_cluster(self, mock_run):
        """step_tekton is skipped entirely when no_cluster=True."""
        from pipeline.setup import step_tekton

        cfg = _make_config(no_cluster=True)
        step_tekton(cfg)

        # run() should not be called at all
        mock_run.assert_not_called()

    @patch("pipeline.setup.run")
    def test_warns_if_pipeline_yaml_missing(self, mock_run, tmp_path, capsys):
        """step_tekton warns (not errors) if pipeline YAML doesn't exist on disk."""
        from pipeline.setup import step_tekton

        nonexistent = str(tmp_path / "does-not-exist.yaml")
        cfg = _make_config(pipeline_yaml=nonexistent)

        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "pod Running"

        # Should not raise
        step_tekton(cfg)

        captured = capsys.readouterr()
        assert "not found" in captured.out.lower() or "WARN" in captured.out


class TestSetupConfigJson:
    """setup_config.json includes pipeline_yaml key."""

    def test_config_output_includes_pipeline_yaml(self, tmp_path):
        """step_config_output writes pipeline_yaml to setup_config.json."""
        from pipeline.setup import step_config_output
        import pipeline.setup as setup_module

        # Point EXPERIMENT_ROOT to tmp_path
        original_experiment_root = setup_module.EXPERIMENT_ROOT
        setup_module.EXPERIMENT_ROOT = tmp_path

        try:
            run_dir = tmp_path / "workspace" / "runs" / "test-run"
            run_dir.mkdir(parents=True)

            cfg = _make_config(pipeline_yaml="/path/to/pipeline.yaml")
            step_config_output(cfg, run_dir, "podman")

            config_path = tmp_path / "workspace" / "setup_config.json"
            assert config_path.exists()
            data = json.loads(config_path.read_text())
            assert "pipeline_yaml" in data
            assert data["pipeline_yaml"] == "/path/to/pipeline.yaml"
        finally:
            setup_module.EXPERIMENT_ROOT = original_experiment_root

    def test_config_output_pipeline_yaml_none(self, tmp_path):
        """When pipeline_yaml is None, key still present with null value."""
        from pipeline.setup import step_config_output
        import pipeline.setup as setup_module

        original_experiment_root = setup_module.EXPERIMENT_ROOT
        setup_module.EXPERIMENT_ROOT = tmp_path

        try:
            run_dir = tmp_path / "workspace" / "runs" / "test-run"
            run_dir.mkdir(parents=True)

            cfg = _make_config(pipeline_yaml=None)
            step_config_output(cfg, run_dir, "podman")

            config_path = tmp_path / "workspace" / "setup_config.json"
            data = json.loads(config_path.read_text())
            assert "pipeline_yaml" in data
            assert data["pipeline_yaml"] is None
        finally:
            setup_module.EXPERIMENT_ROOT = original_experiment_root


class TestBuildParser:
    """build_parser includes --pipeline-yaml flag."""

    def test_pipeline_yaml_flag_exists(self):
        from pipeline.setup import build_parser
        parser = build_parser()
        args = parser.parse_args(["--pipeline-yaml", "/path/to/pipeline.yaml"])
        assert args.pipeline_yaml == "/path/to/pipeline.yaml"

    def test_pipeline_yaml_defaults_to_none(self):
        from pipeline.setup import build_parser
        parser = build_parser()
        args = parser.parse_args([])
        assert args.pipeline_yaml is None

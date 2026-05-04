"""Tests for pipeline.lib.tekton module."""
from pathlib import Path
from unittest.mock import MagicMock, patch


def test_returns_false_when_tektonc_absent(tmp_path):
    """When tektonc binary is missing, compile_pipeline returns False."""
    from pipeline.lib import tekton

    # Patch REPO_ROOT so tektonc path points to nonexistent location
    with patch.object(tekton, "REPO_ROOT", tmp_path):
        template_dir = tmp_path / "templates"
        template_dir.mkdir()
        (template_dir / "pipeline.yaml.j2").write_text("dummy template")

        values_file = tmp_path / "values.yaml"
        values_file.write_text("phase: treatment\n")

        out_dir = tmp_path / "out"

        result = tekton.compile_pipeline(template_dir, values_file, "treatment", out_dir)
        assert result is False


def test_returns_true_on_subprocess_success(tmp_path):
    """When subprocess succeeds, compile_pipeline returns True."""
    from pipeline.lib import tekton

    # Patch REPO_ROOT and create tektonc binary
    with patch.object(tekton, "REPO_ROOT", tmp_path):
        tektonc_path = tmp_path / "tektonc-data-collection" / "tektonc"
        tektonc_path.mkdir(parents=True)
        (tektonc_path / "tektonc.py").write_text("#!/usr/bin/env python3\nprint('dummy')")

        template_dir = tmp_path / "templates"
        template_dir.mkdir()
        (template_dir / "pipeline.yaml.j2").write_text("dummy template")

        values_file = tmp_path / "values.yaml"
        values_file.write_text("phase: treatment\n")

        out_dir = tmp_path / "out"

        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("subprocess.run", return_value=mock_result):
            result = tekton.compile_pipeline(template_dir, values_file, "treatment", out_dir)
            assert result is True


def test_returns_false_on_subprocess_failure(tmp_path):
    """When subprocess fails, compile_pipeline returns False."""
    from pipeline.lib import tekton

    # Patch REPO_ROOT and create tektonc binary
    with patch.object(tekton, "REPO_ROOT", tmp_path):
        tektonc_path = tmp_path / "tektonc-data-collection" / "tektonc"
        tektonc_path.mkdir(parents=True)
        (tektonc_path / "tektonc.py").write_text("#!/usr/bin/env python3\nprint('dummy')")

        template_dir = tmp_path / "templates"
        template_dir.mkdir()
        (template_dir / "pipeline.yaml.j2").write_text("dummy template")

        values_file = tmp_path / "values.yaml"
        values_file.write_text("phase: treatment\n")

        out_dir = tmp_path / "out"

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "compilation error"
        with patch("subprocess.run", return_value=mock_result):
            result = tekton.compile_pipeline(template_dir, values_file, "treatment", out_dir)
            assert result is False


def test_uses_unified_template_when_present(tmp_path):
    """When both unified and phase-specific templates exist, unified takes precedence."""
    from pipeline.lib import tekton

    # Patch REPO_ROOT and create tektonc binary
    with patch.object(tekton, "REPO_ROOT", tmp_path):
        tektonc_path = tmp_path / "tektonc-data-collection" / "tektonc"
        tektonc_path.mkdir(parents=True)
        (tektonc_path / "tektonc.py").write_text("#!/usr/bin/env python3\nprint('dummy')")

        template_dir = tmp_path / "templates"
        template_dir.mkdir()
        # Create both templates
        (template_dir / "pipeline.yaml.j2").write_text("unified template")
        (template_dir / "treatment-pipeline.yaml.j2").write_text("phase-specific template")

        values_file = tmp_path / "values.yaml"
        values_file.write_text("phase: treatment\n")

        out_dir = tmp_path / "out"

        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = tekton.compile_pipeline(template_dir, values_file, "treatment", out_dir)
            assert result is True

            # Verify subprocess.run was called with the unified template
            call_args = mock_run.call_args[0][0]
            # call_args is the list passed to subprocess.run
            # Format: [sys.executable, str(tektonc), "-t", str(template_file), "-f", tmp_file, "-o", str(out_file)]
            template_arg_index = call_args.index("-t") + 1
            template_used = Path(call_args[template_arg_index])
            assert template_used.name == "pipeline.yaml.j2"


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

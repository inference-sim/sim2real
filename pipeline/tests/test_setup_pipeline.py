"""Tests for the trimmed setup.py (workspace config writer).

Cluster-side responsibilities (namespace, RBAC, secrets, PVCs, Tekton) moved
to pipeline/cluster.py + pipeline/lib/cluster_ops.py — see issue #424 and
the epic #416 design. Tests for those primitives live in
pipeline/tests/test_cluster_ops.py and pipeline/tests/test_cluster_py.py.

Run-directory materialization (workspace/runs/<run>/) moved to
`sim2real assemble` in step-2 — see issue #481. setup.py no longer touches
workspace/runs/ or writes run_metadata.json or current_run. Tests here
enforce that invariant.
"""
import json
import pytest


def _make_config(**overrides):
    """Build a minimal SetupConfig with defaults for testing."""
    from pipeline.setup import SetupConfig
    defaults = dict(
        registry="quay.io/test",
        repo_name="llm-d-inference-scheduler",
        registry_user="user",
        registry_token="token",
    )
    defaults.update(overrides)
    return SetupConfig(**defaults)


class TestSetupConfigJson:
    """step_config_output writes the operator-side keys."""

    def test_writes_kept_keys(self, tmp_path):
        from pipeline.setup import step_config_output
        import pipeline.setup as setup_module

        original = setup_module.EXPERIMENT_ROOT
        setup_module.EXPERIMENT_ROOT = tmp_path
        try:
            cfg = _make_config(orchestrator_image="ghcr.io/x/orch:abc")
            step_config_output(cfg)

            data = json.loads((tmp_path / "workspace" / "setup_config.json").read_text())
            assert data["registry"] == "quay.io/test"
            assert data["repo_name"] == "llm-d-inference-scheduler"
            assert data["orchestrator_image"] == "ghcr.io/x/orch:abc"
            assert "sim2real_root" in data
        finally:
            setup_module.EXPERIMENT_ROOT = original

    def test_orchestrator_image_empty_default(self, tmp_path):
        """orchestrator_image written as empty string when not set on cfg."""
        from pipeline.setup import step_config_output
        import pipeline.setup as setup_module

        original = setup_module.EXPERIMENT_ROOT
        setup_module.EXPERIMENT_ROOT = tmp_path
        try:
            step_config_output(_make_config())
            data = json.loads((tmp_path / "workspace" / "setup_config.json").read_text())
            assert "orchestrator_image" in data
            assert data["orchestrator_image"] == ""
        finally:
            setup_module.EXPERIMENT_ROOT = original

    @pytest.mark.parametrize("removed_key", [
        # Cluster-side (moved to cluster.py provision + cluster_config.json)
        "namespace", "namespaces", "is_openshift", "storage_class",
        "hf_secret_name", "workspaces", "tektonc_dir", "setup_timestamp",
        "container_runtime",
        # #442: pipeline_yaml moved to cluster.py provision + cluster_config.json.
        "pipeline_yaml",
        # #481: current_run is owned by sim2real use.
        "current_run",
    ])
    def test_removed_keys_absent(self, tmp_path, removed_key):
        """Cluster-scoped, run-scoped, and cruft keys are not written to setup_config.json."""
        from pipeline.setup import step_config_output
        import pipeline.setup as setup_module

        original = setup_module.EXPERIMENT_ROOT
        setup_module.EXPERIMENT_ROOT = tmp_path
        try:
            step_config_output(_make_config())
            data = json.loads((tmp_path / "workspace" / "setup_config.json").read_text())
            assert removed_key not in data
        finally:
            setup_module.EXPERIMENT_ROOT = original

    def test_preserves_keys_owned_by_other_writers(self, tmp_path):
        """A pre-existing setup_config.json with foreign keys is preserved
        on re-run — setup.py only owns the keys it writes. In particular,
        ``current_run`` (owned by ``sim2real use``) must survive a setup
        re-run.
        """
        from pipeline.setup import step_config_output
        import pipeline.setup as setup_module

        original = setup_module.EXPERIMENT_ROOT
        setup_module.EXPERIMENT_ROOT = tmp_path
        try:
            workspace = tmp_path / "workspace"
            workspace.mkdir(parents=True)
            # Simulate current_run set by `sim2real use` plus an unrelated
            # foreign key.
            (workspace / "setup_config.json").write_text(json.dumps({
                "registry": "old.example/x",
                "current_run": "trial-1",
                "foreign_key": "must-survive",
            }))
            step_config_output(_make_config())

            data = json.loads((workspace / "setup_config.json").read_text())
            assert data["registry"] == "quay.io/test"  # refreshed
            assert data["current_run"] == "trial-1"    # preserved (owned by `sim2real use`)
            assert data["foreign_key"] == "must-survive"  # preserved
        finally:
            setup_module.EXPERIMENT_ROOT = original


class TestSetupDoesNotTouchRuns:
    """Regression tests for issue #481 — setup.py must not create or
    modify anything under ``workspace/runs/``. Any write there would
    collide with ``sim2real assemble``'s existence guard on a fresh
    workspace.
    """

    def test_step_config_output_does_not_create_runs_dir(self, tmp_path):
        from pipeline.setup import step_config_output
        import pipeline.setup as setup_module

        original = setup_module.EXPERIMENT_ROOT
        setup_module.EXPERIMENT_ROOT = tmp_path
        try:
            step_config_output(_make_config())
        finally:
            setup_module.EXPERIMENT_ROOT = original

        assert not (tmp_path / "workspace" / "runs").exists()

    def test_step_config_output_leaves_existing_runs_dir_untouched(
        self, tmp_path
    ):
        """A pre-existing runs/<run>/ from a prior ``sim2real assemble``
        must survive a setup re-run byte-for-byte. If setup.py touched
        the directory, it would collide with the assemble guard on the
        NEXT ``sim2real assemble --run <other>`` invocation.
        """
        from pipeline.setup import step_config_output
        import pipeline.setup as setup_module

        run_dir = tmp_path / "workspace" / "runs" / "trial-1"
        run_dir.mkdir(parents=True)
        marker = run_dir / "marker.txt"
        marker.write_text("assembled-by-sim2real")

        original = setup_module.EXPERIMENT_ROOT
        setup_module.EXPERIMENT_ROOT = tmp_path
        try:
            step_config_output(_make_config())
        finally:
            setup_module.EXPERIMENT_ROOT = original

        assert marker.read_text() == "assembled-by-sim2real"
        # No sibling runs/<other> directory was created.
        siblings = [p.name for p in (tmp_path / "workspace" / "runs").iterdir()]
        assert siblings == ["trial-1"]


class TestBuildParser:
    """build_parser surface: kept flags accepted; removed flags raise."""

    KEPT_FLAGS = [
        ("--registry", "REG"),
        ("--repo-name", "NAME"),
        ("--orchestrator-image", "img"),
        ("--experiment-root", "/x"),
        ("--test-push", None),       # store_true
        ("--test-push-tag", "t"),
        ("--registry-user", "u"),
        ("--registry-token", "t"),
    ]

    REMOVED_FLAGS = [
        "--namespace", "--namespaces", "--storage-class",
        "--hf-token", "--github-token",
        "--no-cluster", "--redeploy-tasks",
        # #442: --pipeline-yaml moved to cluster.py provision.
        "--pipeline-yaml",
        # #481: --run moved to `sim2real assemble --run` / `sim2real use --run`.
        "--run",
    ]

    @pytest.mark.parametrize("flag,value", KEPT_FLAGS)
    def test_kept_flag_parses(self, flag, value):
        from pipeline.setup import build_parser
        argv = [flag] if value is None else [flag, value]
        # Confirm no SystemExit / argparse error.
        build_parser().parse_args(argv)

    @pytest.mark.parametrize("flag", REMOVED_FLAGS)
    def test_removed_flag_raises(self, flag):
        from pipeline.setup import build_parser
        with pytest.raises(SystemExit):
            build_parser().parse_args([flag, "x"])

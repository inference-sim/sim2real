"""Tests for the trimmed setup.py (workspace config writer).

Cluster-side responsibilities (namespace, RBAC, secrets, PVCs, Tekton) moved
to pipeline/cluster.py + pipeline/lib/cluster_ops.py — see issue #424 and
the epic #416 design. Tests for those primitives live in
pipeline/tests/test_cluster_ops.py and pipeline/tests/test_cluster_py.py.
"""
import json
import pytest


def _make_config(**overrides):
    """Build a minimal SetupConfig with defaults for testing."""
    from pipeline.setup import SetupConfig
    defaults = dict(
        registry="quay.io/test",
        repo_name="llm-d-inference-scheduler",
        run_name="test-run",
        registry_user="user",
        registry_token="token",
        pipeline_yaml=None,
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
            run_dir = tmp_path / "workspace" / "runs" / "test-run"
            run_dir.mkdir(parents=True)

            cfg = _make_config(pipeline_yaml="/path/to/pipeline.yaml",
                               orchestrator_image="ghcr.io/x/orch:abc")
            step_config_output(cfg, run_dir)

            data = json.loads((tmp_path / "workspace" / "setup_config.json").read_text())
            assert data["registry"] == "quay.io/test"
            assert data["repo_name"] == "llm-d-inference-scheduler"
            assert data["pipeline_yaml"] == "/path/to/pipeline.yaml"
            assert data["orchestrator_image"] == "ghcr.io/x/orch:abc"
            assert data["current_run"] == "test-run"
            assert "sim2real_root" in data
        finally:
            setup_module.EXPERIMENT_ROOT = original

    def test_pipeline_yaml_none_persisted(self, tmp_path):
        """When pipeline_yaml is None, key still present with null value."""
        from pipeline.setup import step_config_output
        import pipeline.setup as setup_module

        original = setup_module.EXPERIMENT_ROOT
        setup_module.EXPERIMENT_ROOT = tmp_path
        try:
            run_dir = tmp_path / "workspace" / "runs" / "test-run"
            run_dir.mkdir(parents=True)

            step_config_output(_make_config(pipeline_yaml=None), run_dir)

            data = json.loads((tmp_path / "workspace" / "setup_config.json").read_text())
            assert "pipeline_yaml" in data
            assert data["pipeline_yaml"] is None
        finally:
            setup_module.EXPERIMENT_ROOT = original

    def test_orchestrator_image_empty_default(self, tmp_path):
        """orchestrator_image written as empty string when not set on cfg."""
        from pipeline.setup import step_config_output
        import pipeline.setup as setup_module

        original = setup_module.EXPERIMENT_ROOT
        setup_module.EXPERIMENT_ROOT = tmp_path
        try:
            run_dir = tmp_path / "workspace" / "runs" / "test-run"
            run_dir.mkdir(parents=True)
            step_config_output(_make_config(), run_dir)
            data = json.loads((tmp_path / "workspace" / "setup_config.json").read_text())
            assert "orchestrator_image" in data
            assert data["orchestrator_image"] == ""
        finally:
            setup_module.EXPERIMENT_ROOT = original

    @pytest.mark.parametrize("removed_key", [
        "namespace", "namespaces", "is_openshift", "storage_class",
        "hf_secret_name", "workspaces", "tektonc_dir", "setup_timestamp",
        "container_runtime",
    ])
    def test_removed_keys_absent(self, tmp_path, removed_key):
        """Cluster-scoped and cruft keys are not written to setup_config.json."""
        from pipeline.setup import step_config_output
        import pipeline.setup as setup_module

        original = setup_module.EXPERIMENT_ROOT
        setup_module.EXPERIMENT_ROOT = tmp_path
        try:
            run_dir = tmp_path / "workspace" / "runs" / "test-run"
            run_dir.mkdir(parents=True)
            step_config_output(_make_config(), run_dir)
            data = json.loads((tmp_path / "workspace" / "setup_config.json").read_text())
            assert removed_key not in data
        finally:
            setup_module.EXPERIMENT_ROOT = original

    def test_preserves_keys_owned_by_other_writers(self, tmp_path):
        """A pre-existing setup_config.json with foreign keys is preserved
        on re-run — setup.py only owns the keys it writes."""
        from pipeline.setup import step_config_output
        import pipeline.setup as setup_module

        original = setup_module.EXPERIMENT_ROOT
        setup_module.EXPERIMENT_ROOT = tmp_path
        try:
            workspace = tmp_path / "workspace"
            workspace.mkdir(parents=True)
            # Simulate a foreign key (e.g. a future cluster_id pointer, or
            # any other writer's field) already in the file.
            (workspace / "setup_config.json").write_text(json.dumps({
                "registry": "old.example/x",
                "foreign_key": "must-survive",
            }))
            run_dir = workspace / "runs" / "test-run"
            run_dir.mkdir(parents=True)
            step_config_output(_make_config(), run_dir)

            data = json.loads((workspace / "setup_config.json").read_text())
            assert data["registry"] == "quay.io/test"  # refreshed
            assert data["foreign_key"] == "must-survive"  # preserved
        finally:
            setup_module.EXPERIMENT_ROOT = original


class TestBuildParser:
    """build_parser surface: kept flags accepted; removed flags raise."""

    KEPT_FLAGS = [
        ("--registry", "REG"),
        ("--repo-name", "NAME"),
        ("--pipeline-yaml", "/p"),
        ("--orchestrator-image", "img"),
        ("--run", "n"),
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


class TestRunMetadataIdempotent:
    """Re-running setup must preserve deploy-owned fields in run_metadata.json (issue #365)."""

    def _run_setup(self, tmp_path, **cfg_overrides):
        from pipeline.setup import step_config_output
        import pipeline.setup as setup_module

        original = setup_module.EXPERIMENT_ROOT
        setup_module.EXPERIMENT_ROOT = tmp_path
        try:
            run_dir = tmp_path / "workspace" / "runs" / "test-run"
            run_dir.mkdir(parents=True, exist_ok=True)
            cfg = _make_config(**cfg_overrides)
            step_config_output(cfg, run_dir)
            return run_dir
        finally:
            setup_module.EXPERIMENT_ROOT = original

    def test_preserves_source_hashes_on_rerun(self, tmp_path):
        """Deploy-written source_hashes must survive a setup re-run."""
        run_dir = self._run_setup(tmp_path)
        meta_path = run_dir / "run_metadata.json"

        meta = json.loads(meta_path.read_text())
        meta["source_hashes"] = {"quay.io/test/llm-d-inference-scheduler:test-run": "abc123def456"}
        meta_path.write_text(json.dumps(meta))

        self._run_setup(tmp_path)

        meta2 = json.loads(meta_path.read_text())
        assert meta2.get("source_hashes") == {
            "quay.io/test/llm-d-inference-scheduler:test-run": "abc123def456"
        }

    def test_preserves_epp_image_on_rerun(self, tmp_path):
        """Deploy-written epp_image must survive a setup re-run."""
        run_dir = self._run_setup(tmp_path)
        meta_path = run_dir / "run_metadata.json"

        meta = json.loads(meta_path.read_text())
        meta["epp_image"] = "quay.io/test/llm-d-inference-scheduler:test-run"
        meta_path.write_text(json.dumps(meta))

        self._run_setup(tmp_path)

        meta2 = json.loads(meta_path.read_text())
        assert meta2.get("epp_image") == "quay.io/test/llm-d-inference-scheduler:test-run"

    def test_preserves_stages_deploy_last_completed_step(self, tmp_path):
        """stages.deploy.last_completed_step (deploy-owned) must survive a setup re-run."""
        run_dir = self._run_setup(tmp_path)
        meta_path = run_dir / "run_metadata.json"

        meta = json.loads(meta_path.read_text())
        meta.setdefault("stages", {}).setdefault("deploy", {})["last_completed_step"] = "build"
        meta_path.write_text(json.dumps(meta))

        self._run_setup(tmp_path)

        meta2 = json.loads(meta_path.read_text())
        assert meta2["stages"]["deploy"].get("last_completed_step") == "build"

    def test_refreshes_setup_owned_fields_on_rerun(self, tmp_path):
        """Setup-owned fields (registry, repo_name) reflect the latest cfg on re-run."""
        from pipeline.setup import step_config_output
        import pipeline.setup as setup_module

        run_dir = self._run_setup(tmp_path)
        meta_path = run_dir / "run_metadata.json"

        original = setup_module.EXPERIMENT_ROOT
        setup_module.EXPERIMENT_ROOT = tmp_path
        try:
            cfg2 = _make_config(registry="quay.io/new-registry", repo_name="new-repo")
            step_config_output(cfg2, run_dir)
        finally:
            setup_module.EXPERIMENT_ROOT = original

        meta2 = json.loads(meta_path.read_text())
        assert meta2["registry"] == "quay.io/new-registry"
        assert meta2["repo_name"] == "new-repo"

    def test_first_run_creates_metadata(self, tmp_path):
        """First-run path produces setup-owned fields. Cluster-scoped fields
        are NOT written by setup.py anymore (cluster_config.json owns them)."""
        run_dir = self._run_setup(tmp_path)
        meta = json.loads((run_dir / "run_metadata.json").read_text())
        assert meta["version"] == 1
        assert meta["registry"] == "quay.io/test"
        assert meta["repo_name"] == "llm-d-inference-scheduler"
        assert meta["component_image"] == "quay.io/test/llm-d-inference-scheduler:test-run"
        assert meta["stages"]["setup"]["status"] == "completed"
        assert meta["stages"]["prepare"] == {"status": "pending"}
        assert meta["stages"]["deploy"] == {"status": "pending"}
        assert meta["stages"]["results"] == {"status": "pending"}
        # Cluster-scoped fields are NOT written by setup anymore.
        for absent in ("namespace", "storage_class", "is_openshift", "container_runtime"):
            assert absent not in meta

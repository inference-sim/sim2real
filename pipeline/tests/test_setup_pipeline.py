"""Tests for setup.py Pipeline YAML application in step_tekton."""
import json
from unittest.mock import patch


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
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "pod Running"
        mock_run.return_value.stderr = ""

        step_tekton(cfg)

        # Find the call that applies the pipeline YAML (with check=False, capture=True)
        default_pipeline_path = REPO_ROOT / "pipeline" / "pipeline.yaml"
        pipeline_calls = [c for c in mock_run.call_args_list
                          if str(default_pipeline_path) in str(c) and "apply" in str(c)]
        assert len(pipeline_calls) == 1

    @patch("pipeline.setup.run")
    def test_applies_custom_pipeline_yaml(self, mock_run, tmp_path):
        """step_tekton uses custom pipeline_yaml path when set on config."""
        from pipeline.setup import step_tekton

        custom_path = str(tmp_path / "custom-pipeline.yaml")
        (tmp_path / "custom-pipeline.yaml").write_text("apiVersion: tekton.dev/v1\n")
        cfg = _make_config(pipeline_yaml=custom_path)

        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "pod Running"
        mock_run.return_value.stderr = ""

        step_tekton(cfg)

        pipeline_calls = [c for c in mock_run.call_args_list
                          if custom_path in str(c) and "apply" in str(c)]
        assert len(pipeline_calls) == 1

    @patch("pipeline.setup.run")
    def test_skipped_when_no_cluster(self, mock_run):
        """step_tekton is skipped entirely when no_cluster=True."""
        from pipeline.setup import step_tekton

        cfg = _make_config(no_cluster=True)
        step_tekton(cfg)

        # run() should not be called at all
        mock_run.assert_not_called()

    @patch("pipeline.setup.run")
    def test_warns_if_custom_pipeline_yaml_missing(self, mock_run, tmp_path, capsys):
        """step_tekton warns (not errors) if custom pipeline YAML doesn't exist."""
        from pipeline.setup import step_tekton

        nonexistent = str(tmp_path / "does-not-exist.yaml")
        cfg = _make_config(pipeline_yaml=nonexistent)

        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "pod Running"

        # Should not raise — custom path warns
        step_tekton(cfg)

        captured = capsys.readouterr()
        assert "not found" in captured.out.lower() or "WARN" in captured.out

    @patch("pipeline.setup.run")
    @patch("pipeline.setup.REPO_ROOT")
    def test_fatal_if_default_pipeline_yaml_missing(self, mock_root, mock_run, tmp_path):
        """step_tekton exits fatally if default pipeline/pipeline.yaml is missing."""
        from pipeline.setup import step_tekton

        # Point REPO_ROOT to a dir without pipeline/pipeline.yaml
        mock_root.__truediv__ = lambda self, other: tmp_path / other
        cfg = _make_config(pipeline_yaml=None)

        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "pod Running"

        import pytest
        with patch("pipeline.setup.REPO_ROOT", tmp_path):
            with pytest.raises(SystemExit):
                step_tekton(cfg)

    @patch("pipeline.setup.run")
    def test_kubectl_apply_failure_warns_and_continues(self, mock_run, tmp_path, capsys):
        """step_tekton warns (not exits) if kubectl apply for Pipeline fails."""
        from pipeline.setup import step_tekton, REPO_ROOT

        cfg = _make_config()

        def side_effect(cmd, *, check=True, capture=False, input=None):
            class R:
                returncode = 0
                stdout = "pod Running"
                stderr = ""
            r = R()
            if "pipeline.yaml" in str(cmd):
                r.returncode = 1
                r.stderr = "error: RBAC denied"
            return r

        mock_run.side_effect = side_effect

        pipeline_path = REPO_ROOT / "pipeline" / "pipeline.yaml"
        if pipeline_path.exists():
            step_tekton(cfg)
            captured = capsys.readouterr()
            assert "Failed to apply Pipeline" in captured.out


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


    def test_config_output_includes_hf_secret_name(self, tmp_path):
        """setup_config.json includes hf_secret_name field."""
        from pipeline.setup import step_config_output
        import pipeline.setup as setup_module

        original_experiment_root = setup_module.EXPERIMENT_ROOT
        setup_module.EXPERIMENT_ROOT = tmp_path

        try:
            run_dir = tmp_path / "workspace" / "runs" / "test-run"
            run_dir.mkdir(parents=True)

            cfg = _make_config()
            step_config_output(cfg, run_dir, "podman")

            config_path = tmp_path / "workspace" / "setup_config.json"
            data = json.loads(config_path.read_text())
            assert data["hf_secret_name"] == "hf-secret"
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

    def test_orchestrator_image_flag_exists(self):
        from pipeline.setup import build_parser
        parser = build_parser()
        args = parser.parse_args(["--orchestrator-image", "ghcr.io/inference-sim/sim2real/orchestrator:latest"])
        assert args.orchestrator_image == "ghcr.io/inference-sim/sim2real/orchestrator:latest"

    def test_orchestrator_image_defaults_to_none(self):
        from pipeline.setup import build_parser
        parser = build_parser()
        args = parser.parse_args([])
        assert args.orchestrator_image is None


class TestOrchestratorImageConfig:
    """setup_config.json includes orchestrator_image key."""

    def test_config_output_includes_orchestrator_image(self, tmp_path):
        """step_config_output writes orchestrator_image to setup_config.json."""
        from pipeline.setup import step_config_output
        import pipeline.setup as setup_module

        original = setup_module.EXPERIMENT_ROOT
        setup_module.EXPERIMENT_ROOT = tmp_path
        try:
            run_dir = tmp_path / "workspace" / "runs" / "test-run"
            run_dir.mkdir(parents=True)
            cfg = _make_config(orchestrator_image="ghcr.io/inference-sim/sim2real/orchestrator:abc123")
            step_config_output(cfg, run_dir, "podman")
            data = json.loads((tmp_path / "workspace" / "setup_config.json").read_text())
            assert data["orchestrator_image"] == "ghcr.io/inference-sim/sim2real/orchestrator:abc123"
        finally:
            setup_module.EXPERIMENT_ROOT = original

    def test_config_output_orchestrator_image_empty(self, tmp_path):
        """orchestrator_image written as empty string when not set."""
        from pipeline.setup import step_config_output
        import pipeline.setup as setup_module

        original = setup_module.EXPERIMENT_ROOT
        setup_module.EXPERIMENT_ROOT = tmp_path
        try:
            run_dir = tmp_path / "workspace" / "runs" / "test-run"
            run_dir.mkdir(parents=True)
            cfg = _make_config()
            step_config_output(cfg, run_dir, "podman")
            data = json.loads((tmp_path / "workspace" / "setup_config.json").read_text())
            assert "orchestrator_image" in data
            assert data["orchestrator_image"] == ""
        finally:
            setup_module.EXPERIMENT_ROOT = original


class TestRedeployTasksPipeline:
    """--redeploy-tasks also applies Pipeline YAML."""

    @patch("pipeline.setup.run")
    def test_redeploy_applies_pipeline(self, mock_run, tmp_path):
        """--redeploy-tasks applies Pipeline alongside steps/tasks."""
        from pipeline.setup import main, REPO_ROOT

        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""

        pipeline_path = REPO_ROOT / "pipeline" / "pipeline.yaml"
        if not pipeline_path.exists():
            return  # skip if repo structure doesn't have the file

        with patch("sys.argv", ["setup.py", "--redeploy-tasks", "--namespace", "test-ns"]):
            result = main()

        assert result == 0
        pipeline_calls = [c for c in mock_run.call_args_list
                          if "pipeline.yaml" in str(c)]
        assert len(pipeline_calls) >= 1

    @patch("pipeline.setup.run")
    def test_redeploy_custom_pipeline_missing_warns(self, mock_run, tmp_path, capsys):
        """--redeploy-tasks with missing custom --pipeline-yaml warns."""
        from pipeline.setup import main

        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""

        nonexistent = str(tmp_path / "nope.yaml")
        with patch("sys.argv", ["setup.py", "--redeploy-tasks", "--namespace", "ns",
                                "--pipeline-yaml", nonexistent]):
            result = main()

        assert result == 0
        captured = capsys.readouterr()
        assert "not found" in captured.out.lower() or "WARN" in captured.out

    @patch("pipeline.setup.run")
    def test_redeploy_default_pipeline_missing_errors(self, mock_run, tmp_path):
        """--redeploy-tasks fails if default pipeline.yaml is missing."""
        from pipeline.setup import main

        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""

        with patch("pipeline.setup.REPO_ROOT", tmp_path), \
             patch("pipeline.setup.TEKTONC_DIR", tmp_path), \
             patch("sys.argv", ["setup.py", "--redeploy-tasks", "--namespace", "ns"]):
            result = main()

        assert result == 1


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
            step_config_output(cfg, run_dir, "podman")
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
        """Setup-owned fields (registry, namespace, container_runtime) reflect the latest cfg on re-run."""
        from pipeline.setup import step_config_output
        import pipeline.setup as setup_module

        run_dir = self._run_setup(tmp_path)
        meta_path = run_dir / "run_metadata.json"

        original = setup_module.EXPERIMENT_ROOT
        setup_module.EXPERIMENT_ROOT = tmp_path
        try:
            cfg2 = _make_config(registry="quay.io/new-registry", namespace="new-ns")
            step_config_output(cfg2, run_dir, "docker")
        finally:
            setup_module.EXPERIMENT_ROOT = original

        meta2 = json.loads(meta_path.read_text())
        assert meta2["registry"] == "quay.io/new-registry"
        assert meta2["namespace"] == "new-ns"
        assert meta2["container_runtime"] == "docker"

    def test_first_run_creates_full_metadata(self, tmp_path):
        """First-run path (no existing file) still produces all setup-owned fields."""
        run_dir = self._run_setup(tmp_path)
        meta = json.loads((run_dir / "run_metadata.json").read_text())
        assert meta["version"] == 1
        assert meta["namespace"] == "test-ns"
        assert meta["registry"] == "quay.io/test"
        assert meta["component_image"] == "quay.io/test/llm-d-inference-scheduler:test-run"
        assert meta["stages"]["setup"]["status"] == "completed"
        assert meta["stages"]["prepare"] == {"status": "pending"}
        assert meta["stages"]["deploy"] == {"status": "pending"}
        assert meta["stages"]["results"] == {"status": "pending"}


class TestResolveStorageClass:
    """_resolve_storage_class: flag overrides; otherwise empty (cluster default)."""

    def _args(self, storage_class):
        from argparse import Namespace
        return Namespace(storage_class=storage_class)

    def test_default_returns_empty_so_cluster_default_applies(self):
        """No flag → empty string → PVC writer omits storageClassName entirely."""
        from pipeline.setup import _resolve_storage_class
        assert _resolve_storage_class(self._args(None)) == ""

    def test_flag_overrides(self):
        """Flag value is returned verbatim."""
        from pipeline.setup import _resolve_storage_class
        assert _resolve_storage_class(self._args("ibm-spectrum-scale-fileset")) == "ibm-spectrum-scale-fileset"

    def test_empty_flag_treated_as_unset(self):
        """`--storage-class ""` falls through to empty (cluster default)."""
        from pipeline.setup import _resolve_storage_class
        assert _resolve_storage_class(self._args("")) == ""


class TestStepRbacApply:
    """step_rbac applies five YAMLs with required/best-effort semantics.

    Tests stub out the five YAML paths under tmp_path so they don't depend
    on the tektonc-data-collection submodule being checked out — CI runs
    with `submodules: false`.
    """

    APPLY_PREFIX = ["kubectl", "apply", "-f", "-"]
    _STUB_YAML = (
        "apiVersion: v1\n"
        "kind: ServiceAccount\n"
        "metadata:\n"
        "  name: stub\n"
        "  namespace: $NAMESPACE\n"
    )
    _STUB_RELPATHS = [
        "tekton/roles-ns.yaml",
        "tekton/roles-cluster.yaml",
        "tekton/rbac/sim2real-runner.yaml",
        "pipeline/rbac/sim2real-runner-ns.yaml",
        "pipeline/rbac/sim2real-runner-cluster.yaml",
    ]

    def _stage(self, tmp_path, monkeypatch, *, omit=()):
        """Create stub YAMLs (omitting any in `omit`) and patch path constants."""
        import pipeline.setup as setup_mod
        for relpath in self._STUB_RELPATHS:
            if relpath in omit:
                continue
            target = tmp_path / relpath
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(self._STUB_YAML)
        monkeypatch.setattr(setup_mod, "TEKTONC_DIR", tmp_path)
        monkeypatch.setattr(setup_mod, "REPO_ROOT", tmp_path)

    def _apply_calls(self, mock_run):
        return [c for c in mock_run.call_args_list
                if c.args and list(c.args[0][:4]) == self.APPLY_PREFIX]

    def _index_aware_side_effect(self, mock_run, fail_indices, fail_stderr):
        """Build a side_effect that returns Forbidden on the given apply indices."""
        def side_effect(cmd, *args, **kwargs):
            r = type("R", (), {})()
            if list(cmd[:4]) == self.APPLY_PREFIX:
                idx = len([c for c in mock_run.call_args_list
                           if c.args and list(c.args[0][:4]) == self.APPLY_PREFIX]) - 1
                if idx in fail_indices:
                    r.returncode = 1; r.stdout = ""; r.stderr = fail_stderr
                else:
                    r.returncode = 0; r.stdout = "ok\n"; r.stderr = ""
            else:
                r.returncode = 0; r.stdout = ""; r.stderr = ""
            return r
        return side_effect

    @patch("pipeline.setup.run")
    def test_all_succeed(self, mock_run, tmp_path, monkeypatch):
        """Five YAMLs apply cleanly — five kubectl apply calls, no exit."""
        from pipeline.setup import step_rbac
        self._stage(tmp_path, monkeypatch)
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "role.rbac.../helm-access configured\n"
        mock_run.return_value.stderr = ""
        step_rbac(_make_config())
        assert len(self._apply_calls(mock_run)) == 5

    @patch("pipeline.setup.run")
    def test_required_failure_aborts(self, mock_run, tmp_path, monkeypatch, capsys):
        """Required file with non-zero apply causes sys.exit, with file name in stderr."""
        import pytest
        from pipeline.setup import step_rbac
        self._stage(tmp_path, monkeypatch)
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = "Error from server: something broke\n"
        with pytest.raises(SystemExit) as exc:
            step_rbac(_make_config())
        assert exc.value.code == 1
        # Aborts on the FIRST required file (roles-ns.yaml) — only one apply call.
        assert len(self._apply_calls(mock_run)) == 1
        # The labeled err() should name the file so the operator knows what failed.
        captured = capsys.readouterr()
        assert "roles-ns.yaml" in captured.err

    @patch("pipeline.setup.run")
    def test_cluster_rbac_forbidden_skips_with_warn(self, mock_run, tmp_path, monkeypatch, capsys):
        """Best-effort file with Forbidden on clusterrole resource skips and continues."""
        from pipeline.setup import step_rbac
        self._stage(tmp_path, monkeypatch)
        cluster_forbidden = (
            'Error from server (Forbidden): error when creating "STDIN": '
            'clusterroles.rbac.authorization.k8s.io is forbidden: User "x" '
            'cannot create resource "clusterroles" at the cluster scope'
        )
        # Indices in the apply order: 0=ns, 1=cluster (best-effort), 2=runner, 3=runner-ns, 4=runner-cluster (best-effort)
        mock_run.side_effect = self._index_aware_side_effect(
            mock_run, fail_indices={1, 4}, fail_stderr=cluster_forbidden,
        )
        step_rbac(_make_config())
        # Both best-effort skips should have produced a warn line.
        captured = capsys.readouterr()
        assert captured.out.count("Skipped") + captured.err.count("Skipped") >= 2
        assert len(self._apply_calls(mock_run)) == 5

    @patch("pipeline.setup.run")
    def test_cluster_augment_non_rbac_forbidden_aborts(self, mock_run, tmp_path, monkeypatch):
        """Best-effort file with Forbidden NOT on a clusterrole resource still aborts."""
        import pytest
        from pipeline.setup import step_rbac
        self._stage(tmp_path, monkeypatch)
        # Forbidden from an SCC admission webhook — no clusterrole text in error.
        scc_forbidden = (
            'Error from server (Forbidden): admission webhook "scc.openshift.io" '
            'denied the request: pod "x" requires anyuid SCC'
        )
        mock_run.side_effect = self._index_aware_side_effect(
            mock_run, fail_indices={1}, fail_stderr=scc_forbidden,
        )
        with pytest.raises(SystemExit) as exc:
            step_rbac(_make_config())
        assert exc.value.code == 1
        # Aborts on the second file (cluster augment) — exactly two apply calls.
        assert len(self._apply_calls(mock_run)) == 2

    @patch("pipeline.setup.run")
    def test_skip_branch_emits_stderr(self, mock_run, tmp_path, monkeypatch, capsys):
        """Forbidden-skip branch prints kubectl's stderr before the warn."""
        from pipeline.setup import step_rbac
        self._stage(tmp_path, monkeypatch)
        cluster_forbidden = "Error from server (Forbidden): clusterroles cannot create"
        mock_run.side_effect = self._index_aware_side_effect(
            mock_run, fail_indices={1, 4}, fail_stderr=cluster_forbidden,
        )
        step_rbac(_make_config())
        captured = capsys.readouterr()
        # The actual kubectl Forbidden text should reach stderr — not be silently swallowed.
        assert "clusterroles cannot create" in captured.err

    @patch("pipeline.setup.run")
    def test_required_file_not_found_aborts(self, mock_run, tmp_path, monkeypatch):
        """Missing required YAML (e.g. submodule init failed) exits before apply."""
        import pytest
        import pipeline.setup as setup_mod
        # Stage everything EXCEPT the first required file.
        self._stage(tmp_path, monkeypatch, omit=("tekton/roles-ns.yaml",))
        with pytest.raises(SystemExit) as exc:
            setup_mod.step_rbac(_make_config())
        assert exc.value.code == 1
        # No kubectl apply should have been issued.
        assert len(self._apply_calls(mock_run)) == 0


class TestSimRealRunnerNsRoleContent:
    """Content assertions on pipeline/rbac/sim2real-runner-ns.yaml.

    The orchestrator SA needs every rule in this Role to be present in every
    slot namespace. step_rbac applies the file unmodified (just envsubst), so
    the rules here ARE the orchestrator's namespaced permission set. If a rule
    silently disappears, the orchestrator's pod-health polling 403s in slot
    namespaces — see issue #410 for the failure shape.
    """

    def _load_role_rules(self):
        import yaml
        from pipeline.setup import REPO_ROOT
        path = REPO_ROOT / "pipeline" / "rbac" / "sim2real-runner-ns.yaml"
        docs = list(yaml.safe_load_all(path.read_text()))
        role = next(d for d in docs if d.get("kind") == "Role")
        return role["rules"]

    def _rule_for(self, rules, api_group, resource):
        """Return the first rule covering (api_group, resource), or None."""
        for r in rules:
            if api_group in r.get("apiGroups", []) and resource in r.get("resources", []):
                return r
        return None

    def test_pipelineruns_rule_present(self):
        """Tekton PipelineRun CRUD — the orchestrator's primary verb set."""
        rule = self._rule_for(self._load_role_rules(), "tekton.dev", "pipelineruns")
        assert rule is not None
        assert set(rule["verbs"]) >= {"create", "get", "watch", "list", "delete", "patch"}

    def test_pvc_secret_get_rule_present(self):
        """Slot-readiness checks read PVC bind state and HF secret presence."""
        rules = self._load_role_rules()
        for resource in ("persistentvolumeclaims", "secrets"):
            rule = self._rule_for(rules, "", resource)
            assert rule is not None, f"missing rule for core/{resource}"
            assert "get" in rule["verbs"]

    def test_pods_rule_present(self):
        """Issue #410: pods get/list/watch is what _check_pod_health and
        _check_pending_pods exercise on every poll tick; without it, the
        orchestrator's health triage silently degrades to no-ops."""
        rule = self._rule_for(self._load_role_rules(), "", "pods")
        assert rule is not None, "pods rule missing — orchestrator pod health checks will 403"
        assert set(rule["verbs"]) >= {"get", "list", "watch"}

    def test_events_rule_present(self):
        """Issue #410: events get/list feeds get_events(), which provides the
        warning-event context for pod_pending classification (FailedScheduling,
        ImagePullBackOff, etc.)."""
        rule = self._rule_for(self._load_role_rules(), "", "events")
        assert rule is not None, "events rule missing — pending-pod triage loses event context"
        assert set(rule["verbs"]) >= {"get", "list"}

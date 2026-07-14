"""Tests for pipeline/cluster.py — provision orchestrator."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from pipeline import cluster as cluster_cmd
from pipeline.lib import cluster_ops, layout


def _completed(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class FakeRun:
    def __init__(self):
        self.calls: list[list[str]] = []
        self.inputs: list[str | None] = []
        self._responses: list[tuple[list[str], SimpleNamespace]] = []

    def set(self, prefix: list[str], result):
        self._responses.append((prefix, result))

    def __call__(self, cmd, *, check=True, capture=False, input=None):
        self.calls.append(list(cmd))
        self.inputs.append(input)
        for prefix, response in self._responses:
            if cmd[: len(prefix)] == prefix:
                if check and response.returncode != 0:
                    raise subprocess.CalledProcessError(
                        response.returncode, cmd, response.stdout, response.stderr,
                    )
                return response
        return _completed(returncode=0, stdout="", stderr="")


@pytest.fixture
def fake_run(monkeypatch):
    fr = FakeRun()
    monkeypatch.setattr(cluster_ops, "_run", fr)
    return fr


@pytest.fixture(autouse=True)
def _isolated_experiment_root(tmp_path, monkeypatch):
    layout._EXPERIMENT_ROOT = tmp_path
    yield
    layout._EXPERIMENT_ROOT = None


class TestParseNamespaces:
    def test_splits_csv(self):
        assert cluster_cmd._parse_namespaces("a,b,c") == ["a", "b", "c"]

    def test_strips_whitespace(self):
        assert cluster_cmd._parse_namespaces(" a , b ,c ") == ["a", "b", "c"]

    def test_rejects_empty_string(self):
        with pytest.raises(ValueError):
            cluster_cmd._parse_namespaces("")

    def test_rejects_only_whitespace_or_commas(self):
        with pytest.raises(ValueError):
            cluster_cmd._parse_namespaces(" , ,, ")


class TestBuildClusterConfig:
    def test_hardcoded_defaults(self):
        cfg = cluster_cmd._build_cluster_config_dict(
            "ocp-east",
            ["a", "b"],
            is_openshift=True,
            storage_class="",
            has_dockerhub=False,
        )
        assert cfg["cluster_id"] == "ocp-east"
        assert cfg["namespaces"] == ["a", "b"]
        assert cfg["is_openshift"] is True
        assert cfg["storage_class"] == ""
        assert cfg["secret_names"] == {
            "hf_token": "hf-secret",
            "registry_creds": "registry-creds",
            "github_token": "github-token",
            "dockerhub_creds": "",
        }
        assert cfg["workspaces"] == {
            "data-storage": {"persistentVolumeClaim": {"claimName": "data-pvc"}},
            "source":       {"persistentVolumeClaim": {"claimName": "source-pvc"}},
        }

    def test_dockerhub_secret_name_set_when_creds_present(self):
        cfg = cluster_cmd._build_cluster_config_dict(
            "ocp-east", ["a"], is_openshift=False, storage_class="", has_dockerhub=True,
        )
        assert cfg["secret_names"]["dockerhub_creds"] == "dockerhub-creds"

    def test_existing_created_at_preserved(self):
        cfg = cluster_cmd._build_cluster_config_dict(
            "ocp-east", ["a"], is_openshift=False, storage_class="",
            has_dockerhub=False, existing={"created_at": "2026-01-01T00:00:00Z"},
        )
        assert cfg["created_at"] == "2026-01-01T00:00:00Z"

    def test_no_created_at_when_no_existing(self):
        cfg = cluster_cmd._build_cluster_config_dict(
            "ocp-east", ["a"], is_openshift=False, storage_class="", has_dockerhub=False,
        )
        assert "created_at" not in cfg

    def test_pipeline_yaml_omitted_when_none(self):
        """No key written when --pipeline-yaml is not set — apply_cluster_resources
        falls back to the built-in default."""
        cfg = cluster_cmd._build_cluster_config_dict(
            "ocp-east", ["a"], is_openshift=False, storage_class="", has_dockerhub=False,
            pipeline_yaml=None,
        )
        assert "pipeline_yaml" not in cfg

    def test_pipeline_yaml_recorded_when_provided(self):
        """--pipeline-yaml PATH lands in cluster_config for apply_cluster_resources."""
        cfg = cluster_cmd._build_cluster_config_dict(
            "ocp-east", ["a"], is_openshift=False, storage_class="", has_dockerhub=False,
            pipeline_yaml="/custom/pipeline.yaml",
        )
        assert cfg["pipeline_yaml"] == "/custom/pipeline.yaml"


class _FakePrompts:
    """Records every prompt call; returns canned responses by label match."""
    def __init__(self, *, plain=None, secret=None):
        self._plain = plain or {}
        self._secret = secret or {}
        self.plain_calls: list[str] = []
        self.secret_calls: list[str] = []

    def plain(self, label, default=""):
        self.plain_calls.append(label)
        return self._plain.get(label, default)

    def secret(self, label):
        self.secret_calls.append(label)
        return self._secret.get(label, "")


def _ns(**kwargs):
    """argparse.Namespace stand-in for resolution tests."""
    base = dict(
        hf_token=None, github_token=None,
        registry_user=None, registry_token=None,
        dockerhub_user=None, dockerhub_token=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


class TestResolveSecretValues:
    def test_all_flags_provided_no_prompts(self):
        prompts = _FakePrompts()
        args = _ns(hf_token="hf", github_token="gh",
                   registry_user="ru", registry_token="rt",
                   dockerhub_user="du", dockerhub_token="dt")
        values, has_dh = cluster_cmd._resolve_secret_values(
            args, env={}, prompter=prompts.plain, secret_prompter=prompts.secret,
        )
        assert has_dh is True
        assert values["hf_token"] == "hf"
        assert values["github_token"] == "gh"
        assert values["registry_creds"] == {"server": "ghcr.io", "user": "ru", "token": "rt"}
        assert values["dockerhub_creds"] == {"server": "docker.io", "user": "du", "token": "dt"}
        assert prompts.plain_calls == []
        assert prompts.secret_calls == []

    def test_env_var_used_when_flag_absent(self):
        prompts = _FakePrompts()
        args = _ns()
        env = {
            "HF_TOKEN": "hf-from-env",
            "GITHUB_TOKEN": "gh-from-env",
            "REGISTRY_USER": "ru-env",
            "REGISTRY_TOKEN": "rt-env",
        }
        values, has_dh = cluster_cmd._resolve_secret_values(
            args, env=env, prompter=prompts.plain, secret_prompter=prompts.secret,
        )
        assert has_dh is False
        assert values["hf_token"] == "hf-from-env"
        assert values["github_token"] == "gh-from-env"
        assert values["registry_creds"]["user"] == "ru-env"
        assert values["registry_creds"]["token"] == "rt-env"
        assert "dockerhub_creds" not in values
        assert prompts.plain_calls == []
        assert prompts.secret_calls == []

    def test_prompt_fires_when_neither_flag_nor_env(self):
        prompts = _FakePrompts(
            plain={"Registry username": "ru-prompted"},
            secret={"HuggingFace token": "hf-prompted",
                    "Registry token": "rt-prompted"},
        )
        args = _ns()
        values, has_dh = cluster_cmd._resolve_secret_values(
            args, env={}, prompter=prompts.plain, secret_prompter=prompts.secret,
        )
        assert values["hf_token"] == "hf-prompted"
        assert values["registry_creds"]["user"] == "ru-prompted"
        assert values["registry_creds"]["token"] == "rt-prompted"
        assert "HuggingFace token" in prompts.secret_calls
        assert "Registry username" in prompts.plain_calls
        assert has_dh is False

    def test_github_token_does_not_prompt(self):
        """GitHub token is optional; we never block on it. No flag and no env
        means the value is absent (cluster_ops will reuse an existing Secret
        or surface a structured skip)."""
        prompts = _FakePrompts()
        args = _ns(hf_token="hf", registry_user="ru", registry_token="rt")
        values, _ = cluster_cmd._resolve_secret_values(
            args, env={}, prompter=prompts.plain, secret_prompter=prompts.secret,
        )
        assert "github_token" not in values
        assert prompts.plain_calls == []
        assert prompts.secret_calls == []

    def test_dockerhub_server_overridable_by_env(self):
        prompts = _FakePrompts()
        args = _ns(hf_token="hf", registry_user="ru", registry_token="rt",
                   dockerhub_user="du", dockerhub_token="dt")
        env = {"DOCKERHUB_SERVER": "docker.acme.io"}
        values, _ = cluster_cmd._resolve_secret_values(
            args, env=env, prompter=prompts.plain, secret_prompter=prompts.secret,
        )
        assert values["dockerhub_creds"]["server"] == "docker.acme.io"

    def test_registry_server_overridable_by_env(self):
        prompts = _FakePrompts()
        args = _ns(hf_token="hf", registry_user="ru", registry_token="rt")
        env = {"REGISTRY_SERVER": "quay.io"}
        values, _ = cluster_cmd._resolve_secret_values(
            args, env=env, prompter=prompts.plain, secret_prompter=prompts.secret,
        )
        assert values["registry_creds"]["server"] == "quay.io"

    def test_partial_dockerhub_creds_skip(self):
        """User without token (or vice versa) does NOT register dockerhub_creds —
        we only emit a fully-formed dict when both are present."""
        prompts = _FakePrompts()
        args = _ns(hf_token="hf", registry_user="ru", registry_token="rt",
                   dockerhub_user="du")  # token missing
        values, has_dh = cluster_cmd._resolve_secret_values(
            args, env={}, prompter=prompts.plain, secret_prompter=prompts.secret,
        )
        assert has_dh is False
        assert "dockerhub_creds" not in values


class TestParser:
    def test_provision_subcommand_accepts_required_positional_and_namespaces(self):
        parser = cluster_cmd.build_parser()
        args = parser.parse_args(["provision", "ocp-east", "--namespaces", "a,b"])
        assert args.command == "provision"
        assert args.cluster_id == "ocp-east"
        assert args.namespaces == "a,b"

    def test_parser_rejects_unknown_top_level_subcommand(self, capsys):
        parser = cluster_cmd.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["bogus", "ocp-east", "--namespaces", "a"])

    def test_parser_rejects_unknown_flag(self, capsys):
        parser = cluster_cmd.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["provision", "ocp-east", "--namespaces", "a", "--no-cluster"])

    def test_pipeline_yaml_flag_accepted(self):
        """#442: --pipeline-yaml moved here from setup.py."""
        parser = cluster_cmd.build_parser()
        args = parser.parse_args([
            "provision", "ocp-east", "--namespaces", "a",
            "--pipeline-yaml", "/custom/pipeline.yaml",
        ])
        assert args.pipeline_yaml == "/custom/pipeline.yaml"

    def test_pipeline_yaml_defaults_to_none(self):
        parser = cluster_cmd.build_parser()
        args = parser.parse_args(["provision", "ocp-east", "--namespaces", "a"])
        assert args.pipeline_yaml is None


class TestProvisionOrchestration:
    """End-to-end orchestration over mocked kubectl/oc."""

    @pytest.fixture(autouse=True)
    def _freeze_experiment_root(self, monkeypatch):
        """Prevent cmd_provision from overriding the tmp_path set by the
        outer _isolated_experiment_root fixture.  The autouse fixture already
        points layout._EXPERIMENT_ROOT at tmp_path; we just stop
        set_experiment_root(None) from resetting it to cwd."""
        monkeypatch.setattr(layout, "set_experiment_root", lambda _arg: None)

    def _full_arg_setup(self, monkeypatch, fake_run, tmp_path):
        """Common harness: returns the env dict + a 'no-prompt' getpass."""
        monkeypatch.setattr(cluster_ops, "_which", lambda cmd: False)  # not OpenShift
        # No RBAC YAMLs / tekton YAMLs on disk so those steps either fail (rbac)
        # or skip (tekton). For green-path tests, monkeypatch Path methods.
        return None

    def test_happy_path_returns_zero(self, fake_run, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(cluster_ops, "_which", lambda cmd: False)  # not OpenShift
        # Pretend YAML files exist (but fall back to real exists for cluster_config.json
        # so read_cluster_config works before the first write).
        _real_exists = cluster_ops.Path.exists
        monkeypatch.setattr(cluster_ops.Path, "exists",
                            lambda self: _real_exists(self) if str(self).endswith("cluster_config.json") else True)
        monkeypatch.setattr(cluster_ops.Path, "read_text", lambda self: "# yaml\n")

        real_path = cluster_ops.Path
        def fake_glob(self, pattern):
            if pattern == "*.yaml":
                return [real_path("/fake/a.yaml"), real_path("/fake/b.yaml")]
            return []
        monkeypatch.setattr(cluster_ops.Path, "glob", fake_glob)

        # Namespace pre-checks: not present yet.
        fake_run.set(["kubectl", "get", "ns"], _completed(returncode=1))
        # PVC pre-checks: not present yet.
        fake_run.set(["kubectl", "get", "pvc"], _completed(returncode=1))

        rc = cluster_cmd.main([
            "provision", "ocp-east",
            "--namespaces", "ns-a,ns-b",
            "--hf-token", "hf-x",
            "--github-token", "gh-x",
            "--registry-user", "ru",
            "--registry-token", "rt",
        ])
        assert rc == 0

        # cluster_config.json was written with the expected shape.
        cfg = cluster_ops.read_cluster_config("ocp-east")
        assert cfg["cluster_id"] == "ocp-east"
        assert cfg["namespaces"] == ["ns-a", "ns-b"]
        assert cfg["is_openshift"] is False
        assert "created_at" in cfg
        assert cfg["secret_names"]["dockerhub_creds"] == ""  # no dockerhub flags

        # Per-namespace summary lines printed.
        out = capsys.readouterr().out
        assert "ns-a: ok" in out
        assert "ns-b: ok" in out

    def test_returns_one_when_any_namespace_failed(self, fake_run, monkeypatch, capsys):
        _real_exists = cluster_ops.Path.exists
        monkeypatch.setattr(cluster_ops.Path, "exists",
                            lambda self: _real_exists(self) if str(self).endswith("cluster_config.json") else True)
        monkeypatch.setattr(cluster_ops.Path, "read_text", lambda self: "# yaml\n")
        monkeypatch.setattr(cluster_ops.Path, "glob",
                            lambda self, p: ([cluster_ops.Path("/fake/a.yaml")] if p == "*.yaml" else []))

        # Force RBAC apply to fail on ns-b only.
        real_step_rbac = cluster_ops._step_rbac
        def selective_rbac(ns, cfg, sv):
            if ns == "ns-b":
                return ("failed", "synthetic kubectl forbidden")
            return real_step_rbac(ns, cfg, sv)
        monkeypatch.setattr(cluster_ops, "_step_rbac", selective_rbac)

        rc = cluster_cmd.main([
            "provision", "ocp-east",
            "--namespaces", "ns-a,ns-b",
            "--hf-token", "hf",
            "--registry-user", "ru", "--registry-token", "rt",
        ])
        assert rc == 1
        out = capsys.readouterr().out
        assert "ns-a: ok" in out
        assert "ns-b: diverged" in out
        assert "rbac" in out

    def test_idempotent_rerun_preserves_created_at(self, fake_run, monkeypatch, tmp_path):
        _real_exists = cluster_ops.Path.exists
        monkeypatch.setattr(cluster_ops.Path, "exists",
                            lambda self: _real_exists(self) if str(self).endswith("cluster_config.json") else True)
        monkeypatch.setattr(cluster_ops.Path, "read_text", lambda self: "# yaml\n")
        monkeypatch.setattr(cluster_ops.Path, "glob",
                            lambda self, p: ([cluster_ops.Path("/fake/a.yaml")] if p == "*.yaml" else []))
        fake_run.set(["kubectl", "get", "ns"], _completed(returncode=0))  # ns already present
        fake_run.set(["kubectl", "get", "pvc"], _completed(returncode=0))  # pvcs already present

        # First run.
        rc1 = cluster_cmd.main([
            "provision", "ocp-east", "--namespaces", "ns-a",
            "--hf-token", "hf", "--registry-user", "ru", "--registry-token", "rt",
        ])
        assert rc1 == 0
        first = cluster_ops.read_cluster_config("ocp-east")
        first_created = first["created_at"]

        # Second run — must preserve created_at byte-for-byte.
        rc2 = cluster_cmd.main([
            "provision", "ocp-east", "--namespaces", "ns-a",
            "--hf-token", "hf", "--registry-user", "ru", "--registry-token", "rt",
        ])
        assert rc2 == 0
        second = cluster_ops.read_cluster_config("ocp-east")
        assert second["created_at"] == first_created

    def test_empty_namespaces_exits_two(self, fake_run, capsys):
        rc = cluster_cmd.main([
            "provision", "ocp-east", "--namespaces", "  , ,",
            "--hf-token", "hf", "--registry-user", "ru", "--registry-token", "rt",
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "--namespaces" in err

    def test_pipeline_yaml_missing_exits_one(self, fake_run, monkeypatch, capsys):
        # Force apply_cluster_resources to raise FileNotFoundError.
        def boom(_):
            raise FileNotFoundError("Pipeline YAML not found at /nowhere")
        monkeypatch.setattr(cluster_ops, "apply_cluster_resources", boom)

        rc = cluster_cmd.main([
            "provision", "ocp-east", "--namespaces", "ns-a",
            "--hf-token", "hf", "--registry-user", "ru", "--registry-token", "rt",
        ])
        assert rc == 1
        err = capsys.readouterr().err
        assert "Pipeline YAML" in err

    def test_dockerhub_creds_recorded_when_provided(self, fake_run, monkeypatch):
        _real_exists = cluster_ops.Path.exists
        monkeypatch.setattr(cluster_ops.Path, "exists",
                            lambda self: _real_exists(self) if str(self).endswith("cluster_config.json") else True)
        monkeypatch.setattr(cluster_ops.Path, "read_text", lambda self: "# yaml\n")
        monkeypatch.setattr(cluster_ops.Path, "glob",
                            lambda self, p: ([cluster_ops.Path("/fake/a.yaml")] if p == "*.yaml" else []))
        fake_run.set(["kubectl", "get", "ns"], _completed(returncode=0))
        fake_run.set(["kubectl", "get", "pvc"], _completed(returncode=0))

        rc = cluster_cmd.main([
            "provision", "ocp-east", "--namespaces", "ns-a",
            "--hf-token", "hf", "--registry-user", "ru", "--registry-token", "rt",
            "--dockerhub-user", "du", "--dockerhub-token", "dt",
        ])
        assert rc == 0
        cfg = cluster_ops.read_cluster_config("ocp-east")
        assert cfg["secret_names"]["dockerhub_creds"] == "dockerhub-creds"


class TestFormatSummary:
    def test_ok_when_no_divergence(self):
        r = cluster_ops.ProvisionResult(namespace="ns-a", steps_ok=["namespace", "rbac"])
        assert cluster_cmd._format_summary_line(r) == "ns-a: ok"

    def test_skipped_only(self):
        r = cluster_ops.ProvisionResult(
            namespace="ns-a",
            steps_ok=["namespace"],
            steps_skipped=[("secrets", "no value provided for: hf_token(hf-secret)")],
        )
        line = cluster_cmd._format_summary_line(r)
        assert line.startswith("ns-a: diverged: ")
        assert "skipped=secrets" in line
        assert "no value provided" in line

    def test_failed_listed_before_skipped(self):
        r = cluster_ops.ProvisionResult(
            namespace="ns-a",
            steps_failed=[("rbac", "kubectl forbidden")],
            steps_skipped=[("secrets", "no value")],
        )
        line = cluster_cmd._format_summary_line(r)
        # failed= appears before skipped=
        assert line.index("failed=") < line.index("skipped=")


class TestInitAndSlotParser:
    """Argparse-level coverage for the new init / slot subcommands (issue #571)."""

    def test_init_requires_cluster_id_and_primary(self):
        parser = cluster_cmd.build_parser()
        args = parser.parse_args(["init", "ocp-east", "ns-p"])
        assert args.command == "init"
        assert args.cluster_id == "ocp-east"
        assert args.primary_namespace == "ns-p"

    def test_init_rejects_missing_primary(self):
        parser = cluster_cmd.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["init", "ocp-east"])

    def test_slot_add_shape(self):
        parser = cluster_cmd.build_parser()
        args = parser.parse_args(["slot", "add", "ocp-east", "ns-b"])
        assert args.command == "slot"
        assert args.slot_command == "add"
        assert args.cluster_id == "ocp-east"
        assert args.namespace == "ns-b"

    def test_slot_remove_shape(self):
        parser = cluster_cmd.build_parser()
        args = parser.parse_args(["slot", "remove", "ocp-east", "ns-b"])
        assert args.slot_command == "remove"
        assert args.namespace == "ns-b"

    def test_slot_list_shape(self):
        parser = cluster_cmd.build_parser()
        args = parser.parse_args(["slot", "list", "ocp-east"])
        assert args.slot_command == "list"
        assert args.cluster_id == "ocp-east"

    def test_slot_add_requires_slot_command(self):
        parser = cluster_cmd.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["slot"])

    def test_experiment_root_accepted_after_positionals(self):
        """--experiment-root works when placed AFTER the subcommand + positionals
        (the pre-#571 CLI shape). Registered on every subparser, not just the
        top-level parser."""
        parser = cluster_cmd.build_parser()
        # provision
        args = parser.parse_args([
            "provision", "ocp-east", "--namespaces", "ns-a",
            "--experiment-root", "/exp",
        ])
        assert args.experiment_root == "/exp"
        # init
        args = parser.parse_args([
            "init", "ocp-east", "ns-p", "--experiment-root", "/exp2",
        ])
        assert args.experiment_root == "/exp2"
        # slot add
        args = parser.parse_args([
            "slot", "add", "ocp-east", "ns-b", "--experiment-root", "/exp3",
        ])
        assert args.experiment_root == "/exp3"
        # slot remove
        args = parser.parse_args([
            "slot", "remove", "ocp-east", "ns-b", "--experiment-root", "/exp4",
        ])
        assert args.experiment_root == "/exp4"
        # slot list
        args = parser.parse_args([
            "slot", "list", "ocp-east", "--experiment-root", "/exp5",
        ])
        assert args.experiment_root == "/exp5"


def _install_fs_stubs(monkeypatch):
    """Common filesystem stubs used by cluster_ops during provision_namespace.

    Every RBAC / Tekton YAML lookup during ``provision_namespace`` reaches
    into the repo tree. Tests that exercise ``cmd_init`` / ``cmd_slot_add``
    need those lookups to succeed without depending on the actual on-disk
    files, so we stub ``Path.exists`` / ``Path.read_text`` / ``Path.glob``
    the same way ``TestProvisionOrchestration`` does.
    """
    _real_exists = cluster_ops.Path.exists
    monkeypatch.setattr(
        cluster_ops.Path, "exists",
        lambda self: _real_exists(self) if str(self).endswith("cluster_config.json") else True,
    )
    monkeypatch.setattr(cluster_ops.Path, "read_text", lambda self: "# yaml\n")
    monkeypatch.setattr(
        cluster_ops.Path, "glob",
        lambda self, p: ([cluster_ops.Path("/fake/a.yaml")] if p == "*.yaml" else []),
    )


class TestCmdInit:
    """cmd_init: bootstrap from scratch; refuses on existing cluster."""

    @pytest.fixture(autouse=True)
    def _freeze_experiment_root(self, monkeypatch):
        monkeypatch.setattr(layout, "set_experiment_root", lambda _arg: None)

    def test_init_writes_config_and_provisions_primary(
        self, fake_run, monkeypatch, capsys
    ):
        monkeypatch.setattr(cluster_ops, "_which", lambda cmd: False)
        _install_fs_stubs(monkeypatch)
        fake_run.set(["kubectl", "get", "ns"], _completed(returncode=1))
        fake_run.set(["kubectl", "get", "pvc"], _completed(returncode=1))

        rc = cluster_cmd.main([
            "init", "ocp-east", "ns-primary",
            "--hf-token", "hf",
            "--registry-user", "ru", "--registry-token", "rt",
        ])
        assert rc == 0
        cfg = cluster_ops.read_cluster_config("ocp-east")
        assert cfg["namespaces"] == ["ns-primary"]
        assert cfg["is_openshift"] is False
        assert "created_at" in cfg
        assert "ns-primary: ok" in capsys.readouterr().out

    def test_init_refuses_existing_cluster(self, fake_run, monkeypatch, capsys):
        # Seed an existing cluster_config.
        cluster_ops.write_cluster_config("ocp-east", {
            "cluster_id": "ocp-east", "namespaces": ["ns-primary"],
        })

        rc = cluster_cmd.main([
            "init", "ocp-east", "ns-primary",
            "--hf-token", "hf",
            "--registry-user", "ru", "--registry-token", "rt",
        ])
        assert rc == 1
        err = capsys.readouterr().err
        assert "already initialized" in err
        assert "cluster.py slot add" in err

    def test_init_publishes_after_write(self, fake_run, monkeypatch):
        """publish_slot_pool is called; the CM probe fires with the primary ns."""
        monkeypatch.setattr(cluster_ops, "_which", lambda cmd: False)
        _install_fs_stubs(monkeypatch)
        fake_run.set(["kubectl", "get", "ns"], _completed(returncode=1))
        fake_run.set(["kubectl", "get", "pvc"], _completed(returncode=1))
        # No run-inputs CM present.
        fake_run.set(
            ["kubectl", "get", "configmap", "sim2real-run-inputs"],
            _completed(returncode=0, stdout=""),
        )

        rc = cluster_cmd.main([
            "init", "ocp-east", "ns-primary",
            "--hf-token", "hf",
            "--registry-user", "ru", "--registry-token", "rt",
        ])
        assert rc == 0
        # The probe was made against ns-primary.
        probe_calls = [
            c for c in fake_run.calls
            if c[:3] == ["kubectl", "get", "configmap"]
            and c[3] == "sim2real-run-inputs"
            and "-n=ns-primary" in c
        ]
        assert probe_calls, f"expected CM probe against ns-primary; calls={fake_run.calls}"

    def test_init_skips_publish_when_primary_provision_fails(
        self, fake_run, monkeypatch, capsys
    ):
        """When provision_namespace's steps_failed is non-empty, cmd_init
        must NOT call publish_slot_pool — mirrors cmd_slot_add's guard so
        both commands treat divergent provisioning the same way. Prevents
        the 'dangling CM in primary namespace' edge case (called out in
        cmd_init's docstring) from advertising a broken primary."""
        monkeypatch.setattr(cluster_ops, "_which", lambda cmd: False)
        _install_fs_stubs(monkeypatch)

        # Force _step_rbac to fail on the primary namespace.
        def failing_rbac(ns, cfg, sv):
            return ("failed", "synthetic kubectl forbidden")
        monkeypatch.setattr(cluster_ops, "_step_rbac", failing_rbac)

        rc = cluster_cmd.main([
            "init", "ocp-east", "ns-primary",
            "--hf-token", "hf",
            "--registry-user", "ru", "--registry-token", "rt",
        ])
        assert rc == 1
        # No CM probe — publish_slot_pool was never called.
        assert not any(
            c[:4] == ["kubectl", "get", "configmap", "sim2real-run-inputs"]
            for c in fake_run.calls
        ), "publish_slot_pool must not fire when init's provisioning diverged"


class TestCmdSlotAdd:
    """cmd_slot_add: provisions + appends to pool + applies pipeline."""

    @pytest.fixture(autouse=True)
    def _freeze_experiment_root(self, monkeypatch):
        monkeypatch.setattr(layout, "set_experiment_root", lambda _arg: None)

    def _init_cluster(self, monkeypatch, fake_run):
        """Seed a cluster_config on disk to simulate a completed init."""
        cluster_ops.write_cluster_config("ocp-east", {
            "cluster_id": "ocp-east",
            "namespaces": ["ns-primary"],
            "is_openshift": False,
            "storage_class": "",
            "secret_names": dict(cluster_cmd._DEFAULT_SECRET_NAMES),
            "workspaces": dict(cluster_cmd._DEFAULT_WORKSPACES),
        })

    def test_slot_add_errors_when_cluster_uninitialized(self, fake_run, capsys):
        rc = cluster_cmd.main([
            "slot", "add", "ocp-east", "ns-b",
            "--hf-token", "hf",
            "--registry-user", "ru", "--registry-token", "rt",
        ])
        assert rc == 1
        assert "not initialized" in capsys.readouterr().err

    def test_slot_add_provisions_and_appends(
        self, fake_run, monkeypatch, capsys
    ):
        monkeypatch.setattr(cluster_ops, "_which", lambda cmd: False)
        _install_fs_stubs(monkeypatch)
        self._init_cluster(monkeypatch, fake_run)
        fake_run.set(["kubectl", "get", "ns"], _completed(returncode=1))
        fake_run.set(["kubectl", "get", "pvc"], _completed(returncode=1))

        rc = cluster_cmd.main([
            "slot", "add", "ocp-east", "ns-b",
            "--hf-token", "hf",
            "--registry-user", "ru", "--registry-token", "rt",
        ])
        assert rc == 0
        cfg = cluster_ops.read_cluster_config("ocp-east")
        assert cfg["namespaces"] == ["ns-primary", "ns-b"]
        # Pipeline apply targeted just ns-b (not every namespace in the pool).
        pipeline_applies = [
            c for c in fake_run.calls
            if c[:2] == ["kubectl", "apply"] and any(
                "pipeline.yaml" in tok for tok in c
            )
        ]
        ns_flags = [tok for call in pipeline_applies for tok in call if tok.startswith("-n=")]
        assert "-n=ns-b" in ns_flags
        # Nothing applied to ns-primary — this was a per-namespace apply.
        assert "-n=ns-primary" not in ns_flags
        # publish_slot_pool fired: probe against primary namespace.
        assert any(
            c[:4] == ["kubectl", "get", "configmap", "sim2real-run-inputs"]
            and "-n=ns-primary" in c
            for c in fake_run.calls
        ), "expected publish_slot_pool to probe the run-inputs CM against ns-primary"

    def test_slot_add_skips_append_and_publish_when_provision_fails(
        self, fake_run, monkeypatch, capsys
    ):
        """When provision_namespace's steps_failed is non-empty, cmd_slot_add
        must NOT (a) append the namespace to the pool on disk and (b) fire
        publish_slot_pool — advertising a not-usable slot is the whole failure
        mode this guard exists to prevent (cluster.py:460-464)."""
        monkeypatch.setattr(cluster_ops, "_which", lambda cmd: False)
        _install_fs_stubs(monkeypatch)
        self._init_cluster(monkeypatch, fake_run)

        # Force _step_rbac to fail on ns-b (mirrors the pattern in
        # TestProvisionOrchestration.test_returns_one_when_any_namespace_failed).
        def failing_rbac(ns, cfg, sv):
            return ("failed", "synthetic kubectl forbidden")
        monkeypatch.setattr(cluster_ops, "_step_rbac", failing_rbac)

        rc = cluster_cmd.main([
            "slot", "add", "ocp-east", "ns-b",
            "--hf-token", "hf",
            "--registry-user", "ru", "--registry-token", "rt",
        ])
        assert rc == 1
        # Pool contents unchanged — ns-b never made it in.
        cfg = cluster_ops.read_cluster_config("ocp-east")
        assert cfg["namespaces"] == ["ns-primary"]
        # No CM probe — publish_slot_pool was never called.
        assert not any(
            c[:4] == ["kubectl", "get", "configmap", "sim2real-run-inputs"]
            for c in fake_run.calls
        ), "publish_slot_pool must not fire when provisioning diverged"
        # No pipeline apply either — cmd_slot_add's steps_failed early return
        # is above the pipeline-apply call site.
        assert not any(
            c[:2] == ["kubectl", "apply"] and any("pipeline.yaml" in tok for tok in c)
            for c in fake_run.calls
        )

    def test_slot_add_idempotent_for_existing_namespace(
        self, fake_run, monkeypatch
    ):
        monkeypatch.setattr(cluster_ops, "_which", lambda cmd: False)
        _install_fs_stubs(monkeypatch)
        cluster_ops.write_cluster_config("ocp-east", {
            "cluster_id": "ocp-east",
            "namespaces": ["ns-primary", "ns-b"],
            "is_openshift": False,
            "storage_class": "",
            "secret_names": dict(cluster_cmd._DEFAULT_SECRET_NAMES),
            "workspaces": dict(cluster_cmd._DEFAULT_WORKSPACES),
        })
        # ns and pvc already present — provision_namespace should no-op each step.
        fake_run.set(["kubectl", "get", "ns"], _completed(returncode=0))
        fake_run.set(["kubectl", "get", "pvc"], _completed(returncode=0))

        rc = cluster_cmd.main([
            "slot", "add", "ocp-east", "ns-b",
            "--hf-token", "hf",
            "--registry-user", "ru", "--registry-token", "rt",
        ])
        assert rc == 0
        cfg = cluster_ops.read_cluster_config("ocp-east")
        # Namespaces list didn't gain a duplicate.
        assert cfg["namespaces"] == ["ns-primary", "ns-b"]
        # publish_slot_pool still fires on the no-append re-add path.
        # A future refactor that moves publish inside the `namespace
        # not in current:` block would silently stop propagating live
        # state on idempotent re-adds; this assertion locks against
        # that regression.
        assert any(
            c[:4] == ["kubectl", "get", "configmap", "sim2real-run-inputs"]
            and "-n=ns-primary" in c
            for c in fake_run.calls
        ), "expected publish_slot_pool to fire even when namespace is already in pool"

    def test_slot_add_pipeline_apply_missing_yaml_leaves_pool_intact(
        self, fake_run, monkeypatch, capsys
    ):
        """FileNotFoundError from apply_pipeline_to_namespace must
        short-circuit with rc=1 AND leave namespaces list unchanged
        AND not publish. This is the intermediate state the reorder
        (issue #571) was specifically designed to protect."""
        monkeypatch.setattr(cluster_ops, "_which", lambda cmd: False)
        _install_fs_stubs(monkeypatch)
        self._init_cluster(monkeypatch, fake_run)
        fake_run.set(["kubectl", "get", "ns"], _completed(returncode=1))
        fake_run.set(["kubectl", "get", "pvc"], _completed(returncode=1))

        # Force apply_pipeline_to_namespace to raise as if the Pipeline
        # YAML was missing.
        def missing_yaml(_cluster_id, _namespace):
            raise FileNotFoundError("Pipeline YAML not found at /nowhere")
        monkeypatch.setattr(
            cluster_ops, "apply_pipeline_to_namespace", missing_yaml,
        )

        rc = cluster_cmd.main([
            "slot", "add", "ocp-east", "ns-b",
            "--hf-token", "hf",
            "--registry-user", "ru", "--registry-token", "rt",
        ])
        assert rc == 1
        err = capsys.readouterr().err
        assert "Pipeline YAML" in err
        # Pool contents unchanged — reorder guarantee holds.
        cfg = cluster_ops.read_cluster_config("ocp-east")
        assert cfg["namespaces"] == ["ns-primary"]
        # No publish fired.
        assert not any(
            c[:4] == ["kubectl", "get", "configmap", "sim2real-run-inputs"]
            for c in fake_run.calls
        ), "publish_slot_pool must not fire when pipeline apply failed"

    def test_slot_add_pipeline_apply_kubectl_failure_leaves_pool_intact(
        self, fake_run, monkeypatch, capsys
    ):
        """CalledProcessError from apply_pipeline_to_namespace (kubectl
        apply non-zero exit) same story: rc=1, pool unchanged, no
        publish."""
        monkeypatch.setattr(cluster_ops, "_which", lambda cmd: False)
        _install_fs_stubs(monkeypatch)
        self._init_cluster(monkeypatch, fake_run)
        fake_run.set(["kubectl", "get", "ns"], _completed(returncode=1))
        fake_run.set(["kubectl", "get", "pvc"], _completed(returncode=1))

        def kubectl_boom(_cluster_id, _namespace):
            raise subprocess.CalledProcessError(
                returncode=1, cmd=["kubectl", "apply"],
                output="", stderr="apply forbidden",
            )
        monkeypatch.setattr(
            cluster_ops, "apply_pipeline_to_namespace", kubectl_boom,
        )

        rc = cluster_cmd.main([
            "slot", "add", "ocp-east", "ns-b",
            "--hf-token", "hf",
            "--registry-user", "ru", "--registry-token", "rt",
        ])
        assert rc == 1
        err = capsys.readouterr().err
        assert "pipeline apply failed for ns-b" in err
        assert "apply forbidden" in err
        cfg = cluster_ops.read_cluster_config("ocp-east")
        assert cfg["namespaces"] == ["ns-primary"]
        assert not any(
            c[:4] == ["kubectl", "get", "configmap", "sim2real-run-inputs"]
            for c in fake_run.calls
        ), "publish_slot_pool must not fire when pipeline apply failed"


class TestCmdSlotRemove:
    """cmd_slot_remove: drain-only; refuses primary; no cluster-side calls."""

    @pytest.fixture(autouse=True)
    def _freeze_experiment_root(self, monkeypatch):
        monkeypatch.setattr(layout, "set_experiment_root", lambda _arg: None)

    def _init_two_slot_cluster(self):
        cluster_ops.write_cluster_config("ocp-east", {
            "cluster_id": "ocp-east",
            "namespaces": ["ns-primary", "ns-b"],
            "is_openshift": False,
            "storage_class": "",
            "secret_names": dict(cluster_cmd._DEFAULT_SECRET_NAMES),
            "workspaces": dict(cluster_cmd._DEFAULT_WORKSPACES),
        })

    def test_removes_non_primary(self, fake_run, capsys):
        self._init_two_slot_cluster()
        rc = cluster_cmd.main(["slot", "remove", "ocp-east", "ns-b"])
        assert rc == 0
        cfg = cluster_ops.read_cluster_config("ocp-east")
        assert cfg["namespaces"] == ["ns-primary"]
        out = capsys.readouterr().out
        assert "ns-b: removed from pool" in out
        # No cluster-side teardown — no `kubectl delete` calls anywhere.
        assert not any(c[:2] == ["kubectl", "delete"] for c in fake_run.calls)
        # publish_slot_pool fired: probe against primary namespace.
        assert any(
            c[:4] == ["kubectl", "get", "configmap", "sim2real-run-inputs"]
            and "-n=ns-primary" in c
            for c in fake_run.calls
        ), "expected publish_slot_pool to probe the run-inputs CM against ns-primary"

    def test_refuses_primary_removal(self, fake_run, capsys):
        self._init_two_slot_cluster()
        rc = cluster_cmd.main(["slot", "remove", "ocp-east", "ns-primary"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "primary namespace" in err
        cfg = cluster_ops.read_cluster_config("ocp-east")
        assert cfg["namespaces"] == ["ns-primary", "ns-b"]  # unchanged

    def test_errors_on_missing_namespace(self, fake_run, capsys):
        self._init_two_slot_cluster()
        rc = cluster_cmd.main(["slot", "remove", "ocp-east", "ns-unknown"])
        assert rc == 1
        assert "not in pool" in capsys.readouterr().err

    def test_errors_on_uninitialized_cluster(self, fake_run, capsys):
        rc = cluster_cmd.main(["slot", "remove", "ocp-east", "ns-b"])
        assert rc == 1
        assert "not initialized" in capsys.readouterr().err


class TestCmdSlotList:
    """cmd_slot_list: read-only pool report with provisioned probe."""

    @pytest.fixture(autouse=True)
    def _freeze_experiment_root(self, monkeypatch):
        monkeypatch.setattr(layout, "set_experiment_root", lambda _arg: None)

    def test_prints_pool_with_primary_marker(self, fake_run, capsys):
        cluster_ops.write_cluster_config("ocp-east", {
            "cluster_id": "ocp-east",
            "namespaces": ["ns-primary", "ns-b"],
            "is_openshift": False,
            "storage_class": "",
            "secret_names": dict(cluster_cmd._DEFAULT_SECRET_NAMES),
            "workspaces": dict(cluster_cmd._DEFAULT_WORKSPACES),
        })
        # SA probe: primary is provisioned; ns-b is not.
        fake_run.set(
            ["kubectl", "get", "serviceaccount", "sim2real-runner", "-n=ns-primary"],
            _completed(returncode=0),
        )
        fake_run.set(
            ["kubectl", "get", "serviceaccount", "sim2real-runner", "-n=ns-b"],
            _completed(returncode=1),
        )

        rc = cluster_cmd.main(["slot", "list", "ocp-east"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "ns-primary (primary): provisioned" in out
        assert "ns-b: not provisioned" in out

    def test_errors_on_uninitialized_cluster(self, fake_run, capsys):
        rc = cluster_cmd.main(["slot", "list", "ocp-east"])
        assert rc == 1
        assert "not initialized" in capsys.readouterr().err


class TestProvisionSugar:
    """The pre-#571 provision command remains a sugar wrapper (issue #571)."""

    @pytest.fixture(autouse=True)
    def _freeze_experiment_root(self, monkeypatch):
        monkeypatch.setattr(layout, "set_experiment_root", lambda _arg: None)

    def test_provision_on_existing_cluster_delegates_to_slot_add(
        self, fake_run, monkeypatch, capsys
    ):
        """When cluster is already initialized, provision --namespaces N adds
        each N as a slot rather than trying to re-init."""
        monkeypatch.setattr(cluster_ops, "_which", lambda cmd: False)
        _install_fs_stubs(monkeypatch)
        # Seed initialized cluster.
        cluster_ops.write_cluster_config("ocp-east", {
            "cluster_id": "ocp-east",
            "namespaces": ["ns-primary"],
            "is_openshift": False,
            "storage_class": "",
            "secret_names": dict(cluster_cmd._DEFAULT_SECRET_NAMES),
            "workspaces": dict(cluster_cmd._DEFAULT_WORKSPACES),
        })
        fake_run.set(["kubectl", "get", "ns"], _completed(returncode=1))
        fake_run.set(["kubectl", "get", "pvc"], _completed(returncode=1))

        rc = cluster_cmd.main([
            "provision", "ocp-east",
            "--namespaces", "ns-b,ns-c",
            "--hf-token", "hf",
            "--registry-user", "ru", "--registry-token", "rt",
        ])
        assert rc == 0
        cfg = cluster_ops.read_cluster_config("ocp-east")
        assert cfg["namespaces"] == ["ns-primary", "ns-b", "ns-c"]

    def test_provision_warns_on_cluster_wide_flags_for_existing_cluster(
        self, fake_run, monkeypatch, capsys
    ):
        monkeypatch.setattr(cluster_ops, "_which", lambda cmd: False)
        _install_fs_stubs(monkeypatch)
        cluster_ops.write_cluster_config("ocp-east", {
            "cluster_id": "ocp-east",
            "namespaces": ["ns-primary"],
            "is_openshift": False,
            "storage_class": "",
            "secret_names": dict(cluster_cmd._DEFAULT_SECRET_NAMES),
            "workspaces": dict(cluster_cmd._DEFAULT_WORKSPACES),
        })
        fake_run.set(["kubectl", "get", "ns"], _completed(returncode=1))
        fake_run.set(["kubectl", "get", "pvc"], _completed(returncode=1))

        rc = cluster_cmd.main([
            "provision", "ocp-east",
            "--namespaces", "ns-b",
            "--storage-class", "gp3",
            "--hf-token", "hf",
            "--registry-user", "ru", "--registry-token", "rt",
        ])
        assert rc == 0
        err = capsys.readouterr().err
        assert "already initialized" in err
        assert "storage-class" in err


class TestPublishSlotPool:
    """publish_slot_pool: patch when CM exists, skip when absent (issue #571)."""

    @pytest.fixture(autouse=True)
    def _freeze_experiment_root(self, monkeypatch):
        monkeypatch.setattr(layout, "set_experiment_root", lambda _arg: None)

    def test_patches_cm_when_present(self, fake_run):
        import json as _json

        cluster_ops.write_cluster_config("ocp-east", {
            "cluster_id": "ocp-east",
            "namespaces": ["ns-primary", "ns-b"],
        })
        # Probe returns non-empty stdout => CM exists.
        fake_run.set(
            ["kubectl", "get", "configmap", "sim2real-run-inputs"],
            _completed(returncode=0, stdout="configmap/sim2real-run-inputs\n"),
        )

        cluster_ops.publish_slot_pool("ocp-east")

        # The probe uses --ignore-not-found so an absent CM gives rc=0 with
        # empty stdout instead of rc=1. This flag is what makes the
        # "warn on rc != 0 / info on empty stdout" split at cluster_ops.py
        # do the right thing on the CM-absent branch.
        probe_calls = [
            c for c in fake_run.calls
            if c[:4] == ["kubectl", "get", "configmap", "sim2real-run-inputs"]
        ]
        assert len(probe_calls) == 1
        assert "--ignore-not-found" in probe_calls[0]

        patch_calls = [c for c in fake_run.calls if c[:3] == ["kubectl", "patch", "configmap"]]
        assert len(patch_calls) == 1
        patch = patch_calls[0]
        assert "sim2real-run-inputs" in patch
        assert "-n=ns-primary" in patch
        assert "--type=merge" in patch
        # -p payload comes right after --type=merge; verify JSON structure
        # (not just substring match). The payload is a two-level JSON string:
        # the outer {"data": {"cluster_config--<id>": <inner>}} is what
        # kubectl merges, and <inner> is itself a JSON string that
        # `_configmap_items` will project into the Pod's volume mount.
        p_idx = patch.index("-p")
        payload = patch[p_idx + 1]
        payload_obj = _json.loads(payload)
        assert list(payload_obj.keys()) == ["data"]
        assert list(payload_obj["data"].keys()) == ["cluster_config--ocp-east"]
        inner_config = _json.loads(payload_obj["data"]["cluster_config--ocp-east"])
        assert inner_config["namespaces"] == ["ns-primary", "ns-b"]

    def test_skips_when_cm_absent(self, fake_run):
        cluster_ops.write_cluster_config("ocp-east", {
            "cluster_id": "ocp-east",
            "namespaces": ["ns-primary"],
        })
        # --ignore-not-found: rc=0 with empty stdout => absent.
        fake_run.set(
            ["kubectl", "get", "configmap", "sim2real-run-inputs"],
            _completed(returncode=0, stdout=""),
        )

        cluster_ops.publish_slot_pool("ocp-east")

        # No patch call at all.
        assert not any(
            c[:3] == ["kubectl", "patch", "configmap"] for c in fake_run.calls
        )

    def test_skips_when_no_namespaces(self, fake_run):
        cluster_ops.write_cluster_config("ocp-east", {
            "cluster_id": "ocp-east", "namespaces": [],
        })
        cluster_ops.publish_slot_pool("ocp-east")
        # No cluster-side calls at all.
        assert not any(c[:1] == ["kubectl"] for c in fake_run.calls)

    def test_warns_on_probe_failure_no_patch(self, fake_run, capsys):
        """Non-zero probe (cluster unreachable / RBAC denial) surfaces a warn
        and skips the patch, not the misleading 'no CM found' info line."""
        cluster_ops.write_cluster_config("ocp-east", {
            "cluster_id": "ocp-east", "namespaces": ["ns-primary"],
        })
        fake_run.set(
            ["kubectl", "get", "configmap", "sim2real-run-inputs"],
            _completed(returncode=1, stderr="Error from server (Forbidden)"),
        )

        cluster_ops.publish_slot_pool("ocp-east")

        # No patch call — the probe failure short-circuited.
        assert not any(
            c[:3] == ["kubectl", "patch", "configmap"] for c in fake_run.calls
        )
        # Operator-visible warning surfaced (not the info-level CM-absent line).
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "[WARN]" in combined or "probe" in combined.lower()
        assert "Forbidden" in combined

    def test_warns_on_patch_failure_no_raise(self, fake_run, capsys):
        """Non-zero patch (TOCTOU CM deletion, mid-command RBAC change,
        immutable CM) surfaces a warn and returns cleanly — must NOT
        raise. A regression that reverts check=True on the patch call
        would fail this test: CalledProcessError would escape
        publish_slot_pool as an uncaught traceback AFTER the on-disk
        mutation has already committed, which is the exact misleading
        failure mode this branch (cluster_ops.py:281-296) exists to
        prevent."""
        cluster_ops.write_cluster_config("ocp-east", {
            "cluster_id": "ocp-east", "namespaces": ["ns-primary"],
        })
        # Probe succeeds — CM present.
        fake_run.set(
            ["kubectl", "get", "configmap", "sim2real-run-inputs"],
            _completed(returncode=0, stdout="configmap/sim2real-run-inputs\n"),
        )
        # Patch fails — e.g. CM was deleted between probe and patch.
        fake_run.set(
            ["kubectl", "patch", "configmap"],
            _completed(returncode=1, stderr='configmaps "sim2real-run-inputs" not found'),
        )

        # Must NOT raise (regression check on check=False).
        cluster_ops.publish_slot_pool("ocp-east")

        # Patch was attempted (probe branch didn't short-circuit).
        assert any(
            c[:3] == ["kubectl", "patch", "configmap"] for c in fake_run.calls
        )
        # Operator-visible warning surfaced with kubectl stderr.
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "[WARN]" in combined or "patch" in combined.lower()
        assert "not found" in combined


class TestApplyPipelineToNamespace:
    """apply_pipeline_to_namespace: kubectl apply targeting one ns (issue #571)."""

    @pytest.fixture(autouse=True)
    def _freeze_experiment_root(self, monkeypatch):
        monkeypatch.setattr(layout, "set_experiment_root", lambda _arg: None)

    def test_applies_to_named_namespace_only(self, fake_run, monkeypatch):
        cluster_ops.write_cluster_config("ocp-east", {
            "cluster_id": "ocp-east",
            "namespaces": ["ns-primary", "ns-b"],
        })
        # Make Path.exists true for the pipeline.yaml default.
        monkeypatch.setattr(cluster_ops.Path, "exists", lambda self: True)

        cluster_ops.apply_pipeline_to_namespace("ocp-east", "ns-b")

        applies = [c for c in fake_run.calls if c[:2] == ["kubectl", "apply"]]
        assert len(applies) == 1
        assert "-n=ns-b" in applies[0]
        assert "-n=ns-primary" not in applies[0]


class TestScriptImportFromNonRepoCwd:
    """Regression for #439.

    cluster.py must be runnable as a script from any cwd — the common
    operator pattern is `python /path/to/sim2real/pipeline/cluster.py …`
    invoked from the experiment repo. Before the sys.path guard was
    added, this failed with `ModuleNotFoundError: No module named
    'pipeline'` because Python's script-mode auto-path adds only the
    script's own directory (pipeline/) to sys.path, not the repo root.

    This test bypasses pytest's automatic sys.path setup by spawning a
    fresh interpreter with cwd outside the repo tree.
    """

    def test_provision_help_runs_from_tmp_cwd(self, tmp_path):
        import sys as _sys
        from pathlib import Path

        cluster_py = Path(__file__).resolve().parents[2] / "pipeline" / "cluster.py"
        assert cluster_py.exists(), f"cluster.py not found at {cluster_py}"

        # tmp_path is outside the repo tree, so any pipeline.* import from
        # cluster.py can only succeed if the module's own sys.path guard
        # runs first.
        result = subprocess.run(
            [_sys.executable, str(cluster_py), "provision", "--help"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"cluster.py provision --help failed from {tmp_path}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        # Sanity: argparse actually rendered its help text.
        assert "--namespaces" in result.stdout

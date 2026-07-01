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

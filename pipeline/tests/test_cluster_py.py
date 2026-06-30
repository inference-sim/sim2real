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

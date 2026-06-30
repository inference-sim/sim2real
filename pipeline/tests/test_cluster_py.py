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

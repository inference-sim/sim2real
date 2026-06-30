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

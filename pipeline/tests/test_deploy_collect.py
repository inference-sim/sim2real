"""Tests for deploy.py _cmd_collect phase selection logic."""

from pathlib import Path
from unittest.mock import patch

import pytest


def test_collect_default_phases(tmp_path):
    """Without --package, collect passes both baseline and treatment."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)

    class Args:
        package = None
        skip_logs = False

    collected_phases = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None):
        collected_phases.extend(phases)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), {"workloads": []}, run_dir, {"namespace": "ns-0"})

    assert collected_phases == ["baseline", "treatment"]


def test_collect_single_package(tmp_path):
    """With --package treatment, collect passes only treatment."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)

    class Args:
        package = ["treatment"]
        skip_logs = False

    collected_phases = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None):
        collected_phases.extend(phases)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), {"workloads": []}, run_dir, {"namespace": "ns-0"})

    assert collected_phases == ["treatment"]


def test_collect_experiment_expands(tmp_path):
    """With --package experiment, collect expands to baseline and treatment."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)

    class Args:
        package = ["experiment"]
        skip_logs = False

    collected_phases = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None):
        collected_phases.extend(phases)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), {"workloads": []}, run_dir, {"namespace": "ns-0"})

    assert collected_phases == ["baseline", "treatment"]


def test_collect_unknown_package_exits(tmp_path):
    """With --package foo, collect exits with error."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)

    class Args:
        package = ["foo"]
        skip_logs = False

    with pytest.raises(SystemExit):
        deploy._cmd_collect(Args(), {"workloads": []}, run_dir, {"namespace": "ns-0"})

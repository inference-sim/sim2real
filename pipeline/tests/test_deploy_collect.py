"""Tests for deploy.py _cmd_collect phase selection logic."""

import json
from unittest.mock import patch

import pytest


def _write_progress(run_dir, entries):
    """Helper to write a progress.json file in run_dir."""
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "progress.json").write_text(json.dumps(entries))


def test_collect_with_progress_default(tmp_path):
    """Without --package, collect uses all unique packages from progress.json."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    _write_progress(run_dir, {
        "wl-smoke-baseline": {"workload": "wl-smoke", "package": "baseline", "status": "done"},
        "wl-smoke-treatment": {"workload": "wl-smoke", "package": "treatment", "status": "done"},
        "wl-load-baseline": {"workload": "wl-load", "package": "baseline", "status": "pending"},
    })

    class Args:
        package = None
        skip_logs = False

    collected_phases = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None):
        collected_phases.extend(phases)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespace": "ns-0"})

    assert collected_phases == ["baseline", "treatment"]


def test_collect_fallback_no_progress(tmp_path):
    """Without --package and no progress.json, falls back to DATA_PHASES with warning."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    # No progress.json written

    class Args:
        package = None
        skip_logs = False

    collected_phases = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None):
        collected_phases.extend(phases)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract), \
         patch.object(deploy, "warn") as mock_warn:
        deploy._cmd_collect(Args(), run_dir, {"namespace": "ns-0"})

    assert collected_phases == ["baseline", "treatment"]
    mock_warn.assert_called()


def test_collect_single_package_from_progress(tmp_path):
    """With --package treatment and progress containing it, collects only treatment."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    _write_progress(run_dir, {
        "wl-smoke-baseline": {"workload": "wl-smoke", "package": "baseline", "status": "done"},
        "wl-smoke-treatment": {"workload": "wl-smoke", "package": "treatment", "status": "done"},
    })

    class Args:
        package = ["treatment"]
        skip_logs = False

    collected_phases = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None):
        collected_phases.extend(phases)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespace": "ns-0"})

    assert collected_phases == ["treatment"]


def test_collect_experiment_expands_to_all_progress_phases(tmp_path):
    """With --package experiment, expands to all known phases from progress."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    _write_progress(run_dir, {
        "wl-smoke-baseline": {"workload": "wl-smoke", "package": "baseline", "status": "done"},
        "wl-smoke-treatment": {"workload": "wl-smoke", "package": "treatment", "status": "done"},
        "wl-smoke-canary": {"workload": "wl-smoke", "package": "canary", "status": "done"},
    })

    class Args:
        package = ["experiment"]
        skip_logs = False

    collected_phases = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None):
        collected_phases.extend(phases)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespace": "ns-0"})

    # Should expand to all phases from progress (sorted)
    assert collected_phases == ["baseline", "canary", "treatment"]


def test_collect_unknown_package_exits(tmp_path):
    """With --package foo (not in progress), collect exits with error."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    _write_progress(run_dir, {
        "wl-smoke-baseline": {"workload": "wl-smoke", "package": "baseline", "status": "done"},
        "wl-smoke-treatment": {"workload": "wl-smoke", "package": "treatment", "status": "done"},
    })

    class Args:
        package = ["foo"]
        skip_logs = False

    with pytest.raises(SystemExit):
        deploy._cmd_collect(Args(), run_dir, {"namespace": "ns-0"})


def test_collect_custom_package_in_progress(tmp_path):
    """With --package canary where canary IS in progress, succeeds."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    _write_progress(run_dir, {
        "wl-smoke-baseline": {"workload": "wl-smoke", "package": "baseline", "status": "done"},
        "wl-smoke-canary": {"workload": "wl-smoke", "package": "canary", "status": "done"},
    })

    class Args:
        package = ["canary"]
        skip_logs = False

    collected_phases = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None):
        collected_phases.extend(phases)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespace": "ns-0"})

    assert collected_phases == ["canary"]

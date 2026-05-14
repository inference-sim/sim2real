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
    """Without --package and no progress.json, discovers phases from cluster/ with warning."""
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


def test_collect_corrupt_progress_warns_and_falls_back(tmp_path):
    """Corrupt progress.json warns with correct message and falls back."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    run_dir.mkdir(parents=True)
    (run_dir / "progress.json").write_text("{invalid json")

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
    warnings = [str(c) for c in mock_warn.call_args_list]
    assert any("Corrupt" in w for w in warnings)


def test_collect_only_done_phases(tmp_path):
    """Only phases with status done are included."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    _write_progress(run_dir, {
        "wl-a-baseline": {"workload": "wl-a", "package": "baseline", "status": "done"},
        "wl-a-treatment": {"workload": "wl-a", "package": "treatment", "status": "pending"},
        "wl-a-canary": {"workload": "wl-a", "package": "canary", "status": "done"},
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

    # treatment is pending — excluded; baseline and canary are done — included
    assert sorted(collected_phases) == ["baseline", "canary"]


def test_collect_missing_package_key_skipped(tmp_path):
    """Progress entries without a 'package' key are gracefully skipped."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    _write_progress(run_dir, {
        "wl-a-baseline": {"workload": "wl-a", "package": "baseline", "status": "done"},
        "wl-b-broken": {"workload": "wl-b", "status": "done"},
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

    assert collected_phases == ["baseline"]


def test_collect_with_multi_baseline_progress(tmp_path):
    """Collect discovers arbitrary phase names from progress.json."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    _write_progress(run_dir, {
        "wl-smoke-b1": {"workload": "wl-smoke", "package": "b1", "status": "done"},
        "wl-smoke-b2": {"workload": "wl-smoke", "package": "b2", "status": "done"},
        "wl-smoke-ac1": {"workload": "wl-smoke", "package": "ac1", "status": "done"},
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

    assert collected_phases == ["ac1", "b1", "b2"]


def test_collect_fallback_discovers_from_pipelinerun_files(tmp_path):
    """Without progress.json, falls back to discovering phases from pipelinerun YAMLs."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    cluster_dir = run_dir / "cluster"
    cluster_dir.mkdir(parents=True)
    (cluster_dir / "pipelinerun-wl-smoke-b1.yaml").write_text("apiVersion: tekton.dev/v1")
    (cluster_dir / "pipelinerun-wl-smoke-b2.yaml").write_text("apiVersion: tekton.dev/v1")

    class Args:
        package = None
        skip_logs = False

    collected_phases = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None):
        collected_phases.extend(phases)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespace": "ns-0"})

    assert collected_phases == ["b1", "b2"]

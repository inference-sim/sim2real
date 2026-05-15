"""Tests for deploy.py _cmd_collect phase selection logic."""

import json
from unittest.mock import patch, MagicMock

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
        "wl-smoke-baseline": {"workload": "wl-smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment": {"workload": "wl-smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
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
        "wl-smoke-baseline": {"workload": "wl-smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment": {"workload": "wl-smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
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
        "wl-smoke-baseline": {"workload": "wl-smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment": {"workload": "wl-smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-canary": {"workload": "wl-smoke", "package": "canary", "status": "done", "completed_namespace": "ns-0"},
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
        "wl-smoke-baseline": {"workload": "wl-smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment": {"workload": "wl-smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
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
        "wl-smoke-baseline": {"workload": "wl-smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-canary": {"workload": "wl-smoke", "package": "canary", "status": "done", "completed_namespace": "ns-0"},
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


def test_collect_corrupt_progress_warns_and_falls_back(tmp_path, capsys):
    """Corrupt progress.json warns with correct message and falls back to default phases."""
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

    # CM primary returns empty (ConfigMap not found), local secondary has corrupt JSON.
    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=1, stderr="Error from server (NotFound): configmaps \"sim2real-progress\" not found")
        deploy._cmd_collect(Args(), run_dir, {"namespace": "ns-0"})

    assert collected_phases == ["baseline", "treatment"]
    captured = capsys.readouterr()
    assert "Corrupt" in captured.err


def test_collect_only_done_phases(tmp_path):
    """Only phases with status done are included."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    _write_progress(run_dir, {
        "wl-a-baseline": {"workload": "wl-a", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
        "wl-a-treatment": {"workload": "wl-a", "package": "treatment", "status": "pending"},
        "wl-a-canary": {"workload": "wl-a", "package": "canary", "status": "done", "completed_namespace": "ns-0"},
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
        "wl-a-baseline": {"workload": "wl-a", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
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
        "wl-smoke-b1": {"workload": "wl-smoke", "package": "b1", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-b2": {"workload": "wl-smoke", "package": "b2", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-ac1": {"workload": "wl-smoke", "package": "ac1", "status": "done", "completed_namespace": "ns-0"},
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


def test_collect_with_workload_scope(tmp_path):
    """--workload scopes extraction to matching workloads only."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    _write_progress(run_dir, {
        "wl-smoke-baseline": {"workload": "smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment": {"workload": "smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
        "wl-load-baseline": {"workload": "load", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
        "wl-load-treatment": {"workload": "load", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
    })

    class Args:
        only = None
        workload = "smoke"
        package = None
        skip_logs = False

    extract_calls = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None):
        extract_calls.append({"phases": phases, "workload": workload})
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespace": "ns-0"})

    assert len(extract_calls) == 1
    assert extract_calls[0]["workload"] == "smoke"
    assert sorted(extract_calls[0]["phases"]) == ["baseline", "treatment"]


def test_collect_with_only_scope(tmp_path):
    """--only scopes extraction to one pair's workload and package."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    _write_progress(run_dir, {
        "wl-smoke-baseline": {"workload": "smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment": {"workload": "smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
        "wl-load-baseline": {"workload": "load", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
    })

    class Args:
        only = "wl-smoke-baseline"
        workload = None
        package = None
        skip_logs = False

    extract_calls = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None):
        extract_calls.append({"phases": phases, "workload": workload})
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespace": "ns-0"})

    assert len(extract_calls) == 1
    assert extract_calls[0]["workload"] == "smoke"
    assert extract_calls[0]["phases"] == ["baseline"]


def test_collect_only_without_prefix(tmp_path):
    """--only resolves wl- prefix automatically."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    _write_progress(run_dir, {
        "wl-smoke-baseline": {"workload": "smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
    })

    class Args:
        only = "smoke-baseline"
        workload = None
        package = None
        skip_logs = False

    extract_calls = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None):
        extract_calls.append({"phases": phases, "workload": workload})
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespace": "ns-0"})

    assert len(extract_calls) == 1
    assert extract_calls[0]["workload"] == "smoke"


def test_collect_workload_with_package_filter(tmp_path):
    """--workload + --package compose: workload scopes within specified phases."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    _write_progress(run_dir, {
        "wl-smoke-baseline": {"workload": "smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment": {"workload": "smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
        "wl-load-baseline": {"workload": "load", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
    })

    class Args:
        only = None
        workload = "smoke"
        package = ["baseline"]
        skip_logs = False

    extract_calls = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None):
        extract_calls.append({"phases": phases, "workload": workload})
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespace": "ns-0"})

    assert len(extract_calls) == 1
    assert extract_calls[0]["workload"] == "smoke"
    assert extract_calls[0]["phases"] == ["baseline"]


def test_collect_workload_no_match_exits(tmp_path):
    """--workload with no matching pairs exits with error."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    _write_progress(run_dir, {
        "wl-smoke-baseline": {"workload": "smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
    })

    class Args:
        only = None
        workload = "nonexistent"
        package = None
        skip_logs = False

    with pytest.raises(SystemExit):
        deploy._cmd_collect(Args(), run_dir, {"namespace": "ns-0"})


def test_collect_warns_nondone_scoped_pairs(tmp_path):
    """When scoped pairs include non-done entries, warn but continue with done ones."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    _write_progress(run_dir, {
        "wl-smoke-baseline": {"workload": "smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment": {"workload": "smoke", "package": "treatment", "status": "running"},
    })

    class Args:
        only = None
        workload = "smoke"
        package = None
        skip_logs = False

    extract_calls = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None):
        extract_calls.append({"phases": phases, "workload": workload})
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract), \
         patch.object(deploy, "warn") as mock_warn:
        deploy._cmd_collect(Args(), run_dir, {"namespace": "ns-0"})

    assert len(extract_calls) == 1
    assert extract_calls[0]["phases"] == ["baseline"]
    assert any("running" in str(c) for c in mock_warn.call_args_list)


def test_collect_unscoped_unchanged(tmp_path):
    """Without --only/--workload, collect behaves exactly as before (no workload param)."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    _write_progress(run_dir, {
        "wl-smoke-baseline": {"workload": "smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment": {"workload": "smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
    })

    class Args:
        only = None
        workload = None
        package = None
        skip_logs = False

    extract_calls = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None):
        extract_calls.append({"phases": phases, "workload": workload})
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespace": "ns-0"})

    assert len(extract_calls) == 1
    assert extract_calls[0]["workload"] is None
    assert sorted(extract_calls[0]["phases"]) == ["baseline", "treatment"]


def test_collect_scoped_without_progress_exits(tmp_path):
    """--workload without progress.json exits with error."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    run_dir.mkdir(parents=True)

    class Args:
        only = None
        workload = "smoke"
        package = None
        skip_logs = False

    with pytest.raises(SystemExit):
        deploy._cmd_collect(Args(), run_dir, {"namespace": "ns-0"})


def test_collect_scoped_all_nondone_no_extraction(tmp_path):
    """When all scoped pairs are non-done, no extraction is attempted and summary prints 0/0."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    _write_progress(run_dir, {
        "wl-smoke-baseline": {"workload": "smoke", "package": "baseline", "status": "running"},
        "wl-smoke-treatment": {"workload": "smoke", "package": "treatment", "status": "pending"},
    })

    class Args:
        only = None
        workload = "smoke"
        package = None
        skip_logs = False

    extract_calls = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None):
        extract_calls.append({"phases": phases, "workload": workload})
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract), \
         patch.object(deploy, "warn") as mock_warn:
        deploy._cmd_collect(Args(), run_dir, {"namespace": "ns-0"})

    assert len(extract_calls) == 0
    assert any("running" in str(c) or "pending" in str(c) for c in mock_warn.call_args_list)


def test_collect_scoped_runtime_error(tmp_path):
    """RuntimeError from extractor pod is caught and reported."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    _write_progress(run_dir, {
        "wl-smoke-baseline": {"workload": "smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
    })

    class Args:
        only = None
        workload = "smoke"
        package = None
        skip_logs = False

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None):
        raise RuntimeError("pod failed")

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract), \
         patch.object(deploy, "warn") as mock_warn:
        deploy._cmd_collect(Args(), run_dir, {"namespace": "ns-0"})

    assert any("pod failed" in str(c) for c in mock_warn.call_args_list)


def test_collect_scoped_per_phase_failure(tmp_path):
    """Per-phase extraction failure in scoped path populates failed list."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    _write_progress(run_dir, {
        "wl-smoke-baseline": {"workload": "smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment": {"workload": "smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
    })

    class Args:
        only = None
        workload = "smoke"
        package = None
        skip_logs = False

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None):
        return {
            "baseline": None,
            "treatment": RuntimeError("tar failed"),
        }

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract), \
         patch.object(deploy, "warn") as mock_warn:
        deploy._cmd_collect(Args(), run_dir, {"namespace": "ns-0"})

    assert any("tar failed" in str(c) for c in mock_warn.call_args_list)


def test_collect_only_takes_precedence_over_workload(tmp_path):
    """When both --only and --workload are given, --only takes precedence."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    _write_progress(run_dir, {
        "wl-smoke-baseline": {"workload": "smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment": {"workload": "smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
        "wl-load-baseline": {"workload": "load", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
    })

    class Args:
        only = "wl-smoke-baseline"
        workload = "load"
        package = None
        skip_logs = False

    extract_calls = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None):
        extract_calls.append({"phases": phases, "workload": workload})
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespace": "ns-0"})

    assert len(extract_calls) == 1
    assert extract_calls[0]["workload"] == "smoke"
    assert extract_calls[0]["phases"] == ["baseline"]


def test_collect_unscoped_multi_namespace_dispatch(tmp_path):
    """Unscoped collect dispatches one extract call per distinct completed_namespace."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    (run_dir / "progress.json").write_text(json.dumps({
        "wl-smoke-baseline":   {"workload": "smoke", "package": "baseline",  "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment":  {"workload": "smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
        "wl-load-baseline":    {"workload": "load",  "package": "baseline",  "status": "done", "completed_namespace": "ns-1"},
        "wl-load-treatment":   {"workload": "load",  "package": "treatment", "status": "done", "completed_namespace": "ns-1"},
    }))

    class Args:
        package = None
        skip_logs = False

    extract_calls = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None):
        extract_calls.append({"namespace": namespace, "phases": sorted(phases), "workload": workload})
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespace": "ns-0"})

    assert len(extract_calls) == 2
    by_ns = {c["namespace"]: c for c in extract_calls}
    assert set(by_ns.keys()) == {"ns-0", "ns-1"}
    assert by_ns["ns-0"]["phases"] == ["baseline", "treatment"]
    assert by_ns["ns-1"]["phases"] == ["baseline", "treatment"]
    # Unscoped path passes no workload restriction
    assert all(c["workload"] is None for c in extract_calls)


def test_collect_unscoped_missing_completed_namespace_warns_and_skips(tmp_path):
    """Done entries without completed_namespace emit a warning and are skipped."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    (run_dir / "progress.json").write_text(json.dumps({
        "wl-smoke-baseline": {"workload": "smoke", "package": "baseline", "status": "done"},
        "wl-smoke-treatment": {"workload": "smoke", "package": "treatment", "status": "done"},
    }))

    class Args:
        package = None
        skip_logs = False

    extract_calls = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None):
        extract_calls.append(phases)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract), \
         patch.object(deploy, "warn") as mock_warn:
        deploy._cmd_collect(Args(), run_dir, {"namespace": "ns-0"})

    # No extraction — all entries missing completed_namespace
    assert extract_calls == []
    warnings = [str(c) for c in mock_warn.call_args_list]
    assert any("completed_namespace" in w for w in warnings)


def test_collect_scoped_multi_namespace_dispatch(tmp_path):
    """Scoped collect uses each workload's completed_namespace, not primary."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    (run_dir / "progress.json").write_text(json.dumps({
        "wl-smoke-baseline":  {"workload": "smoke", "package": "baseline",  "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment": {"workload": "smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
        "wl-load-baseline":   {"workload": "load",  "package": "baseline",  "status": "done", "completed_namespace": "ns-1"},
        "wl-load-treatment":  {"workload": "load",  "package": "treatment", "status": "done", "completed_namespace": "ns-1"},
    }))

    class Args:
        only = None
        workload = "smoke"
        package = None
        skip_logs = False

    extract_calls = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None):
        extract_calls.append({"namespace": namespace, "phases": sorted(phases), "workload": workload})
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespace": "ns-primary"})

    assert len(extract_calls) == 1
    # Must use smoke's completed_namespace (ns-0), NOT the primary namespace
    assert extract_calls[0]["namespace"] == "ns-0"
    assert extract_calls[0]["workload"] == "smoke"
    assert sorted(extract_calls[0]["phases"]) == ["baseline", "treatment"]


def test_collect_scoped_missing_completed_namespace_warns_and_skips(tmp_path):
    """Scoped collect warns and skips workloads whose done pairs lack completed_namespace."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    (run_dir / "progress.json").write_text(json.dumps({
        "wl-smoke-baseline":  {"workload": "smoke", "package": "baseline",  "status": "done"},
        "wl-smoke-treatment": {"workload": "smoke", "package": "treatment", "status": "done"},
    }))

    class Args:
        only = None
        workload = "smoke"
        package = None
        skip_logs = False

    extract_calls = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None):
        extract_calls.append(phases)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract), \
         patch.object(deploy, "warn") as mock_warn:
        deploy._cmd_collect(Args(), run_dir, {"namespace": "ns-0"})

    assert extract_calls == []
    warnings = [str(c) for c in mock_warn.call_args_list]
    assert any("completed_namespace" in w for w in warnings)


def test_collect_reads_from_configmap_when_no_local_file(tmp_path):
    """collect uses CompositeProgressStore to read from ConfigMap when no local file."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)

    cm_data = {
        "wl-smoke-baseline": {"workload": "smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment": {"workload": "smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
    }

    class Args:
        package = None
        skip_logs = False

    extract_calls = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None):
        extract_calls.append({"phases": phases, "workload": workload})
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(cm_data))
        deploy._cmd_collect(Args(), run_dir, {"namespace": "ns-0"})

    assert len(extract_calls) == 1
    assert sorted(extract_calls[0]["phases"]) == ["baseline", "treatment"]


def test_collect_cm_failure_falls_back_to_local(tmp_path):
    """When CM primary raises RuntimeError, collect falls back to local progress.json."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    _write_progress(run_dir, {
        "wl-smoke-baseline": {"workload": "smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment": {"workload": "smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
    })

    class Args:
        package = None
        skip_logs = False

    extract_calls = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None):
        extract_calls.append({"phases": phases, "workload": workload})
        return {p: None for p in phases}

    # CM primary raises RuntimeError (cluster unreachable)
    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=1, stderr="Unable to connect to the server: dial tcp: lookup cluster: no such host")
        deploy._cmd_collect(Args(), run_dir, {"namespace": "ns-0"})

    assert len(extract_calls) == 1
    assert sorted(extract_calls[0]["phases"]) == ["baseline", "treatment"]


def test_collect_cm_failure_no_local_falls_back_to_discovery(tmp_path):
    """When CM primary raises and no local file, collect falls back to phase discovery."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    cluster_dir = run_dir / "cluster"
    cluster_dir.mkdir(parents=True)
    (cluster_dir / "pipelinerun-wl-smoke-baseline.yaml").write_text("apiVersion: tekton.dev/v1")
    (cluster_dir / "pipelinerun-wl-smoke-treatment.yaml").write_text("apiVersion: tekton.dev/v1")

    class Args:
        package = None
        skip_logs = False

    extract_calls = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None):
        extract_calls.append(phases)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=1, stderr="Unable to connect to the server: dial tcp: lookup cluster: no such host")
        deploy._cmd_collect(Args(), run_dir, {"namespace": "ns-0"})

    assert len(extract_calls) == 1
    assert sorted(extract_calls[0]) == ["baseline", "treatment"]


# ── Incremental collect helpers ──────────────────────────────────────────────

def test_probe_remote_mtimes_parses_output():
    """_probe_remote_mtimes parses stat output into {workload: mtime} dict."""
    from pipeline.deploy import _probe_remote_mtimes

    stat_output = (
        "1715800000 /data/run1/baseline/wl-smoke/trace_data.csv\n"
        "1715800100 /data/run1/baseline/wl-load/trace_data.csv\n"
    )

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0, stdout=stat_output)
        result = _probe_remote_mtimes("pod", "/data/run1/baseline", "ns-0")

    assert result == {"wl-smoke": 1715800000.0, "wl-load": 1715800100.0}


def test_probe_remote_mtimes_returns_empty_on_failure():
    """_probe_remote_mtimes returns {} when kubectl exec fails."""
    from pipeline.deploy import _probe_remote_mtimes

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = _probe_remote_mtimes("pod", "/data/run1/baseline", "ns-0")

    assert result == {}


def test_is_up_to_date_true_when_local_newer(tmp_path):
    """_is_up_to_date returns True when local file is at least as new as remote."""
    from pipeline.deploy import _is_up_to_date

    local_csv = tmp_path / "trace_data.csv"
    local_csv.write_text("data")
    local_mtime = local_csv.stat().st_mtime

    assert _is_up_to_date(local_csv, local_mtime - 100) is True
    assert _is_up_to_date(local_csv, local_mtime) is True


def test_is_up_to_date_false_when_no_local(tmp_path):
    """_is_up_to_date returns False when local file does not exist."""
    from pipeline.deploy import _is_up_to_date

    assert _is_up_to_date(tmp_path / "trace_data.csv", 1715800000.0) is False


def test_is_up_to_date_false_when_remote_newer(tmp_path):
    """_is_up_to_date returns False when remote mtime is newer than local."""
    from pipeline.deploy import _is_up_to_date
    import os

    local_csv = tmp_path / "trace_data.csv"
    local_csv.write_text("data")
    old_time = 1000000000.0
    os.utime(local_csv, (old_time, old_time))

    assert _is_up_to_date(local_csv, old_time + 100) is False


def test_is_up_to_date_false_when_remote_mtime_none(tmp_path):
    """_is_up_to_date returns False when remote_mtime is None."""
    from pipeline.deploy import _is_up_to_date

    local_csv = tmp_path / "trace_data.csv"
    local_csv.write_text("data")

    assert _is_up_to_date(local_csv, None) is False

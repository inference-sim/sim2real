"""Tests for deploy.py wipe subcommand."""

import json
from pathlib import Path


_PROGRESS = {
    "wl-smoke-baseline":   {"workload": "wl-smoke",  "package": "baseline",  "status": "done",      "namespace": None, "retries": 0},
    "wl-smoke-treatment":  {"workload": "wl-smoke",  "package": "treatment", "status": "done",      "namespace": None, "retries": 0},
    "wl-load-baseline":    {"workload": "wl-load",   "package": "baseline",  "status": "pending",   "namespace": None, "retries": 0},
    "wl-load-treatment":   {"workload": "wl-load",   "package": "treatment", "status": "failed",    "namespace": None, "retries": 0},
    "wl-heavy-baseline":   {"workload": "wl-heavy",  "package": "baseline",  "status": "timed-out", "namespace": None, "retries": 1},
}


def _setup_results(run_dir: Path) -> None:
    """Create a realistic results directory tree under run_dir."""
    for pkg in ("baseline", "treatment"):
        for wl in ("wl-smoke", "wl-load", "wl-heavy"):
            d = run_dir / "results" / pkg / wl
            d.mkdir(parents=True, exist_ok=True)
            (d / "trace_header.yaml").write_text("header")
            (d / "trace_data.csv").write_text("data")


def test_wipe_all_deletes_results_and_resets(tmp_path, monkeypatch):
    """Unscoped wipe deletes all results and resets non-pending pairs."""
    import pipeline.deploy as mod

    run_dir = tmp_path / "run1"
    run_dir.mkdir()
    progress_path = run_dir / "progress.json"
    progress_path.write_text(json.dumps(_PROGRESS))
    _setup_results(run_dir)

    monkeypatch.setattr("builtins.input", lambda _: "y")

    class _Args:
        only = None; workload = None; package = None; dry_run = False; yes = True

    mod._cmd_wipe(_Args(), run_dir)

    saved = json.loads(progress_path.read_text())
    # All non-pending pairs reset to pending
    assert saved["wl-smoke-baseline"]["status"] == "pending"
    assert saved["wl-smoke-treatment"]["status"] == "pending"
    assert saved["wl-load-treatment"]["status"] == "pending"
    assert saved["wl-heavy-baseline"]["status"] == "pending"
    # Already-pending pair unchanged
    assert saved["wl-load-baseline"]["status"] == "pending"
    # Results directories deleted
    assert not (run_dir / "results" / "baseline").exists()
    assert not (run_dir / "results" / "treatment").exists()


def test_wipe_scoped_by_workload(tmp_path, monkeypatch):
    """--workload scopes wipe to matching pairs only."""
    import pipeline.deploy as mod

    run_dir = tmp_path / "run1"
    run_dir.mkdir()
    progress_path = run_dir / "progress.json"
    progress_path.write_text(json.dumps(_PROGRESS))
    _setup_results(run_dir)

    class _Args:
        only = None; workload = "wl-smoke"; package = None; dry_run = False; yes = True

    mod._cmd_wipe(_Args(), run_dir)

    saved = json.loads(progress_path.read_text())
    # Scoped pairs reset
    assert saved["wl-smoke-baseline"]["status"] == "pending"
    assert saved["wl-smoke-treatment"]["status"] == "pending"
    # Out-of-scope pairs unchanged
    assert saved["wl-load-treatment"]["status"] == "failed"
    assert saved["wl-heavy-baseline"]["status"] == "timed-out"
    # Only wl-smoke directories deleted
    assert not (run_dir / "results" / "baseline" / "wl-smoke").exists()
    assert not (run_dir / "results" / "treatment" / "wl-smoke").exists()
    # Other workloads untouched
    assert (run_dir / "results" / "baseline" / "wl-load").exists()


def test_wipe_scoped_by_only(tmp_path, monkeypatch):
    """--only scopes wipe to a single pair."""
    import pipeline.deploy as mod

    run_dir = tmp_path / "run1"
    run_dir.mkdir()
    progress_path = run_dir / "progress.json"
    progress_path.write_text(json.dumps(_PROGRESS))
    _setup_results(run_dir)

    class _Args:
        only = "wl-heavy-baseline"; workload = None; package = None; dry_run = False; yes = True

    mod._cmd_wipe(_Args(), run_dir)

    saved = json.loads(progress_path.read_text())
    assert saved["wl-heavy-baseline"]["status"] == "pending"
    # Other pairs unchanged
    assert saved["wl-smoke-baseline"]["status"] == "done"
    # Only wl-heavy/baseline deleted
    assert not (run_dir / "results" / "baseline" / "wl-heavy").exists()
    assert (run_dir / "results" / "baseline" / "wl-smoke").exists()


def test_wipe_scoped_by_package(tmp_path, monkeypatch):
    """--package scopes wipe to pairs matching that package."""
    import pipeline.deploy as mod

    run_dir = tmp_path / "run1"
    run_dir.mkdir()
    progress_path = run_dir / "progress.json"
    progress_path.write_text(json.dumps(_PROGRESS))
    _setup_results(run_dir)

    class _Args:
        only = None; workload = None; package = "treatment"; dry_run = False; yes = True

    mod._cmd_wipe(_Args(), run_dir)

    saved = json.loads(progress_path.read_text())
    # Treatment pairs reset
    assert saved["wl-smoke-treatment"]["status"] == "pending"
    assert saved["wl-load-treatment"]["status"] == "pending"
    # Baseline pairs unchanged
    assert saved["wl-smoke-baseline"]["status"] == "done"
    assert saved["wl-heavy-baseline"]["status"] == "timed-out"
    # Only treatment directories deleted
    assert not (run_dir / "results" / "treatment" / "wl-smoke").exists()
    assert (run_dir / "results" / "baseline" / "wl-smoke").exists()


def test_wipe_dry_run_does_not_delete(tmp_path, capsys):
    """--dry-run prints what would be deleted but does not mutate anything."""
    import pipeline.deploy as mod

    run_dir = tmp_path / "run1"
    run_dir.mkdir()
    progress_path = run_dir / "progress.json"
    progress_path.write_text(json.dumps(_PROGRESS))
    _setup_results(run_dir)

    class _Args:
        only = None; workload = None; package = None; dry_run = True; yes = False

    mod._cmd_wipe(_Args(), run_dir)

    # Nothing deleted
    assert (run_dir / "results" / "baseline" / "wl-smoke").exists()
    # Progress unchanged
    saved = json.loads(progress_path.read_text())
    assert saved == _PROGRESS
    # DRY-RUN mentioned in output
    captured = capsys.readouterr()
    assert "DRY-RUN" in captured.out + captured.err


def test_wipe_skips_pending_pairs(tmp_path):
    """Pending pairs are skipped (nothing to wipe)."""
    import pipeline.deploy as mod

    run_dir = tmp_path / "run1"
    run_dir.mkdir()
    progress = {
        "wl-a-baseline": {"workload": "wl-a", "package": "baseline", "status": "pending",
                          "namespace": None, "retries": 0},
    }
    progress_path = run_dir / "progress.json"
    progress_path.write_text(json.dumps(progress))

    class _Args:
        only = None; workload = None; package = None; dry_run = False; yes = True

    mod._cmd_wipe(_Args(), run_dir)

    saved = json.loads(progress_path.read_text())
    assert saved["wl-a-baseline"]["status"] == "pending"


def test_wipe_no_progress_reports_nothing(tmp_path, capsys):
    """When progress.json doesn't exist, wipe reports nothing to do."""
    import pipeline.deploy as mod

    run_dir = tmp_path / "run1"
    run_dir.mkdir()

    class _Args:
        only = None; workload = None; package = None; dry_run = False; yes = True

    mod._cmd_wipe(_Args(), run_dir)

    captured = capsys.readouterr()
    assert "nothing to wipe" in (captured.out + captured.err).lower()


def test_wipe_confirmation_abort(tmp_path, monkeypatch, capsys):
    """User declining confirmation aborts without changes."""
    import pipeline.deploy as mod

    run_dir = tmp_path / "run1"
    run_dir.mkdir()
    progress_path = run_dir / "progress.json"
    progress_path.write_text(json.dumps(_PROGRESS))
    _setup_results(run_dir)

    monkeypatch.setattr("builtins.input", lambda _: "n")

    class _Args:
        only = None; workload = None; package = None; dry_run = False; yes = False

    mod._cmd_wipe(_Args(), run_dir)

    # Nothing changed
    saved = json.loads(progress_path.read_text())
    assert saved == _PROGRESS
    assert (run_dir / "results" / "baseline" / "wl-smoke").exists()


def test_wipe_filter_mismatch_aborts(tmp_path, capsys):
    """--only with non-existent pair aborts with error."""
    import pipeline.deploy as mod

    run_dir = tmp_path / "run1"
    run_dir.mkdir()
    progress_path = run_dir / "progress.json"
    progress_path.write_text(json.dumps(_PROGRESS))

    class _Args:
        only = "nonexistent"; workload = None; package = None; dry_run = False; yes = True

    with __import__("pytest").raises(SystemExit) as exc_info:
        mod._cmd_wipe(_Args(), run_dir)
    assert exc_info.value.code == 1


def test_wipe_cleans_empty_parent_dirs(tmp_path):
    """After wiping all workloads under a package, the package dir is removed."""
    import pipeline.deploy as mod

    run_dir = tmp_path / "run1"
    run_dir.mkdir()
    progress = {
        "wl-only-baseline": {"workload": "wl-only", "package": "baseline", "status": "done",
                             "namespace": None, "retries": 0},
    }
    progress_path = run_dir / "progress.json"
    progress_path.write_text(json.dumps(progress))
    d = run_dir / "results" / "baseline" / "wl-only"
    d.mkdir(parents=True)
    (d / "trace.csv").write_text("data")

    class _Args:
        only = None; workload = None; package = None; dry_run = False; yes = True

    mod._cmd_wipe(_Args(), run_dir)

    # The whole results tree is gone (package dir was left empty)
    assert not (run_dir / "results" / "baseline").exists()

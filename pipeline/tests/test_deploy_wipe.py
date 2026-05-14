"""Tests for deploy.py wipe subcommand."""

import json
from pathlib import Path


_PROGRESS = {
    "wl-smoke-baseline":   {"workload": "wl-smoke",  "package": "baseline",  "status": "done",      "namespace": "sim2real-0", "retries": 0, "pending_stalls": 0, "pending_since": None},
    "wl-smoke-treatment":  {"workload": "wl-smoke",  "package": "treatment", "status": "done",      "namespace": "sim2real-1", "retries": 0, "pending_stalls": 0, "pending_since": None},
    "wl-load-baseline":    {"workload": "wl-load",   "package": "baseline",  "status": "pending",   "namespace": None,         "retries": 0, "pending_stalls": 0, "pending_since": None},
    "wl-load-treatment":   {"workload": "wl-load",   "package": "treatment", "status": "failed",    "namespace": "sim2real-2", "retries": 1, "pending_stalls": 2, "pending_since": "2026-05-14T10:00:00Z"},
    "wl-heavy-baseline":   {"workload": "wl-heavy",  "package": "baseline",  "status": "timed-out", "namespace": "sim2real-0", "retries": 1, "pending_stalls": 3, "pending_since": "2026-05-14T11:00:00Z"},
}


def _assert_reset(entry: dict) -> None:
    """Assert all fields are reset to their pending-state values."""
    assert entry["status"] == "pending"
    assert entry["retries"] == 0
    assert entry["pending_stalls"] == 0
    assert entry["pending_since"] is None
    assert entry["namespace"] is None


def _setup_results(run_dir: Path) -> None:
    """Create a realistic results directory tree under run_dir."""
    for pkg in ("baseline", "treatment"):
        for wl in ("wl-smoke", "wl-load", "wl-heavy"):
            d = run_dir / "results" / pkg / wl
            d.mkdir(parents=True, exist_ok=True)
            (d / "trace_header.yaml").write_text("header")
            (d / "trace_data.csv").write_text("data")


def test_wipe_all_deletes_results_and_resets(tmp_path):
    """Unscoped wipe deletes results for non-pending pairs and resets them."""
    import pipeline.deploy as mod

    run_dir = tmp_path / "run1"
    run_dir.mkdir()
    progress_path = run_dir / "progress.json"
    progress_path.write_text(json.dumps(_PROGRESS))
    _setup_results(run_dir)

    class _Args:
        only = None; workload = None; package = None; dry_run = False; yes = True

    mod._cmd_wipe(_Args(), run_dir)

    saved = json.loads(progress_path.read_text())
    _assert_reset(saved["wl-smoke-baseline"])
    _assert_reset(saved["wl-smoke-treatment"])
    _assert_reset(saved["wl-load-treatment"])
    _assert_reset(saved["wl-heavy-baseline"])
    # Already-pending pair unchanged
    assert saved["wl-load-baseline"]["status"] == "pending"
    # Non-pending workload dirs deleted
    assert not (run_dir / "results" / "baseline" / "wl-smoke").exists()
    assert not (run_dir / "results" / "baseline" / "wl-heavy").exists()
    assert not (run_dir / "results" / "treatment" / "wl-smoke").exists()
    assert not (run_dir / "results" / "treatment" / "wl-load").exists()
    # Pending pair's dir survives
    assert (run_dir / "results" / "baseline" / "wl-load").exists()


def test_wipe_scoped_by_workload(tmp_path):
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
    _assert_reset(saved["wl-smoke-baseline"])
    _assert_reset(saved["wl-smoke-treatment"])
    # Out-of-scope pairs unchanged
    assert saved["wl-load-treatment"]["status"] == "failed"
    assert saved["wl-heavy-baseline"]["status"] == "timed-out"
    # Only wl-smoke directories deleted
    assert not (run_dir / "results" / "baseline" / "wl-smoke").exists()
    assert not (run_dir / "results" / "treatment" / "wl-smoke").exists()
    # Other workloads untouched
    assert (run_dir / "results" / "baseline" / "wl-load").exists()


def test_wipe_scoped_by_only(tmp_path):
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
    _assert_reset(saved["wl-heavy-baseline"])
    # Other pairs unchanged
    assert saved["wl-smoke-baseline"]["status"] == "done"
    # Only wl-heavy/baseline deleted
    assert not (run_dir / "results" / "baseline" / "wl-heavy").exists()
    assert (run_dir / "results" / "baseline" / "wl-smoke").exists()


def test_wipe_scoped_by_package(tmp_path):
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
    _assert_reset(saved["wl-smoke-treatment"])
    _assert_reset(saved["wl-load-treatment"])
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


def test_wipe_skips_pending_pairs(tmp_path, capsys):
    """Pending pairs are skipped (nothing to wipe)."""
    import pipeline.deploy as mod

    run_dir = tmp_path / "run1"
    run_dir.mkdir()
    progress = {
        "wl-a-baseline": {"workload": "wl-a", "package": "baseline", "status": "pending",
                          "namespace": None, "retries": 0, "pending_stalls": 0, "pending_since": None},
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


def test_wipe_confirmation_accept(tmp_path, monkeypatch):
    """User confirming with 'y' proceeds with wipe."""
    import pipeline.deploy as mod

    run_dir = tmp_path / "run1"
    run_dir.mkdir()
    progress_path = run_dir / "progress.json"
    progress_path.write_text(json.dumps(_PROGRESS))
    _setup_results(run_dir)

    monkeypatch.setattr("builtins.input", lambda _: "y")

    class _Args:
        only = None; workload = None; package = None; dry_run = False; yes = False

    mod._cmd_wipe(_Args(), run_dir)

    saved = json.loads(progress_path.read_text())
    _assert_reset(saved["wl-smoke-baseline"])
    assert not (run_dir / "results" / "baseline" / "wl-smoke").exists()


def test_wipe_eof_on_input_aborts(tmp_path, monkeypatch, capsys):
    """EOFError from input() (non-interactive) aborts without changes."""
    import pipeline.deploy as mod

    run_dir = tmp_path / "run1"
    run_dir.mkdir()
    progress_path = run_dir / "progress.json"
    progress_path.write_text(json.dumps(_PROGRESS))
    _setup_results(run_dir)

    def raise_eof(_):
        raise EOFError

    monkeypatch.setattr("builtins.input", raise_eof)

    class _Args:
        only = None; workload = None; package = None; dry_run = False; yes = False

    mod._cmd_wipe(_Args(), run_dir)

    saved = json.loads(progress_path.read_text())
    assert saved == _PROGRESS
    captured = capsys.readouterr()
    assert "--yes" in captured.out + captured.err


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
                             "namespace": None, "retries": 0, "pending_stalls": 0, "pending_since": None},
    }
    progress_path = run_dir / "progress.json"
    progress_path.write_text(json.dumps(progress))
    d = run_dir / "results" / "baseline" / "wl-only"
    d.mkdir(parents=True)
    (d / "trace.csv").write_text("data")

    class _Args:
        only = None; workload = None; package = None; dry_run = False; yes = True

    mod._cmd_wipe(_Args(), run_dir)

    assert not (run_dir / "results" / "baseline").exists()


def test_wipe_no_results_on_disk(tmp_path, capsys):
    """Wipe succeeds even when results directories don't exist on disk."""
    import pipeline.deploy as mod

    run_dir = tmp_path / "run1"
    run_dir.mkdir()
    progress = {
        "wl-a-baseline": {"workload": "wl-a", "package": "baseline", "status": "done",
                          "namespace": None, "retries": 0, "pending_stalls": 0, "pending_since": None},
    }
    progress_path = run_dir / "progress.json"
    progress_path.write_text(json.dumps(progress))
    # No results/ directory created

    class _Args:
        only = None; workload = None; package = None; dry_run = False; yes = True

    mod._cmd_wipe(_Args(), run_dir)

    saved = json.loads(progress_path.read_text())
    _assert_reset(saved["wl-a-baseline"])


def test_wipe_parent_not_removed_when_siblings_remain(tmp_path):
    """Package dir is kept when out-of-scope workloads remain under it."""
    import pipeline.deploy as mod

    run_dir = tmp_path / "run1"
    run_dir.mkdir()
    progress = {
        "wl-a-baseline": {"workload": "wl-a", "package": "baseline", "status": "done",
                          "namespace": None, "retries": 0, "pending_stalls": 0, "pending_since": None},
        "wl-b-baseline": {"workload": "wl-b", "package": "baseline", "status": "done",
                          "namespace": None, "retries": 0, "pending_stalls": 0, "pending_since": None},
    }
    progress_path = run_dir / "progress.json"
    progress_path.write_text(json.dumps(progress))
    for wl in ("wl-a", "wl-b"):
        d = run_dir / "results" / "baseline" / wl
        d.mkdir(parents=True)
        (d / "trace.csv").write_text("data")

    class _Args:
        only = "wl-a-baseline"; workload = None; package = None; dry_run = False; yes = True

    mod._cmd_wipe(_Args(), run_dir)

    # wl-a deleted, wl-b untouched, parent kept
    assert not (run_dir / "results" / "baseline" / "wl-a").exists()
    assert (run_dir / "results" / "baseline" / "wl-b").exists()
    assert (run_dir / "results" / "baseline").exists()


def test_wipe_rmtree_failure_skips_pair(tmp_path, monkeypatch, capsys):
    """When rmtree fails for one pair, that pair's status is preserved."""
    import shutil
    import pipeline.deploy as mod

    run_dir = tmp_path / "run1"
    run_dir.mkdir()
    progress = {
        "wl-a-baseline": {"workload": "wl-a", "package": "baseline", "status": "done",
                          "namespace": None, "retries": 0, "pending_stalls": 0, "pending_since": None},
        "wl-b-baseline": {"workload": "wl-b", "package": "baseline", "status": "done",
                          "namespace": None, "retries": 0, "pending_stalls": 0, "pending_since": None},
    }
    progress_path = run_dir / "progress.json"
    progress_path.write_text(json.dumps(progress))
    for wl in ("wl-a", "wl-b"):
        d = run_dir / "results" / "baseline" / wl
        d.mkdir(parents=True)
        (d / "trace.csv").write_text("data")

    orig_rmtree = shutil.rmtree

    def failing_rmtree(path, *a, **kw):
        if "wl-a" in str(path):
            raise OSError("permission denied")
        return orig_rmtree(path, *a, **kw)

    monkeypatch.setattr("shutil.rmtree", failing_rmtree)

    class _Args:
        only = None; workload = None; package = None; dry_run = False; yes = True

    mod._cmd_wipe(_Args(), run_dir)

    saved = json.loads(progress_path.read_text())
    # wl-a: rmtree failed → status NOT reset
    assert saved["wl-a-baseline"]["status"] == "done"
    # wl-b: succeeded → status reset
    _assert_reset(saved["wl-b-baseline"])
    # Error count in summary
    captured = capsys.readouterr()
    assert "1 failed" in captured.out + captured.err


def test_wipe_warns_on_missing_package_workload(tmp_path, capsys):
    """Entries missing package/workload fields emit a warning."""
    import pipeline.deploy as mod

    run_dir = tmp_path / "run1"
    run_dir.mkdir()
    progress = {
        "wl-broken": {"status": "done", "namespace": None, "retries": 0,
                      "pending_stalls": 0, "pending_since": None},
        "wl-ok-baseline": {"workload": "wl-ok", "package": "baseline", "status": "done",
                           "namespace": None, "retries": 0, "pending_stalls": 0, "pending_since": None},
    }
    progress_path = run_dir / "progress.json"
    progress_path.write_text(json.dumps(progress))

    class _Args:
        only = None; workload = None; package = None; dry_run = False; yes = True

    mod._cmd_wipe(_Args(), run_dir)

    saved = json.loads(progress_path.read_text())
    _assert_reset(saved["wl-ok-baseline"])
    # Broken entry was skipped — status unchanged
    assert saved["wl-broken"]["status"] == "done"
    captured = capsys.readouterr()
    assert "missing package/workload" in (captured.out + captured.err).lower()

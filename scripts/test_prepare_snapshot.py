"""Tests for persist_scorer_snapshot in prepare.py."""
import json
import sys
import types
from pathlib import Path

import pytest

# ── Load only the function under test, stubbing heavy top-level imports ───────

def _load_prepare():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "prepare", Path(__file__).resolve().parent / "prepare.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("yaml", types.ModuleType("yaml"))
    spec.loader.exec_module(mod)
    return mod


_mod = _load_prepare()
persist_scorer_snapshot = _mod.persist_scorer_snapshot


# ── Helpers ───────────────────────────────────────────────────────────────────

def _setup(tmp: Path):
    """Minimal run_dir + stage3 artifact + fake scorer source files."""
    run_dir = tmp / "run"
    run_dir.mkdir()

    scorer_dir = tmp / "scorer"
    scorer_dir.mkdir()
    scorer = scorer_dir / "evolved_scorer.go"
    scorer.write_text("package scorer\n// impl\n")
    test_f = scorer_dir / "evolved_scorer_test.go"
    test_f.write_text("package scorer_test\n// tests\n")

    stage3 = run_dir / "prepare_stage3_output.json"
    stage3.write_text(json.dumps({
        "scorer_file": str(scorer.relative_to(tmp)),
        "test_file": str(test_f.relative_to(tmp)),
        "register_file": "llm-d-inference-scheduler/pkg/plugins/register.go",
        "scorer_type": "evolved-scorer",
        "tekton_artifacts": {"values_yaml": ""},
    }))

    return run_dir, stage3, scorer, test_f, tmp


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_copies_scorer_and_test(tmp_path, monkeypatch):
    run_dir, stage3, scorer, test_f, root = _setup(tmp_path)
    monkeypatch.setattr(_mod, "REPO_ROOT", root)

    scorer_dest, test_dest = persist_scorer_snapshot(run_dir, stage3)

    assert scorer_dest == run_dir / "prepare_scorer.go"
    assert test_dest == run_dir / "prepare_scorer_test.go"
    assert scorer_dest.read_text() == scorer.read_text()
    assert test_dest.read_text() == test_f.read_text()


def test_missing_test_file_returns_none(tmp_path, monkeypatch):
    run_dir, stage3, scorer, test_f, root = _setup(tmp_path)
    test_f.unlink()
    data = json.loads(stage3.read_text())
    data["test_file"] = ""
    stage3.write_text(json.dumps(data))
    monkeypatch.setattr(_mod, "REPO_ROOT", root)

    scorer_dest, test_dest = persist_scorer_snapshot(run_dir, stage3)

    assert scorer_dest.exists()
    assert test_dest is None


def test_overwrites_stale_snapshot(tmp_path, monkeypatch):
    run_dir, stage3, scorer, test_f, root = _setup(tmp_path)
    (run_dir / "prepare_scorer.go").write_text("stale content")
    monkeypatch.setattr(_mod, "REPO_ROOT", root)

    persist_scorer_snapshot(run_dir, stage3)

    assert (run_dir / "prepare_scorer.go").read_text() == scorer.read_text()

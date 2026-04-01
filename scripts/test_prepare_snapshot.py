"""Tests for persist_plugin_snapshot in prepare.py."""
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
persist_plugin_snapshot = _mod.persist_plugin_snapshot


# ── Helpers ───────────────────────────────────────────────────────────────────

_DEFAULT_MANIFEST = {
    "artifacts": {
        "plugin_snapshot": "prepare_plugin.go",
        "plugin_test_snapshot": "prepare_plugin_test.go",
    },
}


def _setup(tmp: Path):
    """Minimal run_dir + stage3 artifact + fake plugin source files."""
    run_dir = tmp / "run"
    run_dir.mkdir()

    plugin_dir = tmp / "plugin"
    plugin_dir.mkdir()
    plugin = plugin_dir / "evolved_plugin.go"
    plugin.write_text("package plugin\n// impl\n")
    test_f = plugin_dir / "evolved_plugin_test.go"
    test_f.write_text("package plugin_test\n// tests\n")

    stage3 = run_dir / "prepare_stage3_output.json"
    stage3.write_text(json.dumps({
        "plugin_file": str(plugin.relative_to(tmp)),
        "test_file": str(test_f.relative_to(tmp)),
        "register_file": "llm-d-inference-scheduler/pkg/plugins/register.go",
        "plugin_type": "evolved-plugin",
        "tekton_artifacts": {"values_yaml": ""},
    }))

    return run_dir, stage3, plugin, test_f, tmp


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_copies_plugin_and_test(tmp_path, monkeypatch):
    run_dir, stage3, plugin, test_f, root = _setup(tmp_path)
    monkeypatch.setattr(_mod, "REPO_ROOT", root)

    plugin_dest, test_dest = persist_plugin_snapshot(run_dir, stage3, _DEFAULT_MANIFEST)

    assert plugin_dest == run_dir / "prepare_plugin.go"
    assert test_dest == run_dir / "prepare_plugin_test.go"
    assert plugin_dest.read_text() == plugin.read_text()
    assert test_dest.read_text() == test_f.read_text()


def test_missing_test_file_returns_none(tmp_path, monkeypatch):
    run_dir, stage3, plugin, test_f, root = _setup(tmp_path)
    test_f.unlink()
    data = json.loads(stage3.read_text())
    data["test_file"] = ""
    stage3.write_text(json.dumps(data))
    monkeypatch.setattr(_mod, "REPO_ROOT", root)

    plugin_dest, test_dest = persist_plugin_snapshot(run_dir, stage3, _DEFAULT_MANIFEST)

    assert plugin_dest.exists()
    assert test_dest is None


def test_overwrites_stale_snapshot(tmp_path, monkeypatch):
    run_dir, stage3, plugin, test_f, root = _setup(tmp_path)
    (run_dir / "prepare_plugin.go").write_text("stale content")
    monkeypatch.setattr(_mod, "REPO_ROOT", root)

    persist_plugin_snapshot(run_dir, stage3, _DEFAULT_MANIFEST)

    assert (run_dir / "prepare_plugin.go").read_text() == plugin.read_text()

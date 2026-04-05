# tools/test_prepare_mapping.py
"""Tests for mapping-related functions in scripts/prepare.py."""
import importlib.util
import sys
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).parent.parent
_spec = importlib.util.spec_from_file_location("prepare", REPO_ROOT / "scripts" / "prepare.py")
_prepare = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_prepare)


def test_extract_mapping_hash_reads_from_path(tmp_path):
    """_extract_mapping_hash reads hash from the given path, not manifest."""
    mapping = tmp_path / "mapping.md"
    mapping.write_text("**Pinned commit hash:** abc1234\n")
    result = _prepare._extract_mapping_hash(mapping)
    assert result == "abc1234"  # 7 chars


def test_extract_mapping_hash_missing_hash(tmp_path):
    """_extract_mapping_hash exits 1 when no hash found."""
    mapping = tmp_path / "mapping.md"
    mapping.write_text("no hash here\n")
    with pytest.raises(SystemExit):
        _prepare._extract_mapping_hash(mapping)


def test_resolve_mapping_path_override_takes_precedence(tmp_path):
    """Override file in run_dir wins over canonical."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    canonical = tmp_path / "canonical.md"
    canonical.write_text("canonical")
    override = run_dir / "mapping_override.md"
    override.write_text("override")

    resolved = _prepare._resolve_mapping_path(run_dir, canonical)
    assert resolved == override
    assert resolved.read_text() == "override"


def test_resolve_mapping_path_canonical_when_no_override(tmp_path):
    """Falls back to canonical when no override exists."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    canonical = tmp_path / "canonical.md"
    canonical.write_text("canonical")

    resolved = _prepare._resolve_mapping_path(run_dir, canonical)
    assert resolved == canonical


def test_resolve_mapping_path_none_when_neither_exists(tmp_path):
    """Returns None when neither override nor canonical exists."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    canonical = tmp_path / "nonexistent.md"

    resolved = _prepare._resolve_mapping_path(run_dir, canonical)
    assert resolved is None

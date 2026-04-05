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

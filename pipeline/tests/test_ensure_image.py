"""Tests for pipeline/lib/ensure_image.py."""
import json
import subprocess
from pathlib import Path

from pipeline.lib.ensure_image import (
    compute_source_hash,
    image_needs_build,
    load_source_hashes,
    save_source_hash,
)


def _init_git_repo(path: Path) -> None:
    """Create a git repo with an initial commit."""
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@test.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True, capture_output=True)
    (path / "file.txt").write_text("hello")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "init"], check=True, capture_output=True)


class TestComputeSourceHash:
    def test_returns_40_char_hex(self, tmp_path):
        _init_git_repo(tmp_path)
        result = compute_source_hash(tmp_path)
        assert len(result) == 40
        assert all(c in "0123456789abcdef" for c in result)

    def test_changes_on_new_commit(self, tmp_path):
        _init_git_repo(tmp_path)
        hash1 = compute_source_hash(tmp_path)
        (tmp_path / "file.txt").write_text("changed")
        subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "edit"], check=True, capture_output=True)
        hash2 = compute_source_hash(tmp_path)
        assert hash1 != hash2


class TestLoadSourceHashes:
    def test_returns_empty_when_no_file(self, tmp_path):
        assert load_source_hashes(tmp_path) == {}

    def test_returns_empty_when_no_key(self, tmp_path):
        (tmp_path / "run_metadata.json").write_text(json.dumps({"version": 1, "stages": {}}))
        assert load_source_hashes(tmp_path) == {}

    def test_returns_stored_hashes(self, tmp_path):
        meta = {"version": 1, "stages": {}, "source_hashes": {"img:tag": "abc123"}}
        (tmp_path / "run_metadata.json").write_text(json.dumps(meta))
        assert load_source_hashes(tmp_path) == {"img:tag": "abc123"}


class TestSaveSourceHash:
    def test_creates_key(self, tmp_path):
        meta = {"version": 1, "stages": {}}
        (tmp_path / "run_metadata.json").write_text(json.dumps(meta))
        save_source_hash(tmp_path, "ghcr.io/me/repo:v1", "deadbeef" * 5)
        loaded = json.loads((tmp_path / "run_metadata.json").read_text())
        assert loaded["source_hashes"]["ghcr.io/me/repo:v1"] == "deadbeef" * 5

    def test_preserves_existing_hashes(self, tmp_path):
        meta = {"version": 1, "stages": {}, "source_hashes": {"old:ref": "aaa"}}
        (tmp_path / "run_metadata.json").write_text(json.dumps(meta))
        save_source_hash(tmp_path, "new:ref", "bbb")
        loaded = json.loads((tmp_path / "run_metadata.json").read_text())
        assert loaded["source_hashes"]["old:ref"] == "aaa"
        assert loaded["source_hashes"]["new:ref"] == "bbb"


class TestImageNeedsBuild:
    def test_needs_build_when_no_stored_hash(self, tmp_path):
        (tmp_path / "run_metadata.json").write_text(json.dumps({"version": 1, "stages": {}}))
        src = tmp_path / "src"
        src.mkdir()
        _init_git_repo(src)
        assert image_needs_build(tmp_path, "img:tag", src) is True

    def test_no_build_when_hash_matches(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        _init_git_repo(src)
        current_hash = compute_source_hash(src)
        meta = {"version": 1, "stages": {}, "source_hashes": {"img:tag": current_hash}}
        (tmp_path / "run_metadata.json").write_text(json.dumps(meta))
        assert image_needs_build(tmp_path, "img:tag", src) is False

    def test_needs_build_when_hash_differs(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        _init_git_repo(src)
        meta = {"version": 1, "stages": {}, "source_hashes": {"img:tag": "stale_hash"}}
        (tmp_path / "run_metadata.json").write_text(json.dumps(meta))
        assert image_needs_build(tmp_path, "img:tag", src) is True

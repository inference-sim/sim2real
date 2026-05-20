"""Ensure container images exist: source hash comparison and build dispatch."""
import json
import os
import subprocess
from pathlib import Path


def compute_source_hash(source_dir: Path) -> str:
    """Return the HEAD commit hash of the git repo at source_dir."""
    result = subprocess.run(
        ["git", "-C", str(source_dir), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def load_source_hashes(run_dir: Path) -> dict[str, str]:
    """Load source_hashes dict from run_metadata.json. Returns {} if absent."""
    meta_path = run_dir / "run_metadata.json"
    if not meta_path.exists():
        return {}
    meta = json.loads(meta_path.read_text())
    return meta.get("source_hashes", {})


def save_source_hash(run_dir: Path, image_ref: str, source_hash: str) -> None:
    """Persist a source hash for an image ref in run_metadata.json."""
    meta_path = run_dir / "run_metadata.json"
    meta = json.loads(meta_path.read_text())
    meta.setdefault("source_hashes", {})[image_ref] = source_hash
    tmp_path = meta_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(meta, indent=2))
    os.replace(tmp_path, meta_path)


def image_needs_build(run_dir: Path, image_ref: str, source_dir: Path) -> bool:
    """Return True if the image should be (re)built based on source hash comparison."""
    stored = load_source_hashes(run_dir).get(image_ref)
    if stored is None:
        return True
    try:
        current = compute_source_hash(source_dir)
    except subprocess.CalledProcessError:
        return True
    return current != stored

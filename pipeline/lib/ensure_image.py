"""Ensure container images exist: source hash comparison and build dispatch."""
import json
import os
import subprocess
from pathlib import Path

import yaml


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


def compute_baseline_ref(registry_repo: str, source_dir: Path) -> str:
    """Return the baseline image ref tagged by the 8-char HEAD SHA of source_dir."""
    sha = compute_source_hash(source_dir)
    return f"{registry_repo}:{sha[:8]}"


def collect_scenario_images(cluster_dir: Path) -> list[dict]:
    """Extract unique EPP image refs from resolved scenario YAMLs in cluster/.

    Reads ``scenario[*].router.epp.image`` (registry/repository/tag) and
    reconstructs a full ``registry/repository:tag`` ref. Returns list of
    dicts: ``{"image_ref": "registry/repo:tag", "package": "filename_stem"}``.
    Skips pipelinerun-*.yaml files.
    """
    if not cluster_dir.exists():
        return []

    seen: set[str] = set()
    results: list[dict] = []

    for yaml_path in sorted(cluster_dir.glob("*.yaml")):
        if yaml_path.name.startswith("pipelinerun-"):
            continue
        try:
            data = yaml.safe_load(yaml_path.read_text()) or {}
        except (OSError, yaml.YAMLError) as exc:
            import warnings
            warnings.warn(f"Skipping unreadable scenario file {yaml_path.name}: {exc}")
            continue

        for entry in data.get("scenario", []):
            router = entry.get("router") or {}
            epp = router.get("epp") or {} if isinstance(router, dict) else {}
            img = epp.get("image") or {} if isinstance(epp, dict) else {}
            if not img:
                continue
            registry = img.get("registry", "")
            repository = img.get("repository", "")
            tag = img.get("tag", "")
            if not repository or not tag:
                continue
            ref = f"{registry}/{repository}:{tag}" if registry else f"{repository}:{tag}"
            if ref in seen:
                continue
            seen.add(ref)
            results.append({"image_ref": ref, "package": yaml_path.stem})

    return results

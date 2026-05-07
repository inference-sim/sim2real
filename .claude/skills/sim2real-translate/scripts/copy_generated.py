"""Derive file lists from git state and copy to generated/.

Replaces blind trust of translation_output.json lists with git-based
discovery. Semantics: files_created/files_modified = "files required to
reproduce this run's submodule state", not "files changed this session."
"""
import json
import shutil
import subprocess
from pathlib import Path


def copy_generated(target_repo: str, run_dir: str) -> tuple[list[str], list[str]]:
    """Discover changed/new files via git, update translation_output.json, copy to generated/.

    Args:
        target_repo: Path to the target submodule (e.g., llm-d-inference-scheduler).
        run_dir: Path to the run directory containing translation_output.json.

    Returns:
        Tuple of (files_created, files_modified) as written to translation_output.json.
    """
    target = Path(target_repo)
    rd = Path(run_dir)
    gen = rd / "generated"
    gen.mkdir(parents=True, exist_ok=True)

    diff = subprocess.run(
        ["git", "diff", "HEAD", "--name-only"],
        cwd=target, capture_output=True, text=True, check=True,
    )
    files_modified = [f for f in diff.stdout.strip().splitlines() if f]

    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=target, capture_output=True, text=True, check=True,
    )
    files_created = [f for f in untracked.stdout.strip().splitlines() if f]

    to_path = rd / "translation_output.json"
    o = json.loads(to_path.read_text())
    o["files_created"] = files_created
    o["files_modified"] = files_modified
    to_path.write_text(json.dumps(o, indent=2))

    for f in files_created + files_modified:
        src = target / f
        dst = gen / Path(f).name
        shutil.copy2(src, dst)
        print(f"  {Path(f).name} → generated/")

    count = len(files_created) + len(files_modified)
    if count:
        print(f"Generated artifacts ready ({len(files_created)} created, {len(files_modified)} modified).")
    else:
        print("No files differ from HEAD — generated/ left empty (correct for merged-upstream case).")

    return files_created, files_modified

"""Toggle component directory between baseline and treatment states."""
import shutil
import subprocess
from pathlib import Path


def _log(msg: str) -> None:
    """Emit a source-toggle audit line so the overlay lifecycle is traceable.

    Prints to stdout with a stable [source-toggle] prefix so the record
    is preserved alongside any wrapping tool's log capture and survives
    filters keyed on other prefixes.
    """
    print(f"[source-toggle] {msg}", flush=True)


def restore_baseline(component_dir: Path, translation_output: dict) -> None:
    """Restore component_dir to its pre-translation (baseline) state.

    - Deletes files listed in translation_output["files_created"]
    - Runs git checkout on files listed in translation_output["files_modified"]
    """
    files_created = translation_output.get("files_created", [])
    files_modified = translation_output.get("files_modified", [])
    _log(
        f"restore_baseline dir={component_dir} "
        f"files_created={len(files_created)} files_modified={len(files_modified)}"
    )

    for rel_path in files_created:
        target = component_dir / rel_path
        if target.exists():
            _log(f"  delete: {rel_path}")
            target.unlink()
        else:
            _log(f"  skip (not present): {rel_path}")

    if files_modified:
        _log(f"  git checkout -- {' '.join(files_modified)}")
        subprocess.run(
            ["git", "-C", str(component_dir), "checkout", "--"] + files_modified,
            check=True, capture_output=True,
        )


def restore_treatment(
    component_dir: Path,
    generated_dir: Path,
    translation_output: dict,
    algo_name: str | None = None,
) -> None:
    """Restore component_dir to its post-translation (treatment) state.

    Copies files from generated_dir back to their relative paths in component_dir.

    When *algo_name* is provided, files are read from
    ``generated_dir/{algo_name}/{rel_path}`` using full relative paths.
    When *algo_name* is None (default), the legacy basename lookup is used:
    ``generated_dir/{basename}``.
    """
    files_created = translation_output.get("files_created", [])
    files_modified = translation_output.get("files_modified", [])
    all_files = files_created + files_modified
    _log(
        f"restore_treatment dir={component_dir} generated_dir={generated_dir} "
        f"algo_name={algo_name} files_created={len(files_created)} "
        f"files_modified={len(files_modified)}"
    )
    for rel_path in all_files:
        if algo_name is not None:
            src = generated_dir / algo_name / rel_path
        else:
            src = generated_dir / Path(rel_path).name
        dst = component_dir / rel_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        _log(f"  copy {src} -> {dst}")
        shutil.copy2(src, dst)

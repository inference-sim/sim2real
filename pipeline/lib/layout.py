"""Path helpers for the sim2real workspace layout.

Pure path-only module. Filesystem-aware operations (Path.exists,
Path.iterdir) are permitted; content reads (JSON parsing, YAML parsing)
are not. Consumers that need to read or write workspace files use this
module to compute the path and a separate IO layer to touch the content.

Experiment root
---------------

Today's pipeline modules each carry a private ``EXPERIMENT_ROOT`` global,
mutated in ``main()`` from the ``--experiment-root`` CLI flag and falling
back to ``Path.cwd()``. This module consolidates that resolution so other
modules can ``layout.set_experiment_root(args.experiment_root)`` once and
use the helpers below without re-implementing the rule.

Helpers default to ``Path.cwd()`` at use time when the experiment root has
not been set explicitly, matching the documented "omit --experiment-root
to default to cwd" backward-compat behavior.
"""

from __future__ import annotations

from pathlib import Path


_EXPERIMENT_ROOT: Path | None = None


def set_experiment_root(arg: str | Path | None) -> Path:
    """Set the module-level experiment root from a CLI-arg-style value.

    Mirrors the resolution rule that today lives inline in
    ``pipeline/setup.py``, ``pipeline/sim2real.py``, and
    ``pipeline/deploy.py``: a non-empty ``arg`` is resolved to an absolute
    Path; an empty / None ``arg`` falls back to ``Path.cwd()``.

    Returns the resolved Path so callers can use it directly.
    """
    global _EXPERIMENT_ROOT
    _EXPERIMENT_ROOT = Path(arg).resolve() if arg else Path.cwd()
    return _EXPERIMENT_ROOT


def experiment_root() -> Path:
    """Return the current experiment root.

    If ``set_experiment_root`` has not been called, returns ``Path.cwd()``
    evaluated at call time (so a subsequent ``chdir`` is reflected).
    """
    return _EXPERIMENT_ROOT if _EXPERIMENT_ROOT is not None else Path.cwd()


def workspace_dir() -> Path:
    """``<experiment-root>/workspace/``"""
    return experiment_root() / "workspace"


def clusters_dir() -> Path:
    """``workspace/clusters/``"""
    return workspace_dir() / "clusters"


def cluster_dir(cluster_id: str) -> Path:
    """``workspace/clusters/<cluster_id>/``"""
    return clusters_dir() / cluster_id


def cluster_config_path(cluster_id: str) -> Path:
    """``workspace/clusters/<cluster_id>/cluster_config.json``"""
    return cluster_dir(cluster_id) / "cluster_config.json"


def list_cluster_ids() -> list[str]:
    """Return sorted directory names under ``workspace/clusters/``.

    Returns ``[]`` if the directory does not exist or is empty. Files at
    that level are ignored — only subdirectories are reported.
    """
    base = clusters_dir()
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir())


def runs_dir() -> Path:
    """``workspace/runs/``"""
    return workspace_dir() / "runs"


def translations_dir() -> Path:
    """``workspace/translations/``"""
    return workspace_dir() / "translations"


def setup_config_path() -> Path:
    """``workspace/setup_config.json``

    Legacy file: post-Step-0 it holds workspace-scoped fields only
    (cluster-scoped fields move to ``cluster_config.json``).
    """
    return workspace_dir() / "setup_config.json"


def translation_dir(translation_hash: str) -> Path:
    """``workspace/translations/<hash>/``"""
    return translations_dir() / translation_hash


def translation_output_path(translation_hash: str) -> Path:
    """``workspace/translations/<hash>/translation_output.json``"""
    return translation_dir(translation_hash) / "translation_output.json"


def registered_path(translation_hash: str) -> Path:
    """``workspace/translations/<hash>/registered.json`` (BYO-only)."""
    return translation_dir(translation_hash) / "registered.json"


def generated_config_path(translation_hash: str, algorithm_name: str) -> Path:
    """``workspace/translations/<hash>/generated/<algo>/<algo>_config.yaml``"""
    return (
        translation_dir(translation_hash)
        / "generated"
        / algorithm_name
        / f"{algorithm_name}_config.yaml"
    )

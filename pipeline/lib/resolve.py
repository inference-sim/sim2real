"""Hydrated run-view helper for the three-dimensional workspace.

``resolve_run(experiment_root, run_name)`` reads a run's on-disk state
and returns a hydrated JSON view: run metadata, translation refs,
phase/workload roots, cluster-scenario paths, and the assembled-manifest
slice. It is the code-side helper the ported ``sim2real-check`` skill
consumes when invoked with ``--run R`` (step-3 epic #486). Reusable —
future skill ports (bootstrap, analyze) or operator scripts can call it.

Schema is v1. Future versions are additive-only: new fields may appear,
but existing fields keep their name and type. Consumers should tolerate
unknown top-level keys.

This module reads. It never writes. It probes the filesystem for the
enumerations the check skill needs (phases with data, workloads per
phase, cluster-scenario YAMLs) but does not exhaustively enumerate
per-workload sub-artifacts (log files, resource snapshots, per-phase
config detail) — those remain the check skill's responsibility.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from pipeline.lib import layout, translation_ref


SCHEMA_VERSION = 1


class ResolveError(Exception):
    """Raised when a run cannot be resolved into the hydrated view.

    Callers (typically the CLI wrapper in ``sim2real.py``) print the
    message to stderr and exit 2. The message names the specific missing
    or corrupt file plus the ``sim2real`` command that would produce it,
    per the epic's error-handling contract (see ``docs/epics/step-3/design.md``).
    """


def resolve_run(experiment_root: Path, run_name: str) -> dict:
    """Return the hydrated JSON view of ``workspace/runs/<run_name>/``.

    ``experiment_root`` is the caller-supplied experiment repo root
    (``sim2real resolve --experiment-root <path>`` or the default ``.``).
    ``run_name`` is the workspace-registered run name to resolve.

    Raises ``ResolveError`` on the failure modes enumerated in the design:
      - no ``workspace/`` under ``experiment_root``
      - unknown run (``workspace/runs/<run_name>/`` absent)
      - missing or corrupt ``run_metadata.json``
      - translation dir referenced by ``translation_hash`` doesn't exist
    """
    experiment_root = Path(experiment_root).resolve()
    layout.set_experiment_root(experiment_root)

    workspace = layout.workspace_dir()
    if not workspace.is_dir():
        raise ResolveError(
            f"no workspace/ under {experiment_root}. Pass --experiment-root "
            f"or cd into the experiment repo root."
        )

    run_dir = layout.runs_dir() / run_name
    if not run_dir.is_dir():
        raise ResolveError(
            f"run '{run_name}' not found under {layout.runs_dir()}. "
            f"Run 'sim2real assemble --run {run_name}' to create it, "
            f"or 'sim2real list runs' to see existing runs."
        )

    run_meta = _read_run_metadata(run_dir)
    translation_hash = run_meta.get("translation_hash")
    if not translation_hash or not isinstance(translation_hash, str):
        raise ResolveError(
            f"run_metadata.json at {run_dir / 'run_metadata.json'} is "
            f"missing/corrupt: no 'translation_hash' field. Re-run "
            f"'sim2real assemble --run {run_name}' to regenerate."
        )

    translation_dir = layout.translation_dir(translation_hash)
    if not translation_dir.is_dir():
        raise ResolveError(
            f"translation {translation_hash} referenced by run "
            f"'{run_name}' not found. Rebuild the translation by running "
            f"'sim2real translate' (skill-driven) or "
            f"'sim2real translation register' (BYO) with the same inputs, "
            f"then re-assemble."
        )

    tout = _read_translation_output(translation_dir)
    manifest_assembly = _read_manifest_assembly(run_dir)
    cluster_id = run_meta.get("cluster_id") or ""
    cluster_config_path = _resolve_cluster_config_path(cluster_id)

    return {
        "version": SCHEMA_VERSION,
        "run_name": run_meta.get("run_name") or run_name,
        "run_dir": str(run_dir),
        "cluster_id": cluster_id,
        "cluster_config_path": (
            str(cluster_config_path) if cluster_config_path else None
        ),
        "params_hash": run_meta.get("params_hash") or "",
        "image_tag": run_meta.get("image_tag") or "",
        "replicas": run_meta.get("replicas") or 1,
        "assembled_at": run_meta.get("assembled_at") or "",
        "experiment_root": str(experiment_root),
        "translation": _build_translation_section(
            translation_hash, translation_dir, tout, manifest_assembly
        ),
        "results": _build_results_section(run_dir, manifest_assembly),
        "cluster_scenarios": _build_cluster_scenarios_section(
            run_dir, manifest_assembly
        ),
        "manifest_assembly": _build_manifest_assembly_section(
            run_dir, manifest_assembly
        ),
    }


# ── Section builders ─────────────────────────────────────────────────────────


def _build_translation_section(
    translation_hash: str,
    translation_dir: Path,
    tout: dict,
    manifest_assembly: dict | None,
) -> dict:
    """Hydrate the ``translation.*`` sub-object.

    ``tout`` is the already-normalized ``translation_output.json`` shape
    from ``translation_ref.read_translation_output`` (legacy step-1
    top-level ``image_ref`` already duplicated onto each algorithm).

    Baseline entries come from the manifest — ``translation_output.json``
    doesn't carry baseline detail today (the translate checkpoint
    intentionally drops it). Empty list when the manifest is absent
    (partial workspace).
    """
    generated_dir = translation_dir / "generated"
    algorithms = []
    for algo in tout.get("algorithms") or []:
        if not isinstance(algo, dict):
            continue
        name = algo.get("name") or ""
        algo_generated_dir = generated_dir / name
        config_path = layout.generated_config_path(translation_hash, name)
        algorithms.append(
            {
                "name": name,
                "source_path": algo.get("source_path"),
                "source_sha256": algo.get("source_sha256"),
                "image_ref": algo.get("image_ref"),
                "image_digest": algo.get("image_digest"),
                "generated_dir": str(algo_generated_dir),
                "config_path": str(config_path),
            }
        )

    baselines = []
    for bl in (manifest_assembly or {}).get("baselines") or []:
        if not isinstance(bl, dict):
            continue
        bl_name = bl.get("name") or ""
        if not bl_name:
            continue
        overlay_path = (
            generated_dir / f"baseline_{bl_name}" / "baseline_config.yaml"
        )
        baselines.append(
            {
                "name": bl_name,
                "generated_overlay_path": str(overlay_path),
            }
        )

    return {
        "hash": translation_hash,
        "alias": tout.get("alias"),
        "source": tout.get("source"),
        "translations_dir": str(translation_dir),
        "generated_dir": str(generated_dir),
        "algorithms": algorithms,
        "baselines": baselines,
    }


def _build_results_section(run_dir: Path, manifest_assembly: dict | None) -> dict:
    """Hydrate the ``results.*`` sub-object.

    ``phases_declared`` is the union of baseline names + algorithm names
    from ``manifest.assembly.yaml``. ``phases_with_data`` filters that
    list to entries whose subdir contains at least one workload with
    ``trace_data.csv``. Predicate is one-level glob
    ``results/<phase>/*/trace_data.csv`` (not recursive) — avoids false
    positives from sibling dirs like ``plans/``.
    """
    results_dir = run_dir / "results"
    declared = _phases_declared_from_manifest(manifest_assembly)
    with_data: list[str] = []
    workloads_by_phase: dict[str, list[str]] = {}
    if results_dir.is_dir():
        for phase in declared:
            phase_dir = results_dir / phase
            if not phase_dir.is_dir():
                continue
            phase_workloads = sorted(
                wl.name
                for wl in phase_dir.iterdir()
                if wl.is_dir() and (wl / "trace_data.csv").exists()
            )
            if phase_workloads:
                with_data.append(phase)
                workloads_by_phase[phase] = phase_workloads

    return {
        "results_dir": str(results_dir),
        "phases_declared": declared,
        "phases_with_data": with_data,
        "workloads_by_phase": workloads_by_phase,
    }


def _build_cluster_scenarios_section(
    run_dir: Path, manifest_assembly: dict | None
) -> dict:
    """Hydrate the ``cluster_scenarios.*`` sub-object.

    Enumerates the resolved-scenario YAMLs written by ``sim2real
    assemble`` under ``<run>/cluster/``. ``baseline_yaml`` is the
    (currently singular) baseline scenario file; ``treatment_yamls``
    maps each algorithm name to its resolved treatment scenario file;
    ``pipelinerun_yamls`` is the sorted list of ``pipelinerun-*.yaml``
    files (one per (workload, package) pair).
    """
    cluster_dir = run_dir / "cluster"
    baseline_yaml: str | None = None
    treatment_yamls: dict[str, str] = {}
    pipelinerun_yamls: list[str] = []

    if cluster_dir.is_dir():
        algo_names = [
            (a.get("name") or "")
            for a in (manifest_assembly or {}).get("algorithms") or []
            if isinstance(a, dict)
        ]
        for yaml_path in sorted(cluster_dir.glob("*.yaml")):
            name = yaml_path.stem
            if name.startswith("pipelinerun-"):
                pipelinerun_yamls.append(str(yaml_path))
            elif name == "baseline":
                baseline_yaml = str(yaml_path)
            elif name in algo_names:
                treatment_yamls[name] = str(yaml_path)
            # Files that don't match any known shape (e.g., stray
            # unrelated YAML) are silently ignored — the check skill
            # doesn't need them, and being noisy here would surface
            # unrelated operator files.

    return {
        "cluster_dir": str(cluster_dir),
        "baseline_yaml": baseline_yaml,
        "treatment_yamls": treatment_yamls,
        "pipelinerun_yamls": pipelinerun_yamls,
    }


def _build_manifest_assembly_section(
    run_dir: Path, manifest_assembly: dict | None
) -> dict:
    """Hydrate the ``manifest_assembly.*`` sub-object.

    ``path`` is ``null`` when ``manifest.assembly.yaml`` is absent
    (partial workspace or pre-refactor legacy). Other fields default to
    empty structures so downstream consumers can iterate without
    branching on None.
    """
    manifest_path = run_dir / "manifest.assembly.yaml"
    if manifest_assembly is None:
        return {
            "path": None,
            "scenario": "",
            "workloads": [],
            "defaults_disable": [],
            "blis_observe": {},
        }
    defaults = manifest_assembly.get("defaults") or {}
    return {
        "path": str(manifest_path),
        "scenario": manifest_assembly.get("scenario") or "",
        "workloads": list(manifest_assembly.get("workloads") or []),
        "defaults_disable": list(defaults.get("disable") or []),
        "blis_observe": dict(manifest_assembly.get("blis_observe") or {}),
    }


# ── Helpers ──────────────────────────────────────────────────────────────────


def _read_run_metadata(run_dir: Path) -> dict:
    """Read ``run_metadata.json`` or raise ResolveError with a specific message."""
    path = run_dir / "run_metadata.json"
    if not path.exists():
        raise ResolveError(
            f"run_metadata.json not found at {path}. "
            f"Re-run 'sim2real assemble --run {run_dir.name}' to regenerate."
        )
    try:
        data: Any = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ResolveError(
            f"run_metadata.json at {path} is missing/corrupt: {exc}. "
            f"Re-run 'sim2real assemble --run {run_dir.name}' to regenerate."
        ) from exc
    if not isinstance(data, dict):
        raise ResolveError(
            f"run_metadata.json at {path} is not a JSON object. "
            f"Re-run 'sim2real assemble --run {run_dir.name}' to regenerate."
        )
    return data


def _read_translation_output(translation_dir: Path) -> dict:
    """Read the normalized ``translation_output.json`` for a translation dir.

    Uses ``translation_ref.read_translation_output`` so legacy step-1
    top-level ``image_ref`` is duplicated onto each algorithm before
    resolve reads it.
    """
    path = translation_dir / "translation_output.json"
    if not path.exists():
        raise ResolveError(
            f"translation_output.json not found at {path}. Corrupt "
            f"translation directory; re-run 'sim2real translate' or "
            f"'sim2real translation register' with the same inputs."
        )
    try:
        return translation_ref.read_translation_output(path)
    except (OSError, ValueError) as exc:
        raise ResolveError(
            f"translation_output.json at {path} is malformed: {exc}."
        ) from exc


def _read_manifest_assembly(run_dir: Path) -> dict | None:
    """Read ``manifest.assembly.yaml`` — returns ``None`` when absent.

    Absence is a non-fatal partial-workspace state: some downstream check
    subsections use the manifest slice, others don't. Missing file → the
    schema section carries ``path: null`` and empty structures; check
    subsections that need the manifest slice warn and skip.
    """
    path = run_dir / "manifest.assembly.yaml"
    if not path.exists():
        return None
    try:
        parsed = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise ResolveError(
            f"manifest.assembly.yaml at {path} is malformed: {exc}. "
            f"Re-run 'sim2real assemble --run {run_dir.name}'."
        ) from exc
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise ResolveError(
            f"manifest.assembly.yaml at {path} is not a YAML mapping."
        )
    return parsed


def _resolve_cluster_config_path(cluster_id: str) -> Path | None:
    """Compose the cluster_config.json path and return it if the file exists.

    Returns ``None`` when the cluster_id is empty or the file is absent
    (partial workspace / legacy). Resolve intentionally does not raise
    on missing cluster config — the check skill's cluster-side checks
    skip when this is null.
    """
    if not cluster_id:
        return None
    path = layout.cluster_config_path(cluster_id)
    return path if path.exists() else None


def _phases_declared_from_manifest(manifest_assembly: dict | None) -> list[str]:
    """Return the union of baseline names + algorithm names from the manifest.

    Order: baselines first (in manifest order), then algorithms (in
    manifest order). Empty when the manifest is absent.
    """
    if manifest_assembly is None:
        return []
    names: list[str] = []
    for bl in manifest_assembly.get("baselines") or []:
        if isinstance(bl, dict) and bl.get("name"):
            names.append(bl["name"])
    for algo in manifest_assembly.get("algorithms") or []:
        if isinstance(algo, dict) and algo.get("name"):
            names.append(algo["name"])
    return names



#!/usr/bin/env python3
"""Iteration enumerator for sim2real-check (step-5 iN/ traversal).

Reads a run's ``run_metadata.json``, ``manifest.assembly.yaml``, and its
referenced ``translation_output.json``; classifies every ``(phase,
workload, iteration)`` triple in the declared range as one of:

    PRESENT — the ``iN/`` subdir exists on disk (or, in legacy shape,
              the direct ``<phase>/<workload>/`` dir has a
              ``trace_data.csv`` and we synthesize an implicit i1).
    MISSING — the iteration is in the declared range but no ``iN/``
              subdir exists on disk. Not applicable to legacy runs.
    SKIP    — the phase corresponds to an algorithm that is absent
              from the translation. Baseline phases are never SKIP.

Emits a JSON document on stdout listing the classified rows, plus
top-level shape / declared_replicas / divergence_warnings /
malformed_iter_dir_count / counts fields. Divergence warnings also go
to stderr for operator visibility.

Exit codes:
    0 — all rows PRESENT or SKIP.
    1 — at least one MISSING. (FAIL is signal-derived at the
        SKILL.md rollup layer; not this enumerator's business.)
    2 — invocation error (missing run, unreadable ConfigMap, malformed
        inputs).

Usage:
    python enumerate_iterations.py --run <name> [--experiment-root <path>]

The enumerator is intentionally scoped to disk state. Signal-based
PASS/FAIL is computed by the sim2real-check SKILL.md prompt over the
PRESENT rows and folded into the final rollup + exit code.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


_ITER_DIR_RE = re.compile(r"^i[1-9][0-9]*$")


@dataclass
class Row:
    phase: str
    workload: str
    iteration: int
    status: str
    results_dir: str | None = None
    note: str | None = None


@dataclass
class EnumResult:
    run: str
    shape: str
    declared_replicas: int
    divergence_warnings: list[str] = field(default_factory=list)
    malformed_iter_dir_count: int = 0
    rows: list[Row] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    exit_code: int = 0


class EnumError(Exception):
    """Raised on invocation errors — surfaces to exit code 2."""


def enumerate_run(experiment_root: Path, run_name: str) -> EnumResult:
    experiment_root = Path(experiment_root).resolve()
    workspace = experiment_root / "workspace"
    if not workspace.is_dir():
        raise EnumError(
            f"no workspace/ under {experiment_root}. Pass --experiment-root "
            f"or cd into the experiment repo root."
        )

    run_dir = workspace / "runs" / run_name
    if not run_dir.is_dir():
        raise EnumError(
            f"run '{run_name}' not found under {workspace / 'runs'}. "
            f"Run 'sim2real assemble --run {run_name}' to create it, "
            f"or 'sim2real list runs' to see existing runs."
        )

    run_meta = _read_json(run_dir / "run_metadata.json")
    manifest = _read_manifest(run_dir / "manifest.assembly.yaml")
    translation_hash = run_meta.get("translation_hash") or ""
    if not translation_hash:
        raise EnumError(
            f"run_metadata.json at {run_dir / 'run_metadata.json'} is "
            f"missing/corrupt: no 'translation_hash' field."
        )
    tout_path = (
        workspace / "translations" / translation_hash / "translation_output.json"
    )
    tout = _read_json(tout_path)

    declared_replicas = _int_or_default(run_meta.get("replicas"), 1)
    translated_names = {
        (a.get("name") or "")
        for a in (tout.get("algorithms") or [])
        if isinstance(a, dict)
    }
    baseline_names = _names(manifest.get("baselines"))
    algo_names = _names(manifest.get("algorithms"))
    workload_names = _names(manifest.get("workloads"))
    phases = baseline_names + algo_names
    results_dir = run_dir / "results"

    shape, divergence_warnings, malformed = _detect_shape(
        results_dir, phases, workload_names
    )

    rows: list[Row] = []
    for phase in phases:
        is_algo = phase in algo_names
        is_skip = is_algo and phase not in translated_names
        for workload in workload_names:
            wl_dir = results_dir / phase / workload
            if is_skip:
                for it in range(1, declared_replicas + 1):
                    rows.append(
                        Row(
                            phase=phase,
                            workload=workload,
                            iteration=it,
                            status="SKIP",
                            note="algorithm not in translation",
                        )
                    )
                continue

            if shape == "legacy":
                iters = [1]
            else:
                iters = list(range(1, declared_replicas + 1))

            for it in iters:
                if shape == "legacy":
                    disk_dir = wl_dir
                    present = (wl_dir / "trace_data.csv").exists()
                else:
                    disk_dir = wl_dir / f"i{it}"
                    present = (disk_dir / "trace_data.csv").exists()
                if present:
                    rows.append(
                        Row(
                            phase=phase,
                            workload=workload,
                            iteration=it,
                            status="PRESENT",
                            results_dir=str(disk_dir),
                        )
                    )
                elif shape == "legacy":
                    rows.append(
                        Row(
                            phase=phase,
                            workload=workload,
                            iteration=it,
                            status="MISSING",
                            note=f"no {disk_dir} directory",
                        )
                    )
                else:
                    rows.append(
                        Row(
                            phase=phase,
                            workload=workload,
                            iteration=it,
                            status="MISSING",
                            note=f"no results/i{it}/ directory",
                        )
                    )

    counts = {"PRESENT": 0, "MISSING": 0, "SKIP": 0}
    for r in rows:
        counts[r.status] = counts.get(r.status, 0) + 1
    exit_code = 1 if counts.get("MISSING", 0) > 0 else 0

    return EnumResult(
        run=run_name,
        shape=shape,
        declared_replicas=declared_replicas,
        divergence_warnings=divergence_warnings,
        malformed_iter_dir_count=malformed,
        rows=rows,
        counts=counts,
        exit_code=exit_code,
    )


def _detect_shape(
    results_dir: Path,
    phases: list[str],
    workloads: list[str],
) -> tuple[str, list[str], int]:
    """Detect run shape from disk. Returns (shape, warnings, malformed_count).

    shape:
        "replica" — every non-empty ``<phase>/<workload>/`` contains at
                    least one ``iN/`` subdir; no direct trace_data.csv
                    at the workload level.
        "legacy"  — every non-empty ``<phase>/<workload>/`` contains a
                    direct ``trace_data.csv`` and no ``iN/`` subdirs.
        "mixed"   — some workload dirs have ``iN/`` subdirs, others have
                    direct ``trace_data.csv``. Emit a divergence warning
                    and enumerate against the declared range for every
                    pair (legacy pairs then report MISSING for i2..iN).

    Malformed iN dir names (e.g. ``i0``, ``iabc``) are counted but do
    not contribute to shape detection.
    """
    warnings: list[str] = []
    malformed = 0
    has_iN: list[tuple[str, str]] = []
    has_direct: list[tuple[str, str]] = []

    if not results_dir.is_dir():
        return "replica", warnings, malformed

    for phase in phases:
        phase_dir = results_dir / phase
        if not phase_dir.is_dir():
            continue
        for workload in workloads:
            wl_dir = phase_dir / workload
            if not wl_dir.is_dir():
                continue
            iN_here = False
            for d in wl_dir.iterdir():
                if not d.is_dir():
                    continue
                if _ITER_DIR_RE.match(d.name):
                    iN_here = True
                elif d.name.startswith("i"):
                    # Looks like an iteration dir but doesn't match the
                    # grammar (e.g. i0, iabc, i01).
                    malformed += 1
            if iN_here:
                has_iN.append((phase, workload))
            if (wl_dir / "trace_data.csv").exists():
                has_direct.append((phase, workload))

    if has_iN and has_direct:
        warnings.append(
            f"mixed run shape detected: {len(has_iN)} pair(s) have iN/ "
            f"subdirs and {len(has_direct)} pair(s) have direct "
            f"trace_data.csv. Reporting what can be found; declared "
            f"iterations without an iN/ subdir are marked MISSING."
        )
        shape = "mixed"
    elif has_iN:
        shape = "replica"
    elif has_direct:
        shape = "legacy"
    else:
        # No pair has produced results yet — treat as replica shape so
        # MISSING semantics apply against the declared iteration range.
        shape = "replica"

    if malformed:
        warnings.append(
            f"{malformed} malformed iteration dir name(s) under "
            f"{results_dir} (expected 'iN' with N a positive decimal, no "
            f"leading zeros). Ignored for shape detection."
        )

    return shape, warnings, malformed


def _names(items) -> list[str]:
    """Extract ``name`` field from a list of manifest dicts."""
    if not isinstance(items, list):
        return []
    out = []
    for it in items:
        if isinstance(it, dict):
            name = it.get("name")
            if isinstance(name, str) and name:
                out.append(name)
    return out


def _int_or_default(v, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _read_json(path: Path) -> dict:
    if not path.exists():
        raise EnumError(f"{path} not found")
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise EnumError(f"{path} is missing/corrupt: {exc}") from exc


def _read_manifest(path: Path) -> dict:
    if not path.exists():
        # A run assembled by step-1 may not have manifest.assembly.yaml at
        # all — treat as empty declared set. Enumeration then produces
        # zero rows and exits 0.
        return {}
    try:
        import yaml  # PyYAML is a project runtime dep
    except ImportError as exc:  # pragma: no cover
        raise EnumError(
            f"PyYAML is required to read {path}: {exc}. "
            f"Install via 'pip install -r requirements.txt'."
        ) from exc
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise EnumError(f"{path} is missing/corrupt: {exc}") from exc
    if not isinstance(data, dict):
        raise EnumError(f"{path} is missing/corrupt: expected mapping")
    return data


def _row_to_dict(r: Row) -> dict:
    return {
        "phase": r.phase,
        "workload": r.workload,
        "iteration": r.iteration,
        "status": r.status,
        "results_dir": r.results_dir,
        "note": r.note,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run", required=True, help="workspace-registered run name"
    )
    parser.add_argument(
        "--experiment-root",
        default=".",
        help="experiment repo root (default: current working directory)",
    )
    args = parser.parse_args(argv)
    try:
        result = enumerate_run(Path(args.experiment_root), args.run)
    except EnumError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    for w in result.divergence_warnings:
        print(f"WARNING: {w}", file=sys.stderr)

    payload = {
        "run": result.run,
        "shape": result.shape,
        "declared_replicas": result.declared_replicas,
        "divergence_warnings": result.divergence_warnings,
        "malformed_iter_dir_count": result.malformed_iter_dir_count,
        "rows": [_row_to_dict(r) for r in result.rows],
        "counts": result.counts,
        "exit_code": result.exit_code,
    }
    print(json.dumps(payload, indent=2))
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())

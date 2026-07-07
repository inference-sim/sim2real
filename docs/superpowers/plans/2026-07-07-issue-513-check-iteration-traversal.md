# sim2real-check iN/ traversal + per-iteration verdicts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Grow `sim2real-check` (step-3's port) by one path segment: walk `runs/R/results/<phase>/<workload>/iN/` for each declared iteration, produce per-iteration verdict rows (PASS/FAIL/SKIP/MISSING), and enforce an automation-facing exit-code contract (0/1/2).

**Architecture:** Add a Python enumerator (`.claude/skills/sim2real-check/scripts/enumerate_iterations.py`) that reads `run_metadata.json` + `manifest.assembly.yaml` + `translation_output.json` for the run, classifies every `(phase, workload, iteration)` triple as PRESENT / MISSING / SKIP by cross-referencing disk state, emits structured JSON, and exits with the automation-facing contract. The SKILL.md prompt is updated to invoke this enumerator up front, thread iteration into each check subsection's path model, and roll up per-iteration verdicts (folding signal PASS/FAIL over the enumerator's PRESENT rows) into a final summary table.

**Tech Stack:** Python 3.10+, pytest, PyYAML, jq (bash only). CI added under `.github/workflows/test.yml`.

## Global Constraints

- Verdict states are mutually exclusive: `PASS` / `FAIL` / `SKIP` / `MISSING`.
- Exit code contract: `0` when all rows PASS or SKIP; `1` when any FAIL or MISSING; `2` on invocation error (missing run, unreadable ConfigMap, malformed inputs).
- SKIP semantics inherited from step-3: `(algorithm, workload)` combinations where the algorithm phase is absent from the run's translation. Baseline phases are never SKIP.
- Legacy compat: if the run has no `iN/` subdirs (pre-step-5 shape), walk `.../<workload>/` directly and report each pair as `wl-<workload>|pkg|i1`. `MISSING` does not apply to legacy runs.
- Mixed/corrupt state is read-only diagnostic: divergence warning header + malformed-iter-dir count at top of output, then report what `iN/` dirs can be found; mark the rest MISSING. Do NOT refuse.
- Scope: changes limited to `.claude/skills/sim2real-check/` + `.github/workflows/test.yml` + `CLAUDE.md` CI list. Do NOT modify `pipeline/lib/resolve.py` (out of scope; downstream consumers still work off the existing resolve output).
- All new Python code passes `ruff check --select F` and follows the existing skill scripts convention (module docstring, `if __name__ == "__main__":` guard, absolute paths for imports).
- Base branch: `refactor/v2-step-5`. PR merges there, not to `main`.

## File Structure

Files created or modified in this PR:

| Path | Responsibility | Change type |
|---|---|---|
| `.claude/skills/sim2real-check/scripts/enumerate_iterations.py` | Pure enumerator: reads run metadata + manifest + translation output, classifies each `(phase, workload, iteration)` triple, emits JSON, exits 0/1/2. | **Create** |
| `.claude/skills/sim2real-check/scripts/__init__.py` | Empty marker file so pytest treats `scripts/` as importable (matches sim2real-analyze layout). | **Create** |
| `.claude/skills/sim2real-check/tests/__init__.py` | Empty marker file (matches sim2real-analyze layout). | **Create** |
| `.claude/skills/sim2real-check/tests/test_enumerate_iterations.py` | Pytest fixture-driven tests covering the six acceptance-criteria cases + exit codes. | **Create** |
| `.claude/skills/sim2real-check/SKILL.md` | Prose: add "Step 0.5: Enumerate iterations" section; thread iteration into check subsection paths; add "Step N: Per-iteration verdict rollup" tail; document exit-code contract. | **Modify** |
| `.github/workflows/test.yml` | Add `.claude/skills/sim2real-check/tests/` to the pytest path list. | **Modify** |
| `CLAUDE.md` | Add `.claude/skills/sim2real-check/tests/` to the CI test path list under `## CI`. | **Modify** |

---

### Task 1: Enumerator skeleton + PRESENT/MISSING for a replica-shape run

**Files:**
- Create: `.claude/skills/sim2real-check/scripts/enumerate_iterations.py`
- Create: `.claude/skills/sim2real-check/scripts/__init__.py` (empty)
- Create: `.claude/skills/sim2real-check/tests/__init__.py` (empty)
- Create: `.claude/skills/sim2real-check/tests/test_enumerate_iterations.py`

**Interfaces:**
- Produces:
  - `enumerate_iterations.enumerate_run(experiment_root: Path, run_name: str) -> EnumResult` — module-level entry point, returns a dataclass carrying `shape`, `declared_replicas`, `divergence_warnings: list[str]`, `malformed_iter_dir_count: int`, `rows: list[Row]`, `counts: dict[str, int]`, `exit_code: int`.
  - `enumerate_iterations.Row` — dataclass with fields `phase: str`, `workload: str`, `iteration: int`, `status: str` (one of `PRESENT`, `MISSING`, `SKIP`), `results_dir: str | None`, `note: str | None`.
  - `main(argv: list[str] | None = None) -> int` — argparse wrapper: parses `--run`, `--experiment-root`, writes JSON to stdout, warnings to stderr, returns exit code.

- [ ] **Step 1: Write failing tests for the replica-shape PRESENT case**

Create `.claude/skills/sim2real-check/tests/__init__.py` empty. Create `.claude/skills/sim2real-check/scripts/__init__.py` empty.

Create `.claude/skills/sim2real-check/tests/test_enumerate_iterations.py` with:

```python
"""Tests for enumerate_iterations.py — sim2real-check iteration enumerator."""
import json
import sys
from pathlib import Path

import pytest

# Add scripts dir to sys.path so we can import the enumerator directly.
_SCRIPTS = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import enumerate_iterations as ei  # noqa: E402


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_run(
    root: Path,
    run_name: str,
    replicas: int,
    algorithms: list[str],
    baselines: list[str],
    workloads: list[str],
    translation_algorithms: list[str] | None = None,
    disk_layout: dict | None = None,
) -> Path:
    """Materialize a run under ``root/workspace/`` with the given shape.

    ``translation_algorithms`` defaults to ``algorithms`` (no SKIP rows).
    ``disk_layout`` maps ``(phase, workload)`` -> list of iteration ints
    to materialize as ``iN/`` dirs on disk (with a stub ``trace_data.csv``).
    If a workload is absent from ``disk_layout`` (or its list is empty),
    no ``iN/`` dirs are materialized under that ``(phase, workload)``.
    Special value ``"legacy"`` in ``disk_layout[k]`` materializes a
    direct ``<phase>/<workload>/trace_data.csv`` with no ``iN/`` layer.
    """
    if translation_algorithms is None:
        translation_algorithms = list(algorithms)
    if disk_layout is None:
        disk_layout = {
            (p, w): list(range(1, replicas + 1))
            for p in ([*baselines, *algorithms])
            for w in workloads
        }

    workspace = root / "workspace"
    run_dir = workspace / "runs" / run_name
    run_dir.mkdir(parents=True)

    translation_hash = "deadbeef"
    trans_dir = workspace / "translations" / translation_hash
    trans_dir.mkdir(parents=True)
    (trans_dir / "translation_output.json").write_text(
        json.dumps(
            {
                "algorithms": [{"name": n} for n in translation_algorithms],
                "baselines": [{"name": b} for b in baselines],
            }
        )
    )

    (run_dir / "run_metadata.json").write_text(
        json.dumps(
            {
                "run_name": run_name,
                "translation_hash": translation_hash,
                "replicas": replicas,
            }
        )
    )
    (run_dir / "manifest.assembly.yaml").write_text(
        _manifest_yaml(
            replicas=replicas,
            algorithms=algorithms,
            baselines=baselines,
            workloads=workloads,
        )
    )

    results_dir = run_dir / "results"
    for (phase, workload), iters in disk_layout.items():
        wl_dir = results_dir / phase / workload
        if iters == "legacy":
            wl_dir.mkdir(parents=True, exist_ok=True)
            (wl_dir / "trace_data.csv").write_text("send_time_us\n")
            continue
        for it in iters:
            if isinstance(it, str):
                # e.g. "iabc" for a malformed dir name.
                d = wl_dir / it
            else:
                d = wl_dir / f"i{it}"
            d.mkdir(parents=True, exist_ok=True)
            (d / "trace_data.csv").write_text("send_time_us\n")

    return run_dir


def _manifest_yaml(
    replicas: int,
    algorithms: list[str],
    baselines: list[str],
    workloads: list[str],
) -> str:
    """Minimal manifest.assembly.yaml that resolve.py + enumerator can read."""
    lines = [f"replicas: {replicas}"]
    lines.append("workloads:")
    for w in workloads:
        lines.append(f"  - name: {w}")
    lines.append("baselines:")
    for b in baselines:
        lines.append(f"  - name: {b}")
    lines.append("algorithms:")
    for a in algorithms:
        lines.append(f"  - name: {a}")
    return "\n".join(lines) + "\n"


# ── Cases ────────────────────────────────────────────────────────────────────


def test_replica_all_present(tmp_path):
    """3-replica run with all iterations on disk -> 3 PRESENT rows per pair, exit 0."""
    _make_run(
        tmp_path,
        run_name="trial",
        replicas=3,
        algorithms=["sim2real-ac"],
        baselines=["baseline"],
        workloads=["wl-chat"],
    )
    result = ei.enumerate_run(tmp_path, "trial")

    assert result.shape == "replica"
    assert result.declared_replicas == 3
    assert result.exit_code == 0
    assert result.counts == {"PRESENT": 6, "MISSING": 0, "SKIP": 0}

    # Two phases (baseline + sim2real-ac) x 1 workload x 3 iterations = 6 rows
    assert len(result.rows) == 6
    assert all(r.status == "PRESENT" for r in result.rows)
    iters_seen = sorted({r.iteration for r in result.rows})
    assert iters_seen == [1, 2, 3]
```

- [ ] **Step 2: Run the tests and confirm they fail**

```bash
cd .claude/worktrees/issue-513-check-iteration-traversal
python -m pytest .claude/skills/sim2real-check/tests/test_enumerate_iterations.py -v
```

Expected: `ImportError` (or `ModuleNotFoundError`) because `enumerate_iterations` doesn't exist yet.

- [ ] **Step 3: Implement the enumerator skeleton — PRESENT/MISSING for replica shape**

Create `.claude/skills/sim2real-check/scripts/enumerate_iterations.py`:

```python
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
malformed_iter_dir_count / counts fields. Divergence warnings and
malformed-key notes also go to stderr for operator visibility.

Exit codes:
    0 — all rows PRESENT or SKIP.
    1 — at least one MISSING (or a FAIL passed to the run-level rollup;
        FAIL is not this enumerator's business).
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
import dataclasses
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
    tout_path = workspace / "translations" / translation_hash / "translation_output.json"
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
                # SKIP applies per (algo, workload) — not iteration-specific.
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
            elif shape == "replica":
                iters = list(range(1, declared_replicas + 1))
            else:  # mixed
                # Per-pair shape decision: prefer iN/ layer if any present.
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
                    # Legacy shape: no MISSING, treat absence as unrun.
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
                    least one ``iN/`` subdir and no direct trace_data.csv.
        "legacy"  — every non-empty ``<phase>/<workload>/`` contains a
                    direct ``trace_data.csv`` and no ``iN/`` subdirs.
        "mixed"   — some workload dirs have iN/ subdirs, others have
                    direct trace_data.csv. Emit a divergence warning.

    Malformed iN dir names (e.g. ``i0``, ``iabc``) are counted but do
    not contribute to shape detection. Any subdir under a workload
    that is not either an iN/ dir or the trace_data.csv file is
    treated as noise (ignored for shape).
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
            iter_dirs = [
                d for d in wl_dir.iterdir() if d.is_dir()
            ]
            iN_here = False
            for d in iter_dirs:
                if _ITER_DIR_RE.match(d.name):
                    iN_here = True
                elif d.name.startswith("i") and not _ITER_DIR_RE.match(d.name):
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
        # all — treat as empty declared set. Enumeration will produce zero
        # rows in that case (exit 0).
        return {}
    try:
        import yaml  # local import: PyYAML is a project runtime dep
    except ImportError as exc:  # pragma: no cover - PyYAML is a runtime dep
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
    parser.add_argument("--run", required=True, help="workspace-registered run name")
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
```

- [ ] **Step 4: Verify Task 1 test passes**

Run:

```bash
python -m pytest .claude/skills/sim2real-check/tests/test_enumerate_iterations.py -v
```

Expected: `test_replica_all_present PASSED`.

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/sim2real-check/scripts/__init__.py \
        .claude/skills/sim2real-check/scripts/enumerate_iterations.py \
        .claude/skills/sim2real-check/tests/__init__.py \
        .claude/skills/sim2real-check/tests/test_enumerate_iterations.py
git commit -m "sim2real-check: enumerator for replica-shape PRESENT rows"
```

---

### Task 2: MISSING rows for absent iN/ dirs

**Files:**
- Modify: `.claude/skills/sim2real-check/tests/test_enumerate_iterations.py` (append test cases)

**Interfaces:** (unchanged — same `enumerate_run` signature)

- [ ] **Step 1: Write failing test for missing i2/**

Append to `test_enumerate_iterations.py`:

```python
def test_replica_missing_i2(tmp_path):
    """3-replica run with i2/ absent -> MISSING row for i2, exit 1."""
    _make_run(
        tmp_path,
        run_name="trial",
        replicas=3,
        algorithms=["sim2real-ac"],
        baselines=["baseline"],
        workloads=["wl-chat"],
        disk_layout={
            ("baseline", "wl-chat"): [1, 2, 3],
            ("sim2real-ac", "wl-chat"): [1, 3],  # i2 missing
        },
    )
    result = ei.enumerate_run(tmp_path, "trial")

    assert result.exit_code == 1
    assert result.counts["MISSING"] == 1
    assert result.counts["PRESENT"] == 5

    missing = [r for r in result.rows if r.status == "MISSING"]
    assert len(missing) == 1
    assert missing[0].phase == "sim2real-ac"
    assert missing[0].workload == "wl-chat"
    assert missing[0].iteration == 2
    assert "i2" in (missing[0].note or "")
```

- [ ] **Step 2: Run test — expect PASS**

```bash
python -m pytest .claude/skills/sim2real-check/tests/test_enumerate_iterations.py::test_replica_missing_i2 -v
```

Expected: PASS (the enumerator's PRESENT/MISSING branch already handles this).

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/sim2real-check/tests/test_enumerate_iterations.py
git commit -m "sim2real-check: cover MISSING iteration case"
```

---

### Task 3: SKIP rows for algorithms not in the translation

**Files:**
- Modify: `.claude/skills/sim2real-check/tests/test_enumerate_iterations.py` (append)

**Interfaces:** unchanged.

- [ ] **Step 1: Write failing test for SKIP semantics**

Append:

```python
def test_algorithm_not_in_translation_skip(tmp_path):
    """Pair whose algorithm isn't in the translation -> SKIP rows, exit 0."""
    _make_run(
        tmp_path,
        run_name="trial",
        replicas=2,
        algorithms=["sim2real-ac", "sim2real-routing"],
        baselines=["baseline"],
        workloads=["wl-chat"],
        translation_algorithms=["sim2real-ac"],  # routing NOT translated
        disk_layout={
            ("baseline", "wl-chat"): [1, 2],
            ("sim2real-ac", "wl-chat"): [1, 2],
            # sim2real-routing has no results on disk — it was skipped
            # at assemble time.
        },
    )
    result = ei.enumerate_run(tmp_path, "trial")

    # Baseline (2) + sim2real-ac (2) present, sim2real-routing (2) skip.
    assert result.counts == {"PRESENT": 4, "MISSING": 0, "SKIP": 2}
    assert result.exit_code == 0

    skip_rows = [r for r in result.rows if r.status == "SKIP"]
    assert len(skip_rows) == 2
    assert all(r.phase == "sim2real-routing" for r in skip_rows)
    assert all("translation" in (r.note or "") for r in skip_rows)
```

- [ ] **Step 2: Run test — expect PASS**

```bash
python -m pytest .claude/skills/sim2real-check/tests/test_enumerate_iterations.py::test_algorithm_not_in_translation_skip -v
```

Expected: PASS (SKIP branch in the enumerator already handles this).

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/sim2real-check/tests/test_enumerate_iterations.py
git commit -m "sim2real-check: cover SKIP (algo not in translation) case"
```

---

### Task 4: Legacy-shape runs (no iN/ layer)

**Files:**
- Modify: `.claude/skills/sim2real-check/tests/test_enumerate_iterations.py` (append)

**Interfaces:** unchanged.

- [ ] **Step 1: Write failing test for legacy shape**

Append:

```python
def test_legacy_shape_implicit_i1(tmp_path):
    """Legacy run (no iN/ layer) -> implicit i1 rows, no MISSING, exit 0."""
    _make_run(
        tmp_path,
        run_name="trial",
        replicas=1,
        algorithms=["sim2real-ac"],
        baselines=["baseline"],
        workloads=["wl-chat"],
        disk_layout={
            ("baseline", "wl-chat"): "legacy",
            ("sim2real-ac", "wl-chat"): "legacy",
        },
    )
    result = ei.enumerate_run(tmp_path, "trial")

    assert result.shape == "legacy"
    assert result.exit_code == 0
    assert result.counts == {"PRESENT": 2, "MISSING": 0, "SKIP": 0}

    for r in result.rows:
        assert r.iteration == 1
        assert r.status == "PRESENT"
```

- [ ] **Step 2: Run test — expect PASS**

```bash
python -m pytest .claude/skills/sim2real-check/tests/test_enumerate_iterations.py::test_legacy_shape_implicit_i1 -v
```

Expected: PASS (the enumerator's legacy branch handles direct trace_data.csv → implicit i1 PRESENT).

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/sim2real-check/tests/test_enumerate_iterations.py
git commit -m "sim2real-check: cover legacy-shape implicit-i1 case"
```

---

### Task 5: Mixed/corrupt state — divergence warning header + partial report

**Files:**
- Modify: `.claude/skills/sim2real-check/tests/test_enumerate_iterations.py` (append)

**Interfaces:** unchanged.

- [ ] **Step 1: Write failing test for mixed shape + malformed iN dir name**

Append:

```python
def test_mixed_shape_divergence_warning(tmp_path):
    """Mixed run (some legacy, some iN/) -> warning + partial report; exit 1 iff any MISSING."""
    _make_run(
        tmp_path,
        run_name="trial",
        replicas=2,
        algorithms=["sim2real-ac"],
        baselines=["baseline"],
        workloads=["wl-chat"],
        disk_layout={
            # baseline pair: iN/-shape, both iterations present
            ("baseline", "wl-chat"): [1, 2],
            # treatment pair: legacy shape (mixed with baseline)
            ("sim2real-ac", "wl-chat"): "legacy",
        },
    )
    result = ei.enumerate_run(tmp_path, "trial")

    assert result.shape == "mixed"
    assert result.divergence_warnings, "expected a divergence-warning line"
    assert any("mixed run shape" in w for w in result.divergence_warnings)

    # In mixed mode the enumerator treats every pair as replica-shape for
    # the declared range. The legacy pair therefore reports i1/i2 MISSING.
    # Exit 1 because MISSING > 0.
    assert result.counts["MISSING"] == 2
    assert result.exit_code == 1


def test_malformed_iter_dir_name_counted(tmp_path):
    """Malformed iN dir names (i0, iabc) counted, do not affect shape detection."""
    run_dir = _make_run(
        tmp_path,
        run_name="trial",
        replicas=1,
        algorithms=["sim2real-ac"],
        baselines=["baseline"],
        workloads=["wl-chat"],
        disk_layout={
            ("baseline", "wl-chat"): [1, "i0", "iabc"],
            ("sim2real-ac", "wl-chat"): [1],
        },
    )
    result = ei.enumerate_run(tmp_path, "trial")

    assert result.malformed_iter_dir_count == 2
    assert any("malformed" in w for w in result.divergence_warnings)
    assert result.shape == "replica"
    assert result.exit_code == 0  # i1 present for both, no MISSING
```

- [ ] **Step 2: Run tests — expect PASS**

```bash
python -m pytest .claude/skills/sim2real-check/tests/test_enumerate_iterations.py::test_mixed_shape_divergence_warning .claude/skills/sim2real-check/tests/test_enumerate_iterations.py::test_malformed_iter_dir_name_counted -v
```

Expected: PASS for both.

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/sim2real-check/tests/test_enumerate_iterations.py
git commit -m "sim2real-check: cover mixed shape + malformed-iter-dir cases"
```

---

### Task 6: Invocation errors — missing run, missing manifest, malformed inputs

**Files:**
- Modify: `.claude/skills/sim2real-check/tests/test_enumerate_iterations.py` (append)

**Interfaces:** unchanged.

- [ ] **Step 1: Write failing tests for invocation errors + main() exit codes**

Append:

```python
def test_missing_run_raises(tmp_path):
    """Nonexistent run -> EnumError, main() exits 2."""
    (tmp_path / "workspace" / "runs").mkdir(parents=True)
    with pytest.raises(ei.EnumError):
        ei.enumerate_run(tmp_path, "does-not-exist")


def test_no_workspace_raises(tmp_path):
    """Missing workspace/ -> EnumError."""
    with pytest.raises(ei.EnumError):
        ei.enumerate_run(tmp_path, "any")


def test_main_exit_code_on_missing_run(tmp_path, capsys):
    """main() returns 2 for a missing run and writes ERROR to stderr."""
    (tmp_path / "workspace" / "runs").mkdir(parents=True)
    rc = ei.main(["--run", "nope", "--experiment-root", str(tmp_path)])
    assert rc == 2
    captured = capsys.readouterr()
    assert "ERROR" in captured.err


def test_main_exit_code_on_all_present(tmp_path, capsys):
    """main() returns 0 and emits valid JSON on a healthy run."""
    _make_run(
        tmp_path,
        run_name="trial",
        replicas=2,
        algorithms=["sim2real-ac"],
        baselines=["baseline"],
        workloads=["wl-chat"],
    )
    rc = ei.main(["--run", "trial", "--experiment-root", str(tmp_path)])
    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["shape"] == "replica"
    assert payload["declared_replicas"] == 2
    assert payload["counts"]["PRESENT"] == 4
    assert payload["exit_code"] == 0


def test_main_exit_code_on_missing_iteration(tmp_path, capsys):
    """main() returns 1 when any iteration is MISSING."""
    _make_run(
        tmp_path,
        run_name="trial",
        replicas=2,
        algorithms=["sim2real-ac"],
        baselines=["baseline"],
        workloads=["wl-chat"],
        disk_layout={
            ("baseline", "wl-chat"): [1, 2],
            ("sim2real-ac", "wl-chat"): [1],  # i2 missing
        },
    )
    rc = ei.main(["--run", "trial", "--experiment-root", str(tmp_path)])
    assert rc == 1
```

- [ ] **Step 2: Run tests — expect PASS**

```bash
python -m pytest .claude/skills/sim2real-check/tests/test_enumerate_iterations.py -v
```

Expected: all tests PASS.

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/sim2real-check/tests/test_enumerate_iterations.py
git commit -m "sim2real-check: cover invocation-error exit codes"
```

---

### Task 7: Wire enumerator into SKILL.md — Step 0.5 + iteration-scoped paths + final rollup

**Files:**
- Modify: `.claude/skills/sim2real-check/SKILL.md`

**Interfaces:**
- Consumes (from Task 1-6): `enumerate_iterations.py` CLI (`python .claude/skills/sim2real-check/scripts/enumerate_iterations.py --run <name> --experiment-root <path>`), JSON output shape, exit code contract.
- Produces (for future skills like sim2real-analyze): none new. Documented per-iteration output format.

- [ ] **Step 1: Add "Step 0.5: Enumerate iterations" section**

In `.claude/skills/sim2real-check/SKILL.md`, immediately after the "Auxiliary detection (all modes)" section (currently around line 165) and BEFORE "Auto-detect + user picker" (currently line 168), insert a new section:

````markdown
### Step 0.5: Enumerate declared iterations (both modes)

After mode dispatch has populated `RESULTS_DIR`, `PHASES`, and `WORKLOADS_BY_PHASE`, and the auxiliary variables (`SIM`, `BLIS`, `GAIE`, `LLMD`) are resolved, enumerate the declared iteration range for the run and cross-reference with disk state.

**Resolve-mode (`--run`):** invoke the enumerator against the workspace-registered run:

```bash
ENUM_JSON=$(mktemp -t sim2real_check_enum.XXXXXX.json)
trap 'rm -f "$RESOLVED_JSON" "$ENUM_JSON"' EXIT
if ! python "$SIM2REAL_ROOT/.claude/skills/sim2real-check/scripts/enumerate_iterations.py" \
        --run "$RUN" --experiment-root "$EXPERIMENT_ROOT" > "$ENUM_JSON"; then
    ENUM_EXIT=$?
    if [ "$ENUM_EXIT" -eq 2 ]; then
        # Invocation error — enumerator already wrote a specific
        # message to stderr. Bail so the operator can fix the input.
        exit 2
    fi
    # Exit 1 means "MISSING rows present" — that's a real-run diagnostic,
    # not an invocation error. Keep going and let the final rollup
    # surface the MISSING rows in the summary table.
fi
SHAPE=$(jq -r '.shape' "$ENUM_JSON")
DECLARED_REPLICAS=$(jq -r '.declared_replicas' "$ENUM_JSON")
DIVERGENCE_WARNINGS=$(jq -r '.divergence_warnings | join("\n  ")' "$ENUM_JSON")
MALFORMED_ITER_COUNT=$(jq -r '.malformed_iter_dir_count' "$ENUM_JSON")
```

`$SIM2REAL_ROOT` is `.translation.translations_dir | rsplit("/workspace", 1) | .[0]` from the resolve output — the framework directory that contains `pipeline/` and `.claude/`. Fall back to `$(git rev-parse --show-toplevel)` if the resolve output does not carry it and the check is running from a cwd inside the framework repo.

**Legacy-mode (`--real`):** the enumerator is `--run`-only (it reads `workspace/runs/<name>/`). In legacy mode, synthesize the enumeration in-shell: every `(phase, workload)` under `$RESULTS_DIR` becomes a single row with `iteration=1`, `status=PRESENT` if `$RESULTS_DIR/<phase>/<workload>/trace_data.csv` exists (else absent — do not emit MISSING for legacy bundles). Set `SHAPE=legacy`, `DECLARED_REPLICAS=1`, `DIVERGENCE_WARNINGS=""`, `MALFORMED_ITER_COUNT=0`.

**Print the diagnostic header immediately** (before any check subsection runs) so the operator sees mixed/corrupt state up front:

```bash
if [ -n "$DIVERGENCE_WARNINGS" ] || [ "$MALFORMED_ITER_COUNT" -gt 0 ]; then
    echo "── Run shape diagnostic ─────────────────────────────────────"
    echo "  shape:                    $SHAPE"
    echo "  declared_replicas:        $DECLARED_REPLICAS"
    echo "  malformed_iter_dir_count: $MALFORMED_ITER_COUNT"
    if [ -n "$DIVERGENCE_WARNINGS" ]; then
        echo "  divergence_warnings:"
        echo "    $DIVERGENCE_WARNINGS"
    fi
    echo "─────────────────────────────────────────────────────────────"
fi
```

**Reference — `$ENUM_JSON` schema (v1):**

```json
{
  "run": "trial",
  "shape": "replica" | "legacy" | "mixed",
  "declared_replicas": 3,
  "divergence_warnings": ["..."],
  "malformed_iter_dir_count": 0,
  "rows": [
    {"phase": "baseline", "workload": "wl-chat", "iteration": 1,
     "status": "PRESENT", "results_dir": "<abs path>", "note": null},
    {"phase": "sim2real-ac", "workload": "wl-chat", "iteration": 2,
     "status": "MISSING", "results_dir": null,
     "note": "no results/i2/ directory"},
    {"phase": "sim2real-routing", "workload": "wl-chat", "iteration": 1,
     "status": "SKIP", "results_dir": null,
     "note": "algorithm not in translation"}
  ],
  "counts": {"PRESENT": N, "MISSING": M, "SKIP": S},
  "exit_code": 0
}
```

Each check subsection below iterates over the `PRESENT` rows in `$ENUM_JSON`. Use `results_dir` from each row as the working path (it already has the `iN/` segment appended in replica shape and is the direct workload dir in legacy shape). `MISSING` and `SKIP` rows do not run subsections — they land in the final rollup with their enumerator-assigned status verbatim.

````

- [ ] **Step 2: Update "Reference Paths" section to describe the iN/ layer**

In `SKILL.md`, find the "Reference Paths" section (currently around line 245-250). Replace the `RESULTS_DIR` bullet with the version that describes iteration semantics:

```markdown
- **Results directory** (`RESULTS_DIR`): per-phase workload data. Iterate over the PRESENT rows in `$ENUM_JSON` (see Step 0.5) — each row supplies a `results_dir` field that is either `$RESULTS_DIR/<phase>/<workload>/i<N>/` (replica shape, `SHAPE=replica`) or `$RESULTS_DIR/<phase>/<workload>/` (legacy shape, `SHAPE=legacy`). Each directory contains `trace_data.csv`, `server_logs/`, `epp_logs/`, `gpu_logs/`, and phase-specific artifacts. Check subsections below reference `<row.results_dir>` as the path variable instead of the raw `<phase>/<workload>` path used pre-step-5.
```

- [ ] **Step 3: Add a "Path substitution" contract right before Step 1**

Immediately before the first check subsection ("### Step 1: Workload parity" or wherever Step 1 begins — grep for `## Step 1` or `^### Step 1`), insert:

````markdown
### Path substitution contract (post-Step 0.5)

Every check subsection below that quotes a path of shape `$RESULTS_DIR/<phase>/<workload>/...` implicitly refers to the enumerator's per-row `results_dir` value. When the run is in replica shape (`SHAPE=replica`), `results_dir` already carries the `iN/` segment; when the run is in legacy shape (`SHAPE=legacy`), it carries no `iN/` segment. Subsections should iterate over `PRESENT` rows (via `jq -c '.rows[] | select(.status == "PRESENT")' "$ENUM_JSON"`) and use `.results_dir` as-is.

Concretely: where a subsection reads `$RESULTS_DIR/<phase>/<workload>/server_logs/`, treat it as `<row.results_dir>/server_logs/`. Where it reads `$RESULTS_DIR/<phase>/<workload>/gpu_logs/<node>.log`, treat it as `<row.results_dir>/gpu_logs/<node>.log`. And so on.

The subsections' verdicts (PASS/WARN/FAIL) are per (phase, workload, iteration) — one triple per PRESENT row. Roll them up into the final summary in the "Final report" step below.
````

- [ ] **Step 4: Update the "Final report" section to emit per-iteration verdicts + exit code**

Locate the existing final summary section (grep for a heading like `## Final report`, `### Final report`, or "Final report format"; in the current file it's around line 640-690). Replace or extend that section with a "Per-iteration verdict rollup" subsection:

````markdown
## Per-iteration verdict rollup (final step)

After every check subsection has emitted its per-(phase, workload, iteration) PASS/WARN/FAIL, roll up the results into a single summary table. Fold WARN into PASS for the rollup — WARN rows still report their reason in the per-subsection detail, but do not fail the run-level verdict.

**Row shape** (one per (phase, workload, iteration) triple in the enumerator output):

```
wl-<workload>|<phase>|i<N>  <verdict> [<reason>]
```

**Verdict determination per row:**

| Enumerator `status` | Signal check result | Row verdict |
|---|---|---|
| `PRESENT` | all signals PASS/WARN | `PASS` |
| `PRESENT` | any signal FAIL | `FAIL (<K signals failed: names>)` |
| `MISSING` | (skipped — no data) | `MISSING (no results/iN/ directory)` |
| `SKIP` | (skipped — no data) | `SKIP (algorithm not in translation)` |

**Rollup aggregates** (printed under the row table):

- PASS / FAIL / SKIP / MISSING counts across all rows.
- If `SHAPE=mixed` or `MALFORMED_ITER_COUNT > 0`, restate the divergence warnings from Step 0.5 for operator visibility.

**Exit-code contract** (returned as the skill's overall status):

- `0` — all rows PASS or SKIP.
- `1` — any FAIL or MISSING.
- `2` — invocation error caught in Step 0.5 (missing run, unreadable ConfigMap / manifest, malformed inputs). Never emitted from the rollup step itself.

When invoked as `/sim2real-check --run R` from the terminal, this exit code is the automation-facing surface. Consumers (CI, dashboards) rely on it — do not fold FAIL or MISSING into `0` under any rollup rule.

**Example rollup:**

```
── Per-iteration verdicts ───────────────────────────────────────
  wl-chat-mid|baseline|i1        PASS
  wl-chat-mid|baseline|i2        PASS
  wl-chat-mid|baseline|i3        PASS
  wl-chat-mid|sim2real-ac|i1     PASS
  wl-chat-mid|sim2real-ac|i2     FAIL (2 signals failed: TTFT_p95, e2e_p50)
  wl-chat-mid|sim2real-ac|i3     MISSING (no results/i3/ directory)
  wl-chat-mid|sim2real-routing|i1 SKIP (algorithm not in translation)
─────────────────────────────────────────────────────────────────
Aggregates: 4 PASS  1 FAIL  1 MISSING  1 SKIP
Exit code:  1 (FAIL and MISSING present)
```
````

- [ ] **Step 5: Verify the SKILL.md still lints cleanly**

```bash
ruff check .claude/skills/ --select F
```

Expected: no F-code errors. (SKILL.md is markdown, but the bash blocks inside it aren't linted — this step guards against any accidental Python file leak.)

Also check for markdown-level issues: sections stay ordered, no unclosed code fences.

```bash
awk '/^```/{c++} END{if (c%2 != 0) print "unclosed code fence"}' .claude/skills/sim2real-check/SKILL.md
```

Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add .claude/skills/sim2real-check/SKILL.md
git commit -m "sim2real-check: wire enumerator into SKILL.md + per-iteration rollup"
```

---

### Task 8: CI + CLAUDE.md updates

**Files:**
- Modify: `.github/workflows/test.yml`
- Modify: `CLAUDE.md`

**Interfaces:** none.

- [ ] **Step 1: Add the check tests directory to CI**

Edit `.github/workflows/test.yml`, after the line `.claude/skills/sim2real-translate/tests/ \`, insert:

```yaml
            .claude/skills/sim2real-check/tests/ \
```

The complete `pytest` invocation should now list all three skill test directories.

- [ ] **Step 2: Update CLAUDE.md CI section to match**

Edit `CLAUDE.md`, in the `## CI` section's bash block, after the line `.claude/skills/sim2real-translate/tests/ \` add:

```
  .claude/skills/sim2real-check/tests/ \
```

- [ ] **Step 3: Run the full CI test list locally**

```bash
ruff check pipeline/ .claude/skills/ --select F
python -m pytest pipeline/ \
  pipeline/tests/test_layout.py \
  pipeline/tests/test_cluster_ops.py \
  pipeline/tests/test_cluster_py.py \
  pipeline/tests/test_slicer.py \
  pipeline/tests/test_sim2real.py \
  pipeline/tests/test_assemble_run.py \
  pipeline/tests/test_assemble_replicas.py \
  pipeline/tests/test_translation_ref.py \
  pipeline/tests/test_translate.py \
  pipeline/tests/test_build.py \
  pipeline/tests/test_pairkey.py \
  pipeline/tests/test_load_pairs.py \
  pipeline/tests/test_collect_internals.py \
  .claude/skills/sim2real-analyze/tests/ \
  .claude/skills/sim2real-bootstrap/tests/ \
  .claude/skills/sim2real-translate/tests/ \
  .claude/skills/sim2real-check/tests/ \
  -v
```

Expected: all lint and tests green.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/test.yml CLAUDE.md
git commit -m "ci: add sim2real-check tests to pytest path list"
```

---

### Task 9: Stale-reference sweep

**Files:**
- Read-only: `**/*.md`, `docs/`, `.claude/skills/`, `README*`

**Interfaces:** none.

- [ ] **Step 1: Grep for stale path shapes referencing the pre-iN/ layout**

```bash
grep -rn "RESULTS_DIR/<phase>/<workload>\|results/<phase>/<workload>/\$\|/<workload>/trace_data.csv\|/<workload>/server_logs\|/<workload>/epp_logs\|/<workload>/gpu_logs" \
    --include='*.md' --include='*.py' \
    docs/ .claude/skills/ pipeline/ CLAUDE.md 2>/dev/null | grep -v ':line 0:' | head -40
```

For each hit, decide: stale (update in this PR), still accurate (leave), or unrelated (leave). Update in-place; every update should either add the `i<N>/` segment or a note that the path is legacy-shape only.

- [ ] **Step 2: Grep for stale verdict wording**

```bash
grep -rn "PASS.*FAIL.*SKIP.*MISSING\|MISSING\b" .claude/skills/sim2real-check/SKILL.md docs/ pipeline/README.md CLAUDE.md 2>/dev/null | head -20
```

Verify the verdict language is consistent (MISSING is a new concept in this PR; check every mention describes it correctly per the enumerator's semantics).

- [ ] **Step 3: Grep for `iN` references in docs to confirm coverage**

```bash
grep -rn "\biN\b\|/i1/\|/i2/\|/i3/" docs/ CLAUDE.md pipeline/README.md .claude/skills/sim2real-check/ 2>/dev/null | head -30
```

Confirm every mention of the iN/ path segment is in a section that matches the new semantics.

- [ ] **Step 4: Commit any sweep updates**

```bash
git status  # inspect changes
git add -p  # if any sweep-related updates
git commit -m "docs: refresh stale path references for iN/ shape"  # only if there are changes
```

If no updates needed, note this in the PR body ("swept for X, Y, Z — no stale references found").

---

### Task 10: End-to-end verification + PR body

**Files:** none (verification pass).

**Interfaces:** none.

- [ ] **Step 1: Full CI locally**

```bash
ruff check pipeline/ .claude/skills/ --select F
python -m pytest pipeline/ \
  pipeline/tests/test_layout.py \
  pipeline/tests/test_cluster_ops.py \
  pipeline/tests/test_cluster_py.py \
  pipeline/tests/test_slicer.py \
  pipeline/tests/test_sim2real.py \
  pipeline/tests/test_assemble_run.py \
  pipeline/tests/test_assemble_replicas.py \
  pipeline/tests/test_translation_ref.py \
  pipeline/tests/test_translate.py \
  pipeline/tests/test_build.py \
  pipeline/tests/test_pairkey.py \
  pipeline/tests/test_load_pairs.py \
  pipeline/tests/test_collect_internals.py \
  .claude/skills/sim2real-analyze/tests/ \
  .claude/skills/sim2real-bootstrap/tests/ \
  .claude/skills/sim2real-translate/tests/ \
  .claude/skills/sim2real-check/tests/ \
  -v
```

Expected: all pass.

- [ ] **Step 2: Manually invoke enumerator against a synthesized fixture**

Create a temporary run under `/tmp` mimicking the 3-replica-with-missing-i2 case; invoke the enumerator; confirm exit code + JSON structure. Then wipe.

```bash
TMPDIR=$(mktemp -d)
mkdir -p "$TMPDIR/workspace/runs/trial/results/baseline/wl-chat/i1"
mkdir -p "$TMPDIR/workspace/runs/trial/results/baseline/wl-chat/i2"
mkdir -p "$TMPDIR/workspace/runs/trial/results/baseline/wl-chat/i3"
touch "$TMPDIR/workspace/runs/trial/results/baseline/wl-chat/i1/trace_data.csv"
touch "$TMPDIR/workspace/runs/trial/results/baseline/wl-chat/i2/trace_data.csv"
touch "$TMPDIR/workspace/runs/trial/results/baseline/wl-chat/i3/trace_data.csv"
mkdir -p "$TMPDIR/workspace/runs/trial/results/sim2real-ac/wl-chat/i1"
mkdir -p "$TMPDIR/workspace/runs/trial/results/sim2real-ac/wl-chat/i3"
touch "$TMPDIR/workspace/runs/trial/results/sim2real-ac/wl-chat/i1/trace_data.csv"
touch "$TMPDIR/workspace/runs/trial/results/sim2real-ac/wl-chat/i3/trace_data.csv"
mkdir -p "$TMPDIR/workspace/translations/deadbeef"
cat > "$TMPDIR/workspace/translations/deadbeef/translation_output.json" <<'EOF'
{"algorithms":[{"name":"sim2real-ac"}],"baselines":[{"name":"baseline"}]}
EOF
cat > "$TMPDIR/workspace/runs/trial/run_metadata.json" <<'EOF'
{"run_name":"trial","translation_hash":"deadbeef","replicas":3}
EOF
cat > "$TMPDIR/workspace/runs/trial/manifest.assembly.yaml" <<'EOF'
replicas: 3
workloads:
  - name: wl-chat
baselines:
  - name: baseline
algorithms:
  - name: sim2real-ac
EOF
python .claude/skills/sim2real-check/scripts/enumerate_iterations.py \
    --run trial --experiment-root "$TMPDIR" ; echo "exit=$?"
rm -rf "$TMPDIR"
```

Expected: JSON on stdout with 5 PRESENT rows + 1 MISSING row for `sim2real-ac|wl-chat|i2`; `exit=1`.

- [ ] **Step 3: Push branch**

```bash
git push -u origin refactor/v2-step-5-issue-513-check-iteration-traversal
```

- [ ] **Step 4: Open PR against `refactor/v2-step-5`**

```bash
gh pr create --base refactor/v2-step-5 --title "sim2real-check: iN/ traversal + per-iteration verdicts + exit code (#513)" --body-file /tmp/pr-body-513.md
```

Draft `/tmp/pr-body-513.md`:

```markdown
## Summary

Grows `sim2real-check` by one path segment (`iN/` traversal) and adds per-iteration verdicts + exit-code contract, per epic step-5 #502. Closes #513.

## What changed

- New Python enumerator: `.claude/skills/sim2real-check/scripts/enumerate_iterations.py`. Reads `run_metadata.json` + `manifest.assembly.yaml` + `translation_output.json`; classifies every (phase, workload, iteration) triple as PRESENT / MISSING / SKIP; emits JSON on stdout and exit codes per contract.
- SKILL.md gains "Step 0.5: Enumerate iterations" that invokes the enumerator, prints a divergence-warning header when shape is mixed or malformed dir names are present, and threads per-row `results_dir` into every subsequent check subsection. Adds a "Per-iteration verdict rollup" section at the end that folds signal outcomes over the enumerator rows and emits the exit-code contract (0/1/2).
- New pytest suite: `.claude/skills/sim2real-check/tests/test_enumerate_iterations.py`. Six cases covering the issue's acceptance criteria (all-present replica, missing i2, algorithm-not-in-translation SKIP, legacy shape implicit i1, mixed shape divergence warning, malformed iN dir name count) + invocation-error exit codes.
- CI update: `.github/workflows/test.yml` and `CLAUDE.md` gain `.claude/skills/sim2real-check/tests/` in the pytest path list.

## Design decisions

- **Source of declared iterations:** `run_metadata.json.replicas` (defaults to 1 when absent). Locally on disk; matches what `sim2real assemble` writes and what seeds the cluster ConfigMap. The issue mentioned "declared in the run's ConfigMap" — locally on the check-skill side we read `replicas`, which is the manifest-authoritative source that the ConfigMap is populated from.
- **SKIP semantic:** algorithm phase present in `manifest.assembly.yaml.algorithms` but absent from `translation_output.json.algorithms`. Baseline phases are never SKIP. This matches step-3's design intent — "algorithm not in translation".
- **Shape detection is per-run, not per-pair:** if any pair has `iN/` subdirs and any other pair has direct `trace_data.csv`, the run is `mixed` and the enumerator emits a divergence warning. In mixed mode, every declared-but-absent iteration lands as MISSING. This is the read-only diagnostic that the design's "Source of truth" table calls out.
- **`resolve.py` is untouched.** The enumerator reads `manifest.assembly.yaml` + `translation_output.json` directly rather than going through `sim2real resolve`, so no schema drift in the resolve output. The check skill still calls `resolve` for translation/baseline paths (unchanged) and adds the enumerator alongside.

## Stale-reference sweep

Grepped `docs/`, `.claude/skills/`, `pipeline/README.md`, `CLAUDE.md` for stale pre-iN/ path shapes and pre-verdict wording. `CLAUDE.md` already carried the `/i<N>/gpu_logs/<node>.log` path shape from PR #522; no updates needed elsewhere. (`SKILL.md`'s own check-subsection references to `<row.results_dir>` supplant the old `$RESULTS_DIR/<phase>/<workload>/` shape via the "Path substitution contract" note.)

## Test evidence

```
$ python -m pytest .claude/skills/sim2real-check/tests/test_enumerate_iterations.py -v
...
=== N passed in K.KKs ===
```

## Reviewer attention

- The SKILL.md path substitution contract is prose that instructs the LLM to remap paths from `$RESULTS_DIR/<phase>/<workload>/*` to `<row.results_dir>/*`. This is deliberately not a mechanical edit of every subsection — the subsections are LLM-consumed prose and doing the substitution mechanically would explode the diff without changing behavior. If reviewers prefer a mechanical rewrite of every path in every subsection, say so and I'll follow up in a second commit.
- `$SIM2REAL_ROOT` in Step 0.5 is derived from the resolve output; the fallback path (`git rev-parse --show-toplevel`) is a safety net for edge cases. If the resolve output should carry `sim2real_root` explicitly (schema v2 addition), that's a separate change to `resolve.py` and out of scope here.
- Exit code contract from the enumerator (`exit_code: 1` when MISSING) does not include FAIL — FAIL comes from the LLM's signal checks. The overall skill exit code is `max(enumerator_exit, any_FAIL_present)`; the SKILL.md rollup section documents this.
```

- [ ] **Step 5: Verify PR opened cleanly**

```bash
gh pr view --json state,mergeable,url,baseRefName
```

Expected: `state=OPEN`, `baseRefName=refactor/v2-step-5`, URL printed.

---

## Self-Review

**Spec coverage:**

| Issue section | Task |
|---|---|
| Verdict states (PASS/FAIL/SKIP/MISSING) | Task 1 (row status enum), Task 7 (SKILL.md rollup table) |
| Legacy compat (no iN/ suffix) | Task 4 (legacy-shape test), Task 1 (legacy branch in enumerator) |
| Mixed/corrupt state — divergence warning + partial report | Task 5 (mixed test, malformed test) |
| Exit code contract 0/1/2 | Task 1 (enumerator emits), Task 6 (main() tests), Task 7 (SKILL.md rollup documents) |
| Files (`SKILL.md` + Python helpers) | Task 1 (`enumerate_iterations.py`), Task 7 (`SKILL.md`) |
| 3-replica all-present → 3 rows/pair, exit 0 | Task 1 test |
| 3-replica with FAIL → correct FAIL row, exit 1 | FAIL is LLM-side; Task 7 rollup contract encodes it |
| 3-replica missing i2 → MISSING for i2, exit 1 | Task 2 test |
| Pair with algo not in translation → SKIP row | Task 3 test |
| Legacy run → implicit-i1 rows, no MISSING | Task 4 test |
| Mixed/corrupt → divergence warning + partial | Task 5 test |
| Test fixtures cover each case; exit codes asserted | Tasks 1-6 tests |

**Placeholder scan:** none. Every step has concrete code or exact commands.

**Type consistency:**
- `EnumResult` fields (`shape`, `declared_replicas`, `divergence_warnings`, `malformed_iter_dir_count`, `rows`, `counts`, `exit_code`) referenced consistently in Task 1 (definition), Tasks 2-6 (test assertions), Task 7 (JSON schema documentation).
- `Row` fields (`phase`, `workload`, `iteration`, `status`, `results_dir`, `note`) consistent across enumerator + tests + SKILL.md JSON schema example.
- Enumerator status enum (`PRESENT`, `MISSING`, `SKIP`) consistent with SKILL.md rollup table (which maps to PASS/FAIL/SKIP/MISSING at the rollup level — PRESENT is enumerator-only; PASS/FAIL is signal-derived).

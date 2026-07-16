"""Tests for enumerate_iterations.py — sim2real-check iteration enumerator."""
import json
import sys
from pathlib import Path

import pytest

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
    workloads_as_paths: bool = False,
) -> Path:
    """Materialize a run under ``root/workspace/`` with the given shape.

    ``translation_algorithms`` defaults to ``algorithms`` (no SKIP rows).
    ``disk_layout`` maps ``(phase, workload)`` -> list of iteration ints
    to materialize as ``iN/`` dirs on disk (with a stub ``trace_data.csv``).
    String values inside the list (e.g. ``"iabc"``) create a directory
    with that literal name (used for malformed-dir tests).
    Special value ``"legacy"`` in place of the list materializes a
    direct ``<phase>/<workload>/trace_data.csv`` with no ``iN/`` layer.
    Special value ``[]`` materializes an empty ``<phase>/<workload>/`` dir
    (no results at all — every declared iteration will be MISSING).
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
            workloads_as_paths=workloads_as_paths,
        )
    )

    results_dir = run_dir / "results"
    for (phase, workload), iters in disk_layout.items():
        wl_dir = results_dir / phase / workload
        if iters == "legacy":
            wl_dir.mkdir(parents=True, exist_ok=True)
            (wl_dir / "trace_data.csv").write_text("send_time_us\n")
            continue
        if not iters:
            wl_dir.mkdir(parents=True, exist_ok=True)
            continue
        for it in iters:
            if isinstance(it, str):
                # Literal dir name, used for malformed-iter-dir tests.
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
    workloads_as_paths: bool = False,
) -> str:
    """Minimal manifest.assembly.yaml the enumerator can read.

    ``workloads_as_paths=True`` writes workload entries as file-path
    strings (``workloads/<w>.yaml``) matching what ``sim2real assemble``
    writes to ``manifest.assembly.yaml`` in v3. Default False keeps the
    legacy dict shape used by earlier tests.
    """
    lines = [f"replicas: {replicas}"]
    lines.append("workloads:")
    for w in workloads:
        if workloads_as_paths:
            lines.append(f"  - workloads/{w}.yaml")
        else:
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

    # 2 phases (baseline + sim2real-ac) x 1 workload x 3 iterations = 6 rows
    assert len(result.rows) == 6
    assert all(r.status == "PRESENT" for r in result.rows)
    iters_seen = sorted({r.iteration for r in result.rows})
    assert iters_seen == [1, 2, 3]

    # results_dir on PRESENT rows carries the iN/ segment in replica
    # shape — every downstream SKILL.md subsection consumes this path.
    # A regression that dropped or duplicated the iN/ segment would
    # silently misdirect every subsequent check.
    r_i2 = next(
        r for r in result.rows
        if r.phase == "sim2real-ac" and r.workload == "wl-chat" and r.iteration == 2
    )
    assert r_i2.results_dir is not None
    assert r_i2.results_dir.endswith("results/sim2real-ac/wl-chat/i2")


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

    # Baseline (2) + sim2real-ac (2) PRESENT, sim2real-routing (2) SKIP.
    assert result.counts == {"PRESENT": 4, "MISSING": 0, "SKIP": 2}
    assert result.exit_code == 0

    skip_rows = [r for r in result.rows if r.status == "SKIP"]
    assert len(skip_rows) == 2
    assert all(r.phase == "sim2real-routing" for r in skip_rows)
    assert all("translation" in (r.note or "") for r in skip_rows)


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

    # results_dir on legacy-shape PRESENT rows points at the direct
    # workload dir (no iN/ segment). Guards against a regression that
    # accidentally appended an implicit /i1 in legacy shape.
    r = next(r for r in result.rows if r.phase == "baseline")
    assert r.results_dir is not None
    assert r.results_dir.endswith("results/baseline/wl-chat")
    assert not r.results_dir.rstrip("/").endswith("/i1")


def test_legacy_shape_uncollected_workload_is_silent(tmp_path):
    """Legacy run with a workload that has no trace_data.csv yet ->
    no MISSING row, no PRESENT row, exit 0.

    Per the spec, MISSING does not apply to legacy runs — an
    un-collected workload simply produces no row (mirrors the
    SKILL.md --real synth path, which does `[ -f … ] || continue`).
    """
    _make_run(
        tmp_path,
        run_name="trial",
        replicas=1,
        algorithms=["sim2real-ac"],
        baselines=["baseline"],
        workloads=["wl-chat", "wl-code"],
        disk_layout={
            # wl-chat has data (legacy shape); wl-code was never collected.
            ("baseline", "wl-chat"): "legacy",
            ("sim2real-ac", "wl-chat"): "legacy",
            ("baseline", "wl-code"): [],       # empty dir, no trace_data.csv
            ("sim2real-ac", "wl-code"): [],
        },
    )
    result = ei.enumerate_run(tmp_path, "trial")

    assert result.shape == "legacy"
    assert result.exit_code == 0
    assert result.counts == {"PRESENT": 2, "MISSING": 0, "SKIP": 0}

    # Only the collected pairs appear in rows; the uncollected ones
    # are silent (no MISSING, no PRESENT).
    workloads_seen = {r.workload for r in result.rows}
    assert workloads_seen == {"wl-chat"}
    assert all(r.status == "PRESENT" for r in result.rows)


def test_mixed_shape_divergence_warning(tmp_path):
    """Mixed run (some legacy, some iN/) -> warning + partial report; exit 1."""
    _make_run(
        tmp_path,
        run_name="trial",
        replicas=2,
        algorithms=["sim2real-ac"],
        baselines=["baseline"],
        workloads=["wl-chat"],
        disk_layout={
            ("baseline", "wl-chat"): [1, 2],  # iN shape
            ("sim2real-ac", "wl-chat"): "legacy",
        },
    )
    result = ei.enumerate_run(tmp_path, "trial")

    assert result.shape == "mixed"
    assert result.divergence_warnings, "expected a divergence-warning line"
    assert any("mixed run shape" in w for w in result.divergence_warnings)

    # In mixed mode the enumerator treats every pair as replica-shape for
    # the declared range. The legacy pair therefore reports i1 and i2
    # MISSING. Exit 1 because MISSING > 0.
    assert result.counts["MISSING"] == 2
    assert result.exit_code == 1


def test_malformed_iter_dir_name_counted(tmp_path):
    """Malformed iN dir names (i0, iabc) counted, do not affect shape detection."""
    _make_run(
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
    assert result.exit_code == 0  # i1 present for both pairs; no MISSING


def test_missing_run_raises(tmp_path):
    """Nonexistent run -> EnumError."""
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


def test_main_exit_code_on_missing_iteration(tmp_path):
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


# ── Bug #572: _names accepts string entries + end-to-end path-string case ────


def test_names_accepts_dict_entries():
    """Legacy shape: dict with a ``name:`` field."""
    assert ei._names([{"name": "a"}, {"name": "b"}]) == ["a", "b"]


def test_names_accepts_string_entries():
    """Bug #572: workload entries in manifest.assembly.yaml written by
    ``sim2real assemble`` are path strings. ``_names`` must derive the
    workload identifier from ``Path(s).stem``."""
    got = ei._names(
        [
            "workloads/code_generation_4.yaml",
            "workloads/interactive_chat_20.yaml",
        ]
    )
    assert got == ["code_generation_4", "interactive_chat_20"]


def test_names_accepts_mixed_dict_and_string_entries():
    """Neither branch shadows the other."""
    assert ei._names([{"name": "a"}, "workloads/b.yaml"]) == ["a", "b"]


def test_names_silently_skips_junk():
    """Non-str/non-dict entries and empty strings are dropped."""
    assert ei._names([None, "", 42, {"name": ""}, [], {}]) == []


def test_names_bare_string_no_extension():
    """A bare identifier (no ``.yaml``) still passes through — ``Path.stem``
    returns the string unchanged when there's no suffix."""
    assert ei._names(["code_generation_4"]) == ["code_generation_4"]


def test_end_to_end_path_string_workloads_replica_shape(tmp_path, capsys):
    """Bug #572 regression: manifest with workload path strings + replica
    disk shape used to yield zero rows. Must now enumerate the full grid.
    """
    _make_run(
        tmp_path,
        run_name="trial",
        replicas=2,
        algorithms=["sim2real-ac"],
        baselines=["baseline"],
        workloads=["wl-chat", "wl-code"],
        workloads_as_paths=True,
    )
    rc = ei.main(["--run", "trial", "--experiment-root", str(tmp_path)])
    payload = json.loads(capsys.readouterr().out)
    # 2 phases (baseline + sim2real-ac) x 2 workloads x 2 iterations = 8 PRESENT
    assert payload["counts"]["PRESENT"] == 8
    assert payload["counts"]["MISSING"] == 0
    assert payload["counts"]["SKIP"] == 0
    assert rc == 0
    # Confirm the workload identifiers resolved to stems (not paths).
    workloads_seen = {r["workload"] for r in payload["rows"]}
    assert workloads_seen == {"wl-chat", "wl-code"}

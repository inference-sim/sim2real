"""Integration tests for issue #510 — additive-merge assemble.

Tests the decision tree implemented in ``pipeline/lib/assemble_run.py``:
legacy detection, drift detection, grow-only guard, no-op idempotence,
and additive-merge write path.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import pytest
import yaml

from pipeline.lib import assemble_run
from pipeline.tests.test_assemble_run import _make_experiment  # reuse fixture


def _run_dir_of(fx: dict, run: str = "trial-1") -> Path:
    return fx["exp_root"] / "workspace" / "runs" / run


def _cluster_dir_of(fx: dict, run: str = "trial-1") -> Path:
    return _run_dir_of(fx, run) / "cluster"


def _pipelinerun_files(cluster_dir: Path) -> list[str]:
    return sorted(p.name for p in cluster_dir.glob("pipelinerun-*.yaml"))


def _mtimes(paths: list[Path]) -> list[float]:
    return [p.stat().st_mtime_ns for p in paths]


def _assemble(fx: dict, *, replicas: int = 1, force: bool = False,
              now_iso: str = "2026-07-01T00:00:00Z") -> None:
    assemble_run.assemble_run(
        translation_hash=fx["translation_hash"],
        translation_ref=fx["translation_hash"],
        cluster_id=fx["cluster_id"],
        run_name="trial-1",
        experiment_root=fx["exp_root"],
        manifest_path=fx["manifest_path"],
        force=force,
        replicas=replicas,
        now_iso=now_iso,
    )


class TestAssembleReplicas:
    def test_fresh_run_replicas_3_emits_three_iterations(self, tmp_path):
        fx = _make_experiment(tmp_path, algo_names_registered=["sr"],
                              algo_names_manifest=["sr"])
        _assemble(fx, replicas=3)
        names = _pipelinerun_files(_cluster_dir_of(fx))
        assert names == [
            "pipelinerun-wl-a|baseline|i1.yaml",
            "pipelinerun-wl-a|baseline|i2.yaml",
            "pipelinerun-wl-a|baseline|i3.yaml",
            "pipelinerun-wl-a|sr|i1.yaml",
            "pipelinerun-wl-a|sr|i2.yaml",
            "pipelinerun-wl-a|sr|i3.yaml",
        ]

    def test_grow_from_3_to_5_preserves_i1_i3_and_adds_i4_i5(self, tmp_path):
        fx = _make_experiment(tmp_path, algo_names_registered=["sr"],
                              algo_names_manifest=["sr"])
        _assemble(fx, replicas=3, now_iso="2026-07-01T00:00:00Z")
        cluster = _cluster_dir_of(fx)
        keep = sorted(cluster.glob("pipelinerun-*|i[123].yaml"))
        keep_bytes_before = [p.read_bytes() for p in keep]
        keep_mtimes_before = _mtimes(keep)
        # Sleep briefly to ensure any rewrite would change mtime (ns resolution
        # varies by filesystem; 10ms is safe).
        time.sleep(0.01)
        _assemble(fx, replicas=5, now_iso="2026-07-02T00:00:00Z")
        # i1..i3 preserved byte-for-byte AND by mtime.
        keep_bytes_after = [p.read_bytes() for p in sorted(
            cluster.glob("pipelinerun-*|i[123].yaml"))]
        keep_mtimes_after = _mtimes(sorted(
            cluster.glob("pipelinerun-*|i[123].yaml")))
        assert keep_bytes_before == keep_bytes_after
        assert keep_mtimes_before == keep_mtimes_after
        # i4, i5 added.
        names = _pipelinerun_files(cluster)
        assert "pipelinerun-wl-a|baseline|i4.yaml" in names
        assert "pipelinerun-wl-a|baseline|i5.yaml" in names
        assert "pipelinerun-wl-a|sr|i4.yaml" in names
        assert "pipelinerun-wl-a|sr|i5.yaml" in names
        # manifest.assembly.yaml records new replicas.
        ma = yaml.safe_load(
            (_run_dir_of(fx) / "manifest.assembly.yaml").read_text()
        )
        assert ma["replicas"] == 5

    def test_shrink_from_3_to_2_refuses_with_error_naming_count_and_506(
        self, tmp_path,
    ):
        fx = _make_experiment(tmp_path, algo_names_registered=["sr"],
                              algo_names_manifest=["sr"])
        _assemble(fx, replicas=3)
        with pytest.raises(assemble_run.AssembleError) as exc:
            _assemble(fx, replicas=2)
        msg = str(exc.value)
        assert "3" in msg  # current count named
        assert "#506" in msg  # shrink tracking issue referenced

    def test_reassemble_at_same_replica_count_is_noop(self, tmp_path):
        fx = _make_experiment(tmp_path, algo_names_registered=["sr"],
                              algo_names_manifest=["sr"])
        _assemble(fx, replicas=3, now_iso="2026-07-01T00:00:00Z")
        run_dir = _run_dir_of(fx)
        all_paths = sorted(list(run_dir.rglob("*")))
        mtimes_before = _mtimes([p for p in all_paths if p.is_file()])
        time.sleep(0.01)
        _assemble(fx, replicas=3, now_iso="2026-07-02T00:00:00Z")
        mtimes_after = _mtimes([p for p in all_paths if p.is_file()])
        assert mtimes_before == mtimes_after
        # Issue #555: side-band status attrs let the CLI wrapper distinguish
        # the no-op path from actual writes and print a truthful message.
        assert assemble_run.assemble_run.status == "noop"
        assert (
            assemble_run.assemble_run.prior_assembled_at
            == "2026-07-01T00:00:00Z"
        )

    def test_fresh_assemble_sets_status_written_and_empty_prior(self, tmp_path):
        """Issue #555: fresh assemble is a write path — status='written',
        prior_assembled_at=''."""
        fx = _make_experiment(tmp_path, algo_names_registered=["sr"],
                              algo_names_manifest=["sr"])
        _assemble(fx, replicas=1, now_iso="2026-07-01T00:00:00Z")
        assert assemble_run.assemble_run.status == "written"
        assert assemble_run.assemble_run.prior_assembled_at == ""

    def test_additive_grow_sets_status_written(self, tmp_path):
        """Issue #555: additive-grow is a write path — status='written'."""
        fx = _make_experiment(tmp_path, algo_names_registered=["sr"],
                              algo_names_manifest=["sr"])
        _assemble(fx, replicas=3, now_iso="2026-07-01T00:00:00Z")
        _assemble(fx, replicas=5, now_iso="2026-07-02T00:00:00Z")
        assert assemble_run.assemble_run.status == "written"
        assert assemble_run.assemble_run.prior_assembled_at == ""

    def test_force_rebuild_sets_status_written(self, tmp_path):
        """Issue #555: --force full rebuild is a write path — status='written'."""
        fx = _make_experiment(tmp_path, algo_names_registered=["sr"],
                              algo_names_manifest=["sr"])
        _assemble(fx, replicas=3, now_iso="2026-07-01T00:00:00Z")
        _assemble(fx, replicas=3, force=True, now_iso="2026-07-02T00:00:00Z")
        assert assemble_run.assemble_run.status == "written"
        assert assemble_run.assemble_run.prior_assembled_at == ""

    def test_reassemble_at_same_replica_count_with_force_rebuilds(self, tmp_path):
        """Issue #532: --force must rebuild even when the manifest hash and
        --replicas match the prior assemble. Counterpart to
        test_reassemble_at_same_replica_count_is_noop — same setup, +force,
        opposite mtime expectation."""
        fx = _make_experiment(tmp_path, algo_names_registered=["sr"],
                              algo_names_manifest=["sr"])
        _assemble(fx, replicas=3, now_iso="2026-07-01T00:00:00Z")
        run_dir = _run_dir_of(fx)
        # Seed a sentinel to prove rmtree happened (bytes are byte-identical
        # across the two calls when inputs match, so mtime is the only other
        # signal we can rely on).
        (run_dir / "sentinel").write_text("leftover")
        time.sleep(0.01)
        _assemble(fx, replicas=3, force=True,
                  now_iso="2026-07-02T00:00:00Z")
        assert not (run_dir / "sentinel").exists()
        # Cluster/pipelinerun files present after rebuild.
        names = _pipelinerun_files(_cluster_dir_of(fx))
        assert "pipelinerun-wl-a|baseline|i3.yaml" in names
        assert "pipelinerun-wl-a|sr|i3.yaml" in names
        # manifest.assembly.yaml still records replicas=3.
        ma = yaml.safe_load(
            (run_dir / "manifest.assembly.yaml").read_text()
        )
        assert ma["replicas"] == 3

    def test_grow_with_force_rebuilds_instead_of_additive_grow(self, tmp_path):
        """Issue #532: --force with replicas > prior_replicas should do a
        full rebuild, NOT additive-grow. Counterpart to
        test_grow_from_3_to_5_preserves_i1_i3_and_adds_i4_i5 — same setup,
        +force, opposite expectation for the pre-existing iterations."""
        fx = _make_experiment(tmp_path, algo_names_registered=["sr"],
                              algo_names_manifest=["sr"])
        _assemble(fx, replicas=3, now_iso="2026-07-01T00:00:00Z")
        run_dir = _run_dir_of(fx)
        # Seed a sentinel to prove full rebuild (rmtree) rather than
        # additive-grow (which would leave the sentinel in place).
        (run_dir / "sentinel").write_text("leftover")
        time.sleep(0.01)
        _assemble(fx, replicas=5, force=True,
                  now_iso="2026-07-02T00:00:00Z")
        assert not (run_dir / "sentinel").exists()
        # All five iterations present.
        names = _pipelinerun_files(_cluster_dir_of(fx))
        for i in (1, 2, 3, 4, 5):
            assert f"pipelinerun-wl-a|baseline|i{i}.yaml" in names
            assert f"pipelinerun-wl-a|sr|i{i}.yaml" in names
        # manifest.assembly.yaml records new replicas.
        ma = yaml.safe_load(
            (run_dir / "manifest.assembly.yaml").read_text()
        )
        assert ma["replicas"] == 5

    def test_legacy_run_with_replicas_gt_1_refuses(self, tmp_path):
        """Existing run with no `replicas` field in manifest.assembly.yaml."""
        fx = _make_experiment(tmp_path, algo_names_registered=["sr"],
                              algo_names_manifest=["sr"])
        _assemble(fx, replicas=1)
        # Simulate a legacy run: strip replicas field from manifest.assembly.yaml.
        ma_path = _run_dir_of(fx) / "manifest.assembly.yaml"
        ma = yaml.safe_load(ma_path.read_text())
        ma.pop("replicas", None)
        ma_path.write_text(yaml.dump(ma, sort_keys=False))
        with pytest.raises(assemble_run.AssembleError, match="legacy"):
            _assemble(fx, replicas=3)

    def test_legacy_run_with_force_rebuilds(self, tmp_path):
        fx = _make_experiment(tmp_path, algo_names_registered=["sr"],
                              algo_names_manifest=["sr"])
        _assemble(fx, replicas=1)
        ma_path = _run_dir_of(fx) / "manifest.assembly.yaml"
        ma = yaml.safe_load(ma_path.read_text())
        ma.pop("replicas", None)
        ma_path.write_text(yaml.dump(ma, sort_keys=False))
        _assemble(fx, replicas=3, force=True)
        ma = yaml.safe_load(ma_path.read_text())
        assert ma["replicas"] == 3
        names = _pipelinerun_files(_cluster_dir_of(fx))
        assert "pipelinerun-wl-a|baseline|i3.yaml" in names

    def test_drift_with_force_rmtree_rebuilds(self, tmp_path):
        fx = _make_experiment(tmp_path, algo_names_registered=["sr"],
                              algo_names_manifest=["sr"])
        _assemble(fx, replicas=3)
        # Simulate drift: overwrite the stored params_hash with a stale value.
        rm_path = _run_dir_of(fx) / "run_metadata.json"
        import json
        rm = json.loads(rm_path.read_text())
        rm["params_hash"] = "0" * 64
        rm_path.write_text(json.dumps(rm))
        _assemble(fx, replicas=5, force=True)
        # After force-rebuild, the params_hash matches the current content.
        rm = json.loads(rm_path.read_text())
        assert rm["params_hash"] != "0" * 64
        assert rm["replicas"] == 5

    def test_drift_without_force_refuses(self, tmp_path):
        fx = _make_experiment(tmp_path, algo_names_registered=["sr"],
                              algo_names_manifest=["sr"])
        _assemble(fx, replicas=3)
        rm_path = _run_dir_of(fx) / "run_metadata.json"
        import json
        rm = json.loads(rm_path.read_text())
        rm["params_hash"] = "0" * 64
        rm_path.write_text(json.dumps(rm))
        with pytest.raises(assemble_run.AssembleError, match="content changed"):
            _assemble(fx, replicas=5)

    def test_replicas_arg_rejects_zero_and_negative(self):
        from pipeline.sim2real import _positive_int
        with pytest.raises(argparse.ArgumentTypeError):
            _positive_int("0")
        with pytest.raises(argparse.ArgumentTypeError):
            _positive_int("-1")

    def test_default_flow_no_replicas_flag_emits_pipe_shape_i1(self, tmp_path):
        """No --replicas → default 1 → still pipe-shape with |i1 suffix."""
        fx = _make_experiment(tmp_path, algo_names_registered=["sr"],
                              algo_names_manifest=["sr"])
        _assemble(fx)  # no replicas kwarg → default 1
        names = _pipelinerun_files(_cluster_dir_of(fx))
        assert names == [
            "pipelinerun-wl-a|baseline|i1.yaml",
            "pipelinerun-wl-a|sr|i1.yaml",
        ]
        ma = yaml.safe_load(
            (_run_dir_of(fx) / "manifest.assembly.yaml").read_text()
        )
        assert ma["replicas"] == 1

    def test_missing_manifest_assembly_refuses_without_force(self, tmp_path):
        """Branch 2: run_dir exists but manifest.assembly.yaml missing → refuse."""
        fx = _make_experiment(tmp_path, algo_names_registered=["sr"],
                              algo_names_manifest=["sr"])
        _assemble(fx, replicas=1)
        (_run_dir_of(fx) / "manifest.assembly.yaml").unlink()
        with pytest.raises(assemble_run.AssembleError,
                           match="missing manifest.assembly.yaml"):
            _assemble(fx, replicas=1)

    def test_missing_run_metadata_with_force_rebuilds(self, tmp_path):
        """Branch 2 --force counterpart: rmtree + fresh assemble at N."""
        fx = _make_experiment(tmp_path, algo_names_registered=["sr"],
                              algo_names_manifest=["sr"])
        _assemble(fx, replicas=1)
        (_run_dir_of(fx) / "run_metadata.json").unlink()
        _assemble(fx, replicas=3, force=True)
        names = _pipelinerun_files(_cluster_dir_of(fx))
        assert "pipelinerun-wl-a|baseline|i3.yaml" in names

    def test_shrink_with_force_and_drift_still_refuses(self, tmp_path):
        """Grow-only invariant: --force does NOT bypass the shrink guard,
        even when combined with content drift that --force would normally
        override. Regression guard for a review finding on the ordering of
        the drift + shrink checks."""
        import json
        fx = _make_experiment(tmp_path, algo_names_registered=["sr"],
                              algo_names_manifest=["sr"])
        _assemble(fx, replicas=3)
        # Poison params_hash to simulate content drift.
        rm_path = _run_dir_of(fx) / "run_metadata.json"
        rm = json.loads(rm_path.read_text())
        rm["params_hash"] = "0" * 64
        rm_path.write_text(json.dumps(rm))
        with pytest.raises(assemble_run.AssembleError) as exc:
            _assemble(fx, replicas=2, force=True)
        assert "#506" in str(exc.value)
        assert "3" in str(exc.value)

    def test_corrupt_manifest_assembly_refuses_without_force(self, tmp_path):
        """Bare yaml.safe_load must not escape as raw YAMLError. Verifies
        the parse guard around the decision-tree state-file reads."""
        fx = _make_experiment(tmp_path, algo_names_registered=["sr"],
                              algo_names_manifest=["sr"])
        _assemble(fx, replicas=1)
        (_run_dir_of(fx) / "manifest.assembly.yaml").write_text("not: yaml: : :")
        with pytest.raises(assemble_run.AssembleError, match="corrupt"):
            _assemble(fx, replicas=1)

    def test_corrupt_run_metadata_refuses_without_force(self, tmp_path):
        """Bare json.loads must not escape as raw JSONDecodeError."""
        fx = _make_experiment(tmp_path, algo_names_registered=["sr"],
                              algo_names_manifest=["sr"])
        _assemble(fx, replicas=1)
        (_run_dir_of(fx) / "run_metadata.json").write_text("{not-json")
        with pytest.raises(assemble_run.AssembleError, match="corrupt"):
            _assemble(fx, replicas=1)

    def test_corrupt_state_with_force_rebuilds(self, tmp_path):
        """--force lets the operator recover from a corrupt state file."""
        fx = _make_experiment(tmp_path, algo_names_registered=["sr"],
                              algo_names_manifest=["sr"])
        _assemble(fx, replicas=1)
        (_run_dir_of(fx) / "manifest.assembly.yaml").write_text("not: yaml: : :")
        _assemble(fx, replicas=2, force=True)
        names = _pipelinerun_files(_cluster_dir_of(fx))
        assert "pipelinerun-wl-a|baseline|i2.yaml" in names

    def test_additive_grow_missing_scenario_file_raises_assemble_error(
        self, tmp_path,
    ):
        """Grow path must raise AssembleError (not AttributeError) if a
        referenced scenario file has been deleted between prior assemble
        and the grow call. Verifies scenario-resolution failures inside
        the shared `_resolve_packages` helper stay in the AssembleError
        contract on the grow path."""
        fx = _make_experiment(tmp_path, algo_names_registered=["sr"],
                              algo_names_manifest=["sr"])
        _assemble(fx, replicas=3)
        # Delete the baseline scenario file that transfer.yaml references.
        (fx["exp_root"] / "baselines" / "base.yaml").unlink()
        with pytest.raises(assemble_run.AssembleError,
                           match="baseline scenario not found"):
            _assemble(fx, replicas=5)

    def test_additive_grow_corrupt_translation_output_raises_assemble_error(
        self, tmp_path,
    ):
        """Grow path must raise AssembleError (not raw ValueError /
        JSONDecodeError) if translation_output.json has been corrupted
        between prior assemble and the grow call. Verifies the wrapped
        `read_translation_output` call lifted into `_resolve_packages`."""
        fx = _make_experiment(tmp_path, algo_names_registered=["sr"],
                              algo_names_manifest=["sr"])
        _assemble(fx, replicas=3)
        tout = (fx["exp_root"] / "workspace" / "translations"
                / fx["translation_hash"] / "translation_output.json")
        tout.write_text("{not-json")
        with pytest.raises(assemble_run.AssembleError,
                           match="not valid JSON"):
            _assemble(fx, replicas=5)

    def test_additive_grow_backfills_scenario_for_legacy_run(self, tmp_path):
        """A run assembled before #551 has no `scenario` in run_metadata.json.
        Growing it (replicas N → N+1) must backfill the field from the manifest
        so deploy.py's hard guard on scenario doesn't trip. Regression guard
        for the migration story called out in issue #551.
        """
        import json as _json
        fx = _make_experiment(tmp_path, algo_names_registered=["sr"],
                              algo_names_manifest=["sr"])
        _assemble(fx, replicas=2, now_iso="2026-07-01T00:00:00Z")
        # Simulate a pre-#551 run: strip scenario from run_metadata.json.
        rm_path = _run_dir_of(fx) / "run_metadata.json"
        rm = _json.loads(rm_path.read_text())
        assert rm.get("scenario"), "pre-condition: fresh assemble writes scenario"
        del rm["scenario"]
        rm_path.write_text(_json.dumps(rm, indent=2, sort_keys=True) + "\n")
        assert "scenario" not in _json.loads(rm_path.read_text())

        # Additive-grow from 2 to 3 replicas.
        _assemble(fx, replicas=3, now_iso="2026-07-02T00:00:00Z")

        # Scenario is repopulated from the manifest.
        rm_after = _json.loads(rm_path.read_text())
        # _make_experiment uses "test-scenario" in its transfer.yaml.
        assert rm_after["scenario"] == "test-scenario"
        assert rm_after["replicas"] == 3

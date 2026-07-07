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

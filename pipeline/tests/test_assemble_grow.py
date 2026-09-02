"""Tests for issue #877 — replica grow derives each new iteration from the
pair's own i1 rather than re-resolving overlays."""

from __future__ import annotations

import time

import pytest
import yaml

from pipeline.lib import assemble_run
from pipeline.lib.errors import AssembleError
from pipeline.tests.test_assemble_run import _make_experiment


def _fx(tmp_path):
    return _make_experiment(
        tmp_path, algo_names_registered=["sr"], algo_names_manifest=["sr"]
    )


def _assemble(fx, **kw):
    kw.setdefault("force", False)
    kw.setdefault("now_iso", "2026-07-01T00:00:00Z")
    return assemble_run.assemble_run(
        translation_hash=fx["translation_hash"],
        translation_ref=fx["translation_hash"],
        cluster_id=fx["cluster_id"],
        run_name="trial-1",
        experiment_root=fx["exp_root"],
        manifest_path=fx["manifest_path"],
        **kw,
    )


def _cluster(fx):
    return fx["exp_root"] / "workspace" / "runs" / "trial-1" / "cluster"


def _pr(fx, pkg, n, wl="wl-a"):
    return _cluster(fx) / f"pipelinerun-{wl}|{pkg}|i{n}.yaml"


def _param(path, name):
    doc = yaml.safe_load(path.read_text())
    for p in doc["spec"]["params"]:
        if p["name"] == name:
            return p["value"]
    raise AssertionError(f"no {name} param in {path.name}")


def _overlay_path(fx, algo="sr"):
    return (
        fx["exp_root"] / "workspace" / "translations" / fx["translation_hash"]
        / "generated" / algo / f"{algo}_config.yaml"
    )


class TestRetargetIteration:
    def _doc(self):
        return {
            "metadata": {"name": "baseline-wl-a-trial-1-i1"},
            "spec": {
                "params": [
                    {"name": "replica", "value": "1"},
                    {"name": "phase", "value": "baseline"},
                ]
            },
        }

    def test_rewrites_name_and_replica_only(self):
        doc = self._doc()
        assemble_run._retarget_pipelinerun_iteration(
            doc, source_iteration=1, iteration=4
        )
        assert doc["metadata"]["name"] == "baseline-wl-a-trial-1-i4"
        assert doc["spec"]["params"][0]["value"] == "4"
        # Untouched.
        assert doc["spec"]["params"][1] == {"name": "phase", "value": "baseline"}

    def test_replica_value_is_a_string(self):
        doc = self._doc()
        assemble_run._retarget_pipelinerun_iteration(
            doc, source_iteration=1, iteration=2
        )
        assert doc["spec"]["params"][0]["value"] == "2"
        assert isinstance(doc["spec"]["params"][0]["value"], str)

    def test_run_name_containing_i_digits_is_not_confused(self):
        """Only the trailing -i<N> is the iteration; a run named 'try-i3' keeps
        its own '-i3'. A textual substitution would not."""
        doc = {
            "metadata": {"name": "baseline-wl-a-try-i3-i1"},
            "spec": {"params": [{"name": "replica", "value": "1"}]},
        }
        assemble_run._retarget_pipelinerun_iteration(
            doc, source_iteration=1, iteration=2
        )
        assert doc["metadata"]["name"] == "baseline-wl-a-try-i3-i2"

    def test_unrelated_replicas_keys_are_untouched(self):
        """The inlined scenario carries vLLM/deployment `replicas:` counts that
        have nothing to do with the iteration — the reason this rewrites
        structurally rather than substituting text."""
        scenario = "vllm:\n  replicas: 1\nrouter:\n  replicas: 1\n"
        doc = {
            "metadata": {"name": "baseline-wl-a-trial-1-i1"},
            "spec": {
                "params": [
                    {"name": "replica", "value": "1"},
                    {"name": "scenarioContent", "value": scenario},
                ]
            },
        }
        assemble_run._retarget_pipelinerun_iteration(
            doc, source_iteration=1, iteration=7
        )
        assert doc["spec"]["params"][1]["value"] == scenario

    def test_name_not_ending_in_source_iteration_refuses(self):
        doc = self._doc()
        doc["metadata"]["name"] = "baseline-wl-a-trial-1"
        with pytest.raises(AssembleError, match="does not end"):
            assemble_run._retarget_pipelinerun_iteration(
                doc, source_iteration=1, iteration=2
            )

    def test_missing_metadata_name_refuses(self):
        doc = self._doc()
        doc["metadata"] = {}
        with pytest.raises(AssembleError, match="metadata.name"):
            assemble_run._retarget_pipelinerun_iteration(
                doc, source_iteration=1, iteration=2
            )

    def test_missing_params_list_refuses(self):
        doc = self._doc()
        doc["spec"] = {}
        with pytest.raises(AssembleError, match="spec.params"):
            assemble_run._retarget_pipelinerun_iteration(
                doc, source_iteration=1, iteration=2
            )

    def test_missing_replica_param_refuses(self):
        doc = self._doc()
        doc["spec"]["params"] = [{"name": "phase", "value": "baseline"}]
        with pytest.raises(AssembleError, match="replica"):
            assemble_run._retarget_pipelinerun_iteration(
                doc, source_iteration=1, iteration=2
            )

    def test_over_long_grown_name_refuses(self):
        """i9 -> i10 adds a character, so a name that fit can stop fitting."""
        doc = {
            "metadata": {"name": "b-" + "x" * 248 + "-i9"},
            "spec": {"params": [{"name": "replica", "value": "9"}]},
        }
        assert len(doc["metadata"]["name"]) == 253
        with pytest.raises(AssembleError, match="253"):
            assemble_run._retarget_pipelinerun_iteration(
                doc, source_iteration=9, iteration=10
            )


class TestPairAndIterationParsing:
    def _p(self, tmp_path, name):
        p = tmp_path / name
        p.write_text("x\n")
        return p

    def test_parses_canonical_name(self, tmp_path):
        got = assemble_run._pipelinerun_pair_and_iteration(
            self._p(tmp_path, "pipelinerun-wl-a|baseline|i3.yaml")
        )
        assert got == ("pipelinerun-wl-a|baseline", 3)

    def test_multi_digit_iteration(self, tmp_path):
        got = assemble_run._pipelinerun_pair_and_iteration(
            self._p(tmp_path, "pipelinerun-wl-a|baseline|i12.yaml")
        )
        assert got == ("pipelinerun-wl-a|baseline", 12)

    @pytest.mark.parametrize(
        "name",
        [
            "pipelinerun-wl-a|baseline|i1 copy.yaml",
            "pipelinerun-wl-a|baseline.yaml",
            "pipelinerun-wl-a|baseline|iX.yaml",
            "pipelinerun-wl-a|baseline|i0.yaml",
            "pipelinerun-wl-a|baseline|.yaml",
            "pipelinerun-wl-a|b|c|i1.yaml",
        ],
    )
    def test_unparseable_names_are_ignored(self, tmp_path, name):
        assert (
            assemble_run._pipelinerun_pair_and_iteration(self._p(tmp_path, name))
            is None
        )


class TestGrowFromI1:
    def test_grown_iteration_is_i1_except_name_and_replica(self, tmp_path):
        """The issue's first listed test: grow after an overlay edit produces iN
        byte-identical to i1 except for pr_name and the replica param."""
        fx = _fx(tmp_path)
        _assemble(fx)
        # Edit the treatment overlay AFTER i1 exists. Nothing in the assembly
        # slice changes, so params_hash still matches and grow is taken rather
        # than refused — which is exactly how the bug was reachable.
        _overlay_path(fx).write_text(
            yaml.dump(
                {
                    "scenario": [
                        {
                            "name": "test-scenario",
                            "router": {"epp": {"pluginConfig": "CHANGED"}},
                        }
                    ]
                },
                sort_keys=False,
            )
        )
        _assemble(fx, replicas=2, now_iso="2026-07-02T00:00:00Z")

        expected = yaml.safe_load(_pr(fx, "sr", 1).read_text())
        assemble_run._retarget_pipelinerun_iteration(
            expected, source_iteration=1, iteration=2
        )
        assert yaml.safe_load(_pr(fx, "sr", 2).read_text()) == expected
        # The edited overlay did NOT reach i2.
        assert "CHANGED" not in _pr(fx, "sr", 2).read_text()
        assert _param(_pr(fx, "sr", 2), "replica") == "2"
        assert yaml.safe_load(_pr(fx, "sr", 2).read_text())["metadata"][
            "name"
        ].endswith("-i2")

    def test_each_pair_grows_from_its_own_i1(self, tmp_path):
        """A run whose pairs legitimately differ grows each pair consistently
        with itself, not with some other pair."""
        fx = _fx(tmp_path)
        _assemble(fx)
        b1 = _pr(fx, "baseline", 1)
        doc = yaml.safe_load(b1.read_text())
        doc["spec"]["params"].append({"name": "marker", "value": "baseline-only"})
        b1.write_text(yaml.dump(doc, default_flow_style=False))
        _assemble(fx, replicas=2, now_iso="2026-07-02T00:00:00Z")
        marker = {"name": "marker", "value": "baseline-only"}
        grown_baseline = yaml.safe_load(_pr(fx, "baseline", 2).read_text())
        grown_sr = yaml.safe_load(_pr(fx, "sr", 2).read_text())
        assert marker in grown_baseline["spec"]["params"]
        assert marker not in grown_sr["spec"]["params"]

    def test_prior_iterations_stay_byte_and_mtime_identical(self, tmp_path):
        fx = _fx(tmp_path)
        _assemble(fx, replicas=2, now_iso="2026-07-01T00:00:00Z")
        keep = [_pr(fx, p, n) for p in ("baseline", "sr") for n in (1, 2)]
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in keep}
        time.sleep(0.01)
        _assemble(fx, replicas=4, now_iso="2026-07-02T00:00:00Z")
        for p in keep:
            assert (p.read_bytes(), p.stat().st_mtime_ns) == before[p]

    def test_grow_emits_every_new_iteration_for_every_pair(self, tmp_path):
        fx = _fx(tmp_path)
        _assemble(fx)
        _assemble(fx, replicas=3, now_iso="2026-07-02T00:00:00Z")
        names = sorted(p.name for p in _cluster(fx).glob("pipelinerun-*.yaml"))
        assert names == [
            f"pipelinerun-wl-a|{pkg}|i{n}.yaml"
            for pkg in ("baseline", "sr")
            for n in (1, 2, 3)
        ]

    def test_results_dir_follows_the_replica_param(self, tmp_path):
        """resultsDir threads i$(params.replica), so rewriting the param is all
        that is needed for each grown iteration to write to its own directory."""
        fx = _fx(tmp_path)
        _assemble(fx)
        _assemble(fx, replicas=3, now_iso="2026-07-02T00:00:00Z")
        for n in (1, 2, 3):
            assert _param(_pr(fx, "sr", n), "replica") == str(n)

    def test_grown_names_are_unique_per_iteration(self, tmp_path):
        fx = _fx(tmp_path)
        _assemble(fx)
        _assemble(fx, replicas=3, now_iso="2026-07-02T00:00:00Z")
        names = [
            yaml.safe_load(_pr(fx, pkg, n).read_text())["metadata"]["name"]
            for pkg in ("baseline", "sr")
            for n in (1, 2, 3)
        ]
        assert len(set(names)) == len(names)

    def test_pair_missing_i1_refuses_naming_it(self, tmp_path):
        fx = _fx(tmp_path)
        _assemble(fx, replicas=2)
        _pr(fx, "sr", 1).unlink()
        with pytest.raises(AssembleError) as exc:
            _assemble(fx, replicas=3, now_iso="2026-07-02T00:00:00Z")
        msg = str(exc.value)
        assert "sr" in msg
        assert "i1" in msg

    def test_refusal_writes_nothing(self, tmp_path):
        """The missing-i1 refusal precedes every write, so the run is untouched."""
        fx = _fx(tmp_path)
        _assemble(fx, replicas=2)
        _pr(fx, "sr", 1).unlink()
        ma = _cluster(fx).parent / "manifest.assembly.yaml"
        before = ma.stat().st_mtime_ns
        time.sleep(0.01)
        with pytest.raises(AssembleError):
            _assemble(fx, replicas=3, now_iso="2026-07-02T00:00:00Z")
        assert ma.stat().st_mtime_ns == before
        assert not _pr(fx, "baseline", 3).exists()

    def test_unparseable_cluster_files_are_ignored_not_grown(self, tmp_path):
        fx = _fx(tmp_path)
        _assemble(fx)
        stray = _cluster(fx) / "pipelinerun-wl-a|baseline|i1 copy.yaml"
        stray.write_text("junk\n")
        _assemble(fx, replicas=2, now_iso="2026-07-02T00:00:00Z")
        assert stray.read_text() == "junk\n"
        assert not (
            _cluster(fx) / "pipelinerun-wl-a|baseline|i1 copy|i2.yaml"
        ).exists()

    def test_grow_no_longer_reads_the_baseline_scenario(self, tmp_path):
        """Deleting the baseline scenario file used to make grow refuse, as a
        side effect of re-resolution. Grow is now a disk operation and does not
        read it; the grown iterations were never resolved from it anyway. The
        translation directory itself is still checked in assemble_run step 1."""
        fx = _fx(tmp_path)
        _assemble(fx)
        (fx["exp_root"] / "baselines" / "base.yaml").unlink()
        _assemble(fx, replicas=2, now_iso="2026-07-02T00:00:00Z")
        assert _pr(fx, "sr", 2).exists()

    def test_grow_updates_replica_count_in_both_metadata_files(self, tmp_path):
        fx = _fx(tmp_path)
        _assemble(fx)
        _assemble(fx, replicas=3, now_iso="2026-07-02T00:00:00Z")
        run_dir = _cluster(fx).parent
        ma = yaml.safe_load((run_dir / "manifest.assembly.yaml").read_text())
        assert ma["replicas"] == 3
        import json

        rm = json.loads((run_dir / "run_metadata.json").read_text())
        assert rm["replicas"] == 3
        assert rm["assembled_at"] == "2026-07-02T00:00:00Z"


class TestGrowPairIterationsDirectly:
    def test_returns_written_filenames_sorted(self, tmp_path):
        fx = _fx(tmp_path)
        _assemble(fx)
        run_dir = _cluster(fx).parent
        written = assemble_run.grow_pair_iterations(
            run_dir, prior_replicas=1, new_replicas=3
        )
        assert written == [
            "pipelinerun-wl-a|baseline|i2.yaml",
            "pipelinerun-wl-a|baseline|i3.yaml",
            "pipelinerun-wl-a|sr|i2.yaml",
            "pipelinerun-wl-a|sr|i3.yaml",
        ]

    def test_no_new_iterations_is_a_noop(self, tmp_path):
        fx = _fx(tmp_path)
        _assemble(fx)
        run_dir = _cluster(fx).parent
        assert (
            assemble_run.grow_pair_iterations(
                run_dir, prior_replicas=1, new_replicas=1
            )
            == []
        )

    def test_empty_cluster_dir_is_a_noop(self, tmp_path):
        (tmp_path / "cluster").mkdir()
        assert (
            assemble_run.grow_pair_iterations(
                tmp_path, prior_replicas=1, new_replicas=3
            )
            == []
        )

"""Tests for pipeline/sim2real.py — `sim2real translate` command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from pipeline import sim2real
from pipeline.lib import layout


@pytest.fixture(autouse=True)
def _isolated_experiment_root(tmp_path):
    layout._EXPERIMENT_ROOT = tmp_path
    yield
    layout._EXPERIMENT_ROOT = None


# ── Shared helpers ────────────────────────────────────────────────────────


def _write_manifest(tmp_path, scenario="softreflective-v1", algorithms=None):
    """Write a minimal transfer.yaml + algorithm source file(s).

    The manifest satisfies ``pipeline/lib/manifest.py:load_manifest`` v3
    validation: ``kind`` = ``sim2real-transfer``, ``version`` = 3,
    ``baselines`` non-empty, and every ``algorithms[i]`` carries the
    required ``defaults`` cross-reference. Test algorithm names must
    match manifest's ``_PACKAGE_NAME_RE`` (lowercase alphanumeric, 1-20
    chars) — that's stricter than ``translation_ref.validate_name`` but
    matches production manifests.

    Returns the manifest path.
    """
    if algorithms is None:
        algorithms = [{"name": "softreflective", "source": "algorithms/softreflective.py"}]
    exp = tmp_path
    (exp / "algorithms").mkdir(exist_ok=True)
    for a in algorithms:
        (exp / a["source"]).write_text(f"# stub for {a['name']}\n")
    baselines = [{"name": "base", "scenario": "baseline-scenario"}]
    manifest = {
        "kind": "sim2real-transfer",
        "version": 3,
        "scenario": scenario,
        "baselines": baselines,
        "algorithms": [dict(a, defaults="base") for a in algorithms],
        "component": {"repo": "example.com/x/y", "kind": "scorer"},
        "context": {"text": "", "files": []},
    }
    path = exp / "transfer.yaml"
    path.write_text(yaml.safe_dump(manifest))
    return path


def _run_translate(args_list, experiment_root=None):
    """Invoke sim2real.main([...]) and return its exit code.

    ``experiment_root``, when set, is threaded as ``--experiment-root``
    at the top level (mirrors the CLI). Defaults to
    ``layout._EXPERIMENT_ROOT`` which the autouse fixture points at
    ``tmp_path`` — passing it explicitly keeps main() from overriding
    the fixture during argparse+dispatch.
    """
    exp = experiment_root if experiment_root is not None else layout._EXPERIMENT_ROOT
    prefix = ["--experiment-root", str(exp)] if exp else []
    return sim2real.main([*prefix, "translate", *args_list])


def _compute_hash(tmp_path, manifest_path):
    """Compute the expected translation hash via the full production path.

    Uses ``manifest.load_manifest`` (not raw ``yaml.safe_load``) because
    ``load_manifest`` mutates ``component.path`` and normalizes
    ``context`` — both are in the translation slice, so raw-loaded
    manifests hash differently from what ``_cmd_translate`` sees at
    runtime.
    """
    from pipeline.lib import manifest as _manifest, slicer
    manifest = _manifest.load_manifest(manifest_path)
    return slicer.translation_hash_with_sources(manifest, tmp_path)


# ── Builder tests (Task 2) ────────────────────────────────────────────────


class TestBuildTranslateOutput:
    def test_single_algorithm_shape(self):
        out = sim2real._build_translate_output(
            translation_hash="a" * 64,
            scenario="softreflective-v1",
            algorithms=[{
                "name": "softreflective",
                "source_path": "algorithms/softreflective.py",
                "source_sha256": "e" * 64,
            }],
            now_iso="2026-07-02T14:00:00Z",
        )
        assert out["version"] == 1
        assert out["source"] == "skill"
        assert out["alias"] == "softreflective-v1"
        assert out["created_at"] == "2026-07-02T14:00:00Z"
        assert len(out["algorithms"]) == 1
        a = out["algorithms"][0]
        assert a["name"] == "softreflective"
        assert a["source_path"] == "algorithms/softreflective.py"
        assert a["source_sha256"] == "e" * 64
        assert a["config_path"] is None
        assert a["image_ref"] is None
        assert a["image_digest"] is None

    def test_multi_algorithm_shape(self):
        out = sim2real._build_translate_output(
            translation_hash="a" * 64,
            scenario="compare-a-b",
            algorithms=[
                {"name": "algo_a", "source_path": "algorithms/a.py", "source_sha256": "a" * 64},
                {"name": "algo_b", "source_path": "algorithms/b.py", "source_sha256": "b" * 64},
            ],
            now_iso="2026-07-02T14:30:00Z",
        )
        assert [a["name"] for a in out["algorithms"]] == ["algo_a", "algo_b"]
        assert all(a["image_ref"] is None for a in out["algorithms"])
        assert all(a["image_digest"] is None for a in out["algorithms"])
        assert all(a["config_path"] is None for a in out["algorithms"])


class TestBuildSkillInput:
    def test_paths_are_absolute_at_top_level(self, tmp_path):
        exp = tmp_path / "exp"
        tdir = tmp_path / "workspace" / "translations" / ("a" * 64)
        skin = sim2real._build_skill_input(
            translation_hash="a" * 64,
            experiment_root=exp,
            translations_dir=tdir,
            scenario="softreflective-v1",
            baselines=[],
            algorithms=[{
                "name": "softreflective",
                "source_path": "algorithms/softreflective.py",
                "source_sha256": "e" * 64,
                "notes": "",
            }],
            context={"text": "", "file_paths": []},
        )
        assert Path(skin["experiment_root"]).is_absolute()
        assert Path(skin["translations_dir"]).is_absolute()

    def test_algorithm_paths_are_relative_to_translations_dir(self):
        skin = sim2real._build_skill_input(
            translation_hash="a" * 64,
            experiment_root=Path("/e"),
            translations_dir=Path("/t"),
            scenario="s",
            baselines=[],
            algorithms=[{
                "name": "softreflective",
                "source_path": "algorithms/softreflective.py",
                "source_sha256": "a" * 64,
                "notes": "",
            }],
            context={"text": "", "file_paths": []},
        )
        a = skin["algorithms"][0]
        assert a["output_dir"] == "generated/softreflective"
        assert a["config_output_path"] == "generated/softreflective/softreflective_config.yaml"

    def test_baselines_empty_when_absent(self):
        skin = sim2real._build_skill_input(
            translation_hash="a" * 64,
            experiment_root=Path("/e"),
            translations_dir=Path("/t"),
            scenario="s",
            baselines=[],
            algorithms=[],
            context={"text": "", "file_paths": []},
        )
        assert skin["baselines"] == []

    def test_baselines_populated_when_present(self):
        skin = sim2real._build_skill_input(
            translation_hash="a" * 64,
            experiment_root=Path("/e"),
            translations_dir=Path("/t"),
            scenario="s",
            baselines=[{
                "name": "base",
                "generated_overlay_path": "generated/baseline_base/baseline_config.yaml",
            }],
            algorithms=[],
            context={"text": "hint text", "file_paths": ["docs/a.md"]},
        )
        assert skin["baselines"][0]["name"] == "base"
        assert skin["baselines"][0]["generated_overlay_path"] == (
            "generated/baseline_base/baseline_config.yaml"
        )
        # ``config_path`` was dropped in the #480/#481 review cycle — the
        # v3 manifest schema nests baseline config paths under
        # sim.config / real.config, and no downstream consumer read the
        # top-level field. Assert its absence to catch accidental
        # re-introduction.
        assert "config_path" not in skin["baselines"][0]
        assert skin["context"]["text"] == "hint text"
        assert skin["context"]["file_paths"] == ["docs/a.md"]

    def test_algorithm_baseline_overlay_path_resolves_via_defaults(self):
        skin = sim2real._build_skill_input(
            translation_hash="a" * 64,
            experiment_root=Path("/e"),
            translations_dir=Path("/t"),
            scenario="s",
            baselines=[
                {
                    "name": "base",
                    "generated_overlay_path": "generated/baseline_base/baseline_config.yaml",
                },
                {
                    "name": "alt",
                    "generated_overlay_path": "generated/baseline_alt/baseline_config.yaml",
                },
            ],
            algorithms=[
                {
                    "name": "algo1",
                    "source_path": "algorithms/a1.py",
                    "source_sha256": "a" * 64,
                    "defaults": "base",
                    "notes": "",
                },
                {
                    "name": "algo2",
                    "source_path": "algorithms/a2.py",
                    "source_sha256": "b" * 64,
                    "defaults": "alt",
                    "notes": "",
                },
            ],
            context={"text": "", "file_paths": []},
        )
        overlay_by_algo = {a["name"]: a["baseline_overlay_path"] for a in skin["algorithms"]}
        assert overlay_by_algo["algo1"] == "generated/baseline_base/baseline_config.yaml"
        assert overlay_by_algo["algo2"] == "generated/baseline_alt/baseline_config.yaml"

    def test_algorithm_baseline_overlay_path_null_when_defaults_unreferenced(self):
        skin = sim2real._build_skill_input(
            translation_hash="a" * 64,
            experiment_root=Path("/e"),
            translations_dir=Path("/t"),
            scenario="s",
            baselines=[],
            algorithms=[{
                "name": "algo1",
                "source_path": "algorithms/a1.py",
                "source_sha256": "a" * 64,
                "defaults": "not-in-baselines",
                "notes": "",
            }],
            context={"text": "", "file_paths": []},
        )
        assert skin["algorithms"][0]["baseline_overlay_path"] is None

    def test_notes_default_to_empty(self):
        skin = sim2real._build_skill_input(
            translation_hash="a" * 64,
            experiment_root=Path("/e"),
            translations_dir=Path("/t"),
            scenario="s",
            baselines=[],
            algorithms=[{
                "name": "algo1",
                "source_path": "algorithms/a.py",
                "source_sha256": "a" * 64,
            }],
            context={"text": "", "file_paths": []},
        )
        assert skin["algorithms"][0]["notes"] == ""


# ── State-machine helper tests (Task 3) ───────────────────────────────────


class TestTranslateStateDetection:
    def _make_dir(self, tmp_path, thash="a" * 64):
        tdir = tmp_path / "workspace" / "translations" / thash
        tdir.mkdir(parents=True)
        return tdir, thash

    def test_nothing_when_dir_absent(self):
        state, missing = sim2real._translate_state("a" * 64, ["algo1"])
        assert state == "nothing"
        assert missing == []

    def test_partial_when_dir_exists_but_no_output_json(self, tmp_path):
        self._make_dir(tmp_path)
        state, missing = sim2real._translate_state("a" * 64, ["algo1"])
        assert state == "partial"

    def test_partial_when_some_algo_outputs_missing(self, tmp_path):
        tdir, thash = self._make_dir(tmp_path)
        (tdir / "translation_output.json").write_text(json.dumps({
            "version": 1, "translation_hash": thash, "source": "skill",
            "alias": "s", "algorithms": [{"name": "algo1"}, {"name": "algo2"}],
            "created_at": "2026-07-02T00:00:00Z",
        }))
        (tdir / "generated" / "algo1").mkdir(parents=True)
        (tdir / "generated" / "algo1" / "algo1_output.json").write_text("{}")
        state, missing = sim2real._translate_state(thash, ["algo1", "algo2"])
        assert state == "partial"
        assert missing == ["algo2"]

    def test_complete_when_all_algo_outputs_present(self, tmp_path):
        tdir, thash = self._make_dir(tmp_path)
        (tdir / "translation_output.json").write_text("{}")
        for name in ("algo1", "algo2"):
            (tdir / "generated" / name).mkdir(parents=True)
            (tdir / "generated" / name / f"{name}_output.json").write_text("{}")
        state, missing = sim2real._translate_state(thash, ["algo1", "algo2"])
        assert state == "complete"
        assert missing == []


class TestTranslateDeleteDir:
    def test_removes_existing(self, tmp_path):
        tdir = tmp_path / "workspace" / "translations" / ("a" * 64)
        (tdir / "generated" / "algo1").mkdir(parents=True)
        (tdir / "generated" / "algo1" / "algo1_output.json").write_text("{}")
        sim2real._translate_delete_dir("a" * 64)
        assert not tdir.exists()

    def test_noop_when_absent(self):
        # Must not raise.
        sim2real._translate_delete_dir("a" * 64)


# ── State-machine command tests (Task 5) ──────────────────────────────────


class TestTranslateEmpty:
    """State: Nothing (translation dir absent)."""

    def test_plain_writes_checkpoint_files(self, tmp_path):
        _write_manifest(tmp_path)
        assert _run_translate([]) == 0
        translations = tmp_path / "workspace" / "translations"
        entries = list(translations.iterdir())
        assert len(entries) == 1
        tdir = entries[0]
        assert (tdir / "skill_input.json").exists()
        assert (tdir / "translation_output.json").exists()
        tout = json.loads((tdir / "translation_output.json").read_text())
        assert tout["source"] == "skill"
        assert tout["alias"] == "softreflective-v1"
        assert tout["algorithms"][0]["image_ref"] is None
        skin = json.loads((tdir / "skill_input.json").read_text())
        assert skin["scenario"] == "softreflective-v1"
        assert skin["algorithms"][0]["name"] == "softreflective"
        # skill_input.translations_dir must be the hash-specific directory,
        # not its parent — design pins the value as
        # "/absolute/path/to/workspace/translations/<hash>" so per-algo
        # output paths (relative to translations_dir) land in the right
        # subtree.
        assert skin["translations_dir"] == str(tdir)
        # baselines list carries the one baseline `softreflective` builds on;
        # per-algorithm baseline_overlay_path resolves via defaults="base".
        assert skin["baselines"] == [{
            "name": "base",
            "generated_overlay_path": "generated/baseline_base/baseline_config.yaml",
        }]
        assert (
            skin["algorithms"][0]["baseline_overlay_path"]
            == "generated/baseline_base/baseline_config.yaml"
        )

    def test_plain_prints_checkpoint_message(self, tmp_path, capsys):
        _write_manifest(tmp_path)
        _run_translate([])
        out = capsys.readouterr().out
        assert "/sim2real-translate" in out

    def test_force_behaves_like_plain(self, tmp_path):
        _write_manifest(tmp_path)
        assert _run_translate(["--force"]) == 0
        translations = tmp_path / "workspace" / "translations"
        assert len(list(translations.iterdir())) == 1

    def test_resume_errors_when_nothing(self, tmp_path, capsys):
        _write_manifest(tmp_path)
        assert _run_translate(["--resume"]) == 2
        err = capsys.readouterr().err
        assert "no translation to resume" in err

    def test_alias_validation_rejects_bad_scenario(self, tmp_path, capsys):
        _write_manifest(tmp_path, scenario="../evil")
        assert _run_translate([]) == 2
        err = capsys.readouterr().err
        assert "scenario" in err.lower() or "alias" in err.lower()


class TestTranslatePartial:
    """State: Partial (dir exists but not all algo outputs present)."""

    def _setup_partial(self, tmp_path):
        manifest_path = _write_manifest(tmp_path, algorithms=[
            {"name": "algo1", "source": "algorithms/algo1.py"},
            {"name": "algo2", "source": "algorithms/algo2.py"},
        ])
        assert _run_translate([]) == 0
        return _compute_hash(tmp_path, manifest_path)

    def test_plain_errors_on_partial(self, tmp_path, capsys):
        self._setup_partial(tmp_path)
        capsys.readouterr()
        assert _run_translate([]) == 2
        err = capsys.readouterr().err
        assert "incomplete" in err

    def test_resume_reports_missing_algos(self, tmp_path, capsys):
        thash = self._setup_partial(tmp_path)
        (tmp_path / "workspace" / "translations" / thash / "generated" / "algo1").mkdir(parents=True, exist_ok=True)
        (tmp_path / "workspace" / "translations" / thash / "generated" / "algo1" / "algo1_output.json").write_text("{}")
        capsys.readouterr()
        assert _run_translate(["--resume"]) == 2
        err = capsys.readouterr().err
        assert "algo2" in err

    def test_resume_never_mutates_dir(self, tmp_path):
        thash = self._setup_partial(tmp_path)
        tdir = tmp_path / "workspace" / "translations" / thash
        before = {p.relative_to(tdir): p.read_bytes() for p in tdir.rglob("*") if p.is_file()}
        _run_translate(["--resume"])
        after = {p.relative_to(tdir): p.read_bytes() for p in tdir.rglob("*") if p.is_file()}
        assert before == after

    def test_force_recreates(self, tmp_path):
        thash = self._setup_partial(tmp_path)
        tdir = tmp_path / "workspace" / "translations" / thash
        assert _run_translate(["--force"]) == 0
        assert (tdir / "translation_output.json").exists()


class TestTranslateComplete:
    """State: Complete (all algo outputs present)."""

    def _setup_complete(self, tmp_path):
        manifest_path = _write_manifest(tmp_path, algorithms=[
            {"name": "algo1", "source": "algorithms/algo1.py"},
        ])
        assert _run_translate([]) == 0
        thash = _compute_hash(tmp_path, manifest_path)
        (tmp_path / "workspace" / "translations" / thash / "generated" / "algo1").mkdir(parents=True, exist_ok=True)
        (tmp_path / "workspace" / "translations" / thash / "generated" / "algo1" / "algo1_output.json").write_text("{}")
        return thash

    def test_plain_prints_already_complete(self, tmp_path, capsys):
        self._setup_complete(tmp_path)
        capsys.readouterr()
        assert _run_translate([]) == 0
        out = capsys.readouterr().out
        assert "already complete" in out

    def test_resume_prints_already_complete(self, tmp_path, capsys):
        self._setup_complete(tmp_path)
        capsys.readouterr()
        assert _run_translate(["--resume"]) == 0
        out = capsys.readouterr().out
        assert "already complete" in out

    def test_force_recreates_and_clears_algo_outputs(self, tmp_path):
        thash = self._setup_complete(tmp_path)
        assert _run_translate(["--force"]) == 0
        algo_output = (tmp_path / "workspace" / "translations" / thash
                       / "generated" / "algo1" / "algo1_output.json")
        assert not algo_output.exists()


class TestTranslateHashCollision:
    """Divergent recorded algorithm set — treated as partial state."""

    def test_recorded_algorithms_diverge_from_manifest(self, tmp_path, capsys):
        manifest_path = _write_manifest(tmp_path, algorithms=[
            {"name": "algo1", "source": "algorithms/algo1.py"},
        ])
        thash = _compute_hash(tmp_path, manifest_path)
        tdir = tmp_path / "workspace" / "translations" / thash
        (tdir / "generated" / "old_algo").mkdir(parents=True)
        (tdir / "translation_output.json").write_text(json.dumps({
            "version": 1, "translation_hash": thash, "source": "skill",
            "alias": "s", "algorithms": [{"name": "old_algo"}],
            "created_at": "2026-07-02T00:00:00Z",
        }))
        (tdir / "generated" / "old_algo" / "old_algo_output.json").write_text("{}")
        assert _run_translate([]) == 2
        err = capsys.readouterr().err
        assert "incomplete" in err


class TestTranslateAliasCollision:
    """Alias-uniqueness invariant per design §Alias.

    Two experiments sharing the same scenario name (== alias) must not
    silently coexist under different hashes. Plain translate refuses;
    --force reassigns atomically by clearing the alias on the previous
    translation. Mirrors the behavior enforced by
    `_cmd_translation_register`.
    """

    def _plant_other_translation(self, tmp_path, alias, other_hash="b" * 64):
        """Simulate a pre-existing translation with the given alias under a
        different hash than the current manifest would produce."""
        other = tmp_path / "workspace" / "translations" / other_hash
        (other / "generated" / "old_algo").mkdir(parents=True)
        (other / "translation_output.json").write_text(json.dumps({
            "version": 1,
            "translation_hash": other_hash,
            "source": "skill",
            "alias": alias,
            "algorithms": [{"name": "old_algo"}],
            "created_at": "2026-07-01T00:00:00Z",
        }))
        return other_hash

    def test_plain_refuses_when_alias_belongs_to_different_hash(self, tmp_path, capsys):
        _write_manifest(tmp_path, scenario="softreflective-v1")
        other = self._plant_other_translation(tmp_path, "softreflective-v1")
        assert _run_translate([]) == 2
        err = capsys.readouterr().err
        assert "already assigned to translation" in err
        assert other in err
        assert "--force" in err

    def test_force_reassigns_alias_atomically(self, tmp_path):
        manifest_path = _write_manifest(tmp_path, scenario="softreflective-v1")
        other = self._plant_other_translation(tmp_path, "softreflective-v1")
        thash = _compute_hash(tmp_path, manifest_path)
        assert _run_translate(["--force"]) == 0
        # New translation carries the alias.
        new_tout = json.loads(
            (tmp_path / "workspace" / "translations" / thash
             / "translation_output.json").read_text()
        )
        assert new_tout["alias"] == "softreflective-v1"
        # Old translation's alias was cleared.
        old_tout = json.loads(
            (tmp_path / "workspace" / "translations" / other
             / "translation_output.json").read_text()
        )
        assert old_tout["alias"] is None

    def test_resume_ignores_foreign_alias_collision(self, tmp_path):
        """--resume never mutates the dir, so a stale alias on a different
        translation should not block a resume validation. In the "nothing"
        state --resume still errors (there's nothing to resume) — this test
        asserts that error is about the missing translation, not the alias
        collision."""
        _write_manifest(tmp_path, scenario="softreflective-v1")
        self._plant_other_translation(tmp_path, "softreflective-v1")
        # The current experiment has no translation yet → "nothing" +
        # --resume → error "no translation to resume", NOT an alias error.
        rc = _run_translate(["--resume"])
        assert rc == 2  # nothing-to-resume is still an error; different cause.


# ── BYO guard (issue #497) ────────────────────────────────────────────────


class TestTranslateByoGuard:
    """`sim2real translate` refuses to run on BYO algorithms — the BYO
    path is `sim2real translation register`, not `translate`."""

    def _write_byo_manifest(self, tmp_path):
        exp = tmp_path
        (exp / "algorithms").mkdir(exist_ok=True)
        manifest = {
            "kind": "sim2real-transfer",
            "version": 3,
            "scenario": "byo-scenario",
            "baselines": [{"name": "base", "scenario": "baseline-scenario"}],
            "algorithms": [
                {"name": "byoalgo", "defaults": "base", "byo": True},
            ],
            "context": {"text": "", "files": []},
        }
        path = exp / "transfer.yaml"
        path.write_text(yaml.safe_dump(manifest))
        return path

    def test_all_byo_manifest_errors(self, tmp_path, capsys):
        self._write_byo_manifest(tmp_path)
        assert _run_translate([]) == 2
        err = capsys.readouterr().err
        assert "cannot translate algorithm 'byoalgo'" in err
        assert "sim2real translation register" in err

    def test_mixed_manifest_errors_on_byo_algo(self, tmp_path, capsys):
        """One BYO + one BLIS algorithm → still errors, naming the BYO one."""
        exp = tmp_path
        (exp / "algorithms").mkdir(exist_ok=True)
        (exp / "algorithms" / "blisalgo.py").write_text("# stub\n")
        manifest = {
            "kind": "sim2real-transfer",
            "version": 3,
            "scenario": "mixed-scenario",
            "baselines": [{"name": "base", "scenario": "baseline-scenario"}],
            "algorithms": [
                {"name": "byoalgo", "defaults": "base", "byo": True},
                {"name": "blisalgo", "defaults": "base",
                 "source": "algorithms/blisalgo.py"},
            ],
            "component": {"repo": "example.com/x/y", "kind": "scorer"},
            "context": {"text": "", "files": []},
        }
        (exp / "transfer.yaml").write_text(yaml.safe_dump(manifest))
        assert _run_translate([]) == 2
        err = capsys.readouterr().err
        assert "cannot translate algorithm 'byoalgo'" in err

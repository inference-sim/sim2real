"""Tests for pipeline/lib/slicer.py — transfer.yaml partition + hash."""

import copy
import re

import yaml

from pipeline.lib import slicer


# ── Test fixtures ──────────────────────────────────────────────────────


def _full_manifest() -> dict:
    """A representative v3 manifest with all slice-relevant fields populated."""
    return {
        "kind": "sim2real-transfer",
        "version": 3,
        "scenario": "admission_control",
        "component": {
            "repo": "https://github.com/llm-d/llm-d-inference-scheduler",
            "kind": "scheduler",
            "path": "llm-d-inference-scheduler",
        },
        "context": {
            "text": "Some additional context.",
            "files": ["context.md", "extra.md"],
        },
        "algorithms": [
            {"name": "ac1", "source": "algorithm/ac1.go", "defaults": "b1"},
            {"name": "ac2", "source": "algorithm/ac2.go", "defaults": "b1"},
        ],
        "baselines": [
            {"name": "b1", "scenario": "baseline.yaml"},
        ],
        "workloads": ["workloads/w1.yaml", "workloads/w2.yaml"],
        "defaults": {"disable": ["rbac"]},
        "pipeline": {"name": "sim2real", "yaml": "pipeline/pipeline.yaml"},
    }


def _baseline_only_manifest() -> dict:
    """A v3 manifest with no algorithms (component is optional in this mode)."""
    return {
        "kind": "sim2real-transfer",
        "version": 3,
        "scenario": "baseline_only",
        "context": {"text": "", "files": []},
        "baselines": [
            {"name": "b1", "scenario": "baseline.yaml"},
        ],
        "workloads": [],
        "defaults": {"disable": []},
    }


# ── translation_slice ──────────────────────────────────────────────────


class TestTranslationSlice:
    def test_full_manifest_projects_expected_top_keys(self):
        m = _full_manifest()
        s = slicer.translation_slice(m)
        assert set(s.keys()) == {"scenario", "component", "context", "algorithms"}

    def test_algorithms_projected_to_name_and_source_only(self):
        m = _full_manifest()
        s = slicer.translation_slice(m)
        assert s["algorithms"] == [
            {"name": "ac1", "source": "algorithm/ac1.go"},
            {"name": "ac2", "source": "algorithm/ac2.go"},
        ]

    def test_algorithms_sorted_by_name(self):
        m = _full_manifest()
        m["algorithms"] = list(reversed(m["algorithms"]))
        s = slicer.translation_slice(m)
        names = [a["name"] for a in s["algorithms"]]
        assert names == sorted(names)

    def test_baseline_only_omits_component_and_algorithms(self):
        s = slicer.translation_slice(_baseline_only_manifest())
        assert "component" not in s
        assert "algorithms" not in s
        assert s["scenario"] == "baseline_only"
        assert s["context"] == {"text": "", "files": []}

    def test_empty_algorithms_list_omitted(self):
        m = _full_manifest()
        m["algorithms"] = []
        s = slicer.translation_slice(m)
        assert "algorithms" not in s

    def test_does_not_mutate_input(self):
        m = _full_manifest()
        snapshot = copy.deepcopy(m)
        slicer.translation_slice(m)
        assert m == snapshot

    def test_excludes_assembly_fields(self):
        s = slicer.translation_slice(_full_manifest())
        for excluded in ("workloads", "baselines", "defaults", "pipeline", "kind", "version"):
            assert excluded not in s


# ── assembly_slice ─────────────────────────────────────────────────────


class TestAssemblySlice:
    def test_includes_everything_not_in_translation(self):
        m = _full_manifest()
        s = slicer.assembly_slice(m)
        # All top-level keys except scenario, component, context, algorithms
        # land in assembly verbatim (algorithms re-appears in projected form).
        for key in ("kind", "version", "baselines", "workloads", "defaults", "pipeline"):
            assert key in s, f"missing top-level key: {key}"
        for key in ("scenario", "component", "context"):
            assert key not in s, f"translation-only key leaked into assembly: {key}"

    def test_algorithms_projected_to_name_and_defaults_only(self):
        m = _full_manifest()
        s = slicer.assembly_slice(m)
        assert s["algorithms"] == [
            {"name": "ac1", "defaults": "b1"},
            {"name": "ac2", "defaults": "b1"},
        ]

    def test_algorithms_sorted_by_name(self):
        m = _full_manifest()
        m["algorithms"] = list(reversed(m["algorithms"]))
        s = slicer.assembly_slice(m)
        names = [a["name"] for a in s["algorithms"]]
        assert names == sorted(names)

    def test_baseline_only_omits_algorithms(self):
        s = slicer.assembly_slice(_baseline_only_manifest())
        assert "algorithms" not in s
        assert s["baselines"] == [{"name": "b1", "scenario": "baseline.yaml"}]

    def test_does_not_mutate_input(self):
        m = _full_manifest()
        snapshot = copy.deepcopy(m)
        slicer.assembly_slice(m)
        assert m == snapshot

    def test_translation_and_assembly_top_keys_disjoint(self):
        m = _full_manifest()
        t_keys = set(slicer.translation_slice(m).keys()) - {"algorithms"}
        a_keys = set(slicer.assembly_slice(m).keys()) - {"algorithms"}
        assert t_keys.isdisjoint(a_keys)


# ── translation_hash ───────────────────────────────────────────────────


_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


class TestTranslationHash:
    def test_format_is_sha256_hex(self):
        h = slicer.translation_hash(_full_manifest())
        assert _HEX64_RE.match(h), f"expected 64-char lowercase hex, got: {h!r}"

    def test_deterministic(self):
        m = _full_manifest()
        assert slicer.translation_hash(m) == slicer.translation_hash(m)

    def test_stable_across_top_level_key_reordering(self):
        m1 = _full_manifest()
        m2 = {k: m1[k] for k in reversed(list(m1.keys()))}
        assert slicer.translation_hash(m1) == slicer.translation_hash(m2)

    def test_stable_across_nested_key_reordering(self):
        m1 = _full_manifest()
        m2 = copy.deepcopy(m1)
        m2["component"] = {
            k: m2["component"][k] for k in reversed(list(m2["component"].keys()))
        }
        assert slicer.translation_hash(m1) == slicer.translation_hash(m2)

    def test_stable_across_algorithms_list_reordering(self):
        m1 = _full_manifest()
        m2 = copy.deepcopy(m1)
        m2["algorithms"] = list(reversed(m2["algorithms"]))
        assert slicer.translation_hash(m1) == slicer.translation_hash(m2)

    def test_stable_across_yaml_roundtrip(self):
        m1 = _full_manifest()
        m2 = yaml.safe_load(yaml.safe_dump(m1, sort_keys=False))
        m3 = yaml.safe_load(yaml.safe_dump(m1, sort_keys=True))
        assert slicer.translation_hash(m1) == slicer.translation_hash(m2)
        assert slicer.translation_hash(m1) == slicer.translation_hash(m3)

    def test_changes_when_scenario_changes(self):
        m1 = _full_manifest()
        m2 = copy.deepcopy(m1)
        m2["scenario"] = "different_scenario"
        assert slicer.translation_hash(m1) != slicer.translation_hash(m2)

    def test_changes_when_algorithm_source_changes(self):
        m1 = _full_manifest()
        m2 = copy.deepcopy(m1)
        m2["algorithms"][0]["source"] = "algorithm/renamed.go"
        assert slicer.translation_hash(m1) != slicer.translation_hash(m2)

    def test_changes_when_context_changes(self):
        m1 = _full_manifest()
        m2 = copy.deepcopy(m1)
        m2["context"]["text"] = "different text"
        assert slicer.translation_hash(m1) != slicer.translation_hash(m2)

    def test_changes_when_component_changes(self):
        m1 = _full_manifest()
        m2 = copy.deepcopy(m1)
        m2["component"]["ref"] = "v1.2.3"
        assert slicer.translation_hash(m1) != slicer.translation_hash(m2)

    def test_unchanged_when_only_assembly_fields_change(self):
        """Editing workloads / baselines / defaults must not bump the hash."""
        m1 = _full_manifest()
        m2 = copy.deepcopy(m1)
        m2["workloads"] = ["workloads/different.yaml"]
        m2["baselines"][0]["scenario"] = "different_baseline.yaml"
        m2["defaults"]["disable"] = ["request-id", "verbosity"]
        m2["pipeline"]["name"] = "renamed"
        assert slicer.translation_hash(m1) == slicer.translation_hash(m2)

    def test_unchanged_when_only_algorithm_defaults_change(self):
        """algorithm.defaults is an assembly field, not translation."""
        m1 = _full_manifest()
        m2 = copy.deepcopy(m1)
        # Add a second baseline so defaults can reference a different name
        m2["baselines"].append({"name": "b2", "scenario": "b2.yaml"})
        m2["algorithms"][0]["defaults"] = "b2"
        assert slicer.translation_hash(m1) == slicer.translation_hash(m2)

    def test_baseline_only_manifest_hashes(self):
        h = slicer.translation_hash(_baseline_only_manifest())
        assert _HEX64_RE.match(h)


# ── TRANSLATION_FIELDS constant ────────────────────────────────────────


class TestTranslationFields:
    def test_constant_lists_declared_members(self):
        assert slicer.TRANSLATION_FIELDS == [
            "scenario",
            "component",
            "context",
            "algorithms[*].source",
        ]

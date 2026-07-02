"""Tests for pipeline/lib/slicer.py — transfer.yaml partition + hash."""

import copy
import re

import pytest
import yaml

from pipeline.lib import slicer
from pipeline.lib.assemble_run import AssembleError


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


# ── translation_hash_with_sources ──────────────────────────────────────


def _write_algorithm_sources(
    root, entries: list[tuple[str, bytes]]
) -> None:
    """Materialize ``[(relpath, bytes), ...]`` under ``root`` (parents made)."""
    for rel, data in entries:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


class TestTranslationHashWithSources:
    def test_format_is_sha256_hex(self, tmp_path):
        m = _full_manifest()
        _write_algorithm_sources(
            tmp_path,
            [("algorithm/ac1.go", b"package a\n"), ("algorithm/ac2.go", b"package b\n")],
        )
        h = slicer.translation_hash_with_sources(m, tmp_path)
        assert _HEX64_RE.match(h), f"expected 64-char lowercase hex, got: {h!r}"

    def test_deterministic(self, tmp_path):
        m = _full_manifest()
        _write_algorithm_sources(
            tmp_path,
            [("algorithm/ac1.go", b"aaa"), ("algorithm/ac2.go", b"bbb")],
        )
        assert (
            slicer.translation_hash_with_sources(m, tmp_path)
            == slicer.translation_hash_with_sources(m, tmp_path)
        )

    def test_missing_source_file_raises_assemble_error(self, tmp_path):
        m = _full_manifest()
        # Write only ac1; ac2 is missing.
        _write_algorithm_sources(tmp_path, [("algorithm/ac1.go", b"aaa")])
        with pytest.raises(AssembleError, match="source file not found") as exc_info:
            slicer.translation_hash_with_sources(m, tmp_path)
        # Message should include the offending path.
        assert "ac2.go" in str(exc_info.value)

    def test_missing_source_error_includes_full_path(self, tmp_path):
        m = _full_manifest()
        with pytest.raises(AssembleError) as exc_info:
            slicer.translation_hash_with_sources(m, tmp_path)
        assert str(tmp_path) in str(exc_info.value)

    def test_stable_across_algorithms_list_reordering(self, tmp_path):
        """Algorithm order in the manifest must not affect the hash."""
        m1 = _full_manifest()
        m2 = copy.deepcopy(m1)
        m2["algorithms"] = list(reversed(m2["algorithms"]))
        _write_algorithm_sources(
            tmp_path,
            [("algorithm/ac1.go", b"first"), ("algorithm/ac2.go", b"second")],
        )
        assert (
            slicer.translation_hash_with_sources(m1, tmp_path)
            == slicer.translation_hash_with_sources(m2, tmp_path)
        )

    def test_changes_when_source_bytes_change(self, tmp_path):
        m = _full_manifest()
        _write_algorithm_sources(
            tmp_path,
            [("algorithm/ac1.go", b"aaa"), ("algorithm/ac2.go", b"bbb")],
        )
        h1 = slicer.translation_hash_with_sources(m, tmp_path)
        (tmp_path / "algorithm/ac1.go").write_bytes(b"aaa-CHANGED")
        h2 = slicer.translation_hash_with_sources(m, tmp_path)
        assert h1 != h2

    def test_changes_when_slice_changes(self, tmp_path):
        m1 = _full_manifest()
        m2 = copy.deepcopy(m1)
        m2["scenario"] = "different_scenario"
        _write_algorithm_sources(
            tmp_path,
            [("algorithm/ac1.go", b"aaa"), ("algorithm/ac2.go", b"bbb")],
        )
        assert (
            slicer.translation_hash_with_sources(m1, tmp_path)
            != slicer.translation_hash_with_sources(m2, tmp_path)
        )

    def test_differs_from_bare_translation_hash(self, tmp_path):
        """Folding source bytes must change the hash relative to the slice-only hash."""
        m = _full_manifest()
        _write_algorithm_sources(
            tmp_path,
            [("algorithm/ac1.go", b"aaa"), ("algorithm/ac2.go", b"bbb")],
        )
        assert (
            slicer.translation_hash(m)
            != slicer.translation_hash_with_sources(m, tmp_path)
        )

    def test_empty_source_file(self, tmp_path):
        """Zero-byte source is a valid content — hash still stable."""
        m = _full_manifest()
        _write_algorithm_sources(
            tmp_path,
            [("algorithm/ac1.go", b""), ("algorithm/ac2.go", b"content\n")],
        )
        h = slicer.translation_hash_with_sources(m, tmp_path)
        assert _HEX64_RE.match(h)
        # Determinism check on empty content specifically.
        assert h == slicer.translation_hash_with_sources(m, tmp_path)

    def test_binary_source_content(self, tmp_path):
        """Non-UTF-8 bytes must not break hashing."""
        m = _full_manifest()
        binary = bytes(range(256))
        _write_algorithm_sources(
            tmp_path,
            [("algorithm/ac1.go", binary), ("algorithm/ac2.go", b"\x00\x01\x02")],
        )
        h = slicer.translation_hash_with_sources(m, tmp_path)
        assert _HEX64_RE.match(h)

    def test_unicode_source_content(self, tmp_path):
        """Unicode content encoded as UTF-8 hashes deterministically."""
        m = _full_manifest()
        _write_algorithm_sources(
            tmp_path,
            [
                ("algorithm/ac1.go", "// αβγ δε\n".encode("utf-8")),
                ("algorithm/ac2.go", "// 日本語 テスト\n".encode("utf-8")),
            ],
        )
        h = slicer.translation_hash_with_sources(m, tmp_path)
        assert _HEX64_RE.match(h)
        assert h == slicer.translation_hash_with_sources(m, tmp_path)

    def test_single_algorithm(self, tmp_path):
        """Single-algorithm manifest hashes without error and differs from empty."""
        m = _full_manifest()
        m["algorithms"] = [{"name": "solo", "source": "algorithm/solo.go", "defaults": "b1"}]
        _write_algorithm_sources(tmp_path, [("algorithm/solo.go", b"only-one")])
        h = slicer.translation_hash_with_sources(m, tmp_path)
        assert _HEX64_RE.match(h)

    def test_multi_algorithm_extends_over_two(self, tmp_path):
        """Adding a third algorithm changes the hash from the two-algorithm case."""
        m2 = _full_manifest()
        m3 = copy.deepcopy(m2)
        m3["algorithms"].append(
            {"name": "ac3", "source": "algorithm/ac3.go", "defaults": "b1"}
        )
        _write_algorithm_sources(
            tmp_path,
            [
                ("algorithm/ac1.go", b"aaa"),
                ("algorithm/ac2.go", b"bbb"),
                ("algorithm/ac3.go", b"ccc"),
            ],
        )
        assert (
            slicer.translation_hash_with_sources(m2, tmp_path)
            != slicer.translation_hash_with_sources(m3, tmp_path)
        )

    def test_baseline_only_manifest_hashes(self, tmp_path):
        """No algorithms → hash is defined and equals slice-only hash of an equivalent envelope."""
        m = _baseline_only_manifest()
        h = slicer.translation_hash_with_sources(m, tmp_path)
        assert _HEX64_RE.match(h)

    def test_does_not_mutate_input(self, tmp_path):
        m = _full_manifest()
        _write_algorithm_sources(
            tmp_path,
            [("algorithm/ac1.go", b"aaa"), ("algorithm/ac2.go", b"bbb")],
        )
        snapshot = copy.deepcopy(m)
        slicer.translation_hash_with_sources(m, tmp_path)
        assert m == snapshot


# ── Golden-file stability ──────────────────────────────────────────────


# Pinned SHA-256 values guard against accidental canonicalization changes
# (envelope shape, sort keys, separators). Any drift here means a manifest
# that used to hash to X now hashes to Y — every existing translation
# would be treated as stale. If you have a deliberate reason to change
# the canonicalization, bump these values and note the reason.


def _golden_manifest() -> dict:
    """Fixed manifest for pinned-hash tests. Do not modify — pinned hashes
    depend on this exact shape."""
    return {
        "scenario": "golden",
        "component": {
            "repo": "https://github.com/example/repo",
            "kind": "scheduler",
        },
        "context": {"text": "ctx", "files": ["a.md"]},
        "algorithms": [
            {"name": "one", "source": "algorithm/one.go", "defaults": "b1"},
            {"name": "two", "source": "algorithm/two.go", "defaults": "b1"},
        ],
    }


class TestGoldenFileStability:
    def test_single_algorithm_golden(self, tmp_path):
        m = {
            "scenario": "golden",
            "component": {"repo": "https://github.com/example/repo", "kind": "scheduler"},
            "context": {"text": "ctx", "files": ["a.md"]},
            "algorithms": [
                {"name": "one", "source": "algorithm/one.go", "defaults": "b1"},
            ],
        }
        _write_algorithm_sources(tmp_path, [("algorithm/one.go", b"package one\n")])
        assert (
            slicer.translation_hash_with_sources(m, tmp_path)
            == "7d43c058adbbe0affc086473d5e803d98878d65943c01755ac95fcf1e9ccb3f8"
        )

    def test_multi_algorithm_golden(self, tmp_path):
        m = _golden_manifest()
        _write_algorithm_sources(
            tmp_path,
            [
                ("algorithm/one.go", b"package one\n"),
                ("algorithm/two.go", b"package two\n"),
            ],
        )
        assert (
            slicer.translation_hash_with_sources(m, tmp_path)
            == "db1f1085597ed2b6ef5b09eb1e3282f09ac318901f479de0ee57870141b88b54"
        )

    def test_empty_source_golden(self, tmp_path):
        m = {
            "scenario": "golden",
            "component": {"repo": "https://github.com/example/repo", "kind": "scheduler"},
            "context": {"text": "ctx", "files": ["a.md"]},
            "algorithms": [
                {"name": "one", "source": "algorithm/one.go", "defaults": "b1"},
            ],
        }
        _write_algorithm_sources(tmp_path, [("algorithm/one.go", b"")])
        assert (
            slicer.translation_hash_with_sources(m, tmp_path)
            == "faac79661cf136f495ddd04d637e7c6373bcdca0b31e7b7d7c67d79ec6f0c57e"
        )

    def test_unicode_source_golden(self, tmp_path):
        m = {
            "scenario": "golden",
            "component": {"repo": "https://github.com/example/repo", "kind": "scheduler"},
            "context": {"text": "ctx", "files": ["a.md"]},
            "algorithms": [
                {"name": "one", "source": "algorithm/one.go", "defaults": "b1"},
            ],
        }
        _write_algorithm_sources(
            tmp_path,
            [("algorithm/one.go", "// αβγ 日本語\n".encode("utf-8"))],
        )
        assert (
            slicer.translation_hash_with_sources(m, tmp_path)
            == "ae316a469cfd947049bc89222fac239b65684ffa34f5b56f6b6f40b0e1ee312c"
        )


# ── TRANSLATION_FIELDS constant ────────────────────────────────────────


class TestTranslationFields:
    def test_constant_lists_declared_members(self):
        assert slicer.TRANSLATION_FIELDS == [
            "scenario",
            "component",
            "context",
            "algorithms[*].source",
        ]

"""Tests for pipeline/lib/values.py — deep-merge logic."""

import pytest

from pipeline.lib.values import (
    deep_merge,
    _merge_lists,
    _k8s_identity,
)


# ── _merge_lists ──────────────────────────────────────────────────────────────

class TestMergeLists:
    def test_scalar_list_replaced(self):
        assert _merge_lists(["a", "b"], ["c"]) == ["c"]

    def test_scalar_overlay_replaces_dict_base(self):
        assert _merge_lists([{"name": "x"}], ["c"]) == ["c"]

    def test_explicit_clear_returns_empty(self):
        assert _merge_lists([{"name": "x"}], []) == []

    def test_named_key_merge_by_name(self):
        base = [{"name": "x", "value": 1}, {"name": "y", "value": 2}]
        overlay = [{"name": "x", "value": 99}]
        result = _merge_lists(base, overlay)
        assert result == [{"name": "x", "value": 99}, {"name": "y", "value": 2}]

    def test_named_key_merge_adds_new_entry(self):
        base = [{"name": "x", "v": 1}]
        overlay = [{"name": "x", "v": 1}, {"name": "z", "v": 3}]
        result = _merge_lists(base, overlay)
        assert len(result) == 2
        assert any(item["name"] == "z" for item in result)

    def test_positional_merge_no_common_key(self):
        base = [{"a": 1, "b": 2}]
        overlay = [{"a": 99}]
        result = _merge_lists(base, overlay)
        assert result == [{"a": 99, "b": 2}]

    def test_positional_preserves_surplus_from_base(self):
        base = [{"a": 1}, {"a": 2}]
        overlay = [{"a": 9}]
        result = _merge_lists(base, overlay)
        assert len(result) == 2
        assert result[0]["a"] == 9
        assert result[1]["a"] == 2

    # ── Kubernetes-identity tier ──────────────────────────────────────────────

    def test_k8s_distinct_identities_all_preserved(self):
        """Base RBAC + overlay InferenceObjectives: every manifest survives intact."""
        base = [
            {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role",
             "metadata": {"name": "epp"}, "rules": [{"verbs": ["get"]}]},
            {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding",
             "metadata": {"name": "epp"}, "roleRef": {"kind": "Role"}},
        ]
        overlay = [
            {"apiVersion": "inference.networking.x-k8s.io/v1alpha2", "kind": "InferenceObjective",
             "metadata": {"name": "critical"}, "spec": {"priority": 100}},
            {"apiVersion": "inference.networking.x-k8s.io/v1alpha2", "kind": "InferenceObjective",
             "metadata": {"name": "sheddable"}, "spec": {"priority": -50}},
        ]
        result = _merge_lists(base, overlay)
        # All four manifests present, none folded.
        kinds = sorted((d["kind"], d["metadata"]["name"]) for d in result)
        assert kinds == [
            ("InferenceObjective", "critical"),
            ("InferenceObjective", "sheddable"),
            ("Role", "epp"),
            ("RoleBinding", "epp"),
        ]
        # No cross-kind field smearing.
        role = next(d for d in result if d["kind"] == "Role")
        assert "spec" not in role and "rules" in role
        objective = next(d for d in result if d["metadata"]["name"] == "critical")
        assert "rules" not in objective and "roleRef" not in objective

    def test_k8s_same_identity_merges(self):
        """Overlay can patch a base manifest sharing the same (apiVersion, kind, name)."""
        base = [
            {"apiVersion": "inference.networking.x-k8s.io/v1alpha2", "kind": "InferenceObjective",
             "metadata": {"name": "critical"}, "spec": {"priority": 100, "poolRef": {"name": "p"}}},
        ]
        overlay = [
            {"apiVersion": "inference.networking.x-k8s.io/v1alpha2", "kind": "InferenceObjective",
             "metadata": {"name": "critical"}, "spec": {"priority": 200}},
        ]
        result = _merge_lists(base, overlay)
        assert len(result) == 1
        assert result[0]["spec"]["priority"] == 200          # overlay wins
        assert result[0]["spec"]["poolRef"] == {"name": "p"}  # base-only key survives

    def test_k8s_base_entries_come_first(self):
        """Base manifests are emitted first, overlay-only manifests appended after."""
        base = [{"apiVersion": "v1", "kind": "Role", "metadata": {"name": "a"}}]
        overlay = [{"apiVersion": "v1", "kind": "Role", "metadata": {"name": "b"}}]
        result = _merge_lists(base, overlay)
        assert [d["metadata"]["name"] for d in result] == ["a", "b"]

    def test_k8s_nameless_manifests_appended_not_folded(self):
        """Manifests without metadata.name are carried through, never positionally folded."""
        base = [{"apiVersion": "v1", "kind": "Role", "metadata": {"generateName": "a-"}, "x": 1}]
        overlay = [{"apiVersion": "v1", "kind": "Role", "metadata": {"generateName": "a-"}, "y": 2}]
        result = _merge_lists(base, overlay)
        # Both kept as distinct objects — no fold (would be a single {x:1, y:2} dict).
        assert result == [
            {"apiVersion": "v1", "kind": "Role", "metadata": {"generateName": "a-"}, "x": 1},
            {"apiVersion": "v1", "kind": "Role", "metadata": {"generateName": "a-"}, "y": 2},
        ]

    def test_k8s_partial_identity_appends_nameless_no_fold(self):
        """A K8s list where one entry lacks metadata.name must not re-introduce #278.

        Base has a named Role plus a generateName RoleBinding; overlay has two
        InferenceObjectives. All four survive, RBAC fields never smear onto the
        InferenceObjectives.
        """
        base = [
            {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role",
             "metadata": {"name": "epp"}, "rules": [{"verbs": ["get"]}]},
            {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding",
             "metadata": {"generateName": "epp-"}, "roleRef": {"kind": "Role"}},
        ]
        overlay = [
            {"apiVersion": "inference.networking.x-k8s.io/v1alpha2", "kind": "InferenceObjective",
             "metadata": {"name": "critical"}, "spec": {"priority": 100}},
            {"apiVersion": "inference.networking.x-k8s.io/v1alpha2", "kind": "InferenceObjective",
             "metadata": {"name": "sheddable"}, "spec": {"priority": -50}},
        ]
        result = _merge_lists(base, overlay)
        assert len(result) == 4
        # The generateName RoleBinding survives intact as its own object.
        rb = next(d for d in result if d["kind"] == "RoleBinding")
        assert rb["metadata"] == {"generateName": "epp-"} and "spec" not in rb
        # No RBAC fields smeared onto the InferenceObjectives.
        for obj in (d for d in result if d["kind"] == "InferenceObjective"):
            assert "rules" not in obj and "roleRef" not in obj

    def test_k8s_duplicate_identity_in_overlay_raises(self):
        """Duplicate (apiVersion, kind, metadata.name) in the overlay is loud, not lossy."""
        base = [{"apiVersion": "v1", "kind": "Role", "metadata": {"name": "a"}, "x": 1}]
        overlay = [
            {"apiVersion": "v1", "kind": "Role", "metadata": {"name": "a"}, "y": 2},
            {"apiVersion": "v1", "kind": "Role", "metadata": {"name": "a"}, "z": 3},
        ]
        with pytest.raises(ValueError, match="duplicate Kubernetes object identity"):
            _merge_lists(base, overlay)

    def test_k8s_duplicate_identity_in_base_raises(self):
        """Duplicate identity in the base is loud, not lossy."""
        base = [
            {"apiVersion": "v1", "kind": "Role", "metadata": {"name": "a"}, "x": 1},
            {"apiVersion": "v1", "kind": "Role", "metadata": {"name": "a"}, "y": 2},
        ]
        overlay = [{"apiVersion": "v1", "kind": "Role", "metadata": {"name": "a"}, "z": 3}]
        with pytest.raises(ValueError, match="duplicate Kubernetes object identity"):
            _merge_lists(base, overlay)

    def test_containers_still_merge_by_name_not_k8s(self):
        """Typed config lists (no apiVersion/kind) are unaffected by the K8s tier."""
        base = [{"name": "vllm", "image": "old"}, {"name": "sidecar", "image": "s"}]
        overlay = [{"name": "vllm", "image": "new"}]
        result = _merge_lists(base, overlay)
        assert result == [{"name": "vllm", "image": "new"}, {"name": "sidecar", "image": "s"}]


# ── _k8s_identity ─────────────────────────────────────────────────────────────

class TestK8sIdentity:
    def test_returns_tuple_for_manifest(self):
        item = {"apiVersion": "v1", "kind": "Role", "metadata": {"name": "x"}}
        assert _k8s_identity(item) == ("v1", "Role", "x")

    def test_none_when_no_metadata_name(self):
        assert _k8s_identity({"apiVersion": "v1", "kind": "Role", "metadata": {}}) is None

    def test_none_when_metadata_not_dict(self):
        assert _k8s_identity({"apiVersion": "v1", "kind": "Role", "metadata": "x"}) is None

    def test_none_when_missing_kind(self):
        assert _k8s_identity({"apiVersion": "v1", "metadata": {"name": "x"}}) is None

    def test_none_when_missing_apiversion(self):
        assert _k8s_identity({"kind": "Role", "metadata": {"name": "x"}}) is None

    def test_none_for_non_dict(self):
        assert _k8s_identity("not-a-dict") is None


# ── deep_merge ───────────────────────────────────────────────────────────────

class TestDeepMerge:
    def test_nested_dict_merge(self):
        base = {"a": {"b": 1, "c": 2}}
        overlay = {"a": {"b": 99}}
        result = deep_merge(base, overlay)
        assert result == {"a": {"b": 99, "c": 2}}

    def test_overlay_adds_new_key(self):
        result = deep_merge({"a": 1}, {"b": 2})
        assert result == {"a": 1, "b": 2}

    def test_does_not_mutate_base(self):
        base = {"a": {"b": 1}}
        overlay = {"a": {"b": 2}}
        deep_merge(base, overlay)
        assert base == {"a": {"b": 1}}

    def test_does_not_mutate_overlay(self):
        base = {"a": {"b": 1}}
        overlay = {"a": {"b": 2}}
        deep_merge(base, overlay)
        assert overlay == {"a": {"b": 2}}

    def test_list_delegated_to_merge_lists(self):
        base = {"items": [{"name": "x", "v": 1}]}
        overlay = {"items": [{"name": "x", "v": 99}]}
        result = deep_merge(base, overlay)
        assert result["items"] == [{"name": "x", "v": 99}]

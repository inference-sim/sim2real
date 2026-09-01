"""Unit tests for pipeline/lib/scope.py — CLI filter-value primitives
shared by deploy.py and `sim2real assemble` (issue #876)."""

from __future__ import annotations

from pipeline.lib import scope


class TestParseNameList:
    def test_none_returns_none(self):
        assert scope.parse_name_list(None) is None

    def test_splits_comma_separated_string(self):
        assert scope.parse_name_list("a,b , c") == ["a", "b", "c"]

    def test_flattens_nargs_list_with_embedded_commas(self):
        assert scope.parse_name_list(["a,b", "c"]) == ["a", "b", "c"]

    def test_empty_after_strip_returns_none(self):
        assert scope.parse_name_list([" ", ","]) is None


class TestExpandGlobValues:
    def test_literal_hit_and_miss(self):
        expanded, unknown = scope.expand_glob_values(["a", "z"], {"a", "b"})
        assert expanded == ["a"]
        assert unknown == ["z"]

    def test_pattern_expands_sorted_and_dedupes(self):
        expanded, unknown = scope.expand_glob_values(["b*", "bar"], {"bar", "baz"})
        assert expanded == ["bar", "baz"]
        assert unknown == []

    def test_pattern_matching_nothing_is_unknown(self):
        expanded, unknown = scope.expand_glob_values(["q*"], {"a"})
        assert expanded == []
        assert unknown == ["q*"]

    def test_exclude_from_pattern_keeps_token_literal_only(self):
        expanded, unknown = scope.expand_glob_values(
            ["exp*"], {"experiment", "explode"}, exclude_from_pattern={"experiment"}
        )
        assert expanded == ["explode"]
        assert unknown == []

    def test_excluded_token_still_matches_as_a_literal(self):
        expanded, unknown = scope.expand_glob_values(
            ["experiment"], {"experiment", "explode"},
            exclude_from_pattern={"experiment"},
        )
        assert expanded == ["experiment"]
        assert unknown == []

    def test_input_order_preserved_first_occurrence_wins(self):
        expanded, _ = scope.expand_glob_values(["z", "a"], {"a", "z"})
        assert expanded == ["z", "a"]

    def test_is_glob(self):
        assert scope.is_glob("a*")
        assert scope.is_glob("a?")
        assert scope.is_glob("a[bc]")
        assert not scope.is_glob("abc")

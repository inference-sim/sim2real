"""Tests for pipeline/lib/scope.py — CLI filter-value primitives shared by
deploy.py and `sim2real assemble`.

The glob tests moved here from test_deploy_helpers.py when the functions were
lifted out of deploy.py (issue #876); they are unchanged apart from the import.
"""

from __future__ import annotations

from pipeline.lib.scope import expand_glob_values, is_glob, parse_name_list


# ── parse_name_list ───────────────────────────────────────────────────────


class TestParseNameList:
    """Tests for flattening a comma-or-space-separated nargs='+' flag value."""

    def test_none_returns_none(self):
        assert parse_name_list(None) is None

    def test_splits_comma_separated_string(self):
        assert parse_name_list("a,b , c") == ["a", "b", "c"]

    def test_flattens_nargs_list_with_embedded_commas(self):
        assert parse_name_list(["a,b", "c"]) == ["a", "b", "c"]

    def test_empty_after_strip_returns_none(self):
        assert parse_name_list([" ", ","]) is None


# ── is_glob ───────────────────────────────────────────────────────────────


class TestIsGlob:
    """Tests for glob metachar detection."""

    def test_star_is_glob(self):
        assert is_glob("wl-*") is True

    def test_question_mark_is_glob(self):
        assert is_glob("wl-?") is True

    def test_bracket_is_glob(self):
        assert is_glob("wl-[abc]") is True

    def test_literal_is_not_glob(self):
        assert is_glob("wl-code-gen|softreflective|i1") is False

    def test_empty_string_is_not_glob(self):
        assert is_glob("") is False

    def test_star_in_middle(self):
        assert is_glob("wl-code*gen") is True

    def test_hyphen_is_not_glob(self):
        assert is_glob("some-literal-value") is False


# ── expand_glob_values ────────────────────────────────────────────────────


class TestExpandGlobValues:
    """Tests for mixed literal/glob pattern expansion."""

    def test_literal_in_valid_set(self):
        expanded, unknown = expand_glob_values(
            ["alpha", "beta"],
            {"alpha", "beta", "gamma"},
        )
        assert expanded == ["alpha", "beta"]
        assert unknown == []

    def test_literal_not_in_valid_set(self):
        expanded, unknown = expand_glob_values(
            ["nonexistent"],
            {"alpha", "beta"},
        )
        assert expanded == []
        assert unknown == ["nonexistent"]

    def test_star_glob_expands(self):
        expanded, unknown = expand_glob_values(
            ["alpha*"],
            {"alpha1", "alpha2", "beta1"},
        )
        assert set(expanded) == {"alpha1", "alpha2"}
        assert unknown == []

    def test_glob_expansion_is_sorted(self):
        """The pattern pool is sorted, so a pattern's matches land in name
        order regardless of the iteration order of *valid*."""
        expanded, _ = expand_glob_values(["b*"], {"baz", "bar"})
        assert expanded == ["bar", "baz"]

    def test_glob_no_match_reported_as_unknown(self):
        expanded, unknown = expand_glob_values(
            ["zzz*"],
            {"alpha", "beta"},
        )
        assert expanded == []
        assert unknown == ["zzz*"]

    def test_dedup_preserves_first_occurrence_order(self):
        expanded, unknown = expand_glob_values(
            ["beta", "alpha", "beta"],
            {"alpha", "beta", "gamma"},
        )
        assert expanded == ["beta", "alpha"]
        assert unknown == []

    def test_glob_dedup_against_earlier_literal(self):
        """If a literal appears before a glob, the glob doesn't re-add it."""
        expanded, unknown = expand_glob_values(
            ["alpha1", "alpha*"],
            {"alpha1", "alpha2"},
        )
        assert expanded == ["alpha1", "alpha2"]
        assert unknown == []

    def test_exclude_from_pattern(self):
        """Excluded names don't match globs but can be used as literals."""
        expanded, unknown = expand_glob_values(
            ["exp*", "experiment"],
            {"alpha", "experiment", "export"},
            exclude_from_pattern={"experiment"},
        )
        # "exp*" matches "export" from the pattern pool (which excludes "experiment")
        # "experiment" is a literal and IS in valid set
        assert "export" in expanded
        assert "experiment" in expanded
        assert unknown == []

    def test_question_mark_glob(self):
        expanded, _ = expand_glob_values(
            ["a?"],
            {"a1", "a2", "ab", "abc"},
        )
        assert set(expanded) == {"a1", "a2", "ab"}

    def test_bracket_glob(self):
        expanded, _ = expand_glob_values(
            ["a[12]"],
            {"a1", "a2", "a3"},
        )
        assert set(expanded) == {"a1", "a2"}

    def test_mixed_literals_and_globs(self):
        expanded, unknown = expand_glob_values(
            ["gamma", "alpha*", "missing"],
            {"alpha1", "alpha2", "beta", "gamma"},
        )
        assert expanded[0] == "gamma"
        assert "alpha1" in expanded
        assert "alpha2" in expanded
        assert unknown == ["missing"]

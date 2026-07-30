"""Unit tests for deploy.py capacity-gating and dispatch selection helpers.

Covers:
  - _capacity_limited_pairs: greedy GPU budget allocation
  - _select_dispatchable: shuffled dispatch with capacity gating
  - _derive_pair_gpu_costs: GPU cost derivation per pair from scenario content
  - _parse_list: CLI value flattening
  - _is_glob / _expand_glob_values: shell-glob expansion against valid sets
  - _fmt_size: human-readable byte formatting
  - _is_up_to_date: local/remote mtime comparison
  - _is_iteration_up_to_date: iteration-level mtime comparison

These are pure or near-pure functions that require no cluster access.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import patch

import pipeline.deploy as deploy


# ── _capacity_limited_pairs ──────────────────────────────────────────────────

class TestCapacityLimitedPairs:
    """Tests for _capacity_limited_pairs."""

    def test_all_fit(self):
        """All pairs fit within budget."""
        pending = ["a", "b", "c"]
        cost_map = {"a": 2, "b": 2, "c": 2}
        result = deploy._capacity_limited_pairs(
            pending, free_gpus=8, cost_map=cost_map
        )
        assert set(result) == {"a", "b", "c"}

    def test_none_fit(self):
        """No pairs fit when budget is zero."""
        pending = ["a", "b"]
        cost_map = {"a": 4, "b": 4}
        result = deploy._capacity_limited_pairs(
            pending, free_gpus=0, cost_map=cost_map
        )
        assert result == []

    def test_partial_fit_picks_cheapest_first(self):
        """Cheapest pairs are selected first to maximize count."""
        pending = ["expensive", "cheap1", "cheap2"]
        cost_map = {"expensive": 8, "cheap1": 2, "cheap2": 2}
        result = deploy._capacity_limited_pairs(
            pending, free_gpus=5, cost_map=cost_map
        )
        assert "cheap1" in result
        assert "cheap2" in result
        assert "expensive" not in result

    def test_exact_budget(self):
        """Pairs that exactly exhaust the budget are selected."""
        pending = ["a", "b"]
        cost_map = {"a": 4, "b": 4}
        result = deploy._capacity_limited_pairs(
            pending, free_gpus=8, cost_map=cost_map
        )
        assert set(result) == {"a", "b"}

    def test_sorts_ascending_by_cost(self):
        """Output is sorted by ascending cost."""
        pending = ["big", "medium", "small"]
        cost_map = {"big": 8, "medium": 4, "small": 2}
        result = deploy._capacity_limited_pairs(
            pending, free_gpus=14, cost_map=cost_map
        )
        assert result == ["small", "medium", "big"]

    def test_empty_pending(self):
        """Empty pending list returns empty result."""
        result = deploy._capacity_limited_pairs(
            [], free_gpus=100, cost_map={}
        )
        assert result == []

    def test_single_pair_exceeds_budget(self):
        """Single pair that exceeds budget is not selected."""
        result = deploy._capacity_limited_pairs(
            ["big"], free_gpus=3, cost_map={"big": 4}
        )
        assert result == []

    def test_single_pair_fits(self):
        """Single pair within budget is selected."""
        result = deploy._capacity_limited_pairs(
            ["ok"], free_gpus=4, cost_map={"ok": 4}
        )
        assert result == ["ok"]

    def test_greedy_packing_maximizes_count(self):
        """Greedy packing: 3 small items over 1 large item when budget allows."""
        pending = ["large", "s1", "s2", "s3"]
        cost_map = {"large": 6, "s1": 2, "s2": 2, "s3": 2}
        result = deploy._capacity_limited_pairs(
            pending, free_gpus=6, cost_map=cost_map
        )
        # Budget=6: picks s1(2)+s2(2)+s3(2)=6 or s1+s2+large? No, large=6 fills
        # Sorted order: s1,s2,s3,large → picks s1(2),s2(4),s3(6) → all 3 small
        assert set(result) == {"s1", "s2", "s3"}
        assert "large" not in result


# ── _select_dispatchable ─────────────────────────────────────────────────────

class TestSelectDispatchable:
    """Tests for _select_dispatchable."""

    def test_returns_subset_within_budget(self):
        """Result is a subset of pending that fits in the GPU budget."""
        pending = ["a", "b", "c", "d"]
        cost_map = {"a": 2, "b": 2, "c": 2, "d": 2}
        result = deploy._select_dispatchable(
            pending, free_gpus=4, cost_map=cost_map
        )
        assert len(result) == 2
        assert all(p in pending for p in result)

    def test_does_not_mutate_input(self):
        """Input list is not mutated."""
        pending = ["a", "b", "c"]
        original = pending.copy()
        cost_map = {"a": 2, "b": 2, "c": 2}
        deploy._select_dispatchable(pending, free_gpus=4, cost_map=cost_map)
        assert pending == original

    def test_empty_pending(self):
        """Empty pending returns empty."""
        result = deploy._select_dispatchable(
            [], free_gpus=100, cost_map={}
        )
        assert result == []

    def test_budget_zero(self):
        """Zero budget returns empty."""
        result = deploy._select_dispatchable(
            ["a", "b"], free_gpus=0, cost_map={"a": 2, "b": 2}
        )
        assert result == []

    @patch("pipeline.deploy.random.shuffle")
    def test_shuffles_before_capacity_gating(self, mock_shuffle):
        """Shuffles the list before applying capacity gate."""
        pending = ["a", "b", "c"]
        cost_map = {"a": 2, "b": 2, "c": 2}
        # Make shuffle reverse the list
        mock_shuffle.side_effect = lambda x: x.reverse()
        result = deploy._select_dispatchable(
            pending, free_gpus=100, cost_map=cost_map
        )
        # With all equal costs, sorted order preserves shuffled order
        assert set(result) == {"a", "b", "c"}


# ── _derive_pair_gpu_costs ───────────────────────────────────────────────────

class TestDerivePairGpuCosts:
    """Tests for _derive_pair_gpu_costs."""

    def test_no_defaults_uses_fallback(self):
        """When defaults is None, fallback_cost is used."""
        discovered = {"pair-1": {"scenario_content": "scenario: []"}}
        result = deploy._derive_pair_gpu_costs(
            discovered, defaults=None, fallback_cost=4
        )
        assert result == {"pair-1": (4, "fallback")}

    def test_no_scenario_content_uses_defaults_only(self):
        """When scenario_content is missing, uses defaults-only derivation."""
        discovered = {"pair-1": {}}
        # Need a valid defaults dict that capacity.gpu_cost_per_pair can handle
        defaults = {"decode": {"enabled": True, "replicas": 1,
                               "accelerator": {"count": 2}}}
        result = deploy._derive_pair_gpu_costs(
            discovered, defaults=defaults, fallback_cost=4
        )
        assert result["pair-1"][1] == "defaults-only"

    def test_valid_scenario_content_derives(self):
        """Valid scenario content derives cost normally."""
        import yaml
        scenario = yaml.safe_dump({"scenario": [{"decode": {"accelerator": {"count": 4}}}]})
        discovered = {"pair-1": {"scenario_content": scenario}}
        defaults = {"decode": {"enabled": True, "replicas": 1,
                               "accelerator": {"count": 2}}}
        result = deploy._derive_pair_gpu_costs(
            discovered, defaults=defaults, fallback_cost=8
        )
        assert result["pair-1"][1] == "derived"
        assert isinstance(result["pair-1"][0], int)

    def test_invalid_yaml_scenario_uses_defaults_only(self):
        """Invalid YAML in scenario_content falls back to defaults-only."""
        discovered = {"pair-1": {"scenario_content": "{{not yaml"}}
        defaults = {"decode": {"enabled": True, "replicas": 1,
                               "accelerator": {"count": 2}}}
        result = deploy._derive_pair_gpu_costs(
            discovered, defaults=defaults, fallback_cost=4
        )
        # Should fall back to defaults-only since YAML is invalid
        assert result["pair-1"][1] in ("defaults-only", "fallback")

    def test_empty_discovered(self):
        """Empty discovered dict returns empty costs."""
        result = deploy._derive_pair_gpu_costs(
            {}, defaults={"decode": {}}, fallback_cost=4
        )
        assert result == {}

    def test_multiple_pairs(self):
        """Multiple pairs each get their own cost entry."""
        discovered = {
            "pair-1": {},
            "pair-2": {},
        }
        defaults = {"decode": {"enabled": True, "replicas": 1,
                               "accelerator": {"count": 2}}}
        result = deploy._derive_pair_gpu_costs(
            discovered, defaults=defaults, fallback_cost=4
        )
        assert "pair-1" in result
        assert "pair-2" in result


# ── _parse_list ──────────────────────────────────────────────────────────────

class TestParseList:
    """Tests for _parse_list."""

    def test_none_returns_none(self):
        assert deploy._parse_list(None) is None

    def test_empty_string_returns_none(self):
        assert deploy._parse_list("") is None

    def test_single_value(self):
        assert deploy._parse_list("foo") == ["foo"]

    def test_comma_separated(self):
        assert deploy._parse_list("a,b,c") == ["a", "b", "c"]

    def test_list_input(self):
        assert deploy._parse_list(["a,b", "c"]) == ["a", "b", "c"]

    def test_strips_whitespace(self):
        assert deploy._parse_list(" a , b , c ") == ["a", "b", "c"]

    def test_empty_list_returns_none(self):
        assert deploy._parse_list([]) is None

    def test_list_of_empty_strings_returns_none(self):
        assert deploy._parse_list(["", ","]) is None


# ── _is_glob / _expand_glob_values ──────────────────────────────────────────

class TestIsGlob:
    """Tests for _is_glob."""

    def test_star(self):
        assert deploy._is_glob("foo*") is True

    def test_question(self):
        assert deploy._is_glob("fo?") is True

    def test_bracket(self):
        assert deploy._is_glob("[abc]") is True

    def test_literal(self):
        assert deploy._is_glob("foobar") is False

    def test_empty(self):
        assert deploy._is_glob("") is False


class TestExpandGlobValues:
    """Tests for _expand_glob_values."""

    def test_literal_match(self):
        expanded, unknown = deploy._expand_glob_values(
            ["alpha"], {"alpha", "beta", "gamma"}
        )
        assert expanded == ["alpha"]
        assert unknown == []

    def test_literal_unknown(self):
        expanded, unknown = deploy._expand_glob_values(
            ["nope"], {"alpha", "beta"}
        )
        assert expanded == []
        assert unknown == ["nope"]

    def test_glob_star(self):
        expanded, unknown = deploy._expand_glob_values(
            ["al*"], {"alpha", "also", "beta"}
        )
        assert set(expanded) == {"alpha", "also"}
        assert unknown == []

    def test_glob_no_match(self):
        expanded, unknown = deploy._expand_glob_values(
            ["z*"], {"alpha", "beta"}
        )
        assert expanded == []
        assert unknown == ["z*"]

    def test_mixed_literals_and_globs(self):
        expanded, unknown = deploy._expand_glob_values(
            ["alpha", "b*"], {"alpha", "beta", "bravo", "gamma"}
        )
        assert "alpha" in expanded
        assert "beta" in expanded
        assert "bravo" in expanded
        assert unknown == []

    def test_exclude_from_pattern(self):
        """Excluded items are not matched by patterns but can be specified literally."""
        expanded, unknown = deploy._expand_glob_values(
            ["exp*"], {"experiment", "export", "alpha"},
            exclude_from_pattern=frozenset({"experiment"}),
        )
        assert "experiment" not in expanded
        assert "export" in expanded
        assert unknown == []

    def test_exclude_allows_literal(self):
        """Excluded items can still be specified as literals."""
        expanded, unknown = deploy._expand_glob_values(
            ["experiment"], {"experiment", "export"},
            exclude_from_pattern=frozenset({"experiment"}),
        )
        assert expanded == ["experiment"]
        assert unknown == []

    def test_deduplication(self):
        """Same item is not added twice."""
        expanded, unknown = deploy._expand_glob_values(
            ["alpha", "al*"], {"alpha", "also", "beta"}
        )
        assert expanded.count("alpha") == 1

    def test_order_preserved(self):
        """First occurrence wins for ordering."""
        expanded, unknown = deploy._expand_glob_values(
            ["beta", "a*"], {"alpha", "also", "beta"}
        )
        assert expanded[0] == "beta"


# ── _fmt_size ────────────────────────────────────────────────────────────────

class TestFmtSize:
    """Tests for _fmt_size."""

    def test_gb(self):
        assert deploy._fmt_size(2 * (1 << 30)) == "2.0 GB"

    def test_mb(self):
        assert deploy._fmt_size(512 * (1 << 20)) == "512 MB"

    def test_kb(self):
        assert deploy._fmt_size(100 * (1 << 10)) == "100 KB"

    def test_boundary_gb(self):
        """Exactly 1 GB."""
        assert deploy._fmt_size(1 << 30) == "1.0 GB"

    def test_boundary_mb(self):
        """Exactly 1 MB."""
        assert deploy._fmt_size(1 << 20) == "1 MB"

    def test_fractional_gb(self):
        """1.5 GB."""
        assert deploy._fmt_size(int(1.5 * (1 << 30))) == "1.5 GB"


# ── _is_up_to_date ──────────────────────────────────────────────────────────

class TestIsUpToDate:
    """Tests for _is_up_to_date."""

    def test_none_remote_mtime_returns_false(self):
        """remote_mtime=None always means not up to date."""
        assert deploy._is_up_to_date(Path("/tmp/nonexistent"), None) is False

    def test_nonexistent_file_returns_false(self, tmp_path):
        """Non-existent file is never up to date."""
        assert deploy._is_up_to_date(tmp_path / "missing.txt", 100.0) is False

    def test_local_newer_returns_true(self, tmp_path):
        """Local file newer than remote is up to date."""
        f = tmp_path / "data.txt"
        f.write_text("data")
        # Set mtime to far future
        os.utime(f, (time.time() + 1000, time.time() + 1000))
        assert deploy._is_up_to_date(f, 100.0) is True

    def test_local_older_returns_false(self, tmp_path):
        """Local file older than remote is not up to date."""
        f = tmp_path / "data.txt"
        f.write_text("data")
        # Set mtime to old value
        os.utime(f, (100, 100))
        assert deploy._is_up_to_date(f, time.time() + 1000) is False

    def test_equal_mtime_returns_true(self, tmp_path):
        """Local file with same mtime as remote is up to date."""
        f = tmp_path / "data.txt"
        f.write_text("data")
        mtime = f.stat().st_mtime
        assert deploy._is_up_to_date(f, mtime) is True


# ── _is_iteration_up_to_date ─────────────────────────────────────────────────

class TestIsIterationUpToDate:
    """Tests for _is_iteration_up_to_date."""

    def test_none_remote_mtime_returns_false(self, tmp_path):
        """remote_mtime=None always means not up to date."""
        iN_dir = tmp_path / "i0"
        iN_dir.mkdir()
        assert deploy._is_iteration_up_to_date(iN_dir, None) is False

    def test_missing_trace_returns_false(self, tmp_path):
        """Missing trace_data.csv means not up to date."""
        iN_dir = tmp_path / "i0"
        iN_dir.mkdir()
        assert deploy._is_iteration_up_to_date(iN_dir, 100.0) is False

    def test_trace_newer_returns_true(self, tmp_path):
        """trace_data.csv newer than remote is up to date."""
        iN_dir = tmp_path / "i0"
        iN_dir.mkdir()
        trace = iN_dir / "trace_data.csv"
        trace.write_text("data")
        os.utime(trace, (time.time() + 1000, time.time() + 1000))
        assert deploy._is_iteration_up_to_date(iN_dir, 100.0) is True

    def test_trace_older_returns_false(self, tmp_path):
        """trace_data.csv older than remote is not up to date."""
        iN_dir = tmp_path / "i0"
        iN_dir.mkdir()
        trace = iN_dir / "trace_data.csv"
        trace.write_text("data")
        os.utime(trace, (100, 100))
        assert deploy._is_iteration_up_to_date(iN_dir, time.time() + 1000) is False

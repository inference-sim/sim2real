"""Tests for deploy.py runtime-tracking and dispatch helpers.

Covers:
- _mark_running: stamp running_since on dispatch
- _finalize_run: record duration on terminal transitions
- _clear_runtime: clear both runtime fields on reset/requeue
- _fmt_duration: human-readable duration formatting
- _runtime_str: RUNTIME column value for status table
- _derive_pair_gpu_costs: GPU cost derivation per pair
- _capacity_limited_pairs: capacity-gate pair selection
- _select_dispatchable: shuffle + capacity gate
- _parse_list: CLI flag value parsing

These are pure/near-pure utility functions that do not require
subprocess mocking or cluster access.
"""

from __future__ import annotations

import datetime
from unittest.mock import patch

import pytest
import yaml

from pipeline.deploy import (
    _mark_running,
    _finalize_run,
    _clear_runtime,
    _fmt_duration,
    _runtime_str,
    _derive_pair_gpu_costs,
    _capacity_limited_pairs,
    _select_dispatchable,
    _parse_list,
)


# ── _mark_running ───────────────────────────────────────────────────────────


class TestMarkRunning:
    def test_sets_running_since_to_utc_iso(self):
        entry = {"status": "pending", "running_since": None, "last_duration": 123}
        _mark_running(entry)
        assert entry["running_since"] is not None
        # Should be valid ISO format with timezone
        parsed = datetime.datetime.fromisoformat(entry["running_since"])
        assert parsed.tzinfo is not None
        # last_duration is cleared on dispatch
        assert entry["last_duration"] is None

    def test_overwrites_existing_running_since(self):
        entry = {"running_since": "2020-01-01T00:00:00+00:00", "last_duration": None}
        _mark_running(entry)
        assert entry["running_since"] != "2020-01-01T00:00:00+00:00"

    def test_clears_last_duration(self):
        entry = {"running_since": None, "last_duration": 3600.5}
        _mark_running(entry)
        assert entry["last_duration"] is None


# ── _finalize_run ──────────────────────────────────────────────────────────


class TestFinalizeRun:
    def test_computes_duration_from_running_since(self):
        # Set running_since to 60 seconds ago
        now = datetime.datetime.now(datetime.timezone.utc)
        started = (now - datetime.timedelta(seconds=60)).isoformat()
        entry = {"running_since": started, "last_duration": None}
        _finalize_run(entry)
        # Should be approximately 60 seconds (allow 2s for test execution time)
        assert entry["last_duration"] is not None
        assert 58 <= entry["last_duration"] <= 65
        assert entry["running_since"] is None

    def test_clears_running_since_when_not_set(self):
        entry = {"running_since": None, "last_duration": None}
        _finalize_run(entry)
        assert entry["running_since"] is None
        assert entry["last_duration"] is None

    def test_handles_malformed_timestamp_gracefully(self):
        entry = {"running_since": "not-a-timestamp", "last_duration": None}
        _finalize_run(entry)
        # Malformed timestamps just clear running_since without setting duration
        assert entry["running_since"] is None
        assert entry["last_duration"] is None

    def test_clears_running_since_even_with_missing_key(self):
        entry = {}
        _finalize_run(entry)
        assert entry["running_since"] is None


# ── _clear_runtime ─────────────────────────────────────────────────────────


class TestClearRuntime:
    def test_clears_both_fields(self):
        entry = {"running_since": "2026-07-04T15:00:00+00:00", "last_duration": 120.5}
        _clear_runtime(entry)
        assert entry["running_since"] is None
        assert entry["last_duration"] is None

    def test_noop_on_already_cleared(self):
        entry = {"running_since": None, "last_duration": None}
        _clear_runtime(entry)
        assert entry["running_since"] is None
        assert entry["last_duration"] is None


# ── _fmt_duration ───────────────────────────────────────────────────────────


class TestFmtDuration:
    def test_none_returns_dash(self):
        assert _fmt_duration(None) == "—"

    def test_negative_returns_dash(self):
        assert _fmt_duration(-1) == "—"
        assert _fmt_duration(-0.1) == "—"

    def test_zero_returns_0s(self):
        assert _fmt_duration(0) == "0s"

    def test_seconds_range(self):
        assert _fmt_duration(42) == "42s"
        assert _fmt_duration(1) == "1s"
        assert _fmt_duration(59) == "59s"

    def test_minutes_range(self):
        assert _fmt_duration(60) == "1m00s"
        assert _fmt_duration(312) == "5m12s"
        assert _fmt_duration(3599) == "59m59s"

    def test_hours_range(self):
        assert _fmt_duration(3600) == "1h00m"
        assert _fmt_duration(4080) == "1h08m"
        assert _fmt_duration(86399) == "23h59m"

    def test_days_range(self):
        assert _fmt_duration(86400) == "1d00h"
        assert _fmt_duration(86400 + 4 * 3600) == "1d04h"
        assert _fmt_duration(2 * 86400 + 4 * 3600) == "2d04h"

    def test_fractional_seconds_truncated(self):
        # 42.9 seconds should show as 42s
        assert _fmt_duration(42.9) == "42s"


# ── _runtime_str ────────────────────────────────────────────────────────────


class TestRuntimeStr:
    def test_running_entry_shows_live_duration(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        started = (now - datetime.timedelta(seconds=90)).isoformat()
        entry = {"status": "running", "running_since": started, "last_duration": None}
        result = _runtime_str(entry)
        # Should be approximately 1m30s
        assert "m" in result or "s" in result
        assert result != "—"

    def test_running_entry_without_running_since_returns_dash(self):
        entry = {"status": "running", "running_since": None}
        assert _runtime_str(entry) == "—"

    def test_running_entry_with_bad_timestamp_returns_dash(self):
        entry = {"status": "running", "running_since": "garbage"}
        assert _runtime_str(entry) == "—"

    def test_done_entry_shows_last_duration(self):
        entry = {"status": "done", "running_since": None, "last_duration": 312.7}
        result = _runtime_str(entry)
        assert result == "5m12s"

    def test_failed_entry_shows_last_duration(self):
        entry = {"status": "failed", "running_since": None, "last_duration": 42.0}
        result = _runtime_str(entry)
        assert result == "42s"

    def test_pending_entry_returns_dash(self):
        entry = {"status": "pending"}
        assert _runtime_str(entry) == "—"

    def test_empty_entry_returns_dash(self):
        entry = {}
        assert _runtime_str(entry) == "—"


# ── _derive_pair_gpu_costs ────────────────────────────────────────────────


class TestDerivePairGpuCosts:
    def test_derived_from_scenario_content(self):
        """When scenarioContent is present and valid, derive cost from it."""
        scenario = yaml.dump({"scenario": [{"decode": {"replicas": 2, "accelerator": {"count": 4}}}]})
        discovered = {
            "wl-test|algo|i1": {
                "scenario_content": scenario,
            }
        }
        defaults = {"decode": {"enabled": True, "replicas": 1, "accelerator": {"count": 2}}}
        result = _derive_pair_gpu_costs(discovered, defaults=defaults, fallback_cost=8)
        cost, source = result["wl-test|algo|i1"]
        assert source == "derived"
        assert cost == 8  # 2 replicas * 4 GPUs per pod

    def test_no_defaults_uses_fallback(self):
        """When defaults is None, use fallback cost."""
        discovered = {"wl-test|algo|i1": {"scenario_content": "scenario: []"}}
        result = _derive_pair_gpu_costs(discovered, defaults=None, fallback_cost=4)
        cost, source = result["wl-test|algo|i1"]
        assert cost == 4
        assert source == "fallback"

    def test_no_scenario_content_uses_defaults_only(self):
        """When scenarioContent is missing, derive from defaults only."""
        discovered = {"wl-test|algo|i1": {"scenario_content": None}}
        defaults = {"decode": {"enabled": True, "replicas": 1, "accelerator": {"count": 2}}}
        result = _derive_pair_gpu_costs(discovered, defaults=defaults, fallback_cost=8)
        cost, source = result["wl-test|algo|i1"]
        assert source == "defaults-only"
        assert cost == 2

    def test_invalid_yaml_scenario_content_uses_defaults_only(self):
        """When scenarioContent is invalid YAML, fall back to defaults."""
        discovered = {"wl-test|algo|i1": {"scenario_content": "{{{{invalid yaml"}}
        defaults = {"decode": {"enabled": True, "replicas": 1, "accelerator": {"count": 3}}}
        result = _derive_pair_gpu_costs(discovered, defaults=defaults, fallback_cost=8)
        cost, source = result["wl-test|algo|i1"]
        assert source == "defaults-only"
        assert cost == 3

    def test_non_dict_scenario_content_uses_defaults_only(self):
        """When scenarioContent parses to a non-dict, use defaults only."""
        discovered = {"wl-test|algo|i1": {"scenario_content": "- item1\n- item2\n"}}
        defaults = {"decode": {"enabled": True, "replicas": 1, "accelerator": {"count": 2}}}
        result = _derive_pair_gpu_costs(discovered, defaults=defaults, fallback_cost=8)
        cost, source = result["wl-test|algo|i1"]
        assert source == "defaults-only"

    def test_gpu_cost_derivation_failure_uses_fallback(self):
        """When gpu_cost_per_pair returns an error string, use fallback."""
        scenario = yaml.dump({"scenario": [{"decode": {"replicas": 1, "accelerator": {"count": "bogus"}}}]})
        discovered = {"wl-test|algo|i1": {"scenario_content": scenario}}
        defaults = {"decode": {"enabled": True, "replicas": 1}}
        result = _derive_pair_gpu_costs(discovered, defaults=defaults, fallback_cost=4)
        cost, source = result["wl-test|algo|i1"]
        assert source == "fallback"
        assert cost == 4

    def test_multiple_pairs(self):
        """All pairs in discovered are processed."""
        scenario = yaml.dump({"scenario": [{"decode": {"replicas": 1, "accelerator": {"count": 2}}}]})
        discovered = {
            "wl-a|algo|i1": {"scenario_content": scenario},
            "wl-b|algo|i1": {"scenario_content": scenario},
        }
        defaults = {"decode": {"enabled": True, "replicas": 1, "accelerator": {"count": 2}}}
        result = _derive_pair_gpu_costs(discovered, defaults=defaults, fallback_cost=8)
        assert len(result) == 2
        assert all(source == "derived" for _, source in result.values())


# ── _capacity_limited_pairs ──────────────────────────────────────────────


class TestCapacityLimitedPairs:
    def test_all_fit_within_budget(self):
        """When all pairs fit, all are returned."""
        pending = ["a", "b", "c"]
        cost_map = {"a": 2, "b": 2, "c": 2}
        result = _capacity_limited_pairs(pending, free_gpus=10, cost_map=cost_map)
        assert set(result) == {"a", "b", "c"}

    def test_partial_fit(self):
        """When not all fit, smallest-first packing applies."""
        pending = ["big", "small", "medium"]
        cost_map = {"big": 8, "small": 1, "medium": 4}
        result = _capacity_limited_pairs(pending, free_gpus=6, cost_map=cost_map)
        # Sorted by cost ascending: small(1), medium(4), big(8)
        # Budget=6: small(1) fits (budget=5), medium(4) fits (budget=1), big(8) doesn't
        assert result == ["small", "medium"]

    def test_zero_budget(self):
        """Zero GPUs available means nothing dispatches."""
        pending = ["a"]
        cost_map = {"a": 1}
        result = _capacity_limited_pairs(pending, free_gpus=0, cost_map=cost_map)
        assert result == []

    def test_empty_pending(self):
        """No pending pairs returns empty list."""
        result = _capacity_limited_pairs([], free_gpus=100, cost_map={})
        assert result == []

    def test_exact_fit(self):
        """Pairs that exactly exhaust the budget are all included."""
        pending = ["a", "b"]
        cost_map = {"a": 4, "b": 4}
        result = _capacity_limited_pairs(pending, free_gpus=8, cost_map=cost_map)
        assert set(result) == {"a", "b"}

    def test_order_is_by_cost_ascending(self):
        """Results are sorted by cost ascending regardless of input order."""
        pending = ["expensive", "cheap", "mid"]
        cost_map = {"expensive": 10, "cheap": 1, "mid": 5}
        result = _capacity_limited_pairs(pending, free_gpus=100, cost_map=cost_map)
        assert result == ["cheap", "mid", "expensive"]


# ── _select_dispatchable ─────────────────────────────────────────────────


class TestSelectDispatchable:
    def test_returns_subset_within_budget(self):
        """Returned pairs must fit within free_gpus."""
        pending = ["a", "b", "c", "d"]
        cost_map = {"a": 4, "b": 4, "c": 4, "d": 4}
        result = _select_dispatchable(pending, free_gpus=8, cost_map=cost_map)
        # At most 2 pairs (8/4 = 2)
        assert len(result) <= 2
        for pair in result:
            assert pair in pending

    def test_does_not_mutate_input(self):
        pending = ["a", "b", "c"]
        original = list(pending)
        cost_map = {"a": 1, "b": 1, "c": 1}
        _select_dispatchable(pending, free_gpus=2, cost_map=cost_map)
        assert pending == original

    def test_empty_pending(self):
        result = _select_dispatchable([], free_gpus=10, cost_map={})
        assert result == []

    def test_zero_budget_returns_empty(self):
        pending = ["a", "b"]
        cost_map = {"a": 1, "b": 1}
        result = _select_dispatchable(pending, free_gpus=0, cost_map=cost_map)
        assert result == []


# ── _parse_list ──────────────────────────────────────────────────────────


class TestParseList:
    def test_none_returns_none(self):
        assert _parse_list(None) is None

    def test_string_splits_on_comma(self):
        assert _parse_list("a,b,c") == ["a", "b", "c"]

    def test_string_strips_whitespace(self):
        assert _parse_list("a , b , c") == ["a", "b", "c"]

    def test_list_flattens_and_splits(self):
        assert _parse_list(["a,b", "c"]) == ["a", "b", "c"]

    def test_empty_string_returns_none(self):
        assert _parse_list("") is None

    def test_single_value(self):
        assert _parse_list("hello") == ["hello"]

    def test_list_with_empty_items_filtered(self):
        assert _parse_list(",a,,b,") == ["a", "b"]

    def test_empty_list_returns_none(self):
        assert _parse_list([]) is None

    def test_list_of_empty_strings_returns_none(self):
        assert _parse_list(["", ","]) is None

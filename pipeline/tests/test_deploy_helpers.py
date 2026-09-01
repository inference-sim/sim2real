"""Tests for pure helper functions in pipeline/deploy.py.

Covers:
- _is_pair_key: metadata-key filtering predicate
- _key_iteration: iteration extraction from pair keys
- _format_capacity: capacity log line formatting
- _fmt_size: human-readable byte sizes
- _load_pairs_with_errors: pair discovery with grammar validation
- _in_orchestrator_pod: pod detection via environment variable

These are pure/near-pure utility functions that do not require
subprocess mocking or cluster access.
"""

from __future__ import annotations

import yaml

from pipeline.deploy import (
    _is_pair_key,
    _key_iteration,
    _format_capacity,
    _fmt_size,
    _load_pairs_with_errors,
    _in_orchestrator_pod,
)


# ── _is_pair_key ──────────────────────────────────────────────────────────


class TestIsPairKey:
    """Tests for the metadata-key filtering predicate."""

    def test_normal_pair_key_returns_true(self):
        assert _is_pair_key("wl-code-generation-4|softreflective|i1") is True

    def test_legacy_pair_key_returns_true(self):
        assert _is_pair_key("wl-smoke-baseline") is True

    def test_metadata_key_returns_false(self):
        assert _is_pair_key("_meta") is False

    def test_notes_key_returns_false(self):
        assert _is_pair_key("_notes") is False

    def test_double_underscore_returns_false(self):
        assert _is_pair_key("__version") is False

    def test_empty_string_returns_true(self):
        """Empty string doesn't start with underscore."""
        assert _is_pair_key("") is True

    def test_single_underscore_returns_false(self):
        assert _is_pair_key("_") is False


# ── _key_iteration ────────────────────────────────────────────────────────


class TestKeyIteration:
    """Tests for iteration extraction from pair keys."""

    def test_canonical_key_with_iteration(self):
        assert _key_iteration("wl-code-generation-4|softreflective|i3") == 3

    def test_canonical_key_iteration_1(self):
        assert _key_iteration("wl-code-generation-4|softreflective|i1") == 1

    def test_legacy_key_without_iteration_defaults_to_1(self):
        """Legacy keys that don't match the grammar parse as iteration=1."""
        assert _key_iteration("wl-smoke-baseline") == 1

    def test_key_with_no_iter_suffix_defaults_to_1(self):
        assert _key_iteration("wl-smoke|baseline") == 1

    def test_high_iteration_number(self):
        assert _key_iteration("wl-code-gen|softreflective|i42") == 42


# ── _format_capacity ──────────────────────────────────────────────────────


class TestFormatCapacity:
    """Tests for the unified capacity log line formatter."""

    def test_basic_format(self):
        result = _format_capacity(4, 8, 4, 112, 104)
        assert "4 effective free GPUs" in result
        assert "8 probed" in result
        assert "4 reserved" in result
        assert "112 allocatable" in result
        assert "104 requested" in result

    def test_zero_effective(self):
        result = _format_capacity(0, 0, 0, 16, 16)
        assert "0 effective free GPUs" in result

    def test_format_contains_all_five_numbers(self):
        result = _format_capacity(10, 20, 10, 100, 80)
        # All five numbers must appear in the output
        assert "10" in result
        assert "20" in result
        assert "100" in result
        assert "80" in result

    def test_format_starts_with_capacity(self):
        result = _format_capacity(5, 10, 5, 50, 40)
        assert result.startswith("Capacity:")


# ── _fmt_size ─────────────────────────────────────────────────────────────


class TestFmtSize:
    """Tests for human-readable byte size formatting."""

    def test_kb_range(self):
        result = _fmt_size(512 * 1024)  # 512 KB
        assert "512 KB" in result

    def test_mb_range(self):
        result = _fmt_size(150 * 1024 * 1024)  # 150 MB
        assert "150 MB" in result

    def test_gb_range(self):
        result = _fmt_size(3 * 1024 * 1024 * 1024)  # 3 GB
        assert "3.0 GB" in result

    def test_fractional_gb(self):
        result = _fmt_size(int(1.5 * 1024 * 1024 * 1024))  # 1.5 GB
        assert "1.5 GB" in result

    def test_small_value_is_kb(self):
        result = _fmt_size(1024)  # 1 KB
        assert "KB" in result

    def test_boundary_mb(self):
        """Exactly 1 MB boundary."""
        result = _fmt_size(1 << 20)
        assert "MB" in result

    def test_boundary_gb(self):
        """Exactly 1 GB boundary."""
        result = _fmt_size(1 << 30)
        assert "GB" in result


# ── _in_orchestrator_pod ──────────────────────────────────────────────────


class TestInOrchestratorPod:
    """Tests for orchestrator pod detection."""

    def test_returns_true_when_env_set(self, monkeypatch):
        monkeypatch.setenv("SIM2REAL_ORCHESTRATOR_POD", "1")
        assert _in_orchestrator_pod() is True

    def test_returns_false_when_env_absent(self, monkeypatch):
        monkeypatch.delenv("SIM2REAL_ORCHESTRATOR_POD", raising=False)
        assert _in_orchestrator_pod() is False

    def test_returns_false_when_env_empty(self, monkeypatch):
        monkeypatch.setenv("SIM2REAL_ORCHESTRATOR_POD", "")
        assert _in_orchestrator_pod() is False


# ── _load_pairs_with_errors ───────────────────────────────────────────────


class TestLoadPairsWithErrors:
    """Tests for pair discovery with grammar validation."""

    def test_empty_cluster_dir(self, tmp_path):
        """Non-existent cluster dir returns empty pairs and zero malformed."""
        cluster_dir = tmp_path / "cluster"
        pairs, malformed = _load_pairs_with_errors(cluster_dir)
        assert pairs == {}
        assert malformed == 0

    def test_valid_canonical_pair_key(self, tmp_path):
        """A pipelinerun file whose stem yields a valid canonical key."""
        cluster_dir = tmp_path / "cluster"
        cluster_dir.mkdir()
        pr_data = {
            "metadata": {"name": "pr-code-gen|sr|i1", "namespace": "ns-0"},
            "spec": {
                "params": [
                    {"name": "workloadName", "value": "code-gen"},
                    {"name": "phase", "value": "sr"},
                    {"name": "scenarioContent", "value": "content"},
                ]
            },
        }
        (cluster_dir / "pipelinerun-code-gen|sr|i1.yaml").write_text(
            yaml.safe_dump(pr_data)
        )
        pairs, malformed = _load_pairs_with_errors(cluster_dir)
        assert "wl-code-gen|sr|i1" in pairs
        assert pairs["wl-code-gen|sr|i1"]["iteration"] == 1
        assert malformed == 0

    def test_legacy_key_counts_as_malformed(self, tmp_path):
        """A legacy pipelinerun file whose stem doesn't match canonical grammar."""
        cluster_dir = tmp_path / "cluster"
        cluster_dir.mkdir()
        pr_data = {
            "metadata": {"name": "pr-smoke-baseline", "namespace": "ns-0"},
            "spec": {
                "params": [
                    {"name": "workloadName", "value": "smoke"},
                    {"name": "phase", "value": "baseline"},
                ]
            },
        }
        (cluster_dir / "pipelinerun-smoke-baseline.yaml").write_text(
            yaml.safe_dump(pr_data)
        )
        pairs, malformed = _load_pairs_with_errors(cluster_dir)
        # Key is still loaded (for backward compat during rollout)
        assert "wl-smoke-baseline" in pairs
        # But counts as malformed (doesn't match canonical grammar)
        assert malformed == 1
        # No iteration field set on malformed keys
        assert "iteration" not in pairs["wl-smoke-baseline"]

    def test_mixed_valid_and_malformed_keys(self, tmp_path):
        """Mix of canonical and legacy keys counts correctly."""
        cluster_dir = tmp_path / "cluster"
        cluster_dir.mkdir()

        # Canonical key
        pr_valid = {
            "metadata": {"name": "pr-1", "namespace": "ns-0"},
            "spec": {"params": [
                {"name": "workloadName", "value": "code-gen"},
                {"name": "phase", "value": "sr"},
            ]},
        }
        (cluster_dir / "pipelinerun-code-gen|sr|i2.yaml").write_text(
            yaml.safe_dump(pr_valid)
        )

        # Legacy key
        pr_legacy = {
            "metadata": {"name": "pr-2", "namespace": "ns-0"},
            "spec": {"params": [
                {"name": "workloadName", "value": "smoke"},
                {"name": "phase", "value": "baseline"},
            ]},
        }
        (cluster_dir / "pipelinerun-smoke-baseline.yaml").write_text(
            yaml.safe_dump(pr_legacy)
        )

        pairs, malformed = _load_pairs_with_errors(cluster_dir)
        assert len(pairs) == 2
        assert malformed == 1
        assert pairs["wl-code-gen|sr|i2"]["iteration"] == 2

    def test_corrupt_yaml_skipped(self, tmp_path):
        """Corrupt YAML file is skipped without raising."""
        cluster_dir = tmp_path / "cluster"
        cluster_dir.mkdir()
        (cluster_dir / "pipelinerun-bad.yaml").write_text("{{invalid yaml")
        pairs, malformed = _load_pairs_with_errors(cluster_dir)
        assert pairs == {}
        assert malformed == 0

    def test_preserves_scenario_content(self, tmp_path):
        """scenarioContent param is captured in the entry."""
        cluster_dir = tmp_path / "cluster"
        cluster_dir.mkdir()
        pr_data = {
            "metadata": {"name": "pr-1", "namespace": "ns-0"},
            "spec": {"params": [
                {"name": "workloadName", "value": "wl1"},
                {"name": "phase", "value": "pkg1"},
                {"name": "scenarioContent", "value": "scenario: yaml content"},
            ]},
        }
        (cluster_dir / "pipelinerun-wl1|pkg1|i1.yaml").write_text(
            yaml.safe_dump(pr_data)
        )
        pairs, _ = _load_pairs_with_errors(cluster_dir)
        assert pairs["wl-wl1|pkg1|i1"]["scenario_content"] == "scenario: yaml content"

    def test_higher_iteration_number(self, tmp_path):
        """Iteration numbers > 1 are correctly parsed."""
        cluster_dir = tmp_path / "cluster"
        cluster_dir.mkdir()
        pr_data = {
            "metadata": {"name": "pr-10", "namespace": "ns-0"},
            "spec": {"params": [
                {"name": "workloadName", "value": "wl1"},
                {"name": "phase", "value": "sr"},
            ]},
        }
        (cluster_dir / "pipelinerun-wl1|sr|i10.yaml").write_text(
            yaml.safe_dump(pr_data)
        )
        pairs, malformed = _load_pairs_with_errors(cluster_dir)
        assert pairs["wl-wl1|sr|i10"]["iteration"] == 10
        assert malformed == 0

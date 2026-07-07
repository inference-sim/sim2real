"""Tests for the parser-aware behavior of pipeline.deploy._load_pairs.

Covers the step-5 PR 1 integration: iteration field on entries whose key
parses under the new grammar; malformed_count on the ``_load_pairs_with_errors``
variant for keys that don't parse. Existing round-trip coverage of the
YAML-reading side of ``_load_pairs`` lives in ``test_deploy_run.py``.
"""

import yaml as _yaml


def _write_pr(cluster_dir, filename, *, workload, package):
    """Write a minimal pipelinerun-*.yaml at cluster_dir/filename."""
    pr = {
        "apiVersion": "tekton.dev/v1",
        "kind": "PipelineRun",
        "metadata": {"name": f"{package}-{workload}-run1", "namespace": "sim2real-0"},
        "spec": {"params": [
            {"name": "workloadName", "value": f"wl-{workload}"},
            {"name": "phase", "value": package},
        ]},
    }
    (cluster_dir / filename).write_text(_yaml.dump(pr))


class TestLoadPairsIterationField:
    """Entries whose key parses under the new grammar gain an iteration field."""

    def test_new_shape_legacy_iteration_1(self, tmp_path):
        """New-shape pipe-separated filename (no |iN) → iteration=1."""
        from pipeline.deploy import _load_pairs
        _write_pr(tmp_path, "pipelinerun-chat-mid|sim2real-ac.yaml",
                  workload="chat-mid", package="sim2real-ac")

        pairs = _load_pairs(tmp_path)

        assert "wl-chat-mid|sim2real-ac" in pairs
        assert pairs["wl-chat-mid|sim2real-ac"]["iteration"] == 1

    def test_new_shape_with_suffix(self, tmp_path):
        """|iN suffix parses to that iteration."""
        from pipeline.deploy import _load_pairs
        _write_pr(tmp_path, "pipelinerun-chat-mid|sim2real-ac|i3.yaml",
                  workload="chat-mid", package="sim2real-ac")

        pairs = _load_pairs(tmp_path)

        assert pairs["wl-chat-mid|sim2real-ac|i3"]["iteration"] == 3

    def test_legacy_dash_shape_no_iteration_field(self, tmp_path):
        """Legacy dash-separated filename → key does not parse → no iteration field.

        Entry is still returned so downstream commands keep working during
        the multi-PR rollout. PR 2 aligns filename shape to pipe-separated;
        at that point the iteration field will always be populated.
        """
        from pipeline.deploy import _load_pairs
        _write_pr(tmp_path, "pipelinerun-smoke-baseline.yaml",
                  workload="smoke", package="baseline")

        pairs = _load_pairs(tmp_path)

        assert "wl-smoke-baseline" in pairs
        assert "iteration" not in pairs["wl-smoke-baseline"]

    def test_existing_fields_preserved(self, tmp_path):
        """workload/package/pr_name/pr_path/namespace/scenario_content unchanged."""
        from pipeline.deploy import _load_pairs
        _write_pr(tmp_path, "pipelinerun-chat|pkg.yaml",
                  workload="chat", package="pkg")

        pairs = _load_pairs(tmp_path)
        entry = pairs["wl-chat|pkg"]

        assert entry["workload"] == "wl-chat"
        assert entry["package"] == "pkg"
        assert entry["pr_name"] == "pkg-chat-run1"
        assert entry["namespace"] == "sim2real-0"
        assert entry["pr_path"].endswith("pipelinerun-chat|pkg.yaml")


class TestLoadPairsWithErrors:
    """The variant returns (pairs, malformed_count) with the correct count."""

    def test_empty_cluster_dir_returns_zero(self, tmp_path):
        """No pipelinerun files → empty dict, zero malformed."""
        from pipeline.deploy import _load_pairs_with_errors
        pairs, malformed = _load_pairs_with_errors(tmp_path)
        assert pairs == {}
        assert malformed == 0

    def test_missing_cluster_dir_returns_zero(self, tmp_path):
        """Non-existent directory → empty dict, zero malformed."""
        from pipeline.deploy import _load_pairs_with_errors
        pairs, malformed = _load_pairs_with_errors(tmp_path / "nope")
        assert pairs == {}
        assert malformed == 0

    def test_all_new_shape_zero_malformed(self, tmp_path):
        """New-shape keys parse cleanly → malformed=0."""
        from pipeline.deploy import _load_pairs_with_errors
        _write_pr(tmp_path, "pipelinerun-chat|pkg.yaml", workload="chat", package="pkg")
        _write_pr(tmp_path, "pipelinerun-load|treatment.yaml",
                  workload="load", package="treatment")

        pairs, malformed = _load_pairs_with_errors(tmp_path)

        assert len(pairs) == 2
        assert malformed == 0
        assert all("iteration" in v for v in pairs.values())

    def test_all_legacy_shape_counts_all(self, tmp_path):
        """All dash-separated legacy keys → malformed count matches file count."""
        from pipeline.deploy import _load_pairs_with_errors
        _write_pr(tmp_path, "pipelinerun-smoke-baseline.yaml",
                  workload="smoke", package="baseline")
        _write_pr(tmp_path, "pipelinerun-load-treatment.yaml",
                  workload="load", package="treatment")

        pairs, malformed = _load_pairs_with_errors(tmp_path)

        assert len(pairs) == 2
        assert malformed == 2
        assert all("iteration" not in v for v in pairs.values())

    def test_mixed_shape_partial_count(self, tmp_path):
        """Mixed new + legacy keys → malformed matches only the legacy ones."""
        from pipeline.deploy import _load_pairs_with_errors
        _write_pr(tmp_path, "pipelinerun-chat|pkg.yaml", workload="chat", package="pkg")
        _write_pr(tmp_path, "pipelinerun-load-treatment.yaml",
                  workload="load", package="treatment")

        pairs, malformed = _load_pairs_with_errors(tmp_path)

        assert len(pairs) == 2
        assert malformed == 1
        assert pairs["wl-chat|pkg"]["iteration"] == 1
        assert "iteration" not in pairs["wl-load-treatment"]

    def test_corrupt_yaml_not_counted_as_malformed_key(self, tmp_path, capsys):
        """YAML-parse failure is a file-level skip, not a grammar violation.

        Historical behavior (pre-step-5) skips such files with a WARN;
        the new malformed_count is strictly key-grammar, so YAML crashes
        do not increment it.
        """
        from pipeline.deploy import _load_pairs_with_errors
        (tmp_path / "pipelinerun-bad.yaml").write_text("{{invalid yaml: [")
        _write_pr(tmp_path, "pipelinerun-chat|pkg.yaml", workload="chat", package="pkg")

        pairs, malformed = _load_pairs_with_errors(tmp_path)

        assert list(pairs.keys()) == ["wl-chat|pkg"]
        assert malformed == 0
        out = capsys.readouterr().out
        assert "pipelinerun-bad.yaml" in out
        assert "[WARN]" in out

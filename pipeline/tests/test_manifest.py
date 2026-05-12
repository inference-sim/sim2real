"""Tests for manifest loader."""
import pytest
import yaml
from pathlib import Path

from pipeline.lib.manifest import load_manifest, ManifestError

def _write_manifest(tmp_path, data):
    p = tmp_path / "transfer.yaml"
    p.write_text(yaml.dump(data))
    return p


def test_missing_version_raises(tmp_path):
    data = {k: v for k, v in MINIMAL_V3.items() if k != "version"}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="version"):
        load_manifest(path)


def test_missing_required_field(tmp_path):
    for field in ["scenario", "baseline"]:
        data = {k: v for k, v in MINIMAL_V3.items() if k != field}
        path = _write_manifest(tmp_path, data)
        with pytest.raises(ManifestError, match=field):
            load_manifest(path)


def test_algorithm_section_entirely_optional(tmp_path):
    """Manifest without algorithm field is valid (baseline-only mode)."""
    data = {k: v for k, v in MINIMAL_V3.items() if k != "algorithm"}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert "algorithm" not in m


def test_algorithm_null_normalized_to_absent(tmp_path):
    """algorithm: null in YAML is normalized to key-absent (baseline-only)."""
    data = {**MINIMAL_V3, "algorithm": None}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert "algorithm" not in m


def test_missing_algorithm_source(tmp_path):
    data = {**MINIMAL_V3, "algorithm": {"config": "x.yaml"}}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="algorithm.source"):
        load_manifest(path)


def test_missing_algorithm_config_is_valid(tmp_path):
    """algorithm.config is optional — manifests without it load cleanly."""
    data = {**MINIMAL_V3, "algorithm": {"source": "x.go"}}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert "config" not in m["algorithm"]


def test_optional_context_fields(tmp_path):
    data = {**MINIMAL_V3, "context": {
        "files": ["docs/mapping.md"],
        "notes": "Use regime detection pattern",
    }}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["context"]["notes"] == "Use regime detection pattern"
    assert len(m["context"]["files"]) == 1


def test_workloads_must_be_list(tmp_path):
    data = {**MINIMAL_V3, "workloads": "not_a_list.yaml"}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="workloads.*list"):
        load_manifest(path)


def test_empty_workloads_valid_standby_mode(tmp_path):
    """Empty workloads list is valid — standby mode: stack up, no benchmarks."""
    data = {**MINIMAL_V3, "workloads": []}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["workloads"] == []


def test_absent_workloads_defaults_to_empty(tmp_path):
    """Missing workloads key is valid; defaults to []."""
    data = {k: v for k, v in MINIMAL_V3.items() if k != "workloads"}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["workloads"] == []


def test_null_workloads_defaults_to_empty(tmp_path):
    """workloads: null (YAML null) is valid; defaults to []."""
    data = {**MINIMAL_V3, "workloads": None}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["workloads"] == []


def test_wrong_kind(tmp_path):
    data = {**MINIMAL_V3, "kind": "something-else"}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="kind"):
        load_manifest(path)


def test_unsupported_version(tmp_path):
    data = {**MINIMAL_V3, "version": 99}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="version"):
        load_manifest(path)


def test_v2_rejected(tmp_path):
    """Version 2 manifests are no longer accepted."""
    data = {**MINIMAL_V3, "version": 2}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="Unsupported manifest version: 2"):
        load_manifest(path)


def test_file_not_found():
    with pytest.raises(ManifestError, match="not found"):
        load_manifest(Path("/nonexistent/transfer.yaml"))


# ── Hints section ─────────────────────────────────────────────────────────────

def test_hints_section_optional(tmp_path):
    """Manifest without hints loads cleanly; hints defaults to empty."""
    path = _write_manifest(tmp_path, MINIMAL_V3)
    m = load_manifest(path)
    hints = m.get("hints", {})
    assert hints.get("text", "") == ""
    assert hints.get("files", []) == []


def test_hints_text_loaded(tmp_path):
    data = {**MINIMAL_V3, "hints": {"text": "Modify precise_prefix_cache.go"}}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["hints"]["text"] == "Modify precise_prefix_cache.go"


def test_hints_files_contents_embedded(tmp_path):
    hint_file = tmp_path / "hint.md"
    hint_file.write_text("# Transfer hint\nRewrite scorer")
    data = {**MINIMAL_V3, "hints": {"files": [str(hint_file)]}}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert len(m["hints"]["files"]) == 1
    assert m["hints"]["files"][0]["path"] == str(hint_file)
    assert "Rewrite scorer" in m["hints"]["files"][0]["content"]


def test_hints_file_not_found_raises(tmp_path):
    data = {**MINIMAL_V3, "hints": {"files": ["/nonexistent/hint.md"]}}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="hints.files"):
        load_manifest(path)


def test_context_notes_deprecated_warns(tmp_path):
    data = {**MINIMAL_V3, "context": {"notes": "old style note", "files": []}}
    path = _write_manifest(tmp_path, data)
    import warnings
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        m = load_manifest(path)
    dep_warnings = [warning for warning in w if issubclass(warning.category, DeprecationWarning)]
    assert dep_warnings, "No DeprecationWarning emitted"
    texts = [str(warning.message) for warning in dep_warnings]
    assert any("context.notes" in t and "deprecated" in t for t in texts)
    # Value is ignored (not migrated to hints.text)
    assert m.get("hints", {}).get("text", "") == ""


# ── v3 manifest fixtures ───────────────────────────────────────────────────

MINIMAL_V3 = {
    "kind": "sim2real-transfer",
    "version": 3,
    "scenario": "routing",
    "algorithm": {
        "source": "sim2real_golden/routers/router_adaptive_v2.go",
        "config": "sim2real_golden/routers/policy_adaptive_v2.yaml",
    },
    "baseline": {
        "sim": {"config": "sim2real_golden/routers/policy_baseline_211.yaml"},
    },
    "workloads": ["sim2real_golden/workloads/wl1.yaml"],
    "target": {"repo": "llm-d-inference-scheduler"},
    "config": {
        "kind": "EndpointPickerConfig",
    },
    "epp_image": {
        "upstream": {
            "hub": "ghcr.io/llm-d",
            "name": "llm-d-inference-scheduler",
            "tag": "v0.7.1",
            "pullPolicy": "Always",
        },
    },
}


def test_load_valid_v3_minimal(tmp_path):
    """v3 with just baseline.sim.config loads cleanly."""
    path = _write_manifest(tmp_path, MINIMAL_V3)
    m = load_manifest(path)
    assert m["baseline"]["sim"]["config"] == "sim2real_golden/routers/policy_baseline_211.yaml"
    assert m["baseline"]["real"]["config"] is None
    assert m["baseline"]["real"]["notes"] == ""


def test_v3_with_real_config_and_notes(tmp_path):
    """v3 with baseline.real.config + notes loads and preserves both."""
    data = {
        **MINIMAL_V3,
        "baseline": {
            "sim": {"config": "sim2real_golden/routers/policy_baseline_211.yaml"},
            "real": {
                "config": "sim2real_golden/routers/baseline_epp_template.yaml",
                "notes": "Use EndpointPickerConfig.Scorers[]",
            },
        },
    }
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["baseline"]["real"]["config"] == "sim2real_golden/routers/baseline_epp_template.yaml"
    assert "EndpointPickerConfig" in m["baseline"]["real"]["notes"]


def test_v3_missing_sim_config_defaults_to_none(tmp_path):
    """v3 without baseline.sim.config is valid; sim.config defaults to None."""
    data = {**MINIMAL_V3, "baseline": {"real": {"config": "x.yaml"}}}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["baseline"]["sim"]["config"] is None


def test_v3_missing_sim_section_defaults_to_none(tmp_path):
    """v3 baseline without sim key is valid; sim.config defaults to None."""
    data = {**MINIMAL_V3, "baseline": {}}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["baseline"]["sim"]["config"] is None


def test_v3_real_section_entirely_optional(tmp_path):
    """v3 without baseline.real at all is valid; defaults applied."""
    data = {**MINIMAL_V3}  # no baseline.real
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["baseline"]["real"] == {"config": None, "notes": ""}


def test_v3_real_partial_defaults_applied(tmp_path):
    """v3 with baseline.real present but missing notes gets default."""
    data = {
        **MINIMAL_V3,
        "baseline": {
            "sim": {"config": "sim2real_golden/routers/policy_baseline_211.yaml"},
            "real": {"config": "x.yaml"},  # no notes
        },
    }
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["baseline"]["real"]["notes"] == ""




# ── v3 fields ────────────────────────────────────────────────────────────────

def test_v3_target_and_config_loaded(tmp_path):
    """v3 target and config fields are loaded and preserved."""
    path = _write_manifest(tmp_path, MINIMAL_V3)
    m = load_manifest(path)
    assert m["target"]["repo"] == "llm-d-inference-scheduler"
    assert m["config"]["kind"] == "EndpointPickerConfig"


def test_v3_observe_defaults(tmp_path):
    """v3 without observe section gets default request_multiplier=1."""
    data = {k: v for k, v in MINIMAL_V3.items() if k != "observe"}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["observe"]["request_multiplier"] == 1


def test_v3_observe_explicit(tmp_path):
    """v3 with explicit observe.request_multiplier preserves value."""
    data = {**MINIMAL_V3, "observe": {"request_multiplier": 10}}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["observe"]["request_multiplier"] == 10


def test_v3_build_defaults(tmp_path):
    """v3 without build section gets default commands=[]."""
    data = {k: v for k, v in MINIMAL_V3.items() if k != "build"}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["build"]["commands"] == []


def test_v3_build_commands_loaded(tmp_path):
    """v3 with build.commands preserves the list."""
    data = {**MINIMAL_V3, "build": {"commands": [["go", "build", "./..."]]}}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["build"]["commands"] == [["go", "build", "./..."]]


def test_v3_build_commands_not_list_raises(tmp_path):
    """build.commands must be a list."""
    data = {**MINIMAL_V3, "build": {"commands": "go build"}}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="build.commands.*list"):
        load_manifest(path)


def test_v3_epp_image_loaded(tmp_path):
    """v3 epp_image.upstream fields are loaded."""
    path = _write_manifest(tmp_path, MINIMAL_V3)
    m = load_manifest(path)
    assert m["epp_image"]["upstream"]["hub"] == "ghcr.io/llm-d"
    assert m["epp_image"]["upstream"]["name"] == "llm-d-inference-scheduler"
    assert m["epp_image"]["upstream"]["tag"] == "v0.7.1"


def test_v3_epp_image_with_build(tmp_path):
    """v3 epp_image with both upstream and build loads both."""
    data = {**MINIMAL_V3, "epp_image": {
        "upstream": {"hub": "ghcr.io/llm-d", "name": "epp", "tag": "v1"},
        "build": {"hub": "ghcr.io/me", "name": "epp", "tag": "dev", "platform": "linux/amd64"},
    }}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["epp_image"]["build"]["hub"] == "ghcr.io/me"
    assert m["epp_image"]["build"]["tag"] == "dev"


def test_v3_epp_image_missing_upstream_raises(tmp_path):
    """epp_image without upstream raises."""
    data = {**MINIMAL_V3, "epp_image": {"build": {"hub": "x", "name": "y", "tag": "z"}}}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="epp_image.upstream"):
        load_manifest(path)


def test_v3_epp_image_upstream_missing_field_raises(tmp_path):
    """epp_image.upstream missing hub/name/tag raises."""
    for field in ("hub", "name", "tag"):
        upstream = {"hub": "a", "name": "b", "tag": "c"}
        del upstream[field]
        data = {**MINIMAL_V3, "epp_image": {"upstream": upstream}}
        path = _write_manifest(tmp_path, data)
        with pytest.raises(ManifestError, match=f"epp_image.upstream.{field}"):
            load_manifest(path)


def test_v3_epp_image_build_missing_field_raises(tmp_path):
    """epp_image.build missing hub/name/tag raises."""
    for field in ("hub", "name", "tag"):
        build = {"hub": "a", "name": "b", "tag": "c"}
        del build[field]
        data = {**MINIMAL_V3, "epp_image": {
            "upstream": {"hub": "x", "name": "y", "tag": "z"},
            "build": build,
        }}
        path = _write_manifest(tmp_path, data)
        with pytest.raises(ManifestError, match=f"epp_image.build.{field}"):
            load_manifest(path)


def test_v3_epp_image_optional(tmp_path):
    """v3 without epp_image is valid."""
    data = {k: v for k, v in MINIMAL_V3.items() if k != "epp_image"}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert "epp_image" not in m


def test_v3_target_absent_raises(tmp_path):
    """v3 without target raises."""
    data = {k: v for k, v in MINIMAL_V3.items() if k != "target"}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="target"):
        load_manifest(path)


def test_v3_config_absent_raises(tmp_path):
    """v3 without config raises."""
    data = {k: v for k, v in MINIMAL_V3.items() if k != "config"}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="config"):
        load_manifest(path)


def test_v3_config_missing_kind_raises(tmp_path):
    """config without kind raises."""
    data = {**MINIMAL_V3, "config": {}}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="config.kind"):
        load_manifest(path)



def test_v3_target_missing_repo_raises(tmp_path):
    """target without repo raises."""
    data = {**MINIMAL_V3, "target": {}}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="target.repo"):
        load_manifest(path)


# ── pipeline field (optional, v3 only) ─────────────────────────────────────

def test_v3_pipeline_defaults_when_absent(tmp_path):
    """v3 without pipeline section gets defaults: name='sim2real', yaml='pipeline/pipeline.yaml'."""
    data = {k: v for k, v in MINIMAL_V3.items() if k != "pipeline"}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["pipeline"]["name"] == "sim2real"
    assert m["pipeline"]["yaml"] == "pipeline/pipeline.yaml"


def test_v3_pipeline_explicit_values(tmp_path):
    """v3 with explicit pipeline.name and pipeline.yaml preserves values."""
    data = {**MINIMAL_V3, "pipeline": {"name": "custom-pipe", "yaml": "custom/my-pipeline.yaml"}}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["pipeline"]["name"] == "custom-pipe"
    assert m["pipeline"]["yaml"] == "custom/my-pipeline.yaml"


def test_v3_pipeline_partial_name_only(tmp_path):
    """v3 with only pipeline.name gets default yaml."""
    data = {**MINIMAL_V3, "pipeline": {"name": "other"}}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["pipeline"]["name"] == "other"
    assert m["pipeline"]["yaml"] == "pipeline/pipeline.yaml"


def test_v3_pipeline_partial_yaml_only(tmp_path):
    """v3 with only pipeline.yaml gets default name."""
    data = {**MINIMAL_V3, "pipeline": {"yaml": "other/pipe.yaml"}}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["pipeline"]["name"] == "sim2real"
    assert m["pipeline"]["yaml"] == "other/pipe.yaml"


def test_v3_pipeline_not_mapping_raises(tmp_path):
    """pipeline must be a mapping if present."""
    data = {**MINIMAL_V3, "pipeline": "not-a-dict"}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="pipeline must be a mapping"):
        load_manifest(path)


# ── Multi-baseline (v3 extension) ─────────────────────────────────────────

MULTI_BASELINE_V3 = {
    "kind": "sim2real-transfer",
    "version": 3,
    "scenario": "routing",
    "baselines": [
        {"name": "b1", "scenario": "baseline_1.yaml"},
        {"name": "b2", "scenario": "baseline_2.yaml"},
    ],
    "workloads": ["sim2real_golden/workloads/wl1.yaml"],
    "target": {"repo": "llm-d-inference-scheduler"},
    "config": {
        "kind": "EndpointPickerConfig",
    },
}


def test_baselines_list_loaded(tmp_path):
    """v3 with baselines: list loads and normalizes each entry."""
    path = _write_manifest(tmp_path, MULTI_BASELINE_V3)
    m = load_manifest(path)
    assert "baselines" in m
    assert len(m["baselines"]) == 2
    assert m["baselines"][0]["name"] == "b1"
    assert m["baselines"][1]["name"] == "b2"


def test_singular_baseline_normalized_to_baselines(tmp_path):
    """v3 with singular baseline: normalizes to baselines: list of one."""
    path = _write_manifest(tmp_path, MINIMAL_V3)
    m = load_manifest(path)
    assert "baselines" in m
    assert len(m["baselines"]) == 1
    assert m["baselines"][0]["name"] == "baseline"


def test_baselines_must_be_list(tmp_path):
    data = {**MULTI_BASELINE_V3, "baselines": "not-a-list"}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="baselines.*list"):
        load_manifest(path)


def test_baselines_entry_requires_name(tmp_path):
    data = {**MULTI_BASELINE_V3, "baselines": [{"scenario": "x.yaml"}]}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="baselines.*name"):
        load_manifest(path)


def test_baselines_entry_requires_scenario(tmp_path):
    data = {**MULTI_BASELINE_V3, "baselines": [{"name": "b1"}]}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="baselines.*scenario"):
        load_manifest(path)


def test_baselines_name_must_be_valid(tmp_path):
    data = {**MULTI_BASELINE_V3, "baselines": [{"name": "Bad_Name!", "scenario": "x.yaml"}]}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="invalid"):
        load_manifest(path)


def test_baselines_name_rejects_hyphens(tmp_path):
    data = {**MULTI_BASELINE_V3, "baselines": [{"name": "my-algo", "scenario": "x.yaml"}]}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="invalid"):
        load_manifest(path)


def test_baselines_duplicate_name_raises(tmp_path):
    data = {**MULTI_BASELINE_V3, "baselines": [
        {"name": "b1", "scenario": "x.yaml"},
        {"name": "b1", "scenario": "y.yaml"},
    ]}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="duplicate.*b1"):
        load_manifest(path)


def test_baselines_with_defaults_field(tmp_path):
    data = {**MULTI_BASELINE_V3, "baselines": [
        {"name": "b1", "scenario": "x.yaml", "defaults": "defaults.yaml"},
    ]}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["baselines"][0]["defaults"] == "defaults.yaml"


def test_both_baseline_and_baselines_raises(tmp_path):
    data = {**MULTI_BASELINE_V3, "baseline": {"sim": {"config": "x.yaml"}}}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="both.*baseline.*baselines"):
        load_manifest(path)


def test_algorithms_list_loaded(tmp_path):
    data = {**MULTI_BASELINE_V3, "algorithms": [
        {"name": "ac1", "source": "algo.go", "scenario": "treatment.yaml", "defaults": "b1"},
    ]}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert len(m["algorithms"]) == 1
    assert m["algorithms"][0]["name"] == "ac1"
    assert m["algorithms"][0]["defaults"] == "b1"


def test_singular_algorithm_normalized_to_algorithms(tmp_path):
    """v3 with singular algorithm: normalizes to algorithms: list of one."""
    path = _write_manifest(tmp_path, MINIMAL_V3)
    m = load_manifest(path)
    assert "algorithms" in m
    assert len(m["algorithms"]) == 1
    assert m["algorithms"][0]["name"] == "treatment"
    assert m["algorithms"][0]["source"] == MINIMAL_V3["algorithm"]["source"]


def test_no_algorithm_no_algorithms_is_baseline_only(tmp_path):
    data = {k: v for k, v in MULTI_BASELINE_V3.items() if k != "algorithms"}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m.get("algorithms", []) == []

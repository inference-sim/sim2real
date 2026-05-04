"""Tests for v2 manifest loader."""
import pytest
import yaml
from pathlib import Path

from pipeline.lib.manifest import load_manifest, ManifestError

MINIMAL_V2 = {
    "kind": "sim2real-transfer",
    "version": 2,
    "scenario": "routing",
    "algorithm": {
        "source": "sim2real_golden/routers/router_adaptive_v2.go",
        "config": "sim2real_golden/routers/policy_adaptive_v2.yaml",
    },
    "baseline": {"config": "sim2real_golden/routers/policy_baseline_211.yaml"},
    "workloads": ["sim2real_golden/workloads/wl1.yaml"],
}


def _write_manifest(tmp_path, data):
    p = tmp_path / "transfer.yaml"
    p.write_text(yaml.dump(data))
    return p


def test_load_valid_v2(tmp_path):
    path = _write_manifest(tmp_path, MINIMAL_V2)
    m = load_manifest(path)
    assert m["scenario"] == "routing"
    assert m["algorithm"]["source"].endswith(".go")


def test_v1_raises_migration_error(tmp_path):
    v1 = {"kind": "sim2real-transfer", "version": 1, "algorithm": {"experiment_dir": "x"}}
    path = _write_manifest(tmp_path, v1)
    with pytest.raises(ManifestError, match="v1.*v2"):
        load_manifest(path)


def test_missing_version_raises(tmp_path):
    data = {k: v for k, v in MINIMAL_V2.items() if k != "version"}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="version"):
        load_manifest(path)


def test_missing_required_field(tmp_path):
    for field in ["scenario", "algorithm", "baseline"]:
        data = {k: v for k, v in MINIMAL_V2.items() if k != field}
        path = _write_manifest(tmp_path, data)
        with pytest.raises(ManifestError, match=field):
            load_manifest(path)


def test_missing_algorithm_source(tmp_path):
    data = {**MINIMAL_V2, "algorithm": {"config": "x.yaml"}}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="algorithm.source"):
        load_manifest(path)


def test_missing_algorithm_config_is_valid(tmp_path):
    """algorithm.config is optional — manifests without it load cleanly."""
    data = {**MINIMAL_V2, "algorithm": {"source": "x.go"}}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert "config" not in m["algorithm"]


def test_missing_baseline_config(tmp_path):
    data = {**MINIMAL_V2, "baseline": {}}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="baseline.config"):
        load_manifest(path)


def test_optional_context_fields(tmp_path):
    data = {**MINIMAL_V2, "context": {
        "files": ["docs/mapping.md"],
        "notes": "Use regime detection pattern",
    }}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["context"]["notes"] == "Use regime detection pattern"
    assert len(m["context"]["files"]) == 1


def test_workloads_must_be_list(tmp_path):
    data = {**MINIMAL_V2, "workloads": "not_a_list.yaml"}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="workloads.*list"):
        load_manifest(path)


def test_empty_workloads_valid_standby_mode(tmp_path):
    """Empty workloads list is valid — standby mode: stack up, no benchmarks."""
    data = {**MINIMAL_V2, "workloads": []}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["workloads"] == []


def test_absent_workloads_defaults_to_empty(tmp_path):
    """Missing workloads key is valid; defaults to []."""
    data = {k: v for k, v in MINIMAL_V2.items() if k != "workloads"}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["workloads"] == []


def test_null_workloads_defaults_to_empty(tmp_path):
    """workloads: null (YAML null) is valid; defaults to []."""
    data = {**MINIMAL_V2, "workloads": None}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["workloads"] == []


def test_wrong_kind(tmp_path):
    data = {**MINIMAL_V2, "kind": "something-else"}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="kind"):
        load_manifest(path)


def test_unsupported_version(tmp_path):
    data = {**MINIMAL_V2, "version": 99}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="version"):
        load_manifest(path)


def test_file_not_found():
    with pytest.raises(ManifestError, match="not found"):
        load_manifest(Path("/nonexistent/transfer.yaml"))


# ── Hints section ─────────────────────────────────────────────────────────────

def test_hints_section_optional(tmp_path):
    """Manifest without hints loads cleanly; hints defaults to empty."""
    path = _write_manifest(tmp_path, MINIMAL_V2)
    m = load_manifest(path)
    hints = m.get("hints", {})
    assert hints.get("text", "") == ""
    assert hints.get("files", []) == []


def test_hints_text_loaded(tmp_path):
    data = {**MINIMAL_V2, "hints": {"text": "Modify precise_prefix_cache.go"}}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["hints"]["text"] == "Modify precise_prefix_cache.go"


def test_hints_files_contents_embedded(tmp_path):
    hint_file = tmp_path / "hint.md"
    hint_file.write_text("# Transfer hint\nRewrite scorer")
    data = {**MINIMAL_V2, "hints": {"files": [str(hint_file)]}}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert len(m["hints"]["files"]) == 1
    assert m["hints"]["files"][0]["path"] == str(hint_file)
    assert "Rewrite scorer" in m["hints"]["files"][0]["content"]


def test_hints_file_not_found_raises(tmp_path):
    data = {**MINIMAL_V2, "hints": {"files": ["/nonexistent/hint.md"]}}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="hints.files"):
        load_manifest(path)


def test_context_notes_deprecated_warns(tmp_path):
    data = {**MINIMAL_V2, "context": {"notes": "old style note", "files": []}}
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
        "helm_path": "gaie.treatment.helmValues.inferenceExtension.pluginsCustomConfig.custom-plugins.yaml",
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


def test_v2_normalizes_to_v3_shape(tmp_path):
    """v2 manifest: baseline.config is mapped to baseline.sim.config in output."""
    path = _write_manifest(tmp_path, MINIMAL_V2)
    m = load_manifest(path)
    assert "sim" in m["baseline"]
    assert m["baseline"]["sim"]["config"] == "sim2real_golden/routers/policy_baseline_211.yaml"
    assert m["baseline"]["real"]["config"] is None
    assert m["baseline"]["real"]["notes"] == ""


def test_v2_baseline_config_missing_raises(tmp_path):
    """v2 without baseline.config raises ManifestError."""
    data = {**MINIMAL_V2, "baseline": {}}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="baseline.config"):
        load_manifest(path)


def test_v3_accepted_alongside_v2(tmp_path):
    """Version 3 is accepted; version 2 is still accepted."""
    for ver, data in [(2, MINIMAL_V2), (3, MINIMAL_V3)]:
        ver_dir = tmp_path / f"v{ver}"
        ver_dir.mkdir()
        path = _write_manifest(ver_dir, data)
        m = load_manifest(path)
        assert m["version"] == ver


# ── v3 fields (migrated from env_defaults.yaml) ─────────────────────────────

def test_v3_target_and_config_loaded(tmp_path):
    """v3 target and config fields are loaded and preserved."""
    path = _write_manifest(tmp_path, MINIMAL_V3)
    m = load_manifest(path)
    assert m["target"]["repo"] == "llm-d-inference-scheduler"
    assert m["config"]["kind"] == "EndpointPickerConfig"
    assert "helm_path" in m["config"]


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
    data = {**MINIMAL_V3, "config": {"helm_path": "x"}}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="config.kind"):
        load_manifest(path)


def test_v3_config_missing_helm_path_raises(tmp_path):
    """config without helm_path raises."""
    data = {**MINIMAL_V3, "config": {"kind": "EndpointPickerConfig"}}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="config.helm_path"):
        load_manifest(path)


def test_v3_target_missing_repo_raises(tmp_path):
    """target without repo raises."""
    data = {**MINIMAL_V3, "target": {}}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="target.repo"):
        load_manifest(path)

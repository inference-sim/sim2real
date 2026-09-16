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
    for field in ["scenario", "baselines"]:
        data = {k: v for k, v in MINIMAL_V3.items() if k != field}
        path = _write_manifest(tmp_path, data)
        with pytest.raises(ManifestError, match=field):
            load_manifest(path)


def test_algorithms_section_entirely_optional(tmp_path):
    """Manifest without algorithms is valid (baseline-only mode)."""
    data = {k: v for k, v in MINIMAL_V3.items() if k != "algorithms"}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["algorithms"] == []


def test_optional_context_fields(tmp_path):
    data = {**MINIMAL_V3, "context": {
        "files": ["docs/mapping.md"],
    }}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["context"]["files"] == ["docs/mapping.md"]


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


# ── Context section ──────────────────────────────────────────────────────────

def test_context_section_optional(tmp_path):
    """Manifest without context loads cleanly; context defaults to empty."""
    path = _write_manifest(tmp_path, MINIMAL_V3)
    m = load_manifest(path)
    ctx = m.get("context", {})
    assert ctx.get("text", "") == ""
    assert ctx.get("files", []) == []


def test_context_text_loaded(tmp_path):
    data = {**MINIMAL_V3, "context": {"text": "Modify precise_prefix_cache.go"}}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["context"]["text"] == "Modify precise_prefix_cache.go"


def test_context_files_loaded(tmp_path):
    data = {**MINIMAL_V3, "context": {"files": ["docs/mapping.md"]}}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["context"]["files"] == ["docs/mapping.md"]


def test_context_rejects_unknown_keys(tmp_path):
    data = {**MINIMAL_V3, "context": {"text": "ok", "notes": "bad"}}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="context.*unknown.*notes"):
        load_manifest(path)


def test_context_files_must_be_list(tmp_path):
    data = {**MINIMAL_V3, "context": {"files": "not_a_list"}}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="context.files.*list"):
        load_manifest(path)


# ── v3 manifest fixtures ───────────────────────────────────────────────────

MINIMAL_V3 = {
    "kind": "sim2real-transfer",
    "version": 3,
    "scenario": "routing",
    "algorithms": [
        {
            "name": "treatment",
            "source": "sim2real_golden/routers/router_adaptive_v2.go",
            "defaults": "baseline",
        },
    ],
    "baselines": [
        {
            "name": "baseline",
            "scenario": "baseline.yaml",
            "sim": {"config": "sim2real_golden/routers/policy_baseline_211.yaml"},
        },
    ],
    "workloads": ["sim2real_golden/workloads/wl1.yaml"],
    "component": {
        "repo": "github.com/llm-d/llm-d-inference-scheduler",
        "kind": "EndpointPickerConfig",
        "base_image": {
            "hub": "ghcr.io/llm-d",
            "name": "llm-d-inference-scheduler",
            "tag": "v0.7.1",
        },
    },
}


def test_load_valid_v3_minimal(tmp_path):
    """v3 minimal manifest loads cleanly."""
    path = _write_manifest(tmp_path, MINIMAL_V3)
    m = load_manifest(path)
    assert len(m["baselines"]) == 1
    assert m["baselines"][0]["name"] == "baseline"
    assert len(m["algorithms"]) == 1
    assert m["algorithms"][0]["name"] == "treatment"


# ── measurement protocol file (#911) ─────────────────────────────────────────
#
# The observe protocol moved out of transfer.yaml's `blis_observe:` block into a
# bundle-level measurement file. These tests carry over the per-key type and
# allowlist coverage the `blis_observe:` tests had — the roster is unchanged, only
# where it is read from — and add the two things #911 exists to close: a rejected
# legacy key, and a rejected unknown top-level key.

PROTOCOL = {
    "kind": "measurement-protocol",
    "version": 1,
    "maxConcurrency": 10000,
    "timeout": 1800,
    "warmupRequests": 0,
    "prewarmDuration": "60s",
    "detectors": "composite",
    "apiFormat": "completions",
    "recordItl": False,
    "streaming": True,
}


def _write_bundle(tmp_path, manifest_extra=None, protocol=PROTOCOL, *,
                  protocol_name="measurement.yaml", write_protocol=True):
    """Write transfer.yaml (+ optional protocol file) and return the manifest path."""
    data = {**MINIMAL_V3}
    if manifest_extra:
        data.update(manifest_extra)
    path = _write_manifest(tmp_path, data)
    if write_protocol:
        target = tmp_path / protocol_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            protocol if isinstance(protocol, str) else yaml.dump(protocol)
        )
    return path


def _with_measurement(tmp_path, protocol=PROTOCOL, **kw):
    return _write_bundle(tmp_path, {"measurement": "measurement.yaml"},
                         protocol, **kw)


# ── AC-1: values come from the file ──────────────────────────────────────────

def test_measurement_pointer_resolves_to_the_files_values(tmp_path):
    """AC-1. The loader replaces the POINTER with the file's values, so every
    downstream consumer reads values and never has to resolve a path."""
    m = load_manifest(_with_measurement(tmp_path))
    assert m["measurement"] == {
        "maxConcurrency": 10000, "timeout": 1800, "warmupRequests": 0,
        "prewarmDuration": "60s", "detectors": "composite",
        "apiFormat": "completions", "recordItl": False, "streaming": True,
    }
    # kind/version are the file's envelope, not protocol values — they must not
    # leak into the roster dict or the renderer would reject them as unknown.
    assert "kind" not in m["measurement"]
    assert "version" not in m["measurement"]


def test_measurement_values_land_in_the_hashed_assembly_slice(tmp_path):
    """The reproducibility property the pointer design would otherwise lose.

    ``params_hash`` is taken over the assembly-slice bytes. Inlining the resolved
    VALUES means editing the protocol file changes the hash, exactly as editing
    the old inline block did. Had the pointer been left unresolved, only the
    FILENAME would be hashed and two runs with identical hashes could have been
    measured with different instrument settings."""
    from pipeline.lib import slicer
    m1 = load_manifest(_with_measurement(tmp_path))
    slice1 = slicer.assembly_slice(m1)
    assert slice1["measurement"]["warmupRequests"] == 0

    other = tmp_path / "other"
    other.mkdir()
    m2 = load_manifest(_with_measurement(other, {**PROTOCOL, "warmupRequests": 50}))
    slice2 = slicer.assembly_slice(m2)
    assert slice2["measurement"]["warmupRequests"] == 50
    # The whole point: a protocol edit is visible in what gets hashed.
    assert slice1["measurement"] != slice2["measurement"]


def test_measurement_absent_is_valid_and_falls_through_to_roster_defaults(tmp_path):
    """Absent is still legal — a bundle that never configured the instrument.
    What #911 removes is a bundle that DID and was ignored."""
    m = load_manifest(_write_manifest(tmp_path, MINIMAL_V3))
    assert m["measurement"] == {}


def test_measurement_partial_file_keeps_omitted_keys_absent(tmp_path):
    """The loader does not default; omitted keys fall through in the renderer."""
    m = load_manifest(_with_measurement(
        tmp_path, {"kind": "measurement-protocol", "version": 1, "timeout": 3600}))
    assert m["measurement"] == {"timeout": 3600}


# ── AC-2/AC-3: the hard cutover ──────────────────────────────────────────────

def test_blis_observe_is_rejected_and_names_its_replacement(tmp_path):
    """AC-2. Rejected, not dual-supported — the #901 posture. The message has to
    carry the migration, since that is what makes a breaking change navigable."""
    with pytest.raises(ManifestError) as exc:
        load_manifest(_write_bundle(tmp_path, {"blis_observe": {"timeout": 900}},
                                    write_protocol=False))
    msg = str(exc.value)
    assert "blis_observe" in msg
    assert "measurement: measurement.yaml" in msg
    assert "kind: measurement-protocol" in msg
    assert "#911" in msg
    # The message must show the shape, or the operator has to go find it.
    for key in ("maxConcurrency", "timeout", "warmupRequests", "prewarmDuration",
                "detectors", "apiFormat", "recordItl", "streaming"):
        assert key in msg


def test_both_keys_present_is_an_error(tmp_path):
    """AC-3. A half-finished migration must not validate."""
    with pytest.raises(ManifestError, match="blis_observe"):
        load_manifest(_write_bundle(
            tmp_path, {"blis_observe": {}, "measurement": "measurement.yaml"}))


def test_missing_measurement_target_is_an_error(tmp_path):
    """AC-3. A pointer at nothing is the silent-fallback case #911 closes."""
    with pytest.raises(ManifestError, match="measurement file not found"):
        load_manifest(_write_bundle(tmp_path, {"measurement": "nope.yaml"},
                                    write_protocol=False))


@pytest.mark.parametrize("bad", [123, [], {}, "", "   ", True])
def test_measurement_pointer_must_be_a_non_empty_string(tmp_path, bad):
    """A genuine YAML null is NOT here on purpose — `measurement:` absent means
    "instrument never configured", which stays legal and is covered above."""
    with pytest.raises(ManifestError, match="measurement must be a non-empty string"):
        load_manifest(_write_bundle(tmp_path, {"measurement": bad},
                                    write_protocol=False))


def test_measurement_pointer_must_be_relative(tmp_path):
    """An absolute path would escape the experiment root and break portability."""
    with pytest.raises(ManifestError, match="measurement must be a relative path"):
        load_manifest(_write_bundle(tmp_path, {"measurement": "/etc/measurement.yaml"},
                                    write_protocol=False))


# ── AC-4: unknown top-level manifest keys ────────────────────────────────────

def test_unknown_top_level_key_is_rejected(tmp_path):
    """AC-4. The trap that made #911 necessary: before this, `measurement:` (or
    any typo) was accepted and never read."""
    with pytest.raises(ManifestError, match="unknown top-level keys.*mesurement"):
        load_manifest(_write_bundle(tmp_path, {"mesurement": "typo.yaml"},
                                    write_protocol=False))


def test_unknown_top_level_key_message_lists_the_valid_set(tmp_path):
    with pytest.raises(ManifestError) as exc:
        load_manifest(_write_bundle(tmp_path, {"bogus": 1}, write_protocol=False))
    msg = str(exc.value)
    for key in ("kind", "version", "scenario", "baselines", "algorithms",
                "workloads", "context", "defaults", "component", "pipeline",
                "measurement"):
        assert key in msg


def test_every_key_the_loader_reads_is_in_the_top_level_allowlist():
    """A guard against the allowlist being narrower than the schema: any key the
    loader validates must be spelled in the allowlist, or a VALID bundle breaks.
    Asserted as a set so adding a schema key without allowlisting it fails."""
    from pipeline.lib import manifest as mod
    assert mod._VALID_TOP_KEYS >= set(mod._REQUIRED_TOP)
    # MINIMAL_V3 is a known-good bundle; every key in it must be allowed.
    assert set(MINIMAL_V3) <= mod._VALID_TOP_KEYS


def test_minimal_manifest_still_validates_under_the_allowlist(tmp_path):
    """Non-vacuousness for the two tests above: the fixture really does load."""
    assert load_manifest(_write_manifest(tmp_path, MINIMAL_V3))["kind"] == \
        "sim2real-transfer"


# ── AC-5/AC-6: the protocol file's own validation ────────────────────────────

def test_unknown_key_inside_the_protocol_file_is_rejected(tmp_path):
    """AC-5. Validated against the observe_argv roster — snake_case spellings are
    the likely mistake, and every rename is a mapping layer #911 refuses to add."""
    with pytest.raises(ManifestError, match="unknown keys.*warmup_requests"):
        load_manifest(_with_measurement(
            tmp_path, {**PROTOCOL, "warmup_requests": 0}))


def test_protocol_unknown_key_message_lists_the_roster(tmp_path):
    with pytest.raises(ManifestError) as exc:
        load_manifest(_with_measurement(tmp_path, {**PROTOCOL, "postHocDetector": "x"}))
    msg = str(exc.value)
    for key in ("detectors", "apiFormat", "recordItl", "streaming", "extraArgs",
                "maxConcurrency", "timeout", "warmupRequests", "prewarmDuration"):
        assert key in msg


@pytest.mark.parametrize("key,value", [
    ("detectors", "composite"),
    ("detectors", ""),
    ("apiFormat", "chat"),
    ("recordItl", True),
    ("streaming", False),
    ("maxConcurrency", 5000),
    ("prewarmDuration", "30s"),
    ("extraArgs", "--rate 5"),
])
def test_protocol_accepts_every_roster_key(tmp_path, key, value):
    """The roster admits extraArgs (9 keys, not the 8 in the issue's example), so
    the protocol file does too — AC-5 says validate against the roster."""
    m = load_manifest(_with_measurement(
        tmp_path, {"kind": "measurement-protocol", "version": 1, key: value}))
    assert m["measurement"][key] == value


@pytest.mark.parametrize("bad_value", [True, [1, 2], {"nested": 1}, None, "60"])
def test_protocol_rejects_wrong_typed_int_values(tmp_path, bad_value):
    """Carried over from the blis_observe tests: YAML `true` must never satisfy an
    int field (bool subclasses int), and a numeric STRING is rejected too —
    accepting two spellings of one value invites drift."""
    with pytest.raises(ManifestError, match="timeout must be an int"):
        load_manifest(_with_measurement(
            tmp_path, {"kind": "measurement-protocol", "version": 1,
                       "timeout": bad_value}))


@pytest.mark.parametrize("key,bad", [
    ("recordItl", "true"),      # string, not bool
    ("streaming", 1),           # int, not bool
    ("detectors", 5),           # int, not str
    ("apiFormat", True),        # bool, not str
    ("prewarmDuration", 60),    # int, not duration string
])
def test_protocol_rejects_wrong_types_per_key(tmp_path, key, bad):
    """AC-6. Types come from observe_argv, not restated here."""
    with pytest.raises(ManifestError, match=f"{key} must be"):
        load_manifest(_with_measurement(
            tmp_path, {"kind": "measurement-protocol", "version": 1, key: bad}))


def test_protocol_error_names_the_file(tmp_path):
    """A type error has to say WHICH file, since the value is no longer inline."""
    with pytest.raises(ManifestError, match=r"measurement\.yaml"):
        load_manifest(_with_measurement(
            tmp_path, {"kind": "measurement-protocol", "version": 1,
                       "timeout": "sixty"}))


# ── the protocol file's envelope ─────────────────────────────────────────────

def test_protocol_requires_its_kind(tmp_path):
    with pytest.raises(ManifestError, match="expected kind: measurement-protocol"):
        load_manifest(_with_measurement(tmp_path, {**PROTOCOL, "kind": "nope"}))


def test_protocol_requires_a_version(tmp_path):
    proto = {k: v for k, v in PROTOCOL.items() if k != "version"}
    with pytest.raises(ManifestError, match="missing required field: version"):
        load_manifest(_with_measurement(tmp_path, proto))


def test_protocol_rejects_an_unsupported_version(tmp_path):
    with pytest.raises(ManifestError, match="unsupported version: 2"):
        load_manifest(_with_measurement(tmp_path, {**PROTOCOL, "version": 2}))


def test_protocol_rejects_an_empty_file(tmp_path):
    with pytest.raises(ManifestError, match="is empty"):
        load_manifest(_with_measurement(tmp_path, "", write_protocol=True))


def test_protocol_rejects_a_non_mapping(tmp_path):
    with pytest.raises(ManifestError, match="must be a mapping"):
        load_manifest(_with_measurement(tmp_path, "- a\n- b\n"))


def test_protocol_rejects_invalid_yaml(tmp_path):
    with pytest.raises(ManifestError, match="YAML parse error"):
        load_manifest(_with_measurement(tmp_path, "kind: [unclosed\n"))


def test_protocol_that_exists_but_cannot_be_read_is_an_error(tmp_path):
    """The OSError branch: the pointer resolves to something that exists (so the
    not-found check passes) but cannot be read as a file. A directory is the
    reachable case; permission bits are not portable to assert."""
    path = _write_bundle(tmp_path, {"measurement": "measurement.yaml"},
                         write_protocol=False)
    (tmp_path / "measurement.yaml").mkdir()
    with pytest.raises(ManifestError, match="cannot read measurement file"):
        load_manifest(path)


def test_protocol_with_only_an_envelope_is_legal_and_means_no_overrides(tmp_path):
    """Counterpart to the empty-file error. kind+version and zero protocol keys
    is legal — it resolves to {} and every key falls through to the roster
    defaults, exactly as omitting `measurement:` does. The empty-FILE message
    must therefore not claim a protocol key is required (it once did, while
    pipeline/README.md said all keys are optional)."""
    m = load_manifest(_with_measurement(
        tmp_path, {"kind": "measurement-protocol", "version": 1}))
    assert m["measurement"] == {}


def test_empty_file_error_does_not_claim_a_protocol_key_is_required(tmp_path):
    """Guards the wording itself, since the code deliberately does not enforce
    what the old message asserted."""
    with pytest.raises(ManifestError) as exc:
        load_manifest(_with_measurement(tmp_path, "", write_protocol=True))
    msg = str(exc.value)
    assert "kind: measurement-protocol" in msg
    assert "version: 1" in msg
    assert "protocol key" not in msg


def test_protocol_may_live_in_a_subdirectory(tmp_path):
    """The pointer is a path, not a bare filename."""
    path = _write_bundle(tmp_path, {"measurement": "protocol/measurement.yaml"},
                         PROTOCOL, protocol_name="protocol/measurement.yaml")
    assert load_manifest(path)["measurement"]["timeout"] == 1800


def test_manifest_allowlist_matches_the_renderer_table(tmp_path):
    """The allowlist is DERIVED from the renderer's table, so a key cannot be
    accepted by the manifest yet reach no flag. Assert the derivation rather
    than a hardcoded list, so the two cannot drift."""
    from pipeline.lib import observe_argv
    assert observe_argv.VALID_OBSERVE_KEYS == frozenset(
        observe_argv.OBSERVE_FLAGS) | {"extraArgs"}




# ── component section ─────────────────────────────────────────────────────────

def test_component_required_when_algorithms_present(tmp_path):
    """Missing component raises when algorithms are specified."""
    data = {k: v for k, v in MINIMAL_V3.items() if k != "component"}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="component.*required.*algorithms"):
        load_manifest(path)


def test_no_algorithms_no_component_valid(tmp_path):
    """Baseline-only: no algorithms, no component → valid."""
    data = {k: v for k, v in MINIMAL_V3.items() if k not in ("algorithms", "component")}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["algorithms"] == []
    assert m.get("component") is None


def test_no_algorithms_with_component_valid(tmp_path):
    """No algorithms but component present → valid (component used for SHA tracking)."""
    data = {k: v for k, v in MINIMAL_V3.items() if k != "algorithms"}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["algorithms"] == []
    assert m["component"]["repo"] == "github.com/llm-d/llm-d-inference-scheduler"


def test_explicit_component_null_normalized(tmp_path):
    """component: null (explicit YAML null) is removed from manifest, not left as None."""
    data = {k: v for k, v in MINIMAL_V3.items() if k not in ("algorithms", "component")}
    data["component"] = None
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert "component" not in m


def test_component_must_be_mapping(tmp_path):
    """component: 'string' raises."""
    data = {**MINIMAL_V3, "component": "string"}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="component.*mapping"):
        load_manifest(path)


def test_component_repo_required(tmp_path):
    """component without repo raises."""
    data = {**MINIMAL_V3, "component": {"kind": "EndpointPickerConfig"}}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="component.repo"):
        load_manifest(path)


def test_component_kind_required(tmp_path):
    """component without kind raises."""
    data = {**MINIMAL_V3, "component": {"repo": "github.com/llm-d/llm-d-inference-scheduler"}}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="component.kind"):
        load_manifest(path)


def test_component_path_defaults_from_repo(tmp_path):
    """component.path defaults from last segment of repo URL."""
    path = _write_manifest(tmp_path, MINIMAL_V3)
    m = load_manifest(path)
    assert m["component"]["path"] == "llm-d-inference-scheduler"


def test_component_path_explicit(tmp_path):
    """Explicit component.path is preserved."""
    data = {**MINIMAL_V3, "component": {
        "repo": "github.com/llm-d/llm-d-inference-scheduler",
        "kind": "EndpointPickerConfig",
        "path": "custom",
    }}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["component"]["path"] == "custom"


def test_component_base_image_optional(tmp_path):
    """MINIMAL_V3 without base_image is valid."""
    data = {**MINIMAL_V3, "component": {
        "repo": "github.com/llm-d/llm-d-inference-scheduler",
        "kind": "EndpointPickerConfig",
    }}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert "base_image" not in m["component"]


def test_component_base_image_validates_fields(tmp_path):
    """base_image missing hub/name raises."""
    for field in ("hub", "name"):
        base_image = {"hub": "a", "name": "b", "tag": "c"}
        del base_image[field]
        data = {**MINIMAL_V3, "component": {
            "repo": "github.com/llm-d/llm-d-inference-scheduler",
            "kind": "EndpointPickerConfig",
            "base_image": base_image,
        }}
        path = _write_manifest(tmp_path, data)
        with pytest.raises(ManifestError, match=f"component.base_image.{field}"):
            load_manifest(path)


def test_component_base_image_tag_optional(tmp_path):
    """base_image without tag is valid — tag is informational only."""
    data = {**MINIMAL_V3, "component": {
        "repo": "github.com/llm-d/llm-d-inference-scheduler",
        "kind": "EndpointPickerConfig",
        "base_image": {"hub": "ghcr.io/llm-d", "name": "llm-d-inference-scheduler"},
    }}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["component"]["base_image"]["hub"] == "ghcr.io/llm-d"
    assert "tag" not in m["component"]["base_image"]


def test_component_base_image_loaded(tmp_path):
    """Loading MINIMAL_V3 yields correct base_image fields."""
    path = _write_manifest(tmp_path, MINIMAL_V3)
    m = load_manifest(path)
    assert m["component"]["base_image"]["hub"] == "ghcr.io/llm-d"
    assert m["component"]["base_image"]["name"] == "llm-d-inference-scheduler"
    assert m["component"]["base_image"]["tag"] == "v0.7.1"


def test_component_build_optional(tmp_path):
    """MINIMAL_V3 without build is valid."""
    path = _write_manifest(tmp_path, MINIMAL_V3)
    m = load_manifest(path)
    assert "build" not in m["component"]


def test_component_build_defaults_commands(tmp_path):
    """component with build: {} gets commands=[]."""
    data = {**MINIMAL_V3, "component": {
        "repo": "github.com/llm-d/llm-d-inference-scheduler",
        "kind": "EndpointPickerConfig",
        "build": {},
    }}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["component"]["build"]["commands"] == []


def test_component_build_commands_loaded(tmp_path):
    """Explicit commands are preserved."""
    data = {**MINIMAL_V3, "component": {
        "repo": "github.com/llm-d/llm-d-inference-scheduler",
        "kind": "EndpointPickerConfig",
        "build": {"commands": [["go", "build", "./..."]]},
    }}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["component"]["build"]["commands"] == [["go", "build", "./..."]]


def test_component_build_commands_must_be_list(tmp_path):
    """build.commands as string raises."""
    data = {**MINIMAL_V3, "component": {
        "repo": "github.com/llm-d/llm-d-inference-scheduler",
        "kind": "EndpointPickerConfig",
        "build": {"commands": "go build"},
    }}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="component.build.commands.*list"):
        load_manifest(path)


def test_component_build_image_validates_hub(tmp_path):
    """build.image without hub raises."""
    data = {**MINIMAL_V3, "component": {
        "repo": "github.com/llm-d/llm-d-inference-scheduler",
        "kind": "EndpointPickerConfig",
        "build": {"image": {}},
    }}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="component.build.image.hub"):
        load_manifest(path)


def test_component_ref_optional(tmp_path):
    """component without ref is valid."""
    path = _write_manifest(tmp_path, MINIMAL_V3)
    m = load_manifest(path)
    assert "ref" not in m["component"]


def test_component_ref_loaded(tmp_path):
    """component.ref string is preserved."""
    data = {**MINIMAL_V3, "component": {
        **MINIMAL_V3["component"],
        "ref": "abc123def",
    }}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["component"]["ref"] == "abc123def"


def test_component_ref_must_be_string(tmp_path):
    """component.ref as non-string raises."""
    data = {**MINIMAL_V3, "component": {
        **MINIMAL_V3["component"],
        "ref": 123,
    }}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="component.ref.*string"):
        load_manifest(path)


def test_component_ref_must_be_nonempty(tmp_path):
    """component.ref as empty string raises."""
    data = {**MINIMAL_V3, "component": {
        **MINIMAL_V3["component"],
        "ref": "",
    }}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="component.ref.*non-empty"):
        load_manifest(path)


def test_build_image_defaults_hub_from_base_image(tmp_path):
    """build.image without hub inherits from base_image.hub."""
    data = {**MINIMAL_V3, "component": {
        "repo": "github.com/llm-d/llm-d-inference-scheduler",
        "kind": "EndpointPickerConfig",
        "base_image": {"hub": "ghcr.io/llm-d", "name": "llm-d-inference-scheduler"},
        "build": {"image": {"name": "custom-name"}},
    }}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["component"]["build"]["image"]["hub"] == "ghcr.io/llm-d"
    assert m["component"]["build"]["image"]["name"] == "custom-name"


def test_build_image_defaults_name_from_base_image(tmp_path):
    """build.image without name inherits from base_image.name."""
    data = {**MINIMAL_V3, "component": {
        "repo": "github.com/llm-d/llm-d-inference-scheduler",
        "kind": "EndpointPickerConfig",
        "base_image": {"hub": "ghcr.io/llm-d", "name": "llm-d-inference-scheduler"},
        "build": {"image": {"hub": "quay.io/me"}},
    }}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["component"]["build"]["image"]["hub"] == "quay.io/me"
    assert m["component"]["build"]["image"]["name"] == "llm-d-inference-scheduler"


def test_build_image_defaults_both_from_base_image(tmp_path):
    """build.image: {} inherits both hub and name from base_image."""
    data = {**MINIMAL_V3, "component": {
        "repo": "github.com/llm-d/llm-d-inference-scheduler",
        "kind": "EndpointPickerConfig",
        "base_image": {"hub": "ghcr.io/llm-d", "name": "llm-d-inference-scheduler"},
        "build": {"image": {}},
    }}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["component"]["build"]["image"]["hub"] == "ghcr.io/llm-d"
    assert m["component"]["build"]["image"]["name"] == "llm-d-inference-scheduler"


def test_build_image_no_base_image_requires_hub(tmp_path):
    """Without base_image, build.image still requires hub."""
    data = {**MINIMAL_V3, "component": {
        "repo": "github.com/llm-d/llm-d-inference-scheduler",
        "kind": "EndpointPickerConfig",
        "build": {"image": {"name": "foo"}},
    }}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="component.build.image.hub"):
        load_manifest(path)


def test_build_image_explicit_overrides_base_image(tmp_path):
    """Explicit build.image fields are not overwritten by base_image."""
    data = {**MINIMAL_V3, "component": {
        "repo": "github.com/llm-d/llm-d-inference-scheduler",
        "kind": "EndpointPickerConfig",
        "base_image": {"hub": "ghcr.io/llm-d", "name": "llm-d-inference-scheduler"},
        "build": {"image": {"hub": "quay.io/me", "name": "my-image"}},
    }}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["component"]["build"]["image"]["hub"] == "quay.io/me"
    assert m["component"]["build"]["image"]["name"] == "my-image"


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
    "component": {
        "repo": "github.com/llm-d/llm-d-inference-scheduler",
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


def test_algorithms_list_loaded(tmp_path):
    data = {**MULTI_BASELINE_V3, "algorithms": [
        {"name": "ac1", "source": "algo.go", "scenario": "treatment.yaml", "defaults": "b1"},
    ]}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert len(m["algorithms"]) == 1
    assert m["algorithms"][0]["name"] == "ac1"
    assert m["algorithms"][0]["defaults"] == "b1"


def test_no_algorithm_no_algorithms_is_baseline_only(tmp_path):
    data = {k: v for k, v in MULTI_BASELINE_V3.items() if k != "algorithms"}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m.get("algorithms", []) == []


# ── defaults block tests ───────────────────────────────────────────────────

def test_defaults_block_absent_normalizes_to_empty_disable(tmp_path):
    """No defaults: key in manifest → defaults.disable == []."""
    path = _write_manifest(tmp_path, MINIMAL_V3)
    m = load_manifest(path)
    assert m["defaults"] == {"disable": []}


def test_defaults_block_explicit_disable_loaded(tmp_path):
    data = {**MINIMAL_V3, "defaults": {"disable": ["llm-d-rbac", "vllm-logging"]}}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["defaults"]["disable"] == ["llm-d-rbac", "vllm-logging"]


def test_defaults_block_empty_disable_list(tmp_path):
    data = {**MINIMAL_V3, "defaults": {"disable": []}}
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["defaults"] == {"disable": []}


def test_defaults_block_must_be_mapping(tmp_path):
    data = {**MINIMAL_V3, "defaults": "not_a_mapping"}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="defaults must be a mapping"):
        load_manifest(path)


def test_defaults_disable_must_be_list(tmp_path):
    data = {**MINIMAL_V3, "defaults": {"disable": "rbac"}}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="defaults.disable must be a list"):
        load_manifest(path)


def test_defaults_disable_entries_must_be_strings(tmp_path):
    data = {**MINIMAL_V3, "defaults": {"disable": ["valid", 42]}}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match=r"defaults.disable\[1\]"):
        load_manifest(path)


def test_defaults_block_rejects_unknown_keys(tmp_path):
    data = {**MINIMAL_V3, "defaults": {"disable": [], "enable": ["foo"]}}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="defaults contains unknown keys.*enable"):
        load_manifest(path)


# ── byo: true per-algorithm marker (v3 schema addition, issue #497) ──────


def test_byo_true_algorithm_loads_without_source(tmp_path):
    """`algorithms[i].byo: true` makes `source:` optional for that entry.

    Non-BYO manifests still need `component:`, so keep the MINIMAL_V3
    component block; loosening `component:` is exercised separately.
    """
    data = {
        **MINIMAL_V3,
        "algorithms": [
            {"name": "byoalgo", "defaults": "baseline", "byo": True},
        ],
    }
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["algorithms"][0]["name"] == "byoalgo"
    assert m["algorithms"][0]["byo"] is True
    assert "source" not in m["algorithms"][0]


def test_byo_false_algorithm_still_requires_source(tmp_path):
    """`byo: false` leaves `source:` required — regression."""
    data = {
        **MINIMAL_V3,
        "algorithms": [
            {"name": "treatment", "defaults": "baseline", "byo": False},
        ],
    }
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="algorithms.*source"):
        load_manifest(path)


def test_byo_absent_algorithm_still_requires_source(tmp_path):
    """Absent `byo:` behaves identically to `byo: false` — regression."""
    data = {
        **MINIMAL_V3,
        "algorithms": [
            {"name": "treatment", "defaults": "baseline"},
        ],
    }
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="algorithms.*source"):
        load_manifest(path)


@pytest.mark.parametrize("bad_value", ["true", 1, 0, [True], {"v": True}])
def test_byo_must_be_boolean(tmp_path, bad_value):
    """Non-bool `byo:` is rejected with an error naming the field and type."""
    data = {
        **MINIMAL_V3,
        "algorithms": [
            {"name": "byoalgo", "defaults": "baseline", "byo": bad_value,
             "source": "sim2real_golden/routers/router_adaptive_v2.go"},
        ],
    }
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="byo.*bool"):
        load_manifest(path)


def test_component_optional_when_all_algorithms_byo(tmp_path):
    """`component:` may be absent when every algorithm carries `byo: true`."""
    data = {k: v for k, v in MINIMAL_V3.items() if k != "component"}
    data["algorithms"] = [
        {"name": "byoa", "defaults": "baseline", "byo": True},
        {"name": "byob", "defaults": "baseline", "byo": True},
    ]
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert "component" not in m
    assert len(m["algorithms"]) == 2


def test_component_required_when_mixed_byo_and_blis(tmp_path):
    """`component:` still required when any algorithm is non-BYO."""
    data = {k: v for k, v in MINIMAL_V3.items() if k != "component"}
    data["algorithms"] = [
        {"name": "byoalgo", "defaults": "baseline", "byo": True},
        {"name": "blisalgo", "defaults": "baseline",
         "source": "sim2real_golden/routers/router_adaptive_v2.go"},
    ]
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="component.*required"):
        load_manifest(path)


def test_byo_manifest_loads_cleanly_for_downstream(tmp_path):
    """A BYO manifest through `load_manifest` produces a dict whose
    downstream consumers (`translation register`, `assemble`) can access
    without dereferencing a field the BYO entry omits.

    Issue #497 acceptance: "load a BYO manifest through
    `manifest.load_manifest` and verify no field a BYO entry omits is
    dereferenced." Asserts the fields register/assemble actually read
    (algorithm `name`, `defaults`, `byo`) are present, and `source:` /
    `component:` are absent.
    """
    data = {k: v for k, v in MINIMAL_V3.items() if k != "component"}
    data["algorithms"] = [
        {"name": "byoa", "defaults": "baseline", "byo": True},
    ]
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["algorithms"][0]["name"] == "byoa"
    assert m["algorithms"][0]["defaults"] == "baseline"
    assert m["algorithms"][0]["byo"] is True
    assert "source" not in m["algorithms"][0]
    assert "component" not in m
    assert m["algorithms"][0]["defaults"] in {b["name"] for b in m["baselines"]}


# ── Additional validation coverage (quality agent) ────────────────────


def test_yaml_parse_error_raises(tmp_path):
    """Malformed YAML (parse error) raises ManifestError."""
    p = tmp_path / "transfer.yaml"
    p.write_text("---\nkind: sim2real-transfer\nversion: 3\n  bad indent: [")
    with pytest.raises(ManifestError, match="YAML parse error"):
        load_manifest(p)


def test_algorithms_not_a_list_raises(tmp_path):
    """When algorithms is present but not a list, raise ManifestError."""
    data = {**MINIMAL_V3, "algorithms": "not_a_list"}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="algorithms must be a list"):
        load_manifest(path)


def test_algorithms_entry_not_mapping_raises(tmp_path):
    """When an algorithm entry is not a dict, raise ManifestError."""
    data = {**MINIMAL_V3, "algorithms": ["just_a_string"]}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match=r"algorithms\[0\] must be a mapping"):
        load_manifest(path)


def test_algorithms_duplicate_name_raises(tmp_path):
    """Duplicate algorithm names raise ManifestError."""
    data = {**MINIMAL_V3, "algorithms": [
        {"name": "duped", "source": "src1.go", "defaults": "baseline"},
        {"name": "duped", "source": "src2.go", "defaults": "baseline"},
    ]}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="algorithms.*duplicate name"):
        load_manifest(path)


def test_algorithms_missing_required_fields_raises(tmp_path):
    """Algorithm entry missing 'name' raises ManifestError."""
    data = {**MINIMAL_V3, "algorithms": [
        {"source": "src.go", "defaults": "baseline"},  # no name
    ]}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match=r"algorithms\[0\].*missing required field.*name"):
        load_manifest(path)


def test_baselines_entry_not_mapping_raises(tmp_path):
    """When a baseline entry is not a dict, raise ManifestError."""
    data = {**MINIMAL_V3, "baselines": ["just_a_string"]}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match=r"baselines\[0\] must be a mapping"):
        load_manifest(path)


def test_pipeline_name_empty_raises(tmp_path):
    """Empty pipeline.name raises ManifestError."""
    data = {**MINIMAL_V3, "pipeline": {"name": "", "yaml": "p.yaml"}}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="pipeline.name must be a non-empty string"):
        load_manifest(path)


def test_pipeline_yaml_empty_raises(tmp_path):
    """Empty pipeline.yaml raises ManifestError."""
    data = {**MINIMAL_V3, "pipeline": {"name": "sim2real", "yaml": ""}}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="pipeline.yaml must be a non-empty string"):
        load_manifest(path)


def test_pipeline_yaml_absolute_raises(tmp_path):
    """Absolute pipeline.yaml path raises ManifestError."""
    data = {**MINIMAL_V3, "pipeline": {"name": "sim2real", "yaml": "/absolute/path.yaml"}}
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="pipeline.yaml must be a relative path"):
        load_manifest(path)

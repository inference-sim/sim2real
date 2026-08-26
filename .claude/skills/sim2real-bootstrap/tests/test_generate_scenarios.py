"""Tests for generate_scenarios.py — the JSON-input baseline path.

This path had the identical single-role defect as generate_from_config.py and no
test file at all (issue #824). Covers:
  - `vllm_args.prefill_instances` emits a `prefill:` block with `enabled: true`
  - absent prefill input -> no `prefill:` anywhere in the output
  - `workload.prefill_hardware` overrides, otherwise prefill inherits the shared
    hardware
  - a hardware value naming several GPU types -> one type emitted plus a warning
  - `labelValues` never emitted
  - the two new fields are declared in KNOWN_FIELDS, so they do not trip the
    unknown-field warning that exists to catch unmapped input
"""
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parents[1]))
import generate_scenarios as gs


def entry(workload_extra: dict | None = None, vllm_extra: dict | None = None) -> dict:
    return {
        "workload": {
            "model": "Qwen/Qwen3-14B",
            "hardware": "H100_SXM_80GB",
            **(workload_extra or {}),
        },
        "vllm_args": {
            "num_instances": 2,
            "max_num_seqs": 256,
            **(vllm_extra or {}),
        },
    }


def emit(scenario: dict, src: dict, tmp_path: Path) -> str:
    out = tmp_path / "cand.yaml"
    gs.write_commented_yaml(scenario, src, str(out))
    return out.read_text()


# ---------------------------------------------------------------------------
# Prefill emission
# ---------------------------------------------------------------------------

def test_no_prefill_input_emits_no_prefill_block(tmp_path):
    src = entry()
    scenario = gs.build_scenario(src, "cand")
    assert "prefill" not in scenario
    assert "prefill:" not in emit(scenario, src, tmp_path)


def test_prefill_instances_emits_prefill_block(tmp_path):
    src = entry(vllm_extra={"prefill_instances": 1})
    scenario = gs.build_scenario(src, "cand")
    assert scenario["prefill"]["replicas"] == 1
    assert scenario["decode"]["replicas"] == 2
    assert "prefill:" in emit(scenario, src, tmp_path)


def test_prefill_block_carries_enabled_true(tmp_path):
    src = entry(vllm_extra={"prefill_instances": 1})
    scenario = gs.build_scenario(src, "cand")
    assert scenario["prefill"]["enabled"] is True
    parsed = yaml.safe_load(emit(scenario, src, tmp_path))
    assert parsed["scenario"][0]["prefill"]["enabled"] is True


def test_zero_prefill_instances_emits_nothing():
    """0 means aggregated, not 'a prefill pool of size zero'."""
    scenario = gs.build_scenario(entry(vllm_extra={"prefill_instances": 0}), "cand")
    assert "prefill" not in scenario


# ---------------------------------------------------------------------------
# Per-role accelerator
# ---------------------------------------------------------------------------

def test_prefill_hardware_overrides():
    src = entry(
        workload_extra={"prefill_hardware": "A100_SXM_80GB"},
        vllm_extra={"prefill_instances": 1},
    )
    scenario = gs.build_scenario(src, "cand")
    assert scenario["prefill"]["acceleratorType"]["labelValue"] == "NVIDIA-A100-SXM4-80GB"
    assert scenario["decode"]["acceleratorType"]["labelValue"] == "NVIDIA-H100-80GB-HBM3"


def test_prefill_inherits_shared_hardware():
    scenario = gs.build_scenario(entry(vllm_extra={"prefill_instances": 1}), "cand")
    assert scenario["prefill"]["acceleratorType"]["labelValue"] == "NVIDIA-H100-80GB-HBM3"


def test_prefill_inherits_parallelism_and_flags():
    src = entry(vllm_extra={"prefill_instances": 1, "tensor_parallel_size": 4})
    scenario = gs.build_scenario(src, "cand")
    assert scenario["prefill"]["parallelism"] == scenario["decode"]["parallelism"]
    assert scenario["prefill"]["vllm"] == scenario["decode"]["vllm"]


# ---------------------------------------------------------------------------
# Multi-GPU within one role
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", [
    "H100_SXM_80GB, A100_SXM_80GB",
    "H100_SXM_80GB / A100_SXM_80GB",
    "H100_SXM_80GB + A100_SXM_80GB",
    "H100_SXM_80GB and A100_SXM_80GB",
])
def test_multi_gpu_value_warns_and_uses_first(value, capsys):
    scenario = gs.build_scenario(entry(workload_extra={"hardware": value}), "cand")
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "H100_SXM_80GB" in err and "A100_SXM_80GB" in err
    assert "HOMOGENEOUS" in err
    assert scenario["decode"]["acceleratorType"]["labelValue"] == "NVIDIA-H100-80GB-HBM3"


def test_single_gpu_value_does_not_warn(capsys):
    """Asserts on the multi-type GPU warning specifically, not on any warning at
    all: generation legitimately emits unrelated warnings (e.g. #850's unmeasured
    pod-resources default), and a blanket check makes each new one a spurious
    failure here. Mirrors the config.md path's twin test."""
    gs.build_scenario(entry(), "cand")
    assert "GPU types" not in capsys.readouterr().err


def test_labelvalues_never_emitted(tmp_path):
    src = entry(
        workload_extra={"hardware": "H100_SXM_80GB, A100_SXM_80GB"},
        vllm_extra={"prefill_instances": 1},
    )
    scenario = gs.build_scenario(src, "cand")
    assert "labelValues" not in emit(scenario, src, tmp_path)


# ---------------------------------------------------------------------------
# Field registry
# ---------------------------------------------------------------------------

def test_new_fields_do_not_trip_unknown_field_warning():
    src = entry(
        workload_extra={"prefill_hardware": "A100_SXM_80GB"},
        vllm_extra={"prefill_instances": 1},
    )
    assert gs.check_unknown_fields(src, "cand") == []


def test_prefill_hardware_without_instances_warns(capsys):
    """Declared in KNOWN_FIELDS so check_unknown_fields stays quiet, but unused
    without a count — a recognized input silently discarded."""
    src = entry(workload_extra={"prefill_hardware": "A100_SXM_80GB"})
    scenario = gs.build_scenario(src, "cand")
    err = capsys.readouterr().err
    assert "prefill" not in scenario
    assert "prefill_hardware" in err
    assert "NO effect" in err
    assert gs.check_unknown_fields(src, "cand") == []  # not an unknown-field case


def test_prefill_hardware_with_instances_does_not_warn(capsys):
    src = entry(
        workload_extra={"prefill_hardware": "A100_SXM_80GB"},
        vllm_extra={"prefill_instances": 1},
    )
    gs.build_scenario(src, "cand")
    assert "NO effect" not in capsys.readouterr().err


def test_genuinely_unknown_field_still_warns():
    src = entry(vllm_extra={"not_a_real_knob": 1})
    warnings = gs.check_unknown_fields(src, "cand")
    assert any("not_a_real_knob" in w for w in warnings)


# ---------------------------------------------------------------------------
# Provenance comments must name the key the value actually came from
# ---------------------------------------------------------------------------

def test_inherited_prefill_hardware_is_not_attributed_to_prefill_hardware(tmp_path):
    """`prefill_hardware` is optional; citing it when absent misattributes."""
    src = entry(vllm_extra={"prefill_instances": 1})
    assert "prefill_hardware" not in src["workload"]
    text = emit(gs.build_scenario(src, "cand"), src, tmp_path)
    prefill_block = text[text.index("prefill:"):]
    assert "from workload.hardware (lookup table; no prefill_hardware given)" in prefill_block
    assert "from workload.prefill_hardware" not in prefill_block


def test_explicit_prefill_hardware_is_attributed_to_it(tmp_path):
    src = entry(
        workload_extra={"prefill_hardware": "A100_SXM_80GB"},
        vllm_extra={"prefill_instances": 1},
    )
    text = emit(gs.build_scenario(src, "cand"), src, tmp_path)
    prefill_block = text[text.index("prefill:"):]
    assert "from workload.prefill_hardware (lookup table)" in prefill_block


# ---------------------------------------------------------------------------
# Unmapped GPU values are surfaced, not passed through in silence
# ---------------------------------------------------------------------------

def test_unmapped_hardware_warns(capsys):
    """An unmapped value becomes the node-selector verbatim; a selector that
    matches no node leaves pods Pending, so it must not be silent."""
    result = gs.resolve_role_hardware("H200_SXM_141GB", "decode")
    err = capsys.readouterr().err
    assert result == "H200_SXM_141GB"
    assert "not in HARDWARE_LABELS" in err
    assert "decode" in err


def test_mapped_hardware_does_not_warn(capsys):
    assert gs.resolve_role_hardware("H100_SXM_80GB", "decode") == "NVIDIA-H100-80GB-HBM3"
    assert "HARDWARE_LABELS" not in capsys.readouterr().err


def test_emitted_yaml_round_trips_both_roles(tmp_path):
    src = entry(
        workload_extra={"prefill_hardware": "A100_SXM_80GB"},
        vllm_extra={"prefill_instances": 1, "tensor_parallel_size": 4},
    )
    scenario = gs.build_scenario(src, "cand")
    parsed = yaml.safe_load(emit(scenario, src, tmp_path))["scenario"][0]
    assert parsed["prefill"]["replicas"] == 1
    assert parsed["decode"]["replicas"] == 2
    assert parsed["prefill"]["parallelism"]["tensor"] == 4


# ---------------------------------------------------------------------------
# parallelism.workers (issue #831)
# ---------------------------------------------------------------------------

def test_workers_is_one_not_tensor_parallel_size():
    """`workers` is the LWS pods-per-replica count, not a parallelism degree."""
    scenario = gs.build_scenario(entry(vllm_extra={"tensor_parallel_size": 4}), "cand")
    p = scenario["decode"]["parallelism"]
    assert p["tensor"] == 4
    assert p["workers"] == 1


def test_prefill_workers_is_one():
    src = entry(vllm_extra={"prefill_instances": 1, "tensor_parallel_size": 4})
    scenario = gs.build_scenario(src, "cand")
    assert scenario["prefill"]["parallelism"]["tensor"] == 4
    assert scenario["prefill"]["parallelism"]["workers"] == 1


def test_workers_comment_does_not_cite_tensor_parallel_size(tmp_path):
    src = entry(vllm_extra={"tensor_parallel_size": 4})
    scenario = gs.build_scenario(src, "cand")
    text = emit(scenario, src, tmp_path)
    workers_lines = [ln for ln in text.splitlines() if ln.strip().startswith("workers:")]
    assert len(workers_lines) == 1
    assert "tensor_parallel_size" not in workers_lines[0]
    assert "pods per replica" in workers_lines[0]
    assert yaml.safe_load(text)["scenario"][0]["decode"]["parallelism"]["workers"] == 1


def test_dp_only_still_emits_workers_one():
    scenario = gs.build_scenario(entry(vllm_extra={"data_parallel_size": 2}), "cand")
    p = scenario["decode"]["parallelism"]
    assert p["tensor"] == 1
    assert p["workers"] == 1


def test_prefill_workers_comment_also_avoids_tensor_parallel_size(tmp_path):
    """The prefill emit line is a separate code path from decode's."""
    src = entry(vllm_extra={"prefill_instances": 1, "tensor_parallel_size": 4})
    scenario = gs.build_scenario(src, "cand")
    text = emit(scenario, src, tmp_path)
    workers_lines = [ln for ln in text.splitlines() if ln.strip().startswith("workers:")]
    assert len(workers_lines) == 2, "expected one workers line per role"
    for line in workers_lines:
        assert "tensor_parallel_size" not in line
        assert "pods per replica" in line
    parsed = yaml.safe_load(text)["scenario"][0]
    assert parsed["prefill"]["parallelism"]["workers"] == 1
    assert parsed["decode"]["parallelism"]["workers"] == 1


def test_workers_is_one_when_both_tp_and_dp_exceed_one():
    """The gate is `tp > 1 or dp > 1`; cover the AND case too."""
    scenario = gs.build_scenario(
        entry(vllm_extra={"tensor_parallel_size": 4, "data_parallel_size": 2}), "cand"
    )
    p = scenario["decode"]["parallelism"]
    assert (p["tensor"], p["data"], p["dataLocal"]) == (4, 2, 2)
    assert p["workers"] == 1


# ---------------------------------------------------------------------------
# KV transfer (issue #830)
#
# A prefill pool with no KV transport reads as disaggregated and is not: vLLM's
# --kv-transfer-config is gated on vllmCommon.kvTransfer.enabled, which defaults
# to false upstream, so the prefill pod is never routed to and decode prefills
# its own requests -- silently.
#
# `vllmCommon` has TWO independent populators in build_scenario: the enforce_eager
# override, and the prefill block's kvTransfer. Each of the tests below fails on a
# specific regression that the code comments warn about but nothing previously
# caught (both were confirmed to leave the whole suite green when injected):
#   - plain assignment instead of setdefault in either populator, which drops the
#     other one
#   - a hand-rolled emitter branch that renders only one of the two subtrees
# Assertions are on the EMITTED YAML, never the intermediate dict: the emitter
# hardcodes which keys it renders, so a dict-only assertion passes while the
# output silently lacks the key.
# ---------------------------------------------------------------------------


def test_prefill_input_emits_kv_transfer(tmp_path):
    src = entry(vllm_extra={"prefill_instances": 1})
    parsed = yaml.safe_load(emit(gs.build_scenario(src, "cand"), src, tmp_path))
    kv = parsed["scenario"][0]["vllmCommon"]["kvTransfer"]
    assert kv["enabled"] is True
    assert kv["connector"] == "NixlConnector"
    assert kv["role"] == "kv_both"


def test_no_prefill_input_emits_no_kv_transfer(tmp_path):
    src = entry()
    assert "kvTransfer" not in emit(gs.build_scenario(src, "cand"), src, tmp_path)


def test_kv_transfer_and_enforce_eager_coexist(tmp_path):
    """Both vllmCommon populators must survive together, in the emitted YAML.

    `enforce_eager: false` assigns scenario["vllmCommon"] = {"flags": ...} before
    the prefill block runs. A plain assignment in the prefill block drops those
    flags; an emitter that renders only one subtree hides the other. Either
    regression leaves every other test in this suite passing.
    """
    src = entry(vllm_extra={"prefill_instances": 1, "enforce_eager": False})
    parsed = yaml.safe_load(emit(gs.build_scenario(src, "cand"), src, tmp_path))
    vc = parsed["scenario"][0]["vllmCommon"]
    assert vc["flags"]["enforceEager"] is False
    assert vc["kvTransfer"]["enabled"] is True


def test_enforce_eager_alone_still_emits_flags(tmp_path):
    """The flags-only path must survive the emitter branch becoming conditional.

    Narrowing the `if "flags" in ...` guard to also require kvTransfer would drop
    enforceEager from every aggregated bundle that sets it.
    """
    src = entry(vllm_extra={"enforce_eager": False})
    parsed = yaml.safe_load(emit(gs.build_scenario(src, "cand"), src, tmp_path))
    vc = parsed["scenario"][0]["vllmCommon"]
    assert vc["flags"]["enforceEager"] is False
    assert "kvTransfer" not in vc


# ---------------------------------------------------------------------------
# Pod plumbing gates (issue #848) -- parity with generate_from_config.py
# ---------------------------------------------------------------------------
# This generator has its own prefill block and its own hand-rolled emitter. #846
# had to fix both; so does #848. Any drift between the two paths means half of
# bootstrap emits an inert or crashlooping bundle.


def build_and_emit(tmp_path, **vllm_extra):
    """entry -> build_scenario -> write_commented_yaml -> text."""
    e = entry(vllm_extra=vllm_extra)
    return emit(gs.build_scenario(e, "test"), e, tmp_path)


def test_no_gates_emits_no_plumbing_json_path(tmp_path):
    text = build_and_emit(tmp_path)
    for key in ("initContainers", "preprocessScript", "volumes:", "volumeMounts:",
                "extraEnvVars", "routing:", "shared-config", "dshm",
                "NIXL_LOG_LEVEL", "NCCL_DEBUG", "NVSHMEM_DEBUG"):
        assert key not in text, f"{key} leaked into an ungated scenario"


def test_internal_gate_marker_never_reaches_output_json_path(tmp_path):
    assert "_gates" not in build_and_emit(tmp_path, prefill_instances=1)


def test_gate1_emits_the_full_kv_unit_json_path(tmp_path):
    text = build_and_emit(tmp_path, prefill_instances=1)
    parsed = yaml.safe_load(text)["scenario"][0]
    for role in ("decode", "prefill"):
        assert parsed[role]["initContainers"][0]["name"] == "preprocess"
        assert {e["name"] for e in parsed[role]["extraEnvVars"]} == {"NIXL_LOG_LEVEL"}
    assert ". /shared-config/llmdbench_env.sh" in parsed["vllmCommon"]["preprocessScript"]
    assert [v["name"] for v in parsed["vllmCommon"]["volumes"]] == ["shared-config"]
    assert parsed["routing"]["connector"] == "nixlv2"


def test_gate2_emits_dshm_without_prefill_json_path(tmp_path):
    text = build_and_emit(tmp_path, tensor_parallel_size=4)
    parsed = yaml.safe_load(text)["scenario"][0]
    assert [v["name"] for v in parsed["vllmCommon"]["volumes"]] == ["dshm"]
    assert {e["name"] for e in parsed["decode"]["extraEnvVars"]} == {
        "NCCL_DEBUG", "NVSHMEM_DEBUG"}
    assert "routing" not in parsed
    assert "prefill" not in parsed


def test_gate2_fires_on_data_parallel_alone_json_path(tmp_path):
    text = build_and_emit(tmp_path, data_parallel_size=2)
    parsed = yaml.safe_load(text)["scenario"][0]
    assert [v["name"] for v in parsed["vllmCommon"]["volumes"]] == ["dshm"]


def test_both_gates_accumulate_json_path(tmp_path):
    text = build_and_emit(tmp_path, prefill_instances=2, tensor_parallel_size=4)
    parsed = yaml.safe_load(text)["scenario"][0]
    assert [v["name"] for v in parsed["vllmCommon"]["volumes"]] == [
        "shared-config", "dshm"]
    assert [m["mountPath"] for m in parsed["vllmCommon"]["volumeMounts"]] == [
        "/shared-config", "/dev/shm"]
    for role in ("decode", "prefill"):
        assert {e["name"] for e in parsed[role]["extraEnvVars"]} == {
            "NIXL_LOG_LEVEL", "NCCL_DEBUG", "NVSHMEM_DEBUG"}


def test_plumbing_coexists_with_enforce_eager_json_path(tmp_path):
    text = build_and_emit(tmp_path, prefill_instances=1, enforce_eager=False)
    vc = yaml.safe_load(text)["scenario"][0]["vllmCommon"]
    assert vc["flags"]["enforceEager"] is False
    assert vc["kvTransfer"]["enabled"] is True
    assert "preprocessScript" in vc
    assert [v["name"] for v in vc["volumes"]] == ["shared-config"]


def test_both_generators_emit_identical_plumbing_text(tmp_path):
    """The anti-drift assertion. Two hand-rolled emitters, one set of fragments:
    the plumbing lines must match character-for-character, or one path is wrong."""
    import generate_from_config as gfc
    import pd_plumbing as pdp

    rows = [
        {"Parameter": "Model", "Value": "Qwen/Qwen3-14B", "Notes": ""},
        {"Parameter": "GPU", "Value": "H100_SXM_80GB", "Notes": ""},
        {"Parameter": "Number of prefill pods", "Value": "2", "Notes": ""},
        {"Parameter": "tensor_parallel_size", "Value": "4", "Notes": ""},
    ]
    table = gfc.TableSection(
        heading="vLLM Pod Configuration", rows=rows, line_number=0)
    s, p = gfc.build_scenario(gfc.extract_fields(table), "test")
    out = tmp_path / "baseline.yaml"
    gfc.write_provenance_yaml(s, p, str(out))
    from_config_text = out.read_text()

    # Same directory is safe: gfc writes baseline.yaml, gs writes cand.yaml.
    # Do NOT pass a subdirectory here -- write_commented_yaml does not makedirs
    # (unlike write_provenance_yaml, which does).
    json_text = build_and_emit(tmp_path, prefill_instances=2, tensor_parallel_size=4)

    for fragment_lines in (
        pdp.routing_lines(),
        pdp.preprocess_script_lines(),
        pdp.volume_lines(shared_config=True, dshm=True),
        pdp.init_container_lines(),
        pdp.extra_env_var_lines(nixl=True, multigpu=True),
    ):
        block = "\n".join(fragment_lines)
        assert block in from_config_text, "config.md path drifted from pd_plumbing"
        assert block in json_text, "json path drifted from pd_plumbing"



# ---------------------------------------------------------------------------
# Pod CPU/memory resources (issue #850) — parity with generate_from_config.py
# ---------------------------------------------------------------------------
# This generator has its own emitter, so it needs its own coverage. #846 and #848
# both had to fix both paths.


def res_emit(tmp_path, **vllm_extra):
    e = entry(vllm_extra=vllm_extra)
    return emit(gs.build_scenario(e, "test"), e, tmp_path)


def test_resources_emitted_with_role_defaults_json_path(tmp_path):
    parsed = yaml.safe_load(res_emit(tmp_path, prefill_instances=1))["scenario"][0]
    assert parsed["decode"]["resources"]["limits"] == {
        "memory": "128Gi", "cpu": "32"}
    assert parsed["decode"]["resources"]["requests"] == {
        "memory": "64Gi", "cpu": "16"}
    assert parsed["prefill"]["resources"]["limits"] == {
        "memory": "16Gi", "cpu": "8"}


def test_decode_sized_above_prefill_json_path(tmp_path):
    parsed = yaml.safe_load(res_emit(tmp_path, prefill_instances=1))["scenario"][0]
    assert int(parsed["decode"]["resources"]["limits"]["cpu"]) > int(
        parsed["prefill"]["resources"]["limits"]["cpu"])


def test_stated_key_applies_to_both_roles_json_path(tmp_path):
    parsed = yaml.safe_load(
        res_emit(tmp_path, prefill_instances=1, cpu_limit="64"))["scenario"][0]
    assert parsed["decode"]["resources"]["limits"]["cpu"] == "64"
    assert parsed["prefill"]["resources"]["limits"]["cpu"] == "64"


@pytest.mark.parametrize("hostile", ["128", "0.5"])
def test_yaml_hostile_values_stay_strings_json_path(hostile, tmp_path):
    parsed = yaml.safe_load(
        res_emit(tmp_path, memory_limit=hostile))["scenario"][0]
    assert parsed["decode"]["resources"]["limits"]["memory"] == hostile


@pytest.mark.parametrize("bad", ["-", "TBD", "16 Gi", "128GB"])
def test_invalid_quantity_is_a_hard_error_json_path(bad, capsys):
    with pytest.raises(SystemExit) as exc:
        gs.build_scenario(entry(vllm_extra={"memory_limit": bad}), "cand")
    assert exc.value.code == 1
    assert "invalid Kubernetes quantities" in capsys.readouterr().err


def test_all_four_stated_suppresses_warning_json_path(tmp_path, capsys):
    text = res_emit(tmp_path, cpu_limit="64", memory_limit="200Gi",
                    cpu_request="8", memory_request="32Gi")
    assert "Reducing Torch parallelism" not in text
    assert "Reducing Torch parallelism" not in capsys.readouterr().err


def test_any_default_warns_on_stderr_json_path(capsys):
    """The JSON path had no coverage of the RESOURCES stderr specifically (other
    warnings in this file were covered), which is how a config.md
    remediation message on a JSON input went unnoticed."""
    gs.build_scenario(entry(), "cand")
    err = capsys.readouterr().err
    assert "Reducing Torch parallelism" in err
    assert "decode" in err


def test_stderr_names_no_input_file_json_path(capsys):
    """pod_resources is shared by both generators, which read different inputs, so
    the message must name neither."""
    gs.build_scenario(entry(), "cand")
    err = capsys.readouterr().err
    resource_lines = [ln for ln in err.splitlines() if "Torch parallelism" in ln]
    assert resource_lines
    for ln in resource_lines:
        assert "config.md" not in ln


def test_prefill_warn_suppression_is_independent_json_path(tmp_path):
    text = res_emit(tmp_path, prefill_instances=1, cpu_limit="64",
                    memory_limit="200Gi", cpu_request="8", memory_request="32Gi")
    prefill_block = text[text.index("  prefill:"):]
    assert "GENEROUS DEFAULTS" not in prefill_block


def test_resource_keys_do_not_trip_unknown_field_warning(capsys):
    """The four keys must be declared in KNOWN_FIELDS, or the unknown-field check
    that exists to catch unmapped input warns on every bundle using them."""
    e = entry(vllm_extra={"cpu_limit": "64", "memory_limit": "200Gi",
                          "cpu_request": "8", "memory_request": "32Gi"})
    assert gs.check_unknown_fields(e, "test") == []


def test_both_generators_emit_identical_resources_text(tmp_path):
    """Anti-drift: two hand-rolled emitters, one shared module. The resources text
    must match character-for-character or one path is wrong."""
    import generate_from_config as gfc
    import pod_resources as pres

    rows = [
        {"Parameter": "Model", "Value": "Qwen/Qwen3-14B", "Notes": ""},
        {"Parameter": "GPU", "Value": "H100_SXM_80GB", "Notes": ""},
        {"Parameter": "Number of prefill pods", "Value": "1", "Notes": ""},
    ]
    table = gfc.TableSection(
        heading="vLLM Pod Configuration", rows=rows, line_number=0)
    s, p = gfc.build_scenario(gfc.extract_fields(table), "test")
    out = tmp_path / "baseline.yaml"
    gfc.write_provenance_yaml(s, p, str(out))
    from_config_text = out.read_text()

    # Same directory is safe: gfc writes baseline.yaml, gs writes cand.yaml.
    json_text = res_emit(tmp_path, prefill_instances=1)

    for role in ("decode", "prefill"):
        values, prov, _ = pres.resolve_resources(role, dict.fromkeys(pres.KEYS))
        block = "\n".join(pres.resource_lines(values, prov, warn=True))
        assert block in from_config_text, f"config.md path drifted for {role}"
        assert block in json_text, f"json path drifted for {role}"

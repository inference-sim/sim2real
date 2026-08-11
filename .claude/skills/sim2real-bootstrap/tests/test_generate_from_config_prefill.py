"""Tests for generate_from_config.py — per-role (prefill/decode) emission.

Covers the acceptance criteria from issue #824:
  - A config.md naming a prefill pod count emits a `prefill:` block carrying
    `enabled: true` (without it, capacity planning defaults prefill to disabled
    with 0 replicas and plans no prefill GPUs)
  - Per-role `acceleratorType`: prefill and decode may name different GPUs
  - No prefill rows -> output contains no `prefill:` at all, so existing bundles
    regenerate byte-identically
  - A role naming several GPU types -> one type emitted PLUS a warning naming
    every type found and the one used
  - `labelValues` is never emitted, in any configuration
  - An unrecognized replica-count row in the vLLM table is a hard error rather
    than a silent skip; flag-style labels and non-replica labels are unaffected
"""
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parents[1]))
import generate_from_config as gfc

ROWS_BASE = [
    {"Parameter": "Model", "Value": "Qwen/Qwen3-14B", "Notes": ""},
    {"Parameter": "GPU", "Value": "H100_SXM_80GB", "Notes": ""},
    {"Parameter": "max_num_seqs", "Value": "256", "Notes": ""},
]


def make_table(extra_rows: list[dict]) -> gfc.TableSection:
    return gfc.TableSection(
        heading="vLLM Pod Configuration", rows=ROWS_BASE + extra_rows, line_number=0
    )


def build(extra_rows: list[dict], name: str = "test"):
    """Extract + build, returning (scenario, provenance)."""
    fields = gfc.extract_fields(make_table(extra_rows))
    return gfc.build_scenario(fields, name)


def emit(scenario: dict, provenance: dict, tmp_path: Path) -> str:
    out = tmp_path / "baseline.yaml"
    gfc.write_provenance_yaml(scenario, provenance, str(out))
    return out.read_text()


def row(param: str, value: str) -> dict:
    return {"Parameter": param, "Value": value, "Notes": ""}


# ---------------------------------------------------------------------------
# Prefill emission
# ---------------------------------------------------------------------------

def test_no_prefill_rows_emits_no_prefill_block(tmp_path):
    """The regression guard: absent prefill input must leave output unchanged."""
    scenario, prov = build([row("Number of pods", "2")])
    assert "prefill" not in scenario
    assert "prefill:" not in emit(scenario, prov, tmp_path)


def test_prefill_row_emits_prefill_block(tmp_path):
    scenario, prov = build(
        [row("Number of decode pods", "2"), row("Number of prefill pods", "1")]
    )
    assert scenario["prefill"]["replicas"] == 1
    assert scenario["decode"]["replicas"] == 2
    assert "prefill:" in emit(scenario, prov, tmp_path)


def test_prefill_block_carries_enabled_true(tmp_path):
    """capacity.py defaults prefill to (disabled, 0 replicas) -- omitting
    `enabled: true` would plan zero prefill GPUs while reading as disaggregated."""
    scenario, prov = build([row("Number of prefill pods", "1")])
    assert scenario["prefill"]["enabled"] is True
    parsed = yaml.safe_load(emit(scenario, prov, tmp_path))
    assert parsed["scenario"][0]["prefill"]["enabled"] is True


@pytest.mark.parametrize(
    "label", ["Number of prefill pods", "Number of prefill instances", "prefill replicas"]
)
def test_prefill_replica_aliases(label):
    scenario, _ = build([row(label, "3")])
    assert scenario["prefill"]["replicas"] == 3


def test_stated_prefill_count_of_zero_emits_no_block(tmp_path):
    """0 means aggregated, the same as saying nothing. Emitting `enabled: true`
    with `replicas: 0` would be the reads-as-disaggregated-but-plans-nothing
    state this feature exists to avoid."""
    scenario, prov = build([row("Number of prefill pods", "0")])
    assert "prefill" not in scenario
    assert "prefill:" not in emit(scenario, prov, tmp_path)


def test_zero_prefill_agrees_across_both_generators():
    """The two generators must not disagree on what a stated 0 means."""
    sys.path.insert(0, str(Path(__file__).parents[1]))
    import generate_scenarios as gs

    from_config, _ = build([row("Number of prefill pods", "0")])
    from_json = gs.build_scenario(
        {
            "workload": {"model": "Qwen/Qwen3-14B", "hardware": "H100_SXM_80GB"},
            "vllm_args": {"num_instances": 2, "prefill_instances": 0},
        },
        "cand",
    )
    assert "prefill" not in from_config
    assert "prefill" not in from_json


# ---------------------------------------------------------------------------
# Per-role accelerator
# ---------------------------------------------------------------------------

def test_roles_may_name_different_gpus():
    scenario, _ = build(
        [
            row("Prefill GPU", "A100_SXM_80GB"),
            row("Number of prefill pods", "1"),
            row("Number of decode pods", "2"),
        ]
    )
    assert scenario["prefill"]["acceleratorType"]["labelValue"] == "NVIDIA-A100-SXM4-80GB"
    assert scenario["decode"]["acceleratorType"]["labelValue"] == "NVIDIA-H100-80GB-HBM3"


def test_prefill_inherits_shared_gpu_when_unspecified():
    scenario, _ = build([row("Number of prefill pods", "1")])
    assert scenario["prefill"]["acceleratorType"]["labelValue"] == "NVIDIA-H100-80GB-HBM3"


def test_decode_gpu_row_overrides_shared_gpu():
    scenario, _ = build([row("Decode GPU", "A100_PCIE_40GB")])
    assert scenario["decode"]["acceleratorType"]["labelValue"] == "NVIDIA-A100-PCIE-40GB"


def test_prefill_inherits_shared_parallelism_and_flags():
    scenario, _ = build(
        [row("tensor_parallel_size", "4"), row("Number of prefill pods", "1")]
    )
    assert scenario["prefill"]["parallelism"]["tensor"] == 4
    assert scenario["prefill"]["parallelism"] == scenario["decode"]["parallelism"]
    assert scenario["prefill"]["vllm"] == scenario["decode"]["vllm"]


# ---------------------------------------------------------------------------
# Multi-GPU within one role: warn, do not silently homogenize
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cell", [
    "H100_SXM_80GB, A100_SXM_80GB",
    "H100_SXM_80GB / A100_SXM_80GB",
    "H100_SXM_80GB + A100_SXM_80GB",
    "H100_SXM_80GB and A100_SXM_80GB",
])
def test_multi_gpu_cell_warns_and_uses_first(cell, capsys):
    fields = gfc.extract_fields(
        gfc.TableSection(
            heading="vLLM Pod Configuration",
            rows=[
                {"Parameter": "Model", "Value": "Qwen/Qwen3-14B", "Notes": ""},
                {"Parameter": "GPU", "Value": cell, "Notes": ""},
            ],
            line_number=0,
        )
    )
    scenario, _ = gfc.build_scenario(fields, "test")
    err = capsys.readouterr().err
    assert "WARNING" in err
    # names every type found, and which one was used
    assert "H100_SXM_80GB" in err and "A100_SXM_80GB" in err
    assert "HOMOGENEOUS" in err
    assert scenario["decode"]["acceleratorType"]["labelValue"] == "NVIDIA-H100-80GB-HBM3"


def test_single_gpu_cell_does_not_warn(capsys):
    build([row("Number of pods", "2")])
    assert "WARNING" not in capsys.readouterr().err


def test_multi_gpu_warning_is_per_role(capsys):
    """A multi-type prefill cell warns about prefill, not decode."""
    build([row("Prefill GPU", "H100_SXM_80GB, A100_SXM_80GB"), row("Number of prefill pods", "1")])
    err = capsys.readouterr().err
    assert "prefill names 2 GPU types" in err


def test_labelvalues_never_emitted(tmp_path):
    """A permissive allow-list would read as heterogeneity support while
    permitting a homogeneous placement (issue #824 decision)."""
    scenario, prov = build(
        [
            row("GPU", "H100_SXM_80GB, A100_SXM_80GB"),
            row("Prefill GPU", "A100_SXM_80GB"),
            row("Number of prefill pods", "1"),
        ]
    )
    assert "labelValues" not in emit(scenario, prov, tmp_path)


# ---------------------------------------------------------------------------
# Unrecognized replica rows are an error, not a silent skip
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label", [
    # Singular phrasings: an operator stating one pod writes "pod". These dropped
    # silently while the detector matched plurals only.
    "Number of prefill pod",
    "Prefill pod count",
    "Prefill replica count",
    "Decode replica count",
])
def test_singular_replica_labels_are_flagged(label):
    assert gfc.is_unrecognized_replica_label(label) is True


@pytest.mark.parametrize("label", [
    # Ratio labels mention a counted noun without naming a fleet size. Rejecting
    # them would break config.md files that work on main, with advice ("use
    # `number of pods`") that is wrong for the row.
    "Pods per node",
    "Pods per GPU",
    "GPUs per pod",
    "Instances per node",
])
def test_ratio_labels_are_not_replica_counts(label):
    assert gfc.is_unrecognized_replica_label(label) is False


def test_ratio_row_in_vllm_table_does_not_abort(tmp_path):
    """End-to-end: a descriptive row must not turn a working config.md into exit 1."""
    fields = gfc.extract_fields(make_table([row("Pods per node", "1"), row("Number of pods", "2")]))
    scenario, prov = gfc.build_scenario(fields, "test")
    assert scenario["decode"]["replicas"] == 2
    assert "prefill" not in scenario


def test_workers_label_is_left_alone():
    """`workers` collides with parallelism.workers, which derives from
    tensor_parallel_size rather than a replica count, so flagging it would emit
    the same misleading guidance the ratio exclusion exists to prevent."""
    assert gfc.is_unrecognized_replica_label("Prefill workers") is False


@pytest.mark.parametrize("label", [
    "Number of sidecar pods",
    "Number of router instances",
    "worker replicas",
])
def test_unrecognized_replica_label_exits(label, capsys):
    with pytest.raises(SystemExit) as exc:
        gfc.extract_fields(make_table([row(label, "3")]))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert label in err          # names the offending row
    assert "Use one of" in err   # and the recognized vocabulary


@pytest.mark.parametrize("label", [
    "--num-instances",           # a documented CLI flag, not a parameter
    "-n",
])
def test_flag_style_labels_are_not_replica_rows(label):
    """Flags get documented in these tables; they are not ours to resolve."""
    assert gfc.is_unrecognized_replica_label(label) is False
    gfc.extract_fields(make_table([row(label, "4")]))  # must not raise


@pytest.mark.parametrize("label", ["Notes", "dtype_unknown", "Comment"])
def test_non_replica_unknown_labels_still_skipped_silently(label):
    """Only replica-shaped labels became fatal; everything else is unchanged."""
    assert gfc.is_unrecognized_replica_label(label) is False
    fields = gfc.extract_fields(make_table([row(label, "x")]))
    assert "model" in fields  # extraction continued past the unknown row


def test_recognized_replica_labels_are_not_flagged():
    for label in ("Number of pods", "Number of vLLM pods", "Number of decode pods",
                  "Instances", "replicas", "Number of prefill pods"):
        assert gfc.is_unrecognized_replica_label(label) is False, label


# ---------------------------------------------------------------------------
# Emitted YAML is well-formed
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# End-to-end golden: the single-pool path must not move
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures"


def test_single_pool_config_regenerates_byte_identically(tmp_path):
    """The issue's hard acceptance criterion, as an end-to-end guard.

    The synthetic-table tests above prove "no prefill rows -> no prefill block",
    which is a proxy. This drives a realistic config.md through parse -> extract ->
    build -> emit and compares bytes against a golden captured from the generator
    BEFORE this feature existed, so a change to hardware-source formatting, key
    ordering, or comment text in the resolve_role_hardware refactor cannot slip
    through while the unit tests stay green.

    The fixture deliberately includes a `Pods per node` ratio row, which an
    over-broad replica-count check would reject.
    """
    out = tmp_path / "baseline.yaml"
    fields = gfc.extract_fields(
        gfc.find_vllm_table(
            gfc.parse_md_tables((FIXTURES / "single_pool_config.md").read_text().split("\n"))
        )
    )
    scenario, prov = gfc.build_scenario(fields, "golden")
    gfc.write_provenance_yaml(scenario, prov, str(out))
    assert out.read_text() == (FIXTURES / "single_pool_baseline.golden.yaml").read_text()


def test_single_pool_golden_has_no_prefill_artifacts():
    """Guards the golden itself: if someone regenerates it from a future version
    that emits prefill unconditionally, this fails rather than blessing it."""
    golden = (FIXTURES / "single_pool_baseline.golden.yaml").read_text()
    assert "prefill:" not in golden
    assert "labelValues" not in golden


def test_emitted_yaml_round_trips_both_roles(tmp_path):
    scenario, prov = build(
        [
            row("tensor_parallel_size", "4"),
            row("Prefill GPU", "A100_SXM_80GB"),
            row("Number of prefill pods", "1"),
            row("Number of decode pods", "2"),
        ]
    )
    parsed = yaml.safe_load(emit(scenario, prov, tmp_path))["scenario"][0]
    assert parsed["prefill"]["replicas"] == 1
    assert parsed["decode"]["replicas"] == 2
    assert parsed["prefill"]["acceleratorType"]["labelValue"] == "NVIDIA-A100-SXM4-80GB"
    assert parsed["decode"]["acceleratorType"]["labelValue"] == "NVIDIA-H100-80GB-HBM3"

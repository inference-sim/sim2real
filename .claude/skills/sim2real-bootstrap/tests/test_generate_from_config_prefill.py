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
    """`workers` is a pod count -- parallelism.workers is the LWS group size --
    but no input field resolves it yet (#843), so the "use `number of pods`"
    advice this check offers would be wrong for it; left unflagged (#831)."""
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
# Per-role rows in a table the parser does not read
# ---------------------------------------------------------------------------

def _two_table_doc(other_heading: str, other_rows: str) -> list[str]:
    """A config.md with the vLLM table plus a second table, as real bundles have."""
    return (
        "# Configuration\n\n"
        f"## {other_heading}\n\n"
        "| Deployment parameter | Simulator flag | Value | Passed? |\n"
        "|---|---|---|---|\n"
        f"{other_rows}\n"
        "\n## vLLM Pod Configuration\n\n"
        "| Parameter | Value | Notes |\n"
        "|---|---|---|\n"
        "| Model | `Qwen/Qwen3-14B` | |\n"
        "| GPU | H100_SXM_80GB | |\n"
        "| Number of decode pods | 2 | |\n"
    ).split("\n")


def test_prefill_row_in_another_table_warns_and_is_not_consumed(capsys):
    """The review's motivating case: pd-infocomm-2 states its prefill count in a
    simulation->deployment mapping table, so the parser never saw it and emitted a
    decode-only baseline with no diagnostic at all."""
    lines = _two_table_doc(
        "Simulation → deployment mapping",
        "| prefill replicas | --prefill-instances | 1 | yes |",
    )
    tables = gfc.parse_md_tables(lines)
    vllm = gfc.find_vllm_table(tables)
    emitted = gfc.warn_role_rows_outside_vllm_table(tables, vllm)
    err = capsys.readouterr().err

    assert len(emitted) == 1
    assert "prefill replicas" in err
    assert "Simulation → deployment mapping" in err   # names where it found it
    assert "NO effect" in err                          # says what that means
    assert "Move it into that table" in err            # and what to do

    # Crucially NOT consumed: column 1 of a mapping table is a flag name, not a
    # count, so reading it would swap a silent omission for silent garbage.
    scenario, _ = gfc.build_scenario(gfc.extract_fields(vllm), "test")
    assert "prefill" not in scenario


def test_no_warning_when_role_rows_live_in_the_vllm_table(capsys):
    lines = _two_table_doc("Notes", "| something | else | 1 | yes |")
    tables = gfc.parse_md_tables(lines)
    vllm = gfc.find_vllm_table(tables)
    assert gfc.warn_role_rows_outside_vllm_table(tables, vllm) == []
    assert "NO effect" not in capsys.readouterr().err


def test_documented_duplicate_in_a_mapping_table_does_not_warn(capsys):
    """A simulation->deployment mapping table is a required part of a well-formed
    config.md. A bundle that states its count in the vLLM table AND documents it
    there must not draw a warning on every run — only values stated ONLY where
    they cannot take effect are worth flagging."""
    lines = (
        "# Configuration\n\n"
        "## Simulation → deployment mapping\n\n"
        "| Deployment parameter | Simulator flag | Value | Passed? |\n"
        "|---|---|---|---|\n"
        "| prefill replicas | --prefill-instances | 1 | yes |\n"
        "| decode replicas | --decode-instances | 2 | yes |\n"
        "\n## vLLM Pod Configuration\n\n"
        "| Parameter | Value | Notes |\n"
        "|---|---|---|\n"
        "| Model | `Qwen/Qwen3-14B` | |\n"
        "| GPU | H100_SXM_80GB | |\n"
        "| Number of decode pods | 2 | |\n"
        "| Number of prefill pods | 1 | |\n"
    ).split("\n")
    tables = gfc.parse_md_tables(lines)
    vllm = gfc.find_vllm_table(tables)
    fields = gfc.extract_fields(vllm)
    emitted = gfc.warn_role_rows_outside_vllm_table(tables, vllm, set(fields))
    assert emitted == []
    assert "NO effect" not in capsys.readouterr().err
    # and the values that DO take effect are the vLLM table's
    scenario, _ = gfc.build_scenario(fields, "test")
    assert scenario["prefill"]["replicas"] == 1
    assert scenario["decode"]["replicas"] == 2


def test_decode_row_in_another_table_also_warns(capsys):
    """Not prefill-specific: any per-role row in an unread table is inert."""
    lines = _two_table_doc(
        "Simulation → deployment mapping",
        "| decode replicas | --decode-instances | 2 | yes |",
    )
    tables = gfc.parse_md_tables(lines)
    emitted = gfc.warn_role_rows_outside_vllm_table(tables, gfc.find_vllm_table(tables))
    assert len(emitted) == 1
    assert "decode replicas" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# A per-role accelerator with no pod count
# ---------------------------------------------------------------------------

def test_prefill_gpu_without_a_count_warns(capsys):
    """The row is recognized and stored, then never read. Recognizing an input and
    discarding it is the silent drop this feature exists to remove."""
    scenario, _ = build([row("Prefill GPU", "A100_SXM_80GB"), row("Number of pods", "2")])
    err = capsys.readouterr().err
    assert "prefill" not in scenario
    assert "Prefill GPU" in err
    assert "NO effect" in err
    assert "Number of prefill pods" in err  # names the fix


def test_prefill_gpu_with_a_count_does_not_warn(capsys):
    scenario, _ = build(
        [row("Prefill GPU", "A100_SXM_80GB"), row("Number of prefill pods", "1")]
    )
    assert scenario["prefill"]["acceleratorType"]["labelValue"] == "NVIDIA-A100-SXM4-80GB"
    assert "NO effect" not in capsys.readouterr().err


@pytest.mark.parametrize("label", ["Number of prefill nodes", "prefill node count"])
def test_node_counts_are_flagged_not_dropped(label):
    """A node count states fleet size in a unit this generator cannot convert to
    replicas, so it must fail loudly rather than vanish."""
    assert gfc.is_unrecognized_replica_label(label) is True


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


# ---------------------------------------------------------------------------
# parallelism.workers (issue #831)
# ---------------------------------------------------------------------------

def test_workers_is_one_not_tensor_parallel_size():
    """`workers` is the LWS pods-per-replica count, not a parallelism degree.

    A single pod holding 4 GPUs at TP=4 is `tensor: 4, workers: 1`. Emitting
    `workers: 4` claims four pods per replica.
    """
    scenario, _ = build([row("Number of pods", "2"), row("tensor_parallel_size", "4")])
    p = scenario["decode"]["parallelism"]
    assert p["tensor"] == 4
    assert p["workers"] == 1


def test_prefill_workers_is_one():
    scenario, _ = build([
        row("Number of pods", "2"),
        row("Number of prefill pods", "1"),
        row("tensor_parallel_size", "4"),
    ])
    assert scenario["prefill"]["parallelism"]["tensor"] == 4
    assert scenario["prefill"]["parallelism"]["workers"] == 1


def test_workers_provenance_does_not_cite_tensor_parallel_size(tmp_path):
    """The emitted comment must not claim `workers` came from TP."""
    scenario, prov = build([row("Number of pods", "2"), row("tensor_parallel_size", "4")])
    text = emit(scenario, prov, tmp_path)
    workers_lines = [ln for ln in text.splitlines() if ln.strip().startswith("workers:")]
    assert len(workers_lines) == 1
    assert "tensor_parallel_size" not in workers_lines[0]
    assert "pods per replica" in workers_lines[0]
    assert yaml.safe_load(text)["scenario"][0]["decode"]["parallelism"]["workers"] == 1


def test_dp_only_still_emits_workers_one():
    """dp>1 with tp==1 already produced workers: 1; guard against regression."""
    scenario, _ = build([row("Number of pods", "2"), row("data_parallel_size", "2")])
    p = scenario["decode"]["parallelism"]
    assert p["tensor"] == 1
    assert p["workers"] == 1

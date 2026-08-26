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
    """Asserts on the multi-type GPU warning specifically, not on the presence of
    any warning at all: generation legitimately emits unrelated warnings (e.g. the
    unmeasured pod-resources default of #850), and a blanket `"WARNING" not in`
    check turns every new one into a spurious failure here."""
    build([row("Number of pods", "2")])
    assert "GPU types" not in capsys.readouterr().err


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
    that emits prefill unconditionally, this fails rather than blessing it.

    The `labelValues` assertion belongs to this test, not to the pod-resources one
    below: #850's test was originally spliced between the two, which left this
    guard trailing a docstring about CPU and memory.
    """
    golden = (FIXTURES / "single_pool_baseline.golden.yaml").read_text()
    assert "prefill:" not in golden
    assert "labelValues" not in golden


def test_single_pool_golden_carries_pod_resources():
    """The other direction of the same guard (issue #850).

    The golden was regenerated when resources emission landed — verified as a pure
    addition, then again after the review fixes, where the diff was confined to the
    resources block and no emitted value changed. If a future version stops
    emitting `resources` and someone regenerates the golden to match, the
    byte-identity test above would happily bless the regression. This makes that
    fail instead.
    """
    golden = (FIXTURES / "single_pool_baseline.golden.yaml").read_text()
    parsed = yaml.safe_load(golden)["scenario"][0]
    resources = parsed["decode"]["resources"]
    assert set(resources) == {"limits", "requests"}, (
        "the golden lost its requests block — limits-only means Kubernetes "
        "reserves the whole generous limit (#850)"
    )
    assert resources["limits"]["cpu"] == "32"


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


def test_prefill_workers_comment_also_avoids_tensor_parallel_size(tmp_path):
    """The prefill emit line is a separate code path from decode's.

    A transposed provenance key would raise KeyError and be caught by the
    existing round-trip test, but citing `tp_source` instead of the workers
    string would emit silently-wrong text that nothing else asserts.
    """
    scenario, prov = build([
        row("Number of decode pods", "2"),
        row("Number of prefill pods", "1"),
        row("tensor_parallel_size", "4"),
    ])
    text = emit(scenario, prov, tmp_path)
    workers_lines = [ln for ln in text.splitlines() if ln.strip().startswith("workers:")]
    assert len(workers_lines) == 2, "expected one workers line per role"
    for line in workers_lines:
        assert "tensor_parallel_size" not in line
        assert "pods per replica" in line
    parsed = yaml.safe_load(text)["scenario"][0]
    assert parsed["prefill"]["parallelism"]["workers"] == 1
    assert parsed["decode"]["parallelism"]["workers"] == 1


def test_workers_is_one_when_both_tp_and_dp_exceed_one():
    """The gate is `tp > 1 or dp > 1`; the AND case is the one a future
    formula-based derivation would break first."""
    scenario, _ = build([
        row("Number of pods", "2"),
        row("tensor_parallel_size", "4"),
        row("data_parallel_size", "2"),
    ])
    p = scenario["decode"]["parallelism"]
    assert (p["tensor"], p["data"], p["dataLocal"]) == (4, 2, 2)
    assert p["workers"] == 1


# ---------------------------------------------------------------------------
# KV transfer (issue #830)
#
# A prefill pool with no KV transport reads as disaggregated and is not: vLLM's
# --kv-transfer-config is gated on vllmCommon.kvTransfer.enabled, which defaults
# to false upstream, so the prefill pod is never routed to and decode prefills
# its own requests -- silently, with no error.
#
# These assert on the EMITTED TEXT (and its parse), never on the intermediate
# scenario dict alone. Both generators hand-roll their YAML and hardcode which
# keys under vllmCommon get rendered, so a dict-only assertion passes while the
# emitter silently drops the key -- the same shape of gap that produced #830.
# ---------------------------------------------------------------------------


def test_prefill_pool_emits_kv_transfer(tmp_path):
    """Stating a prefill count must turn KV transfer ON in the emitted YAML."""
    scenario, prov = build([row("Number of prefill pods", "1")])
    parsed = yaml.safe_load(emit(scenario, prov, tmp_path))["scenario"][0]
    kv = parsed["vllmCommon"]["kvTransfer"]
    assert kv["enabled"] is True
    assert kv["connector"] == "NixlConnector"
    assert kv["role"] == "kv_both"


def test_no_prefill_pool_emits_no_kv_transfer(tmp_path):
    """Aggregated bundles must regenerate byte-identically — no kvTransfer at all."""
    scenario, prov = build([])
    text = emit(scenario, prov, tmp_path)
    assert "kvTransfer" not in text
    assert "vllmCommon" not in text


def test_stated_zero_prefill_emits_no_kv_transfer(tmp_path):
    """A stated 0 means aggregated, so it must not enable the transport either."""
    scenario, prov = build([row("Number of prefill pods", "0")])
    assert "kvTransfer" not in emit(scenario, prov, tmp_path)


def test_kv_transfer_and_enforce_eager_coexist(tmp_path):
    """Neither vllmCommon subtree may clobber or hide the other.

    `enforce_eager: false` creates scenario["vllmCommon"] before the prefill block
    runs. A plain assignment in either place drops the other; a hand-rolled emitter
    rendering only one branch hides the other. Both must survive into the output.
    """
    scenario, prov = build(
        [row("Number of prefill pods", "1"), row("enforce_eager", "false")]
    )
    parsed = yaml.safe_load(emit(scenario, prov, tmp_path))["scenario"][0]
    assert parsed["vllmCommon"]["flags"]["enforceEager"] is False
    assert parsed["vllmCommon"]["kvTransfer"]["enabled"] is True


def test_enforce_eager_alone_still_emits_flags(tmp_path):
    """The flags-only path must not regress now that the branch is conditional."""
    scenario, prov = build([row("enforce_eager", "false")])
    parsed = yaml.safe_load(emit(scenario, prov, tmp_path))["scenario"][0]
    assert parsed["vllmCommon"]["flags"]["enforceEager"] is False
    assert "kvTransfer" not in parsed["vllmCommon"]


def test_kv_transfer_carries_provenance(tmp_path):
    """Every emitted key gets a provenance comment; these are no exception."""
    scenario, prov = build([row("Number of prefill pods", "1")])
    text = emit(scenario, prov, tmp_path)
    lines = text.split("\n")
    start = next(i for i, ln in enumerate(lines) if ln.strip() == "kvTransfer:")
    block = lines[start + 1 : start + 4]
    for key in ("enabled:", "connector:", "role:"):
        line = next(ln for ln in block if key in ln)
        assert "#" in line, f"{key} emitted without a provenance comment: {line!r}"
    # kv_both is a known-deprecated value shipped knowingly, so the emitted file
    # must point at the issue tracking it rather than stay silent about it.
    assert "#845" in text


def test_kv_transfer_agrees_across_both_generators(tmp_path):
    """generate_scenarios.py has the same prefill block and had the same hole.

    Issue #830 named only generate_from_config.py. Fixing one path and not the
    other would leave half of bootstrap emitting an inert prefill pool, so the two
    generators must agree — both that a prefill pool enables the transport, and
    that no prefill pool leaves it absent.
    """
    sys.path.insert(0, str(Path(__file__).parents[1]))
    import generate_scenarios as gs

    def gs_emit(prefill_instances):
        entry = {
            "workload": {"model": "Qwen/Qwen3-14B", "hardware": "H100_SXM_80GB"},
            "vllm_args": {
                "num_instances": 2,
                "prefill_instances": prefill_instances,
            },
        }
        scenario = gs.build_scenario(entry, "cand")
        out = tmp_path / f"cand-{prefill_instances}.yaml"
        gs.write_commented_yaml(scenario, entry, str(out))
        return out.read_text()

    with_prefill = yaml.safe_load(gs_emit(1))["scenario"][0]
    assert with_prefill["vllmCommon"]["kvTransfer"]["enabled"] is True
    assert with_prefill["vllmCommon"]["kvTransfer"]["connector"] == "NixlConnector"

    assert "kvTransfer" not in gs_emit(0)

    # The two generators must emit identical transport settings for the same intent.
    from_config, prov = build([row("Number of prefill pods", "1")])
    from_config_kv = yaml.safe_load(emit(from_config, prov, tmp_path))["scenario"][0][
        "vllmCommon"
    ]["kvTransfer"]
    assert from_config_kv == with_prefill["vllmCommon"]["kvTransfer"]


# ---------------------------------------------------------------------------
# Pod plumbing gates (issue #848)
# ---------------------------------------------------------------------------
# #846 emits kvTransfer.enabled: true on a prefill pool. Without the plumbing
# below that flag crashloops the worker during "Initializing NIXL wrapper".
# Assertions are on emitted TEXT: the emitter is a hand-rolled line appender, so
# a key present in the scenario dict proves nothing about the output.


def test_no_gates_emits_no_plumbing(tmp_path):
    """The byte-identity contract: no prefill pool and a single-GPU pod must
    regenerate exactly what it did before #848."""
    scenario, prov = build([])
    text = emit(scenario, prov, tmp_path)
    for key in (
        "initContainers",
        "preprocessScript",
        "volumes:",
        "volumeMounts:",
        "extraEnvVars",
        "routing:",
        "shared-config",
        "dshm",
        "NIXL_LOG_LEVEL",
        "NCCL_DEBUG",
        "NVSHMEM_DEBUG",
    ):
        assert key not in text, f"{key} leaked into an ungated scenario"


def test_internal_gate_marker_never_reaches_output(tmp_path):
    """build_scenario stashes the gate booleans on the dict for the emitter. That
    is an internal channel, not part of the emitted schema."""
    scenario, prov = build([row("Number of prefill pods", "1")])
    assert "_gates" not in emit(scenario, prov, tmp_path)


def test_gate1_emits_the_full_kv_unit(tmp_path):
    scenario, prov = build([row("Number of prefill pods", "1")])
    text = emit(scenario, prov, tmp_path)
    parsed = yaml.safe_load(text)["scenario"][0]

    # init container on BOTH roles -- each role is a separate pod
    for role in ("decode", "prefill"):
        ics = parsed[role]["initContainers"]
        assert [ic["name"] for ic in ics] == ["preprocess"]
        assert ics[0]["command"][0] == "set_llmdbench_environment.py"

    assert ". /shared-config/llmdbench_env.sh" in parsed["vllmCommon"]["preprocessScript"]
    assert [v["name"] for v in parsed["vllmCommon"]["volumes"]] == ["shared-config"]
    assert parsed["routing"]["connector"] == "nixlv2"

    for role in ("decode", "prefill"):
        assert {e["name"] for e in parsed[role]["extraEnvVars"]} == {"NIXL_LOG_LEVEL"}


def test_gate1_does_not_emit_gate2_pieces(tmp_path):
    """A single-GPU P/D pod needs no tmpfs and runs no collectives."""
    scenario, prov = build([row("Number of prefill pods", "1")])
    text = emit(scenario, prov, tmp_path)
    assert "dshm" not in text
    assert "NCCL_DEBUG" not in text
    assert "NVSHMEM_DEBUG" not in text


def test_gate2_emits_dshm_and_collective_vars_without_a_prefill_pool(tmp_path):
    """Gate 2 is independent of Gate 1: an aggregated multi-GPU pod gets the
    tmpfs and the collective variables and nothing else."""
    scenario, prov = build([row("tensor_parallel_size", "4")])
    text = emit(scenario, prov, tmp_path)
    parsed = yaml.safe_load(text)["scenario"][0]

    vols = parsed["vllmCommon"]["volumes"]
    assert [v["name"] for v in vols] == ["dshm"]
    assert vols[0]["emptyDir"]["medium"] == "Memory"
    assert {m["mountPath"] for m in parsed["vllmCommon"]["volumeMounts"]} == {"/dev/shm"}
    assert {e["name"] for e in parsed["decode"]["extraEnvVars"]} == {
        "NCCL_DEBUG",
        "NVSHMEM_DEBUG",
    }

    # Gate 1 pieces stay absent
    assert "prefill" not in parsed
    assert "initContainers" not in parsed["decode"]
    assert "preprocessScript" not in parsed["vllmCommon"]
    assert "routing" not in parsed


def test_gate2_fires_on_data_parallel_alone(tmp_path):
    """Plan D1: dataLocal == dp, so dp>1 is also several GPUs in one pod."""
    scenario, prov = build([row("data_parallel_size", "2")])
    parsed = yaml.safe_load(emit(scenario, prov, tmp_path))["scenario"][0]
    assert [v["name"] for v in parsed["vllmCommon"]["volumes"]] == ["dshm"]


def test_both_gates_accumulate_volumes_and_env(tmp_path):
    """The regression this shape invites: two gates writing the same two keys,
    the second clobbering the first."""
    scenario, prov = build(
        [row("Number of prefill pods", "2"), row("tensor_parallel_size", "4")]
    )
    parsed = yaml.safe_load(emit(scenario, prov, tmp_path))["scenario"][0]

    assert [v["name"] for v in parsed["vllmCommon"]["volumes"]] == [
        "shared-config",
        "dshm",
    ]
    assert [m["mountPath"] for m in parsed["vllmCommon"]["volumeMounts"]] == [
        "/shared-config",
        "/dev/shm",
    ]
    for role in ("decode", "prefill"):
        assert {e["name"] for e in parsed[role]["extraEnvVars"]} == {
            "NIXL_LOG_LEVEL",
            "NCCL_DEBUG",
            "NVSHMEM_DEBUG",
        }


def test_kv_transfer_and_routing_connector_agree(tmp_path):
    """The issue's requirement: both halves from one connector value. A bundle
    with the engine on NixlConnector and the sidecar on something else is the
    half-configuration #830 was."""
    import pd_plumbing as pdp

    scenario, prov = build([row("Number of prefill pods", "1")])
    parsed = yaml.safe_load(emit(scenario, prov, tmp_path))["scenario"][0]
    assert parsed["vllmCommon"]["kvTransfer"]["connector"] == pdp.KV_CONNECTOR_ENGINE
    assert parsed["routing"]["connector"] == pdp.KV_CONNECTOR_SIDECAR


def test_plumbing_coexists_with_enforce_eager_override(tmp_path):
    """vllmCommon now has four independent sub-keys. enforce_eager creates the
    dict first; a plain assignment anywhere downstream would drop it."""
    scenario, prov = build(
        [row("enforce_eager", "false"), row("Number of prefill pods", "1")]
    )
    parsed = yaml.safe_load(emit(scenario, prov, tmp_path))["scenario"][0]
    vc = parsed["vllmCommon"]
    assert vc["flags"]["enforceEager"] is False
    assert vc["kvTransfer"]["enabled"] is True
    assert "preprocessScript" in vc
    assert [v["name"] for v in vc["volumes"]] == ["shared-config"]


def test_emitted_plumbing_is_valid_yaml_in_every_gate_combination(tmp_path):
    """Hand-rolled emitter: indentation is the contract."""
    combos = [
        [],
        [row("Number of prefill pods", "1")],
        [row("tensor_parallel_size", "4")],
        [row("Number of prefill pods", "2"), row("tensor_parallel_size", "4")],
    ]
    for i, extra in enumerate(combos):
        scenario, prov = build(extra)
        parsed = yaml.safe_load(emit(scenario, prov, tmp_path / f"c{i}"))
        assert parsed["scenario"][0]["name"] == "test"




# ---------------------------------------------------------------------------
# Pod CPU/memory resources (issue #850)
# ---------------------------------------------------------------------------
# Bootstrap emitted none, so bundles inherited 40Gi / 4 CPU for a pod that may hold
# four GPUs. Four keys, applied to both roles; no per-role rows (an operator who
# needs the roles to differ edits baselines/baseline.yaml, as for anything else the
# generator does not model).
#
# Assertions are on emitted TEXT: the emitter is a hand-rolled line appender, so a
# key in the scenario dict proves nothing about the output.


def _res(parsed, role):
    return parsed[role]["resources"]


def test_resources_emitted_with_role_defaults(tmp_path):
    scenario, prov = build([row("Number of prefill pods", "1")])
    parsed = yaml.safe_load(emit(scenario, prov, tmp_path))["scenario"][0]
    assert _res(parsed, "decode")["limits"] == {"memory": "128Gi", "cpu": "32"}
    assert _res(parsed, "decode")["requests"] == {"memory": "64Gi", "cpu": "16"}
    assert _res(parsed, "prefill")["limits"] == {"memory": "16Gi", "cpu": "8"}
    # prefill's request EQUALS its limit (Guaranteed QoS), because #848 mounts a
    # 16Gi tmpfs at /dev/shm in vllmCommon and tmpfs charges against pod memory.
    assert _res(parsed, "prefill")["requests"] == {"memory": "16Gi", "cpu": "8"}


def test_no_prefill_pool_emits_resources_for_decode_only(tmp_path):
    scenario, prov = build([])
    parsed = yaml.safe_load(emit(scenario, prov, tmp_path))["scenario"][0]
    assert "resources" in parsed["decode"]
    assert "prefill" not in parsed


def test_stated_row_applies_to_both_roles(tmp_path):
    scenario, prov = build(
        [row("Number of prefill pods", "1"), row("cpu limit", "64")]
    )
    parsed = yaml.safe_load(emit(scenario, prov, tmp_path))["scenario"][0]
    assert _res(parsed, "decode")["limits"]["cpu"] == "64"
    assert _res(parsed, "prefill")["limits"]["cpu"] == "64"
    # the three unstated quantities keep their per-role defaults
    assert _res(parsed, "decode")["limits"]["memory"] == "128Gi"
    assert _res(parsed, "prefill")["limits"]["memory"] == "16Gi"


@pytest.mark.parametrize(
    "label", ["cpu limit", "cpu_limit", "cpu limits"]
)
def test_each_declared_alias_spelling_resolves(label, tmp_path):
    """A declared spelling must actually reach the resolver -- deleting an alias would
    otherwise revert a stated row to a generous default with no error. Covers
    cpu_limit's three spellings; the other three keys follow the same pattern in
    PARAMETER_ALIASES and are covered by test_every_resource_field_is_declared."""
    scenario, prov = build([row(label, "64")])
    parsed = yaml.safe_load(emit(scenario, prov, tmp_path))["scenario"][0]
    assert _res(parsed, "decode")["limits"]["cpu"] == "64"


@pytest.mark.parametrize("key", ["cpu_limit", "memory_limit",
                                 "cpu_request", "memory_request"])
def test_every_resource_field_is_declared_and_string_valued(key):
    """A resource field handed to parse_numeric loses its unit whenever the value
    contains whitespace: parse_numeric takes only the first token, so `16 Gi` becomes
    `16`. Unspaced quantities like `128Gi` survive by accident -- int()/float() fail on
    them, so the None fallback returns the whole string. The accident is the reason to
    be explicit here rather than rely on it."""
    assert key in gfc.PARAMETER_ALIASES
    assert key in gfc._STRING_VALUED_FIELDS


@pytest.mark.parametrize(
    "stated,emitted",
    [("128Gi", "128Gi"), ("1536Mi", "1536Mi"), ("2Ti", "2Ti"), ("512M", "512M")]
)
def test_memory_quantity_keeps_its_unit(stated, emitted, tmp_path):
    scenario, prov = build([row("memory limit", stated)])
    parsed = yaml.safe_load(emit(scenario, prov, tmp_path))["scenario"][0]
    assert _res(parsed, "decode")["limits"]["memory"] == emitted


@pytest.mark.parametrize("stated", ["500m", "1.50", "1.5", "32", "0.5"])
def test_cpu_quantity_passes_through_verbatim(stated, tmp_path):
    """`1.50` must not become `1.5`: it is an opaque string the chart forwards to
    Kubernetes, not a number to normalize."""
    scenario, prov = build([row("cpu limit", stated)])
    parsed = yaml.safe_load(emit(scenario, prov, tmp_path))["scenario"][0]
    assert _res(parsed, "decode")["limits"]["cpu"] == stated


@pytest.mark.parametrize("hostile", ["128", "0.5"])
def test_yaml_hostile_values_stay_strings(hostile, tmp_path):
    """Valid quantities that a YAML reader re-types when unquoted: `128` to an int,
    `0.5` to a float. memory used to be emitted unquoted while cpu was quoted, which
    is how these lost their string-ness.

    Values that are BOTH YAML-hostile and invalid quantities (`yes`, `null`, `~`,
    `-`) never reach the emitter now — the validator rejects them first, which is
    covered by test_invalid_quantity_is_a_hard_error below.
    """
    scenario, prov = build([row("memory limit", hostile)])
    parsed = yaml.safe_load(emit(scenario, prov, tmp_path))["scenario"][0]
    assert _res(parsed, "decode")["limits"]["memory"] == hostile


@pytest.mark.parametrize(
    "bad", ["-", "TBD", "N/A", "16 Gi", "32 cores", "yes", "null", "~", "128GB"])
def test_invalid_quantity_is_a_hard_error(bad, capsys):
    """A quantity the cluster rejects at admission must fail here instead. `-` is the
    conventional markdown placeholder and used to produce an unparseable YAML file
    under a zero exit code."""
    with pytest.raises(SystemExit) as exc:
        build([row("memory limit", bad)])
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "not a valid" in err or "invalid Kubernetes quantities" in err


def test_all_four_stated_suppresses_the_warning(tmp_path, capsys):
    scenario, prov = build(
        [
            row("cpu limit", "64"),
            row("memory limit", "200Gi"),
            row("cpu request", "8"),
            row("memory request", "32Gi"),
        ]
    )
    text = emit(scenario, prov, tmp_path)
    assert "Reducing Torch parallelism" not in text
    assert "Reducing Torch parallelism" not in capsys.readouterr().err
    parsed = yaml.safe_load(text)["scenario"][0]
    assert _res(parsed, "decode")["limits"] == {"memory": "200Gi", "cpu": "64"}
    assert _res(parsed, "decode")["requests"] == {"memory": "32Gi", "cpu": "8"}


def test_any_default_keeps_the_warning(tmp_path, capsys):
    scenario, prov = build([row("cpu limit", "64")])
    assert "Reducing Torch parallelism" in emit(scenario, prov, tmp_path)
    err = capsys.readouterr().err
    assert "Reducing Torch parallelism" in err
    assert "cpu_limit" not in err, "warning names a quantity the operator stated"


def test_prefill_warn_suppression_is_independent_of_decode(tmp_path):
    """Both roles gate the preamble separately. Only decode's half was pinned
    before, so forcing prefill's on above stated values went unnoticed."""
    scenario, prov = build(
        [
            row("Number of prefill pods", "1"),
            row("cpu limit", "64"),
            row("memory limit", "200Gi"),
            row("cpu request", "8"),
            row("memory request", "32Gi"),
        ]
    )
    text = emit(scenario, prov, tmp_path)
    prefill_block = text[text.index("  prefill:"):]
    assert "GENEROUS DEFAULTS" not in prefill_block


def test_emitted_values_carry_per_value_sources(tmp_path):
    scenario, prov = build([row("cpu limit", "64")])
    text = emit(scenario, prov, tmp_path)
    cpu = next(ln for ln in text.splitlines() if "cpu: '64'" in ln)
    mem = next(ln for ln in text.splitlines() if "memory: '128Gi'" in ln)
    assert "operator-stated" in cpu
    assert "UNMEASURED" in mem


def test_source_comments_name_no_input_file(tmp_path):
    """Both generators share pod_resources and read different inputs."""
    scenario, prov = build([row("cpu limit", "64")])
    resources_text = "\n".join(
        ln for ln in emit(scenario, prov, tmp_path).splitlines()
        if "operator-stated" in ln or "UNMEASURED" in ln
    )
    assert "vllm_args" not in resources_text
    assert "{" not in resources_text


def test_resources_parse_in_every_row_combination(tmp_path):
    combos = [
        [],
        [row("Number of prefill pods", "2")],
        [row("cpu limit", "64"), row("Number of prefill pods", "2")],
        [row("memory request", "20Gi"), row("Number of prefill pods", "2")],
    ]
    for i, extra in enumerate(combos):
        scenario, prov = build(extra)
        parsed = yaml.safe_load(emit(scenario, prov, tmp_path / f"r{i}"))
        assert "resources" in parsed["scenario"][0]["decode"]


# --- pair reconciliation end-to-end (review must_fix) ----------------------

def test_shared_request_row_clamps_prefill_not_decode(tmp_path, capsys):
    """A `cpu request: 20` row is sensible for decode (limit 32) and impossible for
    prefill (limit 8), and one row feeds both roles. This used to emit
    requests {96Gi, 20} against limits {16Gi, 8} for prefill -- a pod Kubernetes
    rejects at admission -- under a comment claiming requests sit below limits."""
    scenario, prov = build(
        [
            row("Number of prefill pods", "1"),
            row("cpu request", "20"),
            row("memory request", "96Gi"),
        ]
    )
    parsed = yaml.safe_load(emit(scenario, prov, tmp_path))["scenario"][0]
    assert _res(parsed, "decode")["requests"] == {"memory": "96Gi", "cpu": "20"}
    assert _res(parsed, "prefill")["requests"] == {"memory": "16Gi", "cpu": "8"}
    assert _res(parsed, "prefill")["requests"] == _res(parsed, "prefill")["limits"]
    err = capsys.readouterr().err
    assert "exceeds its limit" in err
    assert "prefill" in err


def test_smaller_stated_limit_derives_a_fitting_request(tmp_path):
    """Stating only limits used to leave the larger default requests in place:
    limits {8, 32Gi} with requests {16, 64Gi}."""
    scenario, prov = build([row("cpu limit", "8"), row("memory limit", "32Gi")])
    parsed = yaml.safe_load(emit(scenario, prov, tmp_path))["scenario"][0]
    assert _res(parsed, "decode")["limits"] == {"memory": "32Gi", "cpu": "8"}
    assert _res(parsed, "decode")["requests"] == {"memory": "16Gi", "cpu": "4"}


@pytest.mark.parametrize(
    "rows",
    [
        [row("cpu request", "999"), row("memory request", "999Gi")],
        [row("cpu limit", "1"), row("memory limit", "1Gi")],
        [row("cpu limit", "1"), row("cpu request", "64")],
        [row("memory limit", "2Gi"), row("memory request", "128Gi")],
    ],
)
def test_no_config_md_input_emits_a_request_above_its_limit(rows, tmp_path):
    """The invariant, through the generator, over inputs that previously inverted."""
    import pod_resources as pres

    scenario, prov = build([row("Number of prefill pods", "1")] + rows)
    parsed = yaml.safe_load(emit(scenario, prov, tmp_path))["scenario"][0]
    for role in ("decode", "prefill"):
        res = _res(parsed, role)
        for field in ("cpu", "memory"):
            req = pres._magnitude(res["requests"][field])
            lim = pres._magnitude(res["limits"][field])
            assert req <= lim, f"{role} {field}: {res}"


def test_binary_kilo_quantity_is_accepted(tmp_path):
    """`Ki` is valid Kubernetes and the unit `kubectl describe node` reports memory
    in. The validator used to hard-reject it while accepting `1ki`."""
    scenario, prov = build(
        [row("memory limit", "134217728Ki"), row("memory request", "67108864Ki")]
    )
    parsed = yaml.safe_load(emit(scenario, prov, tmp_path))["scenario"][0]
    assert _res(parsed, "decode")["limits"]["memory"] == "134217728Ki"
    assert _res(parsed, "decode")["requests"]["memory"] == "67108864Ki"


# --- rows stated where they cannot take effect ----------------------------

def test_resource_rows_in_another_table_are_reported(capsys):
    """warn_role_rows_outside_vllm_table exists because a #824 review found a role
    row in a mapping table producing a wrong baseline with no diagnostic. Its
    role_fields set did not include the #850 keys, so a `cpu limit` row in any other
    table was dropped and the defaults warning then asserted nothing was stated."""
    md = "\n".join([
        "## Pod Resource Sizing",
        "",
        "| Parameter | Value |",
        "|---|---|",
        "| cpu limit | 64 |",
        "| memory limit | 256Gi |",
        "",
        "## vLLM Pod Configuration",
        "",
        "| Parameter | Value | Notes |",
        "|---|---|---|",
        "| Model | Qwen/Qwen3-14B | |",
        "| GPU | H100_SXM_80GB | |",
        "",
    ])
    tables = gfc.parse_md_tables(md.split("\n"))
    vllm_table = gfc.find_vllm_table(tables)
    gfc.warn_role_rows_outside_vllm_table(tables, vllm_table)
    err = capsys.readouterr().err
    assert "cpu limit" in err
    assert "not the machine-read table" in err or "NO effect" in err

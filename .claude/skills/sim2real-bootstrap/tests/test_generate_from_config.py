"""Tests for generate_from_config.py — prefix-caching input + output contract.

Covers the acceptance criteria from issues #293 and #295:
  - Legacy keyed input (enable_prefix_caching | true|false)
  - Bare positive flag input (--enable-prefix-caching, empty value column)
  - Bare negative flag input (--no-enable-prefix-caching, empty value column)
  - Field absent → emit --enable-prefix-caching as a sim2real-bootstrap default
    (deployed vLLM predates per-model default resolution; see #295)
  - Contradictory specifications → sys.exit(1)
  - Unparseable boolean → sys.exit(1)
  - Duplicate same-value rows → reconciled silently
  - Sentinel __no_enable_prefix_caching__ never leaks into the returned fields
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import generate_from_config as gfc


def make_table(rows: list[dict]) -> gfc.TableSection:
    """Build a TableSection that find_vllm_table-style code expects."""
    return gfc.TableSection(heading="vLLM Pod Configuration", rows=rows, line_number=0)


def epc_flag(fields: dict[str, gfc.ProvenanceValue]) -> str | None:
    """Return the prefix-caching flag emitted by build_additional_flags, or None."""
    flags = gfc.build_additional_flags(fields)
    for flag, _ in flags:
        if "prefix-caching" in flag:
            return flag
    return None


# ---------------------------------------------------------------------------
# Input form parity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "row,expected_value,expected_flag",
    [
        # Legacy keyed form
        ({"Parameter": "enable_prefix_caching", "Value": "true"}, True, "--enable-prefix-caching"),
        ({"Parameter": "enable_prefix_caching", "Value": "false"}, False, "--no-enable-prefix-caching"),
        # Legacy keyed form via --enable-prefix-caching alias
        ({"Parameter": "--enable-prefix-caching", "Value": "true"}, True, "--enable-prefix-caching"),
        ({"Parameter": "--enable-prefix-caching", "Value": "false"}, False, "--no-enable-prefix-caching"),
        # Bare positive flag (empty value column)
        ({"Parameter": "--enable-prefix-caching", "Value": ""}, True, "--enable-prefix-caching"),
        # Bare negative flag (empty value column)
        ({"Parameter": "--no-enable-prefix-caching", "Value": ""}, False, "--no-enable-prefix-caching"),
    ],
)
def test_accepted_input_forms_resolve_correctly(row, expected_value, expected_flag):
    fields = gfc.extract_fields(make_table([row]))
    assert "enable_prefix_caching" in fields
    assert fields["enable_prefix_caching"].value is expected_value
    assert epc_flag(fields) == expected_flag


def test_field_absent_emits_enable_prefix_caching_default():
    """Per #295: when enable_prefix_caching is absent, emit --enable-prefix-caching
    as a sim2real-bootstrap default. The deployed vLLM version predates per-model
    default resolution, so silent would otherwise resolve to OFF."""
    fields = gfc.extract_fields(make_table([
        {"Parameter": "model", "Value": "meta-llama/Llama-3.1-8B"},
    ]))
    # extract_fields itself does not synthesize the field; only build_additional_flags does.
    assert "enable_prefix_caching" not in fields
    assert epc_flag(fields) == "--enable-prefix-caching"


def test_negative_bare_flag_value_column_is_ignored():
    """`--no-enable-prefix-caching` resolves OFF regardless of value column contents."""
    fields = gfc.extract_fields(make_table([
        {"Parameter": "--no-enable-prefix-caching", "Value": "true"},
    ]))
    assert fields["enable_prefix_caching"].value is False
    assert epc_flag(fields) == "--no-enable-prefix-caching"


# ---------------------------------------------------------------------------
# Reconciliation: duplicates vs conflicts
# ---------------------------------------------------------------------------

def test_duplicate_agreeing_rows_reconcile_silently():
    """Multiple rows asserting the same value collapse to one observation."""
    fields = gfc.extract_fields(make_table([
        {"Parameter": "enable_prefix_caching", "Value": "true"},
        {"Parameter": "--enable-prefix-caching", "Value": ""},
    ]))
    assert fields["enable_prefix_caching"].value is True
    assert epc_flag(fields) == "--enable-prefix-caching"


def test_contradictory_bare_flags_exit_1(capsys):
    """Both --enable-prefix-caching and --no-enable-prefix-caching present is an error."""
    table = make_table([
        {"Parameter": "--enable-prefix-caching", "Value": ""},
        {"Parameter": "--no-enable-prefix-caching", "Value": ""},
    ])
    with pytest.raises(SystemExit) as exc:
        gfc.extract_fields(table)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "conflicting enable_prefix_caching" in err
    # Both source rows should be cited.
    assert "--enable-prefix-caching" in err
    assert "--no-enable-prefix-caching" in err


def test_contradictory_legacy_and_bare_exit_1():
    """Legacy false + bare positive is also a conflict."""
    table = make_table([
        {"Parameter": "enable_prefix_caching", "Value": "false"},
        {"Parameter": "--enable-prefix-caching", "Value": ""},
    ])
    with pytest.raises(SystemExit) as exc:
        gfc.extract_fields(table)
    assert exc.value.code == 1


# ---------------------------------------------------------------------------
# Unparseable boolean: must NOT silently drop the row
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_value", ["treu", "on", "enabled", "maybe"])
def test_unparseable_boolean_exits_1(bad_value, capsys):
    """An unparseable value column is a configuration error, not a silent skip."""
    table = make_table([
        {"Parameter": "enable_prefix_caching", "Value": bad_value},
    ])
    with pytest.raises(SystemExit) as exc:
        gfc.extract_fields(table)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "could not parse boolean" in err
    assert bad_value in err


# ---------------------------------------------------------------------------
# Sentinel hygiene
# ---------------------------------------------------------------------------

def test_sentinel_key_never_appears_in_output_fields():
    """The internal __no_enable_prefix_caching__ canonical must be folded away."""
    fields = gfc.extract_fields(make_table([
        {"Parameter": "--no-enable-prefix-caching", "Value": ""},
    ]))
    assert "__no_enable_prefix_caching__" not in fields
    assert "enable_prefix_caching" in fields


def test_sentinel_does_not_skew_vllm_table_detection():
    """__no_enable_prefix_caching__ must not be in VLLM_INDICATOR_FIELDS."""
    assert "__no_enable_prefix_caching__" not in gfc.VLLM_INDICATOR_FIELDS


# ---------------------------------------------------------------------------
# Output contract: exact bare flag, never legacy keyed
# ---------------------------------------------------------------------------

def test_output_uses_bare_form_not_keyed():
    """build_additional_flags must emit `--enable-prefix-caching`, not `--enable-prefix-caching=true`."""
    fields = {
        "enable_prefix_caching": gfc.ProvenanceValue(value=True, source="test", raw_param=""),
    }
    flags = gfc.build_additional_flags(fields)
    flag_strs = [f for f, _ in flags]
    assert "--enable-prefix-caching" in flag_strs
    assert not any("=" in f and "prefix-caching" in f for f in flag_strs)


def test_output_absent_emits_default_enable_with_bootstrap_provenance():
    """No prefix-caching key in fields → emit --enable-prefix-caching with a
    sim2real-bootstrap provenance source (issue #295)."""
    fields = {}
    flags = gfc.build_additional_flags(fields)
    pc_flags = [(f, src) for f, src in flags if "prefix-caching" in f]
    assert len(pc_flags) == 1
    flag, source = pc_flags[0]
    assert flag == "--enable-prefix-caching"
    assert "sim2real-bootstrap default" in source

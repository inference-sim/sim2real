"""Tests for pod_resources.py — CPU/memory for the model-server pods (issue #850).

Bootstrap emitted no `resources`, so bundles inherited the framework default of
`limits` AND `requests` both 40Gi / "4" for a pod that may hold four GPUs sharing its
cgroup with the routing sidecar. That starves vLLM and surfaces as ITL noise rather
than a loud failure, corrupting the metric arms are compared on.

The resolver, validator and emitter are tested here; the per-role wiring lives in the
two generators and is tested in their own files.
"""
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parents[1]))
import pod_resources as pres


def parse(lines, role="decode"):
    """Parse emitter output inside the role block the caller has printed."""
    doc = f"scenario:\n- name: t\n  {role}:\n" + "\n".join(lines) + "\n"
    return yaml.safe_load(doc)["scenario"][0][role]


NONE = dict.fromkeys(pres.KEYS)
ALL_STATED = {"cpu_limit": "1", "memory_limit": "2Gi",
              "cpu_request": "3", "memory_request": "4Gi"}


# --- defaults --------------------------------------------------------------

def test_keys_are_the_four_quantities():
    assert set(pres.KEYS) == {
        "cpu_limit", "memory_limit", "cpu_request", "memory_request"}


@pytest.mark.parametrize("role", ["decode", "prefill"])
def test_every_role_has_all_four_defaults(role):
    assert set(pres.DEFAULTS[role]) == set(pres.KEYS)


def test_decode_is_sized_above_prefill():
    """The operator's stated constraint, and upstream's own sizing."""
    d, p = pres.DEFAULTS["decode"], pres.DEFAULTS["prefill"]
    assert int(d["cpu_limit"]) > int(p["cpu_limit"])
    assert int(d["memory_limit"].removesuffix("Gi")) > int(
        p["memory_limit"].removesuffix("Gi"))


def test_defaults_match_the_observed_working_limits():
    """Verbatim from pd-disaggregation.yaml:410-416 / :321-327, which pd-infocomm-2
    also carries."""
    assert pres.DEFAULTS["decode"]["cpu_limit"] == "32"
    assert pres.DEFAULTS["decode"]["memory_limit"] == "128Gi"
    assert pres.DEFAULTS["prefill"]["cpu_limit"] == "8"
    assert pres.DEFAULTS["prefill"]["memory_limit"] == "16Gi"


@pytest.mark.parametrize("role", ["decode", "prefill"])
def test_default_requests_sit_below_default_limits(role):
    """requests are what the scheduler reserves; reserving the generous ceiling on
    every replica would make a multi-replica pool unschedulable."""
    v = pres.DEFAULTS[role]
    assert int(v["cpu_request"]) < int(v["cpu_limit"])
    assert int(v["memory_request"].removesuffix("Gi")) < int(
        v["memory_limit"].removesuffix("Gi"))


@pytest.mark.parametrize("role", ["decode", "prefill"])
def test_every_default_is_a_valid_quantity(role):
    v, _ = pres.resolve_resources(role, NONE)
    assert pres.invalid_quantities(v) == []


# --- resolution ------------------------------------------------------------

def test_defaults_used_when_nothing_stated():
    v, prov = pres.resolve_resources("decode", NONE)
    assert v == pres.DEFAULTS["decode"]
    assert set(prov.values()) == {pres.DEFAULTED}


def test_stated_wins_and_the_rest_still_default():
    v, prov = pres.resolve_resources("decode", {**NONE, "cpu_limit": "64"})
    assert v["cpu_limit"] == "64"
    assert prov["cpu_limit"] == pres.STATED
    assert v["memory_limit"] == pres.DEFAULTS["decode"]["memory_limit"]
    assert prov["memory_limit"] == pres.DEFAULTED


def test_stated_values_pass_through_verbatim():
    """These are opaque Kubernetes quantities; re-serializing could change them."""
    stated = {"cpu_limit": "500m", "memory_limit": "1536Mi",
              "cpu_request": "1.5", "memory_request": "512Mi"}
    v, _ = pres.resolve_resources("prefill", stated)
    assert v == stated


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_blank_counts_as_unstated(blank):
    v, prov = pres.resolve_resources("decode", {**NONE, "cpu_limit": blank})
    assert v["cpu_limit"] == pres.DEFAULTS["decode"]["cpu_limit"]
    assert prov["cpu_limit"] == pres.DEFAULTED


def test_defaulted_keys_lists_only_the_missing_ones():
    _, prov = pres.resolve_resources("decode", {**NONE, "cpu_limit": "64"})
    assert pres.defaulted_keys(prov) == [
        "memory_limit", "cpu_request", "memory_request"]
    _, all_prov = pres.resolve_resources("decode", ALL_STATED)
    assert pres.defaulted_keys(all_prov) == []


# --- quantity validation ---------------------------------------------------

@pytest.mark.parametrize(
    "good", ["32", "1", "0.5", "1.5", "500m", "128Gi", "1536Mi", "2Ti", "64G",
             "1e3", "100k"])
def test_valid_quantities_accepted(good):
    v, _ = pres.resolve_resources("decode", {**NONE, "memory_limit": good})
    assert pres.invalid_quantities(v) == []


@pytest.mark.parametrize(
    "bad", ["TBD", "N/A", "-", "same as decode", "16 Gi", "32 cores", "", "??",
            "128GB", "lots"])
def test_invalid_quantities_rejected(bad):
    """A quantity the cluster would reject at admission must fail at generation,
    far cheaper than failing on the cluster far from the row that caused it.

    `""` is here for completeness: it resolves to the default, so it can never
    actually reach the validator — hence the assertion is on the resolved value.
    """
    v, _ = pres.resolve_resources("decode", {**NONE, "memory_limit": bad})
    if bad.strip():
        assert pres.invalid_quantities(v) == [f"memory_limit={bad!r}"]
    else:
        assert pres.invalid_quantities(v) == []  # blank fell back to the default


# --- emission --------------------------------------------------------------

def test_emitted_yaml_carries_both_limits_and_requests():
    v, prov = pres.resolve_resources("decode", NONE)
    r = parse(pres.resource_lines(v, prov, warn=True))["resources"]
    assert r["limits"] == {"memory": "128Gi", "cpu": "32"}
    assert r["requests"] == {"memory": "64Gi", "cpu": "16"}


@pytest.mark.parametrize("section", ["limits", "requests"])
@pytest.mark.parametrize("field", ["memory", "cpu"])
def test_every_emitted_value_is_a_quoted_string(section, field):
    """Unquoted, a YAML reader re-types these: `128` becomes an int, `yes` True,
    and `-` a sequence entry that will not parse at all. cpu was quoted and memory
    was not, which is how a `-` placeholder produced an unparseable baseline."""
    v, prov = pres.resolve_resources("decode", NONE)
    r = parse(pres.resource_lines(v, prov, warn=True))["resources"]
    assert isinstance(r[section][field], str)


@pytest.mark.parametrize("hostile", ["128", "yes", "no", "null", "~", "0x40"])
def test_yaml_hostile_values_survive_as_strings(hostile):
    """Each of these is re-typed by YAML when unquoted -- to int, bool or None."""
    v, prov = pres.resolve_resources("decode", {**NONE, "memory_limit": hostile})
    r = parse(pres.resource_lines(v, prov, warn=True))["resources"]
    assert r["limits"]["memory"] == hostile


def test_prefill_emits_under_the_prefill_block():
    v, prov = pres.resolve_resources("prefill", NONE)
    r = parse(pres.resource_lines(v, prov, warn=True), role="prefill")["resources"]
    assert r["limits"]["cpu"] == "8"


def test_per_value_sources_distinguish_stated_from_default():
    v, prov = pres.resolve_resources("decode", {**NONE, "cpu_limit": "64"})
    text = "\n".join(pres.resource_lines(v, prov, warn=True))
    cpu = next(ln for ln in text.splitlines() if "cpu: '64'" in ln)
    mem = next(ln for ln in text.splitlines() if "memory: '128Gi'" in ln)
    assert pres.STATED in cpu
    assert pres.DEFAULTED in mem


def test_source_comments_name_no_input_file():
    """Both generators share this module and read different inputs, so naming one
    would be wrong on the other path."""
    v, prov = pres.resolve_resources("decode", {**NONE, "cpu_limit": "64"})
    text = "\n".join(pres.resource_lines(v, prov, warn=True))
    assert "config.md" not in text
    assert "vllm_args" not in text
    assert "{" not in text, "an unsubstituted template placeholder was emitted"


def test_warning_comment_names_the_starvation_signal():
    v, prov = pres.resolve_resources("decode", NONE)
    assert "Reducing Torch parallelism" in "\n".join(
        pres.resource_lines(v, prov, warn=True))


def test_no_warning_comment_when_everything_was_stated():
    v, prov = pres.resolve_resources("decode", ALL_STATED)
    text = "\n".join(pres.resource_lines(v, prov, warn=False))
    assert "Reducing Torch parallelism" not in text
    assert parse(pres.resource_lines(v, prov, warn=False))[
        "resources"]["limits"]["cpu"] == "1"


@pytest.mark.parametrize("role", ["decode", "prefill"])
@pytest.mark.parametrize("warn", [True, False])
def test_emitted_yaml_parses_in_every_combination(role, warn):
    """Hand-rolled emitter: indentation is the whole contract."""
    v, prov = pres.resolve_resources(role, NONE)
    r = parse(pres.resource_lines(v, prov, warn=warn), role=role)["resources"]
    assert set(r) == {"limits", "requests"}


# --- stderr warning --------------------------------------------------------

def test_warning_reports_emitted_values_not_the_default_table():
    """It fires when ANY quantity defaults, so quoting the whole default table would
    contradict the YAML whenever the operator overrode part of it."""
    v, prov = pres.resolve_resources("decode", {**NONE, "cpu_limit": "64"})
    msg = pres.starvation_warning("decode", v, prov)
    assert "cpu_limit" not in msg
    assert "memory_limit=128Gi" in msg
    assert "3 of 4" in msg


def test_warning_names_the_role_and_the_signal_and_no_input_file():
    v, prov = pres.resolve_resources("prefill", NONE)
    msg = pres.starvation_warning("prefill", v, prov)
    assert "prefill" in msg
    assert "Reducing Torch parallelism" in msg
    assert "config.md" not in msg and "vllm_args" not in msg

"""Tests for pod_resources.py — CPU/memory for the model-server pods (issue #850).

Bootstrap emitted no `resources` at all, so bundles inherited the framework default
of 4 CPU / 40Gi for a pod that may hold four GPUs and shares its cgroup with the
routing sidecar. That starves vLLM and shows up as ITL noise rather than a loud
failure, which corrupts the metric arms are compared on.

The resolver and emitter are tested here; the per-role wiring lives in the two
generators and is tested in their own files.
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


def test_keys_are_the_four_quantities():
    assert set(pres.KEYS) == {
        "cpu_limit", "memory_limit", "cpu_request", "memory_request"}


@pytest.mark.parametrize("role", ["decode", "prefill"])
def test_every_role_has_all_four_defaults(role):
    assert set(pres.DEFAULTS[role]) == set(pres.KEYS)


def test_decode_gets_more_than_prefill():
    """The operator's stated constraint, and upstream's own sizing."""
    d, p = pres.DEFAULTS["decode"], pres.DEFAULTS["prefill"]
    assert int(d["cpu_limit"]) > int(p["cpu_limit"])
    assert int(d["memory_limit"].removesuffix("Gi")) > int(
        p["memory_limit"].removesuffix("Gi"))


def test_defaults_match_the_observed_working_limits():
    """Verbatim from pd-disaggregation.yaml:410-416 / :321-327, which
    pd-infocomm-2 also carries."""
    assert pres.DEFAULTS["decode"]["cpu_limit"] == "32"
    assert pres.DEFAULTS["decode"]["memory_limit"] == "128Gi"
    assert pres.DEFAULTS["prefill"]["cpu_limit"] == "8"
    assert pres.DEFAULTS["prefill"]["memory_limit"] == "16Gi"


@pytest.mark.parametrize("role", ["decode", "prefill"])
def test_requests_are_below_limits(role):
    """limits without requests is NOT 'no reservation' -- Kubernetes copies the
    limit into the request, so requests must be emitted AND smaller (#850)."""
    v = pres.DEFAULTS[role]
    assert int(v["cpu_request"]) < int(v["cpu_limit"])
    assert int(v["memory_request"].removesuffix("Gi")) < int(
        v["memory_limit"].removesuffix("Gi"))


def test_defaults_used_when_nothing_stated():
    v, prov = pres.resolve_resources("decode", NONE)
    assert v == pres.DEFAULTS["decode"]
    assert all("default" in prov[k] for k in pres.KEYS)


def test_stated_value_wins_and_others_still_default():
    v, prov = pres.resolve_resources("decode", {**NONE, "cpu_limit": "64"})
    assert v["cpu_limit"] == "64"
    assert "config.md" in prov["cpu_limit"]
    assert v["memory_limit"] == pres.DEFAULTS["decode"]["memory_limit"]
    assert "default" in prov["memory_limit"]


def test_stated_values_pass_through_verbatim():
    """500m, 1.5 and Mi units must not be re-serialized into something else."""
    stated = {"cpu_limit": "500m", "memory_limit": "1536Mi",
              "cpu_request": "1.5", "memory_request": "512Mi"}
    v, prov = pres.resolve_resources("prefill", stated)
    assert v == stated


def test_used_any_default_distinguishes_the_two_cases():
    _, all_stated = pres.resolve_resources(
        "decode", {"cpu_limit": "1", "memory_limit": "2Gi",
                   "cpu_request": "3", "memory_request": "4Gi"})
    assert pres.used_any_default(all_stated) is False
    _, one_missing = pres.resolve_resources("decode", {**NONE, "cpu_limit": "1"})
    assert pres.used_any_default(one_missing) is True


def test_emitted_yaml_carries_both_limits_and_requests():
    v, prov = pres.resolve_resources("decode", NONE)
    r = parse(pres.resource_lines(v, prov, warn=True))["resources"]
    assert r["limits"] == {"memory": "128Gi", "cpu": "32"}
    assert r["requests"] == {"memory": "64Gi", "cpu": "16"}


def test_cpu_is_emitted_as_a_string():
    """Matches the framework default and both source scenarios; a bare int is a
    different YAML type than the chart's other cpu values."""
    v, prov = pres.resolve_resources("decode", NONE)
    r = parse(pres.resource_lines(v, prov, warn=True))["resources"]
    assert isinstance(r["limits"]["cpu"], str)
    assert isinstance(r["requests"]["cpu"], str)


def test_prefill_emits_under_the_prefill_block():
    v, prov = pres.resolve_resources("prefill", NONE)
    r = parse(pres.resource_lines(v, prov, warn=True), role="prefill")["resources"]
    assert r["limits"]["cpu"] == "8"


def test_warning_comment_names_the_starvation_signal():
    v, prov = pres.resolve_resources("decode", NONE)
    text = "\n".join(pres.resource_lines(v, prov, warn=True))
    assert "Reducing Torch parallelism" in text


def test_no_warning_comment_when_everything_was_stated():
    stated = {"cpu_limit": "1", "memory_limit": "2Gi",
              "cpu_request": "3", "memory_request": "4Gi"}
    v, prov = pres.resolve_resources("decode", stated)
    text = "\n".join(pres.resource_lines(v, prov, warn=False))
    assert "Reducing Torch parallelism" not in text
    assert parse(pres.resource_lines(v, prov, warn=False))["resources"]["limits"]["cpu"] == "1"


def test_stderr_warning_names_the_role_and_the_signal():
    v, prov = pres.resolve_resources("prefill", NONE)
    msg = pres.starvation_warning("prefill", v, prov)
    assert "prefill" in msg
    assert "Reducing Torch parallelism" in msg


def test_emitted_yaml_parses_for_every_role_and_warn_combination():
    """Hand-rolled emitter: indentation is the whole contract."""
    for role in ("decode", "prefill"):
        for warn in (True, False):
            v, prov = pres.resolve_resources(role, NONE)
            r = parse(pres.resource_lines(v, prov, warn=warn), role=role)["resources"]
            assert set(r) == {"limits", "requests"}


# ---------------------------------------------------------------------------
# Review findings from PR #857 (both `important`)
# ---------------------------------------------------------------------------

def test_warning_reports_the_emitted_values_not_the_default_table():
    """Finding 1: the warning fires when ANY quantity defaults, so printing the
    whole default table contradicted the YAML whenever the operator overrode part
    of it -- stating `cpu limit: 64` still announced 'limits 32 CPU', which reads
    as the override having been ignored."""
    v, prov = pres.resolve_resources("decode", {**NONE, "cpu_limit": "64"})
    msg = pres.starvation_warning("decode", v, prov)
    assert "32" not in msg, "warning still quotes the overridden default"
    assert "cpu_limit" not in msg, "warning names a quantity that was stated"
    assert "memory_limit=128Gi" in msg


def test_warning_names_only_the_defaulted_quantities():
    v, prov = pres.resolve_resources(
        "prefill", {**NONE, "cpu_limit": "9", "memory_limit": "20Gi"})
    msg = pres.starvation_warning("prefill", v, prov)
    assert "2 of 4" in msg
    assert "cpu_request=4" in msg and "memory_request=8Gi" in msg


def test_defaulted_keys_lists_only_the_missing_ones():
    _, prov = pres.resolve_resources("decode", {**NONE, "cpu_limit": "64"})
    assert pres.defaulted_keys(prov) == [
        "memory_limit", "cpu_request", "memory_request"]


def test_per_value_sources_distinguish_stated_from_default():
    """The honest form of the same fix: a header comment cannot say where four
    independently-resolved values came from, so each line carries its own."""
    v, prov = pres.resolve_resources("decode", {**NONE, "cpu_limit": "64"})
    text = "\n".join(pres.resource_lines(v, prov, warn=True))
    cpu_line = next(ln for ln in text.splitlines() if "cpu: '64'" in ln)
    mem_line = next(ln for ln in text.splitlines() if "memory: 128Gi" in ln)
    assert "config.md" in cpu_line
    assert "UNMEASURED" in mem_line


def test_header_comment_no_longer_claims_all_limits_are_upstream():
    """It said 'Limits are the llm-d pd-disaggregation guide's observed-working
    figures', which is false for any limit the operator overrode."""
    v, prov = pres.resolve_resources("decode", {**NONE, "cpu_limit": "64"})
    text = "\n".join(pres.resource_lines(v, prov, warn=True))
    assert "Limits are the llm-d" not in text


# --- request > limit sanity check (the review's recommendation) -------------

def test_request_exceeding_limit_is_reported():
    """Each quantity resolves independently, so a stated request with an unstated
    limit can exceed the default limit -- Kubernetes rejects that at admission."""
    v, _ = pres.resolve_resources("decode", {**NONE, "cpu_request": "40"})
    problems = pres.request_exceeds_limit(v)
    assert len(problems) == 1
    assert "request 40 > limit 32" in problems[0]


def test_defaults_never_trip_the_request_limit_check():
    for role in ("decode", "prefill"):
        v, _ = pres.resolve_resources(role, NONE)
        assert pres.request_exceeds_limit(v) == []


def test_request_limit_check_compares_matching_suffixes():
    v, _ = pres.resolve_resources(
        "decode", {**NONE, "memory_request": "200Gi", "memory_limit": "128Gi"})
    assert any("200Gi > limit 128Gi" in p for p in pres.request_exceeds_limit(v))


def test_request_limit_check_stays_silent_on_units_it_cannot_compare():
    """Conservative by design: a False must mean 'not proven', never 'verified
    fine'. Comparing 500m against 1 needs a real quantity parser."""
    v, _ = pres.resolve_resources(
        "decode", {**NONE, "cpu_request": "2000m", "cpu_limit": "1"})
    assert pres.request_exceeds_limit(v) == []

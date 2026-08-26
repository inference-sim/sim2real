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


def test_decode_default_request_is_below_its_limit():
    """requests are what the scheduler reserves; reserving decode's generous ceiling
    on every replica would make a multi-replica pool unschedulable."""
    v = pres.DEFAULTS["decode"]
    assert int(v["cpu_request"]) < int(v["cpu_limit"])
    assert int(v["memory_request"].removesuffix("Gi")) < int(
        v["memory_limit"].removesuffix("Gi"))


def test_prefill_default_request_equals_its_limit():
    """Guaranteed QoS, matching upstream. #848 mounts a `medium: Memory` tmpfs at
    /dev/shm sized 16Gi in vllmCommon (so both roles). tmpfs charges against pod
    memory, so a prefill request below that 16Gi would put the pod above its request
    as soon as collectives filled /dev/shm -- an eviction candidate under node
    pressure, mid-run, on exactly the TP>1 P/D configuration this targets."""
    v = pres.DEFAULTS["prefill"]
    assert v["memory_request"] == v["memory_limit"] == "16Gi"
    assert v["cpu_request"] == v["cpu_limit"] == "8"


@pytest.mark.parametrize("role", ["decode", "prefill"])
def test_every_default_is_a_valid_quantity(role):
    v, _, _ = pres.resolve_resources(role, NONE)
    assert pres.invalid_quantities(v) == []


# --- resolution ------------------------------------------------------------

def test_defaults_used_when_nothing_stated():
    v, prov, _ = pres.resolve_resources("decode", NONE)
    assert v == pres.DEFAULTS["decode"]
    assert set(prov.values()) == {pres.DEFAULTED}


def test_stated_wins_and_the_rest_still_default():
    v, prov, _ = pres.resolve_resources("decode", {**NONE, "cpu_limit": "64"})
    assert v["cpu_limit"] == "64"
    assert prov["cpu_limit"] == pres.STATED
    assert v["memory_limit"] == pres.DEFAULTS["decode"]["memory_limit"]
    assert prov["memory_limit"] == pres.DEFAULTED


def test_stated_values_pass_through_verbatim():
    """These are opaque Kubernetes quantities; re-serializing could change them."""
    stated = {"cpu_limit": "1500m", "memory_limit": "1536Mi",
              "cpu_request": "500m", "memory_request": "512Mi"}
    v, _, _ = pres.resolve_resources("prefill", stated)
    assert v == stated


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_blank_counts_as_unstated(blank):
    v, prov, _ = pres.resolve_resources("decode", {**NONE, "cpu_limit": blank})
    assert v["cpu_limit"] == pres.DEFAULTS["decode"]["cpu_limit"]
    assert prov["cpu_limit"] == pres.DEFAULTED


def test_defaulted_keys_lists_only_the_missing_ones():
    _, prov, _ = pres.resolve_resources("decode", {**NONE, "cpu_limit": "64"})
    # cpu_request is DERIVED from the stated limit, not defaulted.
    assert pres.defaulted_keys(prov) == ["memory_limit", "memory_request"]
    _, all_prov, _ = pres.resolve_resources("decode", ALL_STATED)
    assert pres.defaulted_keys(all_prov) == []


# --- quantity validation ---------------------------------------------------

@pytest.mark.parametrize(
    "good", ["32", "1", "0.5", "1.5", "500m", "128Gi", "1536Mi", "2Ti", "64G",
             "1e3", "100k"])
def test_valid_quantities_accepted(good):
    v, _, _ = pres.resolve_resources("decode", {**NONE, "memory_limit": good})
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
    v, _, _ = pres.resolve_resources("decode", {**NONE, "memory_limit": bad})
    if bad.strip():
        # Exactly one key: the request is NOT derived from an unreadable limit, so
        # the error points at the single offending row.
        assert pres.invalid_quantities(v) == [f"memory_limit={bad!r}"]
    else:
        assert pres.invalid_quantities(v) == []  # blank fell back to the default


# --- emission --------------------------------------------------------------

def test_emitted_yaml_carries_both_limits_and_requests():
    v, prov, _ = pres.resolve_resources("decode", NONE)
    r = parse(pres.resource_lines(v, prov, warn=True))["resources"]
    assert r["limits"] == {"memory": "128Gi", "cpu": "32"}
    assert r["requests"] == {"memory": "64Gi", "cpu": "16"}


@pytest.mark.parametrize("section", ["limits", "requests"])
@pytest.mark.parametrize("field", ["memory", "cpu"])
def test_every_emitted_value_is_a_quoted_string(section, field):
    """Unquoted, a YAML reader re-types these: `128` becomes an int, `yes` True,
    and `-` a sequence entry that will not parse at all. cpu was quoted and memory
    was not, which is how a `-` placeholder produced an unparseable baseline."""
    v, prov, _ = pres.resolve_resources("decode", NONE)
    r = parse(pres.resource_lines(v, prov, warn=True))["resources"]
    assert isinstance(r[section][field], str)


@pytest.mark.parametrize("hostile", ["128", "yes", "no", "null", "~", "0x40"])
def test_yaml_hostile_values_survive_as_strings(hostile):
    """Each of these is re-typed by YAML when unquoted -- to int, bool or None."""
    v, prov, _ = pres.resolve_resources("decode", {**NONE, "memory_limit": hostile})
    r = parse(pres.resource_lines(v, prov, warn=True))["resources"]
    assert r["limits"]["memory"] == hostile


def test_prefill_emits_under_the_prefill_block():
    v, prov, _ = pres.resolve_resources("prefill", NONE)
    r = parse(pres.resource_lines(v, prov, warn=True), role="prefill")["resources"]
    assert r["limits"]["cpu"] == "8"


def test_per_value_sources_distinguish_stated_from_default():
    v, prov, _ = pres.resolve_resources("decode", {**NONE, "cpu_limit": "64"})
    text = "\n".join(pres.resource_lines(v, prov, warn=True))
    cpu = next(ln for ln in text.splitlines() if "cpu: '64'" in ln)
    mem = next(ln for ln in text.splitlines() if "memory: '128Gi'" in ln)
    assert pres.STATED in cpu
    assert pres.DEFAULTED in mem


def test_source_comments_name_no_input_file():
    """Both generators share this module and read different inputs, so naming one
    would be wrong on the other path."""
    v, prov, _ = pres.resolve_resources("decode", {**NONE, "cpu_limit": "64"})
    text = "\n".join(pres.resource_lines(v, prov, warn=True))
    assert "config.md" not in text
    assert "vllm_args" not in text
    assert "{" not in text, "an unsubstituted template placeholder was emitted"


def test_warning_comment_names_the_starvation_signal():
    v, prov, _ = pres.resolve_resources("decode", NONE)
    assert "Reducing Torch parallelism" in "\n".join(
        pres.resource_lines(v, prov, warn=True))


def test_no_warning_comment_when_everything_was_stated():
    v, prov, _ = pres.resolve_resources("decode", ALL_STATED)
    text = "\n".join(pres.resource_lines(v, prov, warn=False))
    assert "Reducing Torch parallelism" not in text
    assert parse(pres.resource_lines(v, prov, warn=False))[
        "resources"]["limits"]["cpu"] == "1"


@pytest.mark.parametrize("role", ["decode", "prefill"])
@pytest.mark.parametrize("warn", [True, False])
def test_emitted_yaml_parses_in_every_combination(role, warn):
    """Hand-rolled emitter: indentation is the whole contract."""
    v, prov, _ = pres.resolve_resources(role, NONE)
    r = parse(pres.resource_lines(v, prov, warn=warn), role=role)["resources"]
    assert set(r) == {"limits", "requests"}


# --- stderr warning --------------------------------------------------------

def test_warning_reports_emitted_values_not_the_default_table():
    """It fires when ANY quantity defaults, so quoting the whole default table would
    contradict the YAML whenever the operator overrode part of it."""
    v, prov, _ = pres.resolve_resources("decode", {**NONE, "cpu_limit": "64"})
    msg = pres.starvation_warning("decode", v, prov)
    assert "cpu_limit" not in msg
    assert "memory_limit=128Gi" in msg
    assert "2 of 4" in msg  # cpu_request was derived, not defaulted


def test_warning_names_the_role_and_the_signal_and_no_input_file():
    v, prov, _ = pres.resolve_resources("prefill", NONE)
    msg = pres.starvation_warning("prefill", v, prov)
    assert "prefill" in msg
    assert "Reducing Torch parallelism" in msg
    assert "config.md" not in msg and "vllm_args" not in msg


# --- pair reconciliation (the must_fix from PR #857's review) ---------------
# The four quantities used to resolve independently, so a request could exceed its
# limit -- a pod spec Kubernetes rejects at admission. Worse, the input rows are
# shared by both roles while the defaults differ 4-8x, so a request sized for decode
# inverted prefill unconditionally.

def test_stating_a_limit_derives_a_matching_request():
    """A smaller stated limit must not leave the larger default request in place."""
    v, prov, notices = pres.resolve_resources(
        "decode", {**NONE, "cpu_limit": "8", "memory_limit": "32Gi"})
    assert v["cpu_request"] == "4"
    assert v["memory_request"] == "16Gi"
    assert prov["cpu_request"] == pres.DERIVED
    assert notices == []


def test_shared_request_row_is_clamped_to_prefills_smaller_limit():
    """The reproduction from the review: `cpu request: 20` is sensible for decode
    (limit 32) and impossible for prefill (limit 8). Both roles read the same row."""
    decode, _, d_notes = pres.resolve_resources(
        "decode", {**NONE, "cpu_request": "20", "memory_request": "96Gi"})
    prefill, p_prov, p_notes = pres.resolve_resources(
        "prefill", {**NONE, "cpu_request": "20", "memory_request": "96Gi"})

    assert decode["cpu_request"] == "20"          # fits decode's 32
    assert d_notes == []
    assert prefill["cpu_request"] == "8"          # clamped to prefill's limit
    assert prefill["memory_request"] == "16Gi"
    assert p_prov["cpu_request"] == pres.CLAMPED
    assert len(p_notes) == 2
    assert "exceeds its limit" in p_notes[0]
    assert "prefill" in p_notes[0]


@pytest.mark.parametrize("role", ["decode", "prefill"])
def test_no_input_can_produce_a_request_above_its_limit(role):
    """The invariant, over inputs that previously inverted it."""
    hostile = [
        {"cpu_request": "999", "memory_request": "999Gi"},
        {"cpu_limit": "1", "memory_limit": "1Gi"},
        {"cpu_limit": "1", "cpu_request": "64"},
        {"memory_limit": "2Gi", "memory_request": "128Gi"},
        {"cpu_limit": "500m", "cpu_request": "1"},
    ]
    for stated in hostile:
        v, _, _ = pres.resolve_resources(role, {**NONE, **stated})
        for kind in ("cpu", "memory"):
            req = pres._magnitude(v[f"{kind}_request"])
            lim = pres._magnitude(v[f"{kind}_limit"])
            assert req is not None and lim is not None
            assert req <= lim, f"{role} {kind}: {v} from {stated}"


def test_clamp_notice_explains_the_shared_row_and_names_the_role():
    _, _, notices = pres.resolve_resources("prefill", {**NONE, "cpu_request": "20"})
    assert len(notices) == 1
    note = notices[0]
    assert "prefill" in note
    assert "shared by both roles" in note
    assert "state a cpu limit too" in note


def test_both_stated_and_coherent_is_left_alone():
    v, prov, notices = pres.resolve_resources(
        "prefill", {**NONE, "cpu_limit": "16", "cpu_request": "12"})
    assert (v["cpu_limit"], v["cpu_request"]) == ("16", "12")
    assert prov["cpu_request"] == pres.STATED
    assert notices == []


# --- quantity grammar ------------------------------------------------------
# The regex was written from memory and was wrong in both directions: it rejected a
# valid `1Ki` outright while accepting `1ki`, `1.2.3`, `...`, `1e3Gi` and negatives.

@pytest.mark.parametrize(
    "quantity", ["1Ki", "524288Ki", "1Mi", "1Gi", "1Ti", "1Pi", "1Ei",
                 "100m", "1k", "1M", "1G", "1T", "1P", "1E", "32", "0.5", "1e3",
                 "1.5e2"])
def test_real_kubernetes_quantities_are_accepted(quantity):
    assert pres._magnitude(quantity) is not None, f"{quantity} wrongly rejected"


@pytest.mark.parametrize(
    "quantity", ["1ki", "1KI", "1e3Gi", "1.5e2Gi", "1.2.3", "1..2", "...", ".",
                 "1Gim", "32Gim", "-32", "-32Gi", "-0.5", "16 Gi", "128GB", "TBD"])
def test_non_quantities_are_rejected(quantity):
    assert pres._magnitude(quantity) is None, f"{quantity} wrongly accepted"


@pytest.mark.parametrize(
    "quantity,expected",
    [("1Ki", 1024.0), ("1Mi", 1048576.0), ("1k", 1000.0), ("500m", 0.5),
     ("32", 32.0), ("1e3", 1000.0), ("2Gi", 2147483648.0)])
def test_magnitudes_use_the_real_multipliers(quantity, expected):
    """Binary suffixes are powers of 1024, decimal ones powers of 1000 -- getting
    these backwards would make the clamp comparison silently wrong."""
    assert pres._magnitude(quantity) == expected


@pytest.mark.parametrize(
    "quantity,expected",
    [("128Gi", "64Gi"), ("32", "16"), ("8", "4"), ("500m", "250m"),
     ("1Gi", "0.5Gi"), ("1", "0.5")])
def test_halving_keeps_the_suffix(quantity, expected):
    assert pres._halve(quantity) == expected


@pytest.mark.parametrize("padded", [" 64 ", "\t64", "64\n"])
def test_whitespace_padded_stated_values_are_trimmed(padded):
    v, prov, _ = pres.resolve_resources("decode", {**NONE, "cpu_limit": padded})
    assert v["cpu_limit"] == "64"
    assert prov["cpu_limit"] == pres.STATED
    assert pres.invalid_quantities(v) == []


# --- review findings on the derivation branch (PR #857, round 3) ------------
# The derivation added to fix the previous round's must_fix introduced two narrower
# bugs of its own, both in the same shape: a value labelled as something it is not.

@pytest.mark.parametrize("limit", ["4e1", "1e3", "1.5e2"])
def test_exponent_limit_never_yields_a_falsely_derived_request(limit):
    """`_magnitude` accepts exponent notation via _EXPONENT_RE, which `_halve` does
    not match. The fallback returned the limit UNCHANGED while labelling it "half the
    stated limit" -- the YAML asserted a halving that never happened and the request
    reserved the full limit."""
    v, prov, _ = pres.resolve_resources("decode", {**NONE, "cpu_limit": limit})
    if prov["cpu_request"] == pres.DERIVED:
        assert pres._magnitude(v["cpu_request"]) < pres._magnitude(v["cpu_limit"]), (
            "labelled DERIVED but not actually smaller than the limit"
        )
    # Whatever path was taken, the pair must stay valid.
    assert pres._magnitude(v["cpu_request"]) <= pres._magnitude(v["cpu_limit"])


@pytest.mark.parametrize("quantity", ["4e1", "1e3", "1.5e2"])
def test_halve_declines_exponent_notation(quantity):
    assert pres._halve(quantity) is None


@pytest.mark.parametrize("quantity", ["10000000", "99999999"])
def test_halve_declines_when_the_result_would_change_notation(quantity):
    """`%g` switches to exponent form above six significant digits, so `10000000`
    would halve to `5e+06` -- textually unlike anything the operator wrote, breaking
    this module's promise not to reformat values."""
    assert pres._halve(quantity) is None


def test_no_emitted_value_ever_uses_exponent_notation():
    for limit in ("10000000", "4e1", "99999999", "128Gi", "32"):
        v, _, _ = pres.resolve_resources("decode", {**NONE, "cpu_limit": limit})
        for key in pres.KEYS:
            if v[key] != limit:  # a stated value passes through untouched
                assert "e" not in v[key].lower(), f"{key}={v[key]} from limit {limit}"


# --- prefill keeps Guaranteed QoS even when its limit is stated -------------

@pytest.mark.parametrize("limit", ["16Gi", "24Gi", "32Gi", "8Gi"])
def test_stated_prefill_memory_limit_keeps_request_equal_to_it(limit):
    """The invariant lived only in DEFAULTS['prefill'], so the DERIVED branch halved
    a stated limit unconditionally: a stated 24Gi limit produced a 12Gi request,
    below the 16Gi /dev/shm tmpfs #848 mounts, silently reintroducing the mid-run
    eviction risk this module argues against."""
    v, prov, _ = pres.resolve_resources("prefill", {**NONE, "memory_limit": limit})
    assert v["memory_request"] == limit
    assert prov["memory_request"] == pres.MATCHED


def test_matched_label_is_distinct_from_derived():
    """Reusing DERIVED here would be the same false-provenance bug one line up."""
    assert pres.MATCHED != pres.DERIVED
    assert "half" not in pres.MATCHED


def test_prefill_cpu_still_halves():
    """Only memory has the tmpfs concern; CPU keeps the ordinary derivation."""
    v, prov, _ = pres.resolve_resources("prefill", {**NONE, "cpu_limit": "16"})
    assert v["cpu_request"] == "8"
    assert prov["cpu_request"] == pres.DERIVED


def test_decode_memory_still_halves():
    """Decode has no shm floor to protect -- its request stays half the limit."""
    v, prov, _ = pres.resolve_resources("decode", {**NONE, "memory_limit": "32Gi"})
    assert v["memory_request"] == "16Gi"
    assert prov["memory_request"] == pres.DERIVED


def test_every_provenance_label_describes_its_value():
    """The class of bug behind both findings: a label asserting something the value
    does not satisfy. Checks every label against the value it annotates."""
    cases = [
        ("decode", {"memory_limit": "32Gi"}),
        ("decode", {"cpu_limit": "4e1"}),
        ("decode", {"cpu_limit": "10000000"}),
        ("prefill", {"memory_limit": "24Gi"}),
        ("prefill", {"cpu_limit": "16"}),
        ("prefill", {"cpu_request": "20"}),
        ("decode", {}),
    ]
    for role, stated in cases:
        v, prov, _ = pres.resolve_resources(role, {**NONE, **stated})
        for kind in ("cpu", "memory"):
            rk, lk = f"{kind}_request", f"{kind}_limit"
            src, req, lim = prov[rk], v[rk], v[lk]
            if src == pres.DERIVED:
                assert pres._magnitude(req) < pres._magnitude(lim), (
                    f"{role} {rk}: labelled '{src}' but {req} is not below {lim}")
            elif src == pres.MATCHED:
                assert req == lim, f"{role} {rk}: labelled '{src}' but {req} != {lim}"
            elif src == pres.CLAMPED:
                assert req == lim, f"{role} {rk}: labelled '{src}' but {req} != {lim}"
            elif src == pres.STATED:
                assert req == str(stated.get(rk, "")).strip()
            else:
                assert src == pres.DEFAULTED

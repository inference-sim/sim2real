#!/usr/bin/env python3
"""CPU/memory for the model-server pods (issue #850).

Bootstrap emitted no `resources`, so every bundle inherited the framework default:
`limits` AND `requests` both `{memory: 40Gi, cpu: "4"}` for each role
(llm-d-benchmark@76473d0 config/templates/values/defaults.yaml:848-858 decode,
:993-1000 prefill). Four CPU for a pod that may hold four GPUs, sharing its cgroup
with the routing sidecar that requests four more, starves vLLM. The signal is
`Reducing Torch parallelism from N threads to 1`, which surfaces as ITL noise --
corrupting the metric arms are compared on instead of failing loudly.

TWO NUMBERS PER RESOURCE, and Kubernetes requires request <= limit or the pod never
admits:

  limits    generous. Headroom costs nothing unless it is used.
  requests  what the scheduler actually reserves.

THE PAIR IS RECONCILED PER ROLE, which is the whole reason this module exists rather
than four independent lookups. The defaults differ by 4-8x between roles (decode
32 CPU / 128Gi, prefill 8 / 16Gi), while the input rows are shared and apply to both.
So a `cpu request: 20` row that is entirely sensible for decode exceeds prefill's
8-CPU limit. Resolving the four quantities independently emitted exactly that --
`requests {96Gi, 20}` against `limits {16Gi, 8}` -- an invalid pod spec, under a
comment claiming requests sit below limits. `resolve_resources` now clamps a request
to its own role's limit and says so, so an invalid pair cannot be expressed.

WHERE THE NUMBERS COME FROM. Limits are the observed-working figures from llm-d's
pd-disaggregation guide (config/scenarios/guides/pd-disaggregation.yaml:410-416
decode, :321-327 prefill), which pd-infocomm-2's baseline also carries; decode is
sized above prefill there, matching the operator's constraint.

Decode's request is half its limit -- headroom without reserving the ceiling on every
replica. PREFILL'S REQUEST EQUALS ITS LIMIT, matching upstream, because #848 mounts a
`medium: Memory` tmpfs at /dev/shm sized 16Gi in `vllmCommon` (both roles). tmpfs
charges against the pod's memory, so a prefill pod whose request was half its 16Gi
limit would sit above its request as soon as collectives filled /dev/shm, making it
an eviction candidate under node pressure mid-run. Guaranteed QoS is what upstream
uses there and is what protects it.

NOTHING HERE IS MEASURED on any cluster but the one the figures came from, at one
tensor-parallel degree and one model. Fixed starting values were chosen over a
derived formula, to be revised once there is data. `warn` keeps that visible in the
emitted YAML rather than letting generous-looking numbers read as authoritative.

Values are emitted QUOTED, both memory and cpu. They are opaque Kubernetes quantity
strings, and an unquoted one is re-typed by any YAML reader: `128` becomes an int,
`yes` becomes True, `-` becomes a sequence entry that will not parse at all.

Indentation contract: emitted lines sit INSIDE a role block (`decode:` / `prefill:`)
that the caller has already printed -- `resources:` at 4 spaces, `limits:`/
`requests:` at 6, their leaves at 8. Callers own blank-line separation.
"""

import re

KEYS = ("cpu_limit", "memory_limit", "cpu_request", "memory_request")

# Per-role fixed defaults. Limits verbatim from the upstream guide. Decode's request
# is half its limit; prefill's equals its limit -- see the /dev/shm note above.
DEFAULTS = {
    "decode": {
        "cpu_limit": "32",
        "memory_limit": "128Gi",
        "cpu_request": "16",
        "memory_request": "64Gi",
    },
    "prefill": {
        "cpu_limit": "8",
        "memory_limit": "16Gi",
        "cpu_request": "8",
        "memory_request": "16Gi",
    },
}

STATED = "operator-stated"
DEFAULTED = "sim2real-bootstrap default (UNMEASURED)"
DERIVED = "half the stated limit"
CLAMPED = "clamped to this role's limit"

# Kubernetes' quantity grammar (apimachinery/pkg/api/resource): a number with an
# optional binary (Ki..Ei) or decimal (m, k..E) suffix, OR a bare decimal exponent
# which cannot be combined with a suffix. Written from the grammar rather than from
# memory: an earlier version put lowercase `k` in the class but not the uppercase `K`
# that binary kilo uses, so it hard-rejected a valid `1Ki` while accepting `1ki`,
# `1.2.3`, `...`, `1e3Gi` and negatives.
_SUFFIX_MULTIPLIER = {
    "": 1.0, "m": 1e-3,
    "k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15, "E": 1e18,
    "Ki": 2 ** 10, "Mi": 2 ** 20, "Gi": 2 ** 30,
    "Ti": 2 ** 40, "Pi": 2 ** 50, "Ei": 2 ** 60,
}
_SUFFIXED_RE = re.compile(
    r"^(?P<num>[0-9]+(?:\.[0-9]+)?)(?P<suffix>Ki|Mi|Gi|Ti|Pi|Ei|m|k|M|G|T|P|E)?$"
)
_EXPONENT_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)?[eE][+-]?[0-9]+$")


def _magnitude(quantity: str) -> "float | None":
    """Numeric value of a Kubernetes quantity, or None if it is not one.

    Negative values parse per the grammar but are rejected by pod validation
    (`must be greater than or equal to 0`), so a leading sign is not accepted here.
    """
    text = quantity.strip()
    match = _SUFFIXED_RE.match(text)
    if match:
        return float(match.group("num")) * _SUFFIX_MULTIPLIER[
            match.group("suffix") or ""]
    if _EXPONENT_RE.match(text):
        return float(text)
    return None


def _halve(quantity: str) -> str:
    """Half a quantity, keeping its suffix. `128Gi` -> `64Gi`, `32` -> `16`."""
    match = _SUFFIXED_RE.match(quantity.strip())
    if not match:  # pragma: no cover - callers validate first
        return quantity
    halved = float(match.group("num")) / 2
    number = f"{halved:g}"
    return f"{number}{match.group('suffix') or ''}"


def invalid_quantities(values: dict) -> "list[str]":
    """Values that are not valid Kubernetes quantities.

    A pod carrying one is rejected at admission, far from the row that caused it, so
    this is checked at generation time instead.
    """
    return [f"{k}={values[k]!r}" for k in KEYS if _magnitude(values[k]) is None]


def resolve_resources(role: str, stated: dict) -> "tuple[dict, dict, list[str]]":
    """Resolve one role's limits and requests into a coherent pair.

    Per resource: the limit is what the input stated, else the role default. The
    request is what the input stated; failing that, half the limit when the limit was
    stated (the role's default request may not fit a limit the operator changed), else
    the role default request. The request is then clamped to the limit, because the
    shared input rows apply to both roles and a value sized for decode exceeds
    prefill's smaller limits.

    Absent, None and whitespace-only all count as unstated. Values pass through as
    strings and are never reformatted -- `32`, `500m`, `1.5`, `128Gi`, `1536Mi` are
    all valid quantities and re-serializing risks changing them.

    Returns (values, provenance, notices); notices describe any clamping, for the
    caller to print.
    """
    def clean(key):
        raw = stated.get(key)
        text = "" if raw is None else str(raw).strip()
        return text or None

    values, provenance, notices = {}, {}, []
    for kind in ("cpu", "memory"):
        limit_key, request_key = f"{kind}_limit", f"{kind}_request"

        limit = clean(limit_key)
        if limit:
            values[limit_key], provenance[limit_key] = limit, STATED
        else:
            values[limit_key] = DEFAULTS[role][limit_key]
            provenance[limit_key] = DEFAULTED

        request = clean(request_key)
        if request:
            values[request_key], provenance[request_key] = request, STATED
        elif limit and _magnitude(values[limit_key]) is not None:
            # Half the stated limit: the role default request may not fit a limit the
            # operator changed. Only when that limit is a readable quantity -- halving
            # an invalid one would propagate it and make the error name two keys
            # instead of the single bad row.
            values[request_key] = _halve(values[limit_key])
            provenance[request_key] = DERIVED
        else:
            values[request_key] = DEFAULTS[role][request_key]
            provenance[request_key] = DEFAULTED

        # Clamp only when both sides are readable quantities; invalid_quantities
        # reports the unreadable ones and the caller fails on those.
        req_mag = _magnitude(values[request_key])
        lim_mag = _magnitude(values[limit_key])
        if req_mag is not None and lim_mag is not None and req_mag > lim_mag:
            notices.append(
                f"{role} {kind} request {values[request_key]} exceeds its limit "
                f"{values[limit_key]}, so it was clamped to the limit. Kubernetes "
                f"rejects a pod whose request exceeds its limit. The CPU/memory rows "
                f"are shared by both roles, and {role}'s limits are "
                f"{DEFAULTS[role][limit_key]} by default -- state a "
                f"{kind} limit too if you need a larger request here."
            )
            values[request_key] = values[limit_key]
            provenance[request_key] = CLAMPED

    return values, provenance, notices


def defaulted_keys(provenance: dict) -> "list[str]":
    """Which quantities fell back to a role default, in KEYS order."""
    return [k for k in KEYS if provenance.get(k) == DEFAULTED]


def starvation_warning(role: str, values: dict, provenance: dict) -> str:
    """Generation-time warning naming the quantities that defaulted and their values.

    Reports what was actually emitted: printing the whole default table would
    contradict the YAML whenever the operator overrode part of it. Names no input
    file, because both generators share this and they read different inputs.
    """
    missing = defaulted_keys(provenance)
    shown = ", ".join(f"{k}={values[k]}" for k in missing)
    return (
        f"  WARNING: {role} has no stated value for {len(missing)} of {len(KEYS)} "
        f"CPU/memory quantities, so generous UNMEASURED defaults were emitted for "
        f"them ({shown}). They are headroom, not a measurement. Watch the {role} "
        f"logs for 'Reducing Torch parallelism from N threads to 1' -- that is CPU "
        f"starvation, and it surfaces as ITL noise rather than a failure. State "
        f"cpu/memory limit and request values in the bootstrap input to override."
    )


def resource_lines(values: dict, provenance: dict, *, warn: bool) -> "list[str]":
    """`resources` for one role block, as YAML lines.

    Emitted inside a role block the caller has already printed. Each value carries
    its own `# source` comment: the four are resolved independently, so a single
    header comment could not say where each came from. `warn` adds the
    unmeasured-defaults preamble; pass False when every quantity was stated.
    """
    lines = []
    if warn:
        lines += [
            "    # Some or all of these are GENEROUS DEFAULTS, NOT measured -- see",
            "    # the per-value sources. Defaults' limits come from llm-d's",
            "    # pd-disaggregation guide. Watch for 'Reducing Torch parallelism",
            "    # from N threads to 1' in the pod log: that is CPU starvation, and",
            "    # it shows up as ITL noise rather than a failure.",
            "    #",
            "    # requests never exceed limits -- Kubernetes rejects such a pod.",
            "    # Both halves are stated because the framework default sets both,",
            "    # so emitting only limits would leave requests at the inherited",
            "    # 40Gi/4.",
        ]
    lines.append("    resources:")
    for section, mem_key, cpu_key in (
        ("limits", "memory_limit", "cpu_limit"),
        ("requests", "memory_request", "cpu_request"),
    ):
        lines.append(f"      {section}:")
        lines.append(f"        memory: '{values[mem_key]}'  # {provenance[mem_key]}")
        lines.append(f"        cpu: '{values[cpu_key]}'  # {provenance[cpu_key]}")
    return lines

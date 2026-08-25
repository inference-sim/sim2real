#!/usr/bin/env python3
"""CPU/memory for the model-server pods (issue #850).

Bootstrap emitted no `resources`, so every bundle inherited the framework default:
`limits` AND `requests` both `{memory: 40Gi, cpu: "4"}` for each role
(llm-d-benchmark@76473d0 config/templates/values/defaults.yaml:848-858 decode,
:993-1000 prefill). Four CPU for a pod that may hold four GPUs, sharing its cgroup
with the routing sidecar that requests four more, starves vLLM. The signal is
`Reducing Torch parallelism from N threads to 1`, which surfaces as ITL noise --
corrupting the metric arms are compared on instead of failing loudly.

BOTH HALVES ARE EMITTED, and they differ:

  limits    generous. Headroom costs nothing unless it is used.
  requests  below the limit. This is what the scheduler actually reserves, and
            reserving the full generous ceiling on every replica would make a
            multi-replica pool unschedulable on a busy cluster.

Emitting both rather than limits alone is deliberate. The framework default sets
both, and a scenario is deep-merged over it, so emitting only `limits` would leave
`requests` at the inherited 40Gi/"4" -- a decode pod would end up asking for 40Gi
while permitted 128Gi. That pairing would be implicit and surprising; stating both
keeps it visible in one place.

WHERE THE NUMBERS COME FROM. The limits are the observed-working figures from
llm-d's pd-disaggregation guide (config/scenarios/guides/pd-disaggregation.yaml:410-416
decode, :321-327 prefill), which pd-infocomm-2's baseline also carries. Decode is
sized above prefill there, matching the operator's constraint. Requests are half
those limits -- a stated default, not a measurement.

NOTHING HERE IS MEASURED on any cluster but the one the figures came from, at one
tensor-parallel degree and one model. That is deliberate: fixed starting values were
chosen over a derived formula, to be revised once there is data. `warn` keeps that
visible in the emitted YAML rather than letting generous-looking numbers read as
authoritative.

Values are emitted QUOTED, both memory and cpu. They are opaque Kubernetes quantity
strings, and an unquoted one is re-typed by any YAML reader: `128` becomes an int,
`yes` becomes True, `-` becomes a list item that will not parse at all.

Indentation contract: emitted lines sit INSIDE a role block (`decode:` / `prefill:`)
that the caller has already printed -- `resources:` at 4 spaces, `limits:`/
`requests:` at 6, their leaves at 8. Callers own blank-line separation.
"""

import re

# The four quantities, in emission order within each of limits/requests.
KEYS = ("cpu_limit", "memory_limit", "cpu_request", "memory_request")

# Per-role fixed defaults. Limits verbatim from the upstream guide; requests half.
# decode > prefill on every quantity -- the operator's constraint and upstream's own
# sizing.
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
        "cpu_request": "4",
        "memory_request": "8Gi",
    },
}

STATED = "operator-stated"
DEFAULTED = "sim2real-bootstrap default (UNMEASURED)"

# Kubernetes' own quantity grammar (apimachinery/pkg/api/resource). Deliberately not
# a parser -- just enough to reject what the cluster would reject at admission, so a
# `TBD`, `N/A`, `32 cores` or `16 Gi` row fails here rather than on the cluster far
# from the row that caused it.
_QUANTITY_RE = re.compile(r"^[+-]?[0-9.]+(?:[eE][+-]?[0-9]+)?[EPTGMk]?i?m?$")


def resolve_resources(role: str, stated: dict) -> "tuple[dict, dict]":
    """Resolve one role's four quantities: the stated value, else the role default.

    `stated` maps each of KEYS to what the input declared, or None. Absent, None and
    whitespace-only all count as unstated. Values pass through as strings and are
    never parsed -- `32`, `500m`, `1.5`, `128Gi`, `1536Mi` are all valid Kubernetes
    quantities and re-serializing risks changing them.

    Returns (values, provenance), both keyed by KEYS.
    """
    values, provenance = {}, {}
    for key in KEYS:
        given = stated.get(key)
        text = "" if given is None else str(given).strip()
        if text:
            values[key], provenance[key] = text, STATED
        else:
            values[key], provenance[key] = DEFAULTS[role][key], DEFAULTED
    return values, provenance


def invalid_quantities(values: dict) -> "list[str]":
    """Stated values that are not valid Kubernetes quantities.

    Only stated values can be invalid -- the defaults are known-good -- but this
    checks all four so a bad default could never slip through either.
    """
    return [
        f"{key}={values[key]!r}"
        for key in KEYS
        if not _QUANTITY_RE.match(values[key])
    ]


def defaulted_keys(provenance: dict) -> "list[str]":
    """Which quantities fell back to a default, in KEYS order."""
    return [k for k in KEYS if provenance.get(k) == DEFAULTED]


def starvation_warning(role: str, values: dict, provenance: dict) -> str:
    """Generation-time warning naming the quantities that defaulted, and their values.

    Reports what was actually emitted. Printing the whole default table would
    contradict the YAML whenever the operator overrode part of it. Names no input
    file: both generators share this, and they read different inputs.
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
    its own `# source` comment: the four resolve independently, so a single header
    comment could not say which came from where. `warn` adds the unmeasured-defaults
    preamble; pass False when every quantity was stated.
    """
    lines = []
    if warn:
        lines += [
            "    # Some or all of these are GENEROUS DEFAULTS, NOT measured -- see",
            "    # the per-value sources. Defaults' limits come from llm-d's",
            "    # pd-disaggregation guide; their requests are half that. Watch for",
            "    # 'Reducing Torch parallelism from N threads to 1' in the pod log:",
            "    # that is CPU starvation, and it shows up as ITL noise rather than",
            "    # a failure.",
            "    #",
            "    # requests sit below limits so the scheduler reserves less than the",
            "    # ceiling. Both halves are stated because the framework default",
            "    # sets both, and emitting only limits would leave requests at the",
            "    # inherited 40Gi/4.",
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

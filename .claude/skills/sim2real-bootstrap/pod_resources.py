#!/usr/bin/env python3
"""CPU/memory for the model-server pods (issue #850).

Bootstrap emitted no `resources` at all, so every bundle inherited the framework
default -- `limits: {memory: 40Gi, cpu: "4"}` with no `requests`
(llm-d-benchmark@76473d0 config/templates/values/defaults.yaml:848-851 decode,
:994-997 prefill). Four CPU for a pod that may hold four GPUs, sharing its cgroup
with the routing sidecar that requests four more, starves vLLM. The signal is
`Reducing Torch parallelism from N threads to 1`, which surfaces as ITL noise --
so it corrupts the metric arms are compared on instead of failing loudly.

THE limits/requests TRAP. `limits` without `requests` is not "no reservation":
Kubernetes copies the limit into the request when the request is absent. So a
decode block carrying `limits: {128Gi, 32}` and no `requests` reserves 128Gi and
32 CPU per replica before prefill is scheduled at all -- on a busy cluster an
unschedulable pod, which reads as a capacity problem rather than a config one.
Both halves are therefore emitted, and they differ:

  limits    generous. Headroom costs nothing unless it is used.
  requests  modest and EXPLICIT. This is what the scheduler actually reserves.

WHERE THE NUMBERS COME FROM. The limits are the observed-working figures from
llm-d's pd-disaggregation guide (config/scenarios/guides/pd-disaggregation.yaml:410-416
decode, :321-327 prefill), which pd-infocomm-2's baseline also carries. Decode is
sized above prefill there, matching the operator's constraint that decode needs
more. Requests are half those limits -- a stated default, not a measurement.

NOTHING HERE IS MEASURED on any cluster but the one the figures came from, at one
tensor-parallel degree and one model. That is deliberate: the operator chose fixed
starting values over a derived formula, to be revised once there is data. The
`warn` path exists to keep that visible in the emitted YAML rather than letting
generous-looking numbers read as authoritative.

Deliberately NOT scaled by GPU count. `tensor x dataLocal` was considered and
rejected for now: it would reproduce one cluster's decode block at TP=4 while
inventing every other cell of the table. Fixed values are wrong in a way an
operator can see and correct; a formula is wrong in a way that looks principled.

Indentation contract: emitted lines sit INSIDE a role block (`decode:` /
`prefill:`) that the caller has already printed -- `resources:` at 4 spaces, its
`limits:`/`requests:` at 6, their leaves at 8. Callers own blank-line separation.
"""

# The four quantities, in emission order within each of limits/requests.
KEYS = ("cpu_limit", "memory_limit", "cpu_request", "memory_request")

# Per-role fixed defaults. Limits verbatim from the upstream guide; requests half.
# decode > prefill on every quantity -- the operator's constraint and upstream's
# own sizing. CPU values are strings: the chart's other cpu values are quoted, and
# a bare int is a different YAML type.
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

_DEFAULT_SOURCE = (
    "sim2real-bootstrap default (generous, UNMEASURED; limits from llm-d "
    "pd-disaggregation guide, requests half)"
)
_STATED_SOURCE = "config.md row"


def resolve_resources(
    role: str, stated: "dict[str, str | None]"
) -> "tuple[dict, dict]":
    """Resolve one role's four quantities, preferring stated values.

    `stated` maps each of KEYS to the value the input declared, or None. Values
    pass through as strings and are never parsed: `32`, `500m`, `1.5`, `128Gi` and
    `1536Mi` are all valid Kubernetes quantities, and re-serializing risks
    changing them.

    Returns (values, provenance), both keyed by KEYS.
    """
    defaults = DEFAULTS[role]
    values: dict = {}
    provenance: dict = {}
    for key in KEYS:
        given = stated.get(key)
        if given is not None and str(given).strip():
            values[key] = str(given).strip()
            provenance[key] = _STATED_SOURCE
        else:
            values[key] = defaults[key]
            provenance[key] = _DEFAULT_SOURCE
    return values, provenance


def used_any_default(provenance: dict) -> bool:
    """True when at least one quantity fell back to a default.

    Drives both warnings: an input that states all four gets no warning, because
    then the numbers are the operator's and saying they are unmeasured would be
    both wrong and noise.
    """
    return any(src == _DEFAULT_SOURCE for src in provenance.values())


def starvation_warning(role: str) -> str:
    """The generation-time (stderr) warning text, shared by both generators."""
    return (
        f"  WARNING: no CPU/memory rows for {role}, so generous UNMEASURED "
        f"defaults were emitted (limits "
        f"{DEFAULTS[role]['cpu_limit']} CPU / {DEFAULTS[role]['memory_limit']}, "
        f"requests {DEFAULTS[role]['cpu_request']} CPU / "
        f"{DEFAULTS[role]['memory_request']}). They are headroom, not a "
        f"measurement. Watch the {role} logs for 'Reducing Torch parallelism "
        f"from N threads to 1' -- that is CPU starvation, and it surfaces as ITL "
        f"noise rather than a failure. State '{role} cpu limit' and "
        f"'{role} memory limit' rows in config.md to override."
    )


def resource_lines(values: dict, *, warn: bool) -> "list[str]":
    """`resources` for one role block, as YAML lines.

    Emitted inside a role block the caller has already printed. `warn` adds the
    unmeasured-defaults comment; pass False when every quantity was stated.
    """
    lines: list[str] = []
    if warn:
        lines += [
            "    # Generous headroom, NOT measured. Limits are the llm-d",
            "    # pd-disaggregation guide's observed-working figures; requests are",
            "    # half of them. Watch for 'Reducing Torch parallelism from N",
            "    # threads to 1' in the pod log -- that is CPU starvation, and it",
            "    # shows up as ITL noise rather than a failure.",
            "    #",
            "    # requests are stated explicitly on purpose: omitting them does",
            "    # NOT mean 'no reservation'. Kubernetes copies the limit into the",
            "    # request when the request is absent, so limits-only would reserve",
            "    # the whole generous figure on every replica.",
            "    #",
            "    # `resources` is a mapping, so a downstream baseline overriding",
            "    # e.g. limits.cpu deep-merges key by key -- the requests below",
            "    # survive. That differs from the scalar lists elsewhere in this",
            "    # skill, which are replaced wholesale.",
        ]
    lines += [
        "    resources:",
        "      limits:",
        f"        memory: {values['memory_limit']}",
        f"        cpu: '{values['cpu_limit']}'",
        "      requests:",
        f"        memory: {values['memory_request']}",
        f"        cpu: '{values['cpu_request']}'",
    ]
    return lines

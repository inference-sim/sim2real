#!/usr/bin/env python3
"""Generate llm-d-benchmark baseline scenario YAML from config.md markdown tables.

Parses the vLLM configuration table in config.md, applies lookup tables and
default rules, and writes a scenario YAML with provenance comments showing
where each value originated.

The output filename is always ``baseline.yaml`` (issue #544 — the baseline
identifier in transfer.yaml is the literal string ``baseline`` regardless of
project). The ``-n/--name`` flag overrides only the *inner* ``scenario: -
name:`` label inside the emitted YAML, which is a separate concept used by
the benchmark harness.

Usage:
    python3 generate_from_config.py [config.md] [-o baselines/] [-n label] [--dry-run]
"""

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Lookup tables
# ---------------------------------------------------------------------------

MODEL_METADATA = {
    "meta-llama/Llama-3.1-8B": {
        "shortName": "meta-llama-llama-3-1-8b",
        "path": "models/meta-llama/Llama-3.1-8B",
        "size": "1Ti",
        "maxModelLen": 131072,
    },
    "Qwen/Qwen3-14B": {
        "shortName": "qwen-qwen3-14b",
        "path": "models/Qwen/Qwen3-14B",
        "size": "1Ti",
        "maxModelLen": 40960,
    },
    "Qwen/Qwen2.5-14B-Instruct-1M": {
        "shortName": "qwen2-5-14b-instruct-1m",
        "path": "models/Qwen/Qwen2.5-14B-Instruct-1M",
        "size": "1Ti",
        "maxModelLen": 131072,  # H100-80GB KV feasibility cap (25.8GB/req); model native ~1,010,000
    },
}

HARDWARE_LABELS = {
    "H100_SXM_80GB": "NVIDIA-H100-80GB-HBM3",
    "A100_SXM_80GB": "NVIDIA-A100-SXM4-80GB",
    "A100_PCIE_40GB": "NVIDIA-A100-PCIE-40GB",
}

# Maps canonical field name -> set of recognized aliases (lowercased)
PARAMETER_ALIASES = {
    "model": {"model", "--model"},
    "hardware": {"gpu", "hardware"},
    "tensor_parallel_size": {"tensor_parallel_size", "--tensor-parallel-size", "tp"},
    "max_num_seqs": {"max_num_seqs", "--max-num-seqs"},
    "max_num_batched_tokens": {"max_num_batched_tokens", "--max-num-batched-tokens"},
    "block_size": {"block_size", "--block-size", "block_size_in_tokens"},
    "gpu_memory_utilization": {"gpu_memory_utilization", "--gpu-memory-utilization"},
    "max_model_len": {"max_model_len", "--max-model-len", "max_seq_len"},
    "enable_chunked_prefill": {"enable_chunked_prefill", "--enable-chunked-prefill"},
    "enable_prefix_caching": {"enable_prefix_caching", "--enable-prefix-caching"},
    # Negative bare flag: presence (with empty value column) means caching OFF.
    # Folded into enable_prefix_caching at the end of extract_fields.
    "__no_enable_prefix_caching__": {"--no-enable-prefix-caching"},
    "replicas": {
        "number of pods",
        "number of vllm pods",   # BLIS experiment folders use this exact label (issue #549)
        "number of decode pods",  # anticipate variant label names
        "number of decode instances",
        "decode replicas",        # symmetric with prefill_replicas' "prefill replicas"
        "decode instances",
        "instances",
        "replicas",
        "num_instances",
    },
    # Per-role overrides (issue #824). All optional: a config.md with none of
    # these produces exactly the single-`decode:` output it always has, so
    # existing bundles regenerate byte-identically.
    #
    # `replicas` above stays the decode/aggregated count -- it is the one every
    # existing bundle uses -- and `hardware` stays the required shared GPU. These
    # rows only add a second role and let either role name a different GPU.
    "prefill_replicas": {
        "number of prefill pods",
        "number of prefill instances",
        "prefill replicas",
        "prefill_replicas",
        "prefill instances",
    },
    "prefill_hardware": {"prefill gpu", "prefill hardware"},
    "decode_hardware": {"decode gpu", "decode hardware"},
    "dtype": {"dtype", "--dtype"},
    "pipeline_parallel_size": {"pipeline_parallel_size", "--pipeline-parallel-size"},
    "data_parallel_size": {"data_parallel_size", "--data-parallel-size"},
    "swap_space": {"swap_space", "--swap-space"},
    "enforce_eager": {"enforce_eager", "--enforce-eager"},
}

# Section heading keywords that indicate a vLLM configuration table
VLLM_SECTION_KEYWORDS = [
    "vllm pod configuration",
    "vllm server arguments",
    "real deployment",
    "pod configuration",
    "vllm configuration",
]

# Fields whose presence in a table signals it's the vLLM config table
VLLM_INDICATOR_FIELDS = {"model", "max_num_seqs", "hardware", "replicas", "gpu_memory_utilization"}

# ---------------------------------------------------------------------------
# blis observe → blis_observe:  (issue #403)
# ---------------------------------------------------------------------------

OBSERVE_TUNING_FLAGS = {
    "--max-concurrency": "maxConcurrency",
    "--timeout": "timeout",
    "--warmup-requests": "warmupRequests",
    "--prewarm-duration": "prewarmDuration",
}

# Hardcoded by tekton/tasks/run-workload-blis-observe-binary.yaml — the block
# in config.md typically lists them for readability but the Tekton task
# supplies them at runtime, so they must NOT leak into extraArgs.
#
# The corpus-mode trio (--corpus-header/--corpus-data and the pool flags
# --concurrent-sessions/--total-sessions) is injected by the task's trace-mode
# branch from the trace workload's descriptor (tracePath + pool block via
# assemble), NOT from config.md. Listing them here keeps a config.md block that
# spells them out (for readability) from double-injecting them into extraArgs.
# --trace-header/--trace-data are the OUTPUT flags the task always injects and
# stay listed. --session-mode is NOT in this set (the task does not inject it):
# it is not a blis observe flag at all, so it is dropped by the
# OBSERVE_VALID_FLAGS allowlist guardrail below rather than here (issue #602).
OBSERVE_PIPELINE_INJECTED_FLAGS = {
    "--server-url",
    "--model",
    "--workload-spec",
    "--trace-header",
    "--trace-data",
    "--saturation-report",
    "--post-hoc-detector",
    "--corpus-header",
    "--corpus-data",
    "--concurrent-sessions",
    "--total-sessions",
}

# The complete set of flags `blis observe` accepts, from inference-sim
# @ 583f7195 (PR #1499, corpus-mode). This is the allowlist: a flag in
# config.md's observe block is written to transfer.yaml only if it appears here
# (as a first-class OBSERVE_TUNING_FLAGS key, or — for anything without a
# dedicated key — verbatim in extraArgs). Anything NOT here is dropped, never
# transcribed (issue #602).
#
# Two source sites, so both must be regenerated in lockstep when observe's flags
# change (the second is easy to miss):
#   1. cmd/observe_cmd.go — flags registered directly on observeCmd. Match EVERY
#      pflag setter, including Int64Var/Float64Var/DurationVar, not just Int/String.
#   2. cmd/root.go:registerSaturationFlags(observeCmd) — the --saturation-* /
#      backlog-drift block, shared with `blis run`/`blis replay` and registered
#      on observe via that helper call (observe_cmd.go).
OBSERVE_VALID_FLAGS = {
    # --- cmd/observe_cmd.go (registered directly on observeCmd) ---
    "--api-format", "--api-key", "--concurrency", "--concurrent-sessions",
    "--corpus-data", "--corpus-header", "--defaults-filepath", "--horizon",
    "--itl-output", "--lazy-generation", "--max-concurrency", "--model",
    "--no-streaming", "--num-requests", "--output-tokens", "--output-tokens-max",
    "--output-tokens-min", "--output-tokens-stdev", "--post-hoc-detector",
    "--prefix-tokens", "--prewarm-duration", "--prompt-tokens",
    "--prompt-tokens-max", "--prompt-tokens-min", "--prompt-tokens-stdev",
    "--rate", "--record-itl", "--rtt-ms", "--saturation-report",
    "--saturation-threshold-ms", "--seed", "--server-type", "--server-url",
    "--session-id-header", "--slo-e2e", "--slo-itl", "--slo-ttft",
    "--think-time-dist", "--think-time-ms", "--timeout", "--total-sessions",
    "--trace-data", "--trace-header", "--unconstrained-output",
    "--warmup-requests", "--workload", "--workload-spec",
    # --- cmd/root.go:registerSaturationFlags (attached to observeCmd) ---
    "--saturation-ci", "--saturation-classifier",
    "--saturation-drain-ratio-saturated", "--saturation-drain-ratio-transient",
    "--saturation-min-windows", "--saturation-peak-band",
    "--saturation-peak-ratio", "--saturation-tail-windows",
    "--saturation-warmup-windows", "--saturation-window",
}

# Flags that belong to `blis replay` (the simulator's load generator) or to
# `blis run`'s model-of-the-world, and have NO `blis observe` equivalent. An
# operator hand-authoring config.md by transcribing the sim's `blis replay`
# invocation will list these; they must be dropped, never transcribed into the
# observe command (issue #602). --session-mode is the canonical case: observe
# infers closed-loop from the session pool, so the flag does not exist and
# `blis observe` aborts with "unknown flag: --session-mode" if it leaks. This
# set exists only to emit a precise warning — any flag absent from
# OBSERVE_VALID_FLAGS is dropped regardless of whether it is listed here.
OBSERVE_REPLAY_ONLY_FLAGS = {
    "--session-mode", "--results-path", "--trace-output",  # blis replay load/output
    "--num-instances", "--routing-policy",                 # sim routing
    "--total-kv-blocks", "--hardware", "--tp",             # sim hardware/model
}

# Match pipeline/pipeline.yaml:36-50. Update in lockstep if those defaults
# ever change.
OBSERVE_DEFAULTS = {
    "maxConcurrency": "10000",
    "timeout": "1800",
    "warmupRequests": "50",
    "prewarmDuration": "60s",
    "extraArgs": "",
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ProvenanceValue:
    value: object
    source: str
    raw_param: str = ""


@dataclass
class TableSection:
    heading: str
    rows: list = field(default_factory=list)
    line_number: int = 0


# ---------------------------------------------------------------------------
# Markdown table parsing
# ---------------------------------------------------------------------------

def normalize_cell(raw: str) -> str:
    """Strip whitespace, backticks, and surrounding quotes from a table cell."""
    s = raw.strip()
    if s.startswith("`") and s.endswith("`"):
        s = s[1:-1]
    if s.startswith('"') and s.endswith('"'):
        s = s[1:-1]
    if s.startswith("'") and s.endswith("'"):
        s = s[1:-1]
    return s.strip()


def is_separator_row(line: str) -> bool:
    """Check if a line is a markdown table separator (|---|---|)."""
    return bool(re.match(r"^\s*\|[\s\-:|]+\|\s*$", line))


def parse_table_row(line: str) -> list[str]:
    """Split a pipe-delimited row into cells."""
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [cell.strip() for cell in line.split("|")]


def parse_md_tables(lines: list[str]) -> list[TableSection]:
    """Find all markdown tables in the file, grouped by their nearest heading."""
    tables = []
    current_heading = ""
    i = 0

    while i < len(lines):
        line = lines[i]

        # Track headings
        heading_match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading_match:
            current_heading = heading_match.group(2).strip()
            i += 1
            continue

        # Detect table start: a line with pipes that's followed by a separator
        if "|" in line and not is_separator_row(line):
            if i + 1 < len(lines) and is_separator_row(lines[i + 1]):
                # Parse header
                headers = [normalize_cell(c) for c in parse_table_row(line)]
                i += 2  # skip header and separator

                rows = []
                while i < len(lines) and "|" in lines[i] and not is_separator_row(lines[i]):
                    cells = [normalize_cell(c) for c in parse_table_row(lines[i])]
                    if len(cells) >= len(headers):
                        cells = cells[: len(headers)]
                    else:
                        cells.extend([""] * (len(headers) - len(cells)))
                    row = dict(zip(headers, cells))
                    rows.append(row)
                    i += 1

                tables.append(TableSection(heading=current_heading, rows=rows, line_number=i))
                continue

        i += 1

    return tables


def canonicalize_parameter(raw: str) -> str | None:
    """Resolve a raw parameter name from config.md to its canonical form."""
    cleaned = normalize_cell(raw).lower().strip()
    for canonical, aliases in PARAMETER_ALIASES.items():
        if cleaned in aliases:
            return canonical
    return None


# A parameter label that names a replica count but matches no alias. Before
# issue #824 such a row was skipped in silence, so `| Number of prefill pods |`
# read as twelve rows and eleven extracted fields with exit 0 -- the operator's
# stated topology discarded with no diagnostic.
#
# Deliberately narrow, and only ever applied to the PARAMETER column of the one
# table find_vllm_table selected. A file-wide or value-column check would fire on
# every known-good bundle: `| 4 instances |` is a value cell in
# admission-control-pf's BLIS mapping table, `| --num-instances |` is a flag row
# in three others, and a simulation->deployment mapping table legitimately has
# `| prefill replicas |` in its parameter column. None of those is the vLLM table.
# A replica COUNT is the head of its label: the label ends with the counted noun,
# optionally followed by "count" ("Number of prefill pods", "prefill replicas",
# "Prefill pod count", "Instances"). Singular and plural both, since an operator
# writing one pod says "pod".
_COUNT_NOUN_TAIL_RE = re.compile(r"\b(?:pods?|instances?|replicas?|nodes?)(?:\s+count)?$")

# A RATIO mentions the same nouns but is describing a per-unit quantity, not a
# fleet size: "Pods per node", "Pods per GPU", "GPUs per pod". These are
# informational rows that this generator has always ignored, and treating them as
# malformed replica counts would reject config.md files that work on main.
_RATIO_LABEL_RE = re.compile(r"\b(?:per|each)\s+\S+$")


def is_unrecognized_replica_label(raw: str) -> bool:
    """True when a vLLM-table parameter label names a replica count we cannot map.

    Two exclusions keep this from firing on rows it has no business rejecting:

    - Labels beginning with `-` are CLI flags being documented (`--num-instances`),
      not parameters this generator resolves.
    - Ratio labels (`Pods per node`) mention a counted noun without naming a fleet
      size. Erroring on them would both break working inputs and offer advice
      ("use `number of pods`") that is wrong for the row.

    "workers" is deliberately NOT a counted noun here: it collides with
    `parallelism.workers`, which derives from tensor_parallel_size rather than a
    replica count, so flagging it would emit the same misleading guidance.
    """
    cleaned = normalize_cell(raw).lower().strip()
    if not cleaned or cleaned.startswith("-"):
        return False
    if canonicalize_parameter(raw) is not None:
        return False
    if _RATIO_LABEL_RE.search(cleaned):
        return False
    return bool(_COUNT_NOUN_TAIL_RE.search(cleaned))


# Separators an operator might use to name several GPU types in one cell.
_GPU_LIST_SPLIT_RE = re.compile(r"\s*(?:,|/|\+|\band\b)\s*", re.IGNORECASE)


def split_hardware_cell(raw: str) -> list[str]:
    """Split a GPU cell into the types it names. Single type -> one element."""
    cleaned = normalize_cell(raw)
    if not cleaned:
        return []
    return [part for part in _GPU_LIST_SPLIT_RE.split(cleaned) if part]


def warn_role_rows_outside_vllm_table(
    tables: list[TableSection],
    vllm_table: TableSection,
    already_extracted: set[str] | None = None,
) -> list[str]:
    """Warn when per-role rows sit in a table this generator does not read.

    Only the single table `find_vllm_table` selects is machine-read. A `config.md`
    that states its prefill count in some other table -- a simulation -> deployment
    mapping table, say -- produced a decode-only baseline with no diagnostic at
    all: the decode row IS recognized, so the unrecognized-row check never fires,
    and the prefill row is simply never seen (issue #824 review).

    These rows are NOT consumed from the other table, deliberately. A mapping
    table's columns mean something else: in `pd-infocomm-2/config.md` the row is
    `| prefill replicas | --prefill-instances | 1 | yes |`, so column 1 is the
    simulator flag name, not a count. Reading it would substitute silent garbage
    for a silent omission. The fix an operator needs is to move the row into the
    vLLM table, and that is what the warning says.

    A field already extracted from the vLLM table is NOT warned about. A
    simulation -> deployment mapping table is a required part of a well-formed
    config.md -- it exists so the two dialects can be audited against each other --
    so a bundle that correctly states its counts in the vLLM table and also
    documents them there would otherwise draw a warning on every run. The warning
    is for values stated ONLY where they cannot take effect.

    Returns the warning lines emitted, for testability.
    """
    role_fields = {"prefill_replicas", "prefill_hardware", "decode_hardware", "replicas"}
    satisfied = already_extracted or set()
    emitted = []
    for table in tables:
        if table is vllm_table:
            continue
        for row in table.rows:
            if not row:
                continue
            raw_param = list(row.values())[0]
            canonical = canonicalize_parameter(raw_param)
            if canonical not in role_fields or canonical in satisfied:
                continue
            msg = (
                f"  WARNING: '{normalize_cell(raw_param)}' appears under "
                f"\"{table.heading}\", which is not the machine-read table. Only "
                f"\"{vllm_table.heading}\" is parsed, so this row has NO effect on the "
                f"generated baseline. Move it into that table for it to take effect."
            )
            print(msg, file=sys.stderr)
            emitted.append(msg)
    return emitted


def find_vllm_table(tables: list[TableSection]) -> TableSection | None:
    """Select the table most likely to contain vLLM pod configuration."""
    # First pass: match by section heading
    for table in tables:
        heading_lower = table.heading.lower()
        if any(kw in heading_lower for kw in VLLM_SECTION_KEYWORDS):
            return table

    # Second pass: match by content (table with most vLLM indicator fields)
    best = None
    best_score = 0
    for table in tables:
        score = 0
        for row in table.rows:
            first_col = list(row.values())[0] if row else ""
            canonical = canonicalize_parameter(first_col)
            if canonical and canonical in VLLM_INDICATOR_FIELDS:
                score += 1
        if score > best_score:
            best = table
            best_score = score

    return best


# ---------------------------------------------------------------------------
# Field extraction
# ---------------------------------------------------------------------------

def parse_boolean(raw: str) -> bool | None:
    """Parse a boolean value, handling annotations like '(true required...)'."""
    lower = raw.lower().strip()
    if lower in ("true", "yes", "1"):
        return True
    if lower in ("false", "no", "0"):
        return False
    # Check for boolean inside parenthetical
    if "true" in lower:
        return True
    if "false" in lower:
        return False
    return None


def parse_numeric(raw: str) -> int | float | None:
    """Parse a numeric value, stripping any trailing annotations."""
    # Take only the first token (before any spaces/notes)
    token = raw.split()[0] if raw.split() else raw
    try:
        if "." in token:
            return float(token)
        return int(token)
    except ValueError:
        return None


def extract_fields(table: TableSection) -> dict[str, ProvenanceValue]:
    """Extract canonical fields from a parsed table, with provenance tracking."""
    fields = {}
    # Determine which column holds the parameter name (usually first)
    if not table.rows:
        return fields

    first_row_keys = list(table.rows[0].keys())
    param_col = first_row_keys[0]
    value_col = first_row_keys[1] if len(first_row_keys) > 1 else None

    if value_col is None:
        return fields

    # Collected enable_prefix_caching observations across all four accepted forms;
    # reconciled after the row loop.
    epc_observations: list[tuple[bool, str, str]] = []  # (resolved_bool, source, raw_param)

    for row in table.rows:
        raw_param = row.get(param_col, "")
        raw_value = row.get(value_col, "")
        canonical = canonicalize_parameter(raw_param)

        if canonical is None:
            if is_unrecognized_replica_label(raw_param):
                recognized = sorted(
                    PARAMETER_ALIASES["replicas"] | PARAMETER_ALIASES["prefill_replicas"]
                )
                print(
                    f"ERROR: unrecognized fleet-size row in the vLLM configuration "
                    f"table: '{normalize_cell(raw_param)}'. Dropping it would discard a "
                    f"stated pod count, and this generator cannot convert other units "
                    f"(nodes, workers) into replicas. Use one of: "
                    f"{', '.join(recognized)}",
                    file=sys.stderr,
                )
                sys.exit(1)
            continue

        source = f'config.md row "{normalize_cell(raw_param)}"'

        # Prefix caching: accept legacy keyed form (enable_prefix_caching=true|false)
        # AND bare flags (--enable-prefix-caching / --no-enable-prefix-caching with
        # empty value column). Reconcile after the loop.
        if canonical == "__no_enable_prefix_caching__":
            epc_observations.append((False, source, raw_param))
            continue
        if canonical == "enable_prefix_caching":
            if normalize_cell(raw_value) == "":
                # Bare positive flag: presence implies caching ON.
                epc_observations.append((True, source, raw_param))
            else:
                bv = parse_boolean(raw_value)
                if bv is None:
                    # The user expressed an intent we cannot parse; treat as a
                    # configuration error rather than silently dropping the row,
                    # which would re-introduce the silent-default behavior the
                    # bare-flag rewrite was meant to eliminate.
                    print(
                        f"ERROR: could not parse boolean for {canonical}: '{raw_value}' (expected true/false)",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                epc_observations.append((bv, source, raw_param))
            continue

        # Parse value based on field type
        if canonical in ("model", "hardware", "dtype"):
            value = normalize_cell(raw_value)
        elif canonical in ("enable_chunked_prefill", "enforce_eager"):
            value = parse_boolean(raw_value)
            if value is None:
                print(f"  warning: could not parse boolean for {canonical}: '{raw_value}'", file=sys.stderr)
                continue
        else:
            value = parse_numeric(raw_value)
            if value is None:
                # Try as string (may be a model name in an unusual column)
                value = normalize_cell(raw_value)

        fields[canonical] = ProvenanceValue(value=value, source=source, raw_param=raw_param)

    # Reconcile enable_prefix_caching observations.
    if epc_observations:
        distinct = {v for v, _, _ in epc_observations}
        if len(distinct) > 1:
            joined = "; ".join(f"{s} -> {v}" for v, s, _ in epc_observations)
            print(
                f"ERROR: conflicting enable_prefix_caching specifications in config.md ({joined})",
                file=sys.stderr,
            )
            sys.exit(1)
        resolved = next(iter(distinct))
        first_source = epc_observations[0][1]
        first_raw = epc_observations[0][2]
        fields["enable_prefix_caching"] = ProvenanceValue(
            value=resolved, source=first_source, raw_param=first_raw
        )

    return fields


# ---------------------------------------------------------------------------
# Scenario building
# ---------------------------------------------------------------------------

def normalize_hardware_key(raw: str) -> str:
    """Normalize hardware string: 'H100-SXM-80GB' -> 'H100_SXM_80GB'."""
    return re.sub(r"[-\s]", "_", raw.strip())


def derive_scenario_name(config_path: str, override: str | None = None) -> str:
    """Derive scenario name from folder basename, sanitized."""
    if override:
        sanitized = re.sub(r"[^a-z0-9]", "", override.lower())
        return sanitized[:20]
    folder = Path(config_path).resolve().parent.name
    sanitized = re.sub(r"[^a-z0-9]", "", folder.lower())
    return sanitized[:20]


def build_additional_flags(
    fields: dict[str, ProvenanceValue],
) -> list[tuple[str, str]]:
    """Build additionalFlags list with provenance. Returns (flag, source) tuples."""
    flags = []

    if "max_num_seqs" in fields:
        f = fields["max_num_seqs"]
        flags.append((f"--max-num-seqs={f.value}", f.source))

    if "max_num_batched_tokens" in fields:
        f = fields["max_num_batched_tokens"]
        flags.append((f"--max-num-batched-tokens={f.value}", f.source))

    if "enable_chunked_prefill" in fields and fields["enable_chunked_prefill"].value:
        f = fields["enable_chunked_prefill"]
        flags.append(("--enable-chunked-prefill", f.source))

    # Emit a single bare flag matching the user's intent. When config.md is
    # silent on prefix caching, default to --enable-prefix-caching: the
    # deployed vLLM version predates per-model default resolution
    # (vllm/engine/arg_utils.py:_set_default_chunked_prefill_and_prefix_caching_args),
    # so an unset value would otherwise fall back to OFF rather than ON for
    # supported models. See issue #295.
    epc = fields.get("enable_prefix_caching")
    if epc is None:
        flags.append((
            "--enable-prefix-caching",
            "sim2real-bootstrap default (config.md silent; deployed vLLM requires explicit ON)",
        ))
    elif epc.value:
        flags.append(("--enable-prefix-caching", epc.source))
    else:
        flags.append(("--no-enable-prefix-caching", epc.source))

    if "dtype" in fields and fields["dtype"].value != "auto":
        f = fields["dtype"]
        flags.append((f"--dtype={f.value}", f.source))

    if "swap_space" in fields and fields["swap_space"].value != 4:
        f = fields["swap_space"]
        flags.append((f"--swap-space={f.value}", f.source))

    if "pipeline_parallel_size" in fields and fields["pipeline_parallel_size"].value > 1:
        f = fields["pipeline_parallel_size"]
        flags.append((f"--pipeline-parallel-size={f.value}", f.source))

    return flags


def build_scenario(
    fields: dict[str, ProvenanceValue], name: str
) -> tuple[dict, dict[str, str]]:
    """Build scenario dict and provenance map from extracted fields."""
    # --- Validate required fields ---
    if "model" not in fields:
        print("ERROR: required field 'model' not found in config.md", file=sys.stderr)
        sys.exit(1)
    if "hardware" not in fields:
        print("ERROR: required field 'hardware' (GPU) not found in config.md", file=sys.stderr)
        sys.exit(1)

    model_name = fields["model"].value

    def resolve_role_hardware(role: str, field_name: str) -> tuple[str, str]:
        """Resolve one role's accelerator label, warning if the cell names several.

        A role is one Deployment, so it carries one node selector and its replicas
        are fungible -- there is no way to place replica 0 and replica 1 on
        different GPU types. When a cell names more than one, the extra types
        cannot be honored, so say exactly what was dropped instead of emitting a
        permissive `labelValues` list that would look like support while allowing
        a homogeneous placement (issue #824).
        """
        field = fields.get(field_name) or fields["hardware"]
        types = split_hardware_cell(str(field.value))
        if len(types) > 1:
            print(
                f"  WARNING: {role} names {len(types)} GPU types "
                f"({', '.join(types)}); a Deployment carries one node selector, so "
                f"'{types[0]}' was used and the {role} block is HOMOGENEOUS in the "
                f"generated baseline. Heterogeneity within one role is not "
                f"expressible on the target -- see issue #824.",
                file=sys.stderr,
            )
        chosen = types[0] if types else str(field.value)
        key = normalize_hardware_key(chosen)
        label = HARDWARE_LABELS.get(key)
        if label is None:
            print(f"  warning: hardware '{key}' not in HARDWARE_LABELS", file=sys.stderr)
            return f"NVIDIA-{key}", f"best-effort ('{key}' not in lookup table)"
        source = f'lookup: HARDWARE_LABELS["{key}"]'
        if len(types) > 1:
            source += f" (first of {len(types)}; see warning)"
        elif field_name in fields:
            source += f' via {field.source}'
        return label, source

    # --- Model metadata ---
    meta = MODEL_METADATA.get(model_name)
    if meta is None:
        print(f"  warning: model '{model_name}' not in MODEL_METADATA, deriving values", file=sys.stderr)
        short_name = model_name.replace("/", "-").lower()
        model_path = f"models/{model_name}"
        size = "1Ti"
        # max_model_len must come from config.md
        if "max_model_len" not in fields:
            print(f"ERROR: model '{model_name}' not in lookup table and max_model_len not in config.md", file=sys.stderr)
            sys.exit(1)
        max_model_len = int(fields["max_model_len"].value)
        meta_source = "derived (model not in lookup table)"
    else:
        short_name = meta["shortName"]
        model_path = meta["path"]
        size = meta["size"]
        max_model_len = meta["maxModelLen"]
        meta_source = f'lookup: MODEL_METADATA["{model_name}"]'

    # Override max_model_len from config.md if present
    if "max_model_len" in fields:
        max_model_len = int(fields["max_model_len"].value)
        max_model_len_source = fields["max_model_len"].source
    else:
        max_model_len_source = meta_source + ".maxModelLen"

    # --- Hardware, per role ---
    hw_label, hw_source = resolve_role_hardware("decode", "decode_hardware")

    # --- Numeric fields with defaults ---
    def get_int(field_name, default, default_source="default (not in config.md)"):
        if field_name in fields:
            return int(fields[field_name].value), fields[field_name].source
        return default, default_source

    def get_float(field_name, default, default_source="default (not in config.md)"):
        if field_name in fields:
            return float(fields[field_name].value), fields[field_name].source
        return default, default_source

    replicas, replicas_source = get_int("replicas", 1)
    block_size, block_size_source = get_int("block_size", 16)
    gpu_mem, gpu_mem_source = get_float("gpu_memory_utilization", 0.9)
    tp, tp_source = get_int("tensor_parallel_size", 1)
    dp, dp_source = get_int("data_parallel_size", 1)

    # --- Build scenario dict ---
    scenario = {"name": name}

    scenario["model"] = {
        "name": model_name,
        "shortName": short_name,
        "path": model_path,
        "huggingfaceId": model_name,
        "size": size,
        "maxModelLen": max_model_len,
        "blockSize": block_size,
        "gpuMemoryUtilization": gpu_mem,
    }

    decode = {"replicas": replicas}
    decode["acceleratorType"] = {
        "labelKey": "nvidia.com/gpu.product",
        "labelValue": hw_label,
    }

    if tp > 1 or dp > 1:
        decode["parallelism"] = {
            "data": dp,
            "dataLocal": dp,
            "tensor": tp,
            "workers": tp,
        }

    additional_flags = build_additional_flags(fields)
    if additional_flags:
        decode["vllm"] = {"additionalFlags": additional_flags}

    # enforce_eager: defaults.yaml sets true; only emit override if false
    if "enforce_eager" in fields and not fields["enforce_eager"].value:
        scenario["vllmCommon"] = {"flags": {"enforceEager": False}}

    scenario["decode"] = decode

    # --- Prefill role (issue #824) ---
    # Only when config.md names a prefill pod count. Absent, the output is exactly
    # what it has always been, so existing bundles regenerate byte-identically.
    #
    # `enabled: true` is NOT decoration. pipeline/lib/capacity.py:238-241 defaults
    # prefill to ("prefill", False, 0), so a prefill block without it is skipped by
    # capacity planning -- the scenario would read as disaggregated while planning
    # zero prefill GPUs. config/scenarios/guides/pd-disaggregation.yaml sets it
    # explicitly for the same reason.
    # A stated count of 0 means aggregated -- the same as saying nothing -- so no
    # block is emitted. Emitting `enabled: true` with `replicas: 0` would be the
    # exact "reads as disaggregated while planning zero prefill GPUs" state this
    # comment warns about, and generate_scenarios.py already skips on 0.
    prefill_hw_label = None
    prefill_hw_source = None
    prefill_field = fields.get("prefill_replicas")
    prefill_replicas = int(prefill_field.value or 0) if prefill_field else 0
    if prefill_replicas > 0:
        prefill_hw_label, prefill_hw_source = resolve_role_hardware(
            "prefill", "prefill_hardware"
        )
        prefill = {"enabled": True, "replicas": prefill_replicas}
        prefill["acceleratorType"] = {
            "labelKey": "nvidia.com/gpu.product",
            "labelValue": prefill_hw_label,
        }
        # Parallelism and vLLM flags are shared, not per-role: no bundle to date
        # states them per role, and inventing per-role aliases for values nobody
        # supplies would add vocabulary with no source to cite.
        if tp > 1 or dp > 1:
            prefill["parallelism"] = {
                "data": dp,
                "dataLocal": dp,
                "tensor": tp,
                "workers": tp,
            }
        if additional_flags:
            prefill["vllm"] = {"additionalFlags": additional_flags}
        scenario["prefill"] = prefill

        # KV transfer is what makes the prefill pool actually do anything (#830).
        # vllmCommon.kvTransfer.enabled defaults to false upstream
        # (llm-d-benchmark config/templates/values/defaults.yaml:725-726) and the
        # --kv-transfer-config flag is gated on it: _macros.j2:103 sets
        # has_kv_transfer inside the single mode-parameterized macro
        # build_vllm_command(mode) (:83-188), which emits the flag at :111 and
        # :169 and is invoked for BOTH roles from 13_ms-values.yaml.j2 (:409
        # decode, :826 prefill). So a prefill pool WITHOUT this
        # block reads as disaggregated and is not: no KV connector is
        # instantiated, the prefill pod is never routed to and logs zero
        # requests, and the decode pods do the prefill work themselves. Nothing
        # errors -- observed as 415 requests all served by decode with prefill
        # idle throughout.
        #
        # Same failure class as `enabled: true` above, one layer down: an upstream
        # default of "off" turning a stated intention into silence. That one is
        # guarded at the capacity-planning layer; this is the model-server layer.
        #
        # connector/role are stated rather than inherited from the upstream
        # anchors (defaults.yaml:56-57, NixlConnector / kv_both) so the values are
        # this bundle's decision and not a downstream fallback -- the same
        # principle the specify skill applies to the `blis observe` block.
        #
        # `role: kv_both` is DEPRECATED for NixlConnector, which wants
        # kv_producer on prefill and kv_consumer on decode. Not expressible today:
        # vllmCommon is shared by both roles and there is no prefill.kvTransfer.
        # Tracked as #845; kv_both works and warns until then.
        #
        # setdefault, NOT assignment: the enforce_eager override above may already
        # have created scenario["vllmCommon"], and a plain assignment here would
        # silently drop it.
        scenario.setdefault("vllmCommon", {})["kvTransfer"] = {
            "enabled": True,
            "connector": "NixlConnector",
            "role": "kv_both",
        }
    elif "prefill_hardware" in fields:
        # The row was recognized and stored, then never read, because a prefill
        # accelerator without a prefill pod count describes a pool that does not
        # exist. Recognizing an input and then discarding it is the silent-drop
        # this feature exists to remove (issue #824 review).
        print(
            f"  WARNING: '{fields['prefill_hardware'].raw_param}' was given but no "
            f"prefill pod count, so no prefill pool is emitted and the row has NO "
            f"effect. Add a prefill replica count (e.g. 'Number of prefill pods') "
            f"for it to apply.",
            file=sys.stderr,
        )

    # --- Build provenance map ---
    provenance = {
        "model.name": fields["model"].source,
        "model.shortName": meta_source + ".shortName" if meta else "derived from model name",
        "model.path": meta_source + ".path" if meta else "derived from model name",
        "model.huggingfaceId": fields["model"].source,
        "model.size": meta_source + ".size" if meta else "default estimate",
        "model.maxModelLen": max_model_len_source,
        "model.blockSize": block_size_source,
        "model.gpuMemoryUtilization": gpu_mem_source,
        "decode.replicas": replicas_source,
        "decode.acceleratorType.labelValue": hw_source,
    }

    if tp > 1 or dp > 1:
        provenance["decode.parallelism.tensor"] = tp_source
        provenance["decode.parallelism.data"] = dp_source

    if "prefill" in scenario:
        provenance["prefill.replicas"] = fields["prefill_replicas"].source
        provenance["prefill.acceleratorType.labelValue"] = prefill_hw_source
        if tp > 1 or dp > 1:
            provenance["prefill.parallelism.tensor"] = tp_source
            provenance["prefill.parallelism.data"] = dp_source
        # Not from config.md -- implied by stating a prefill pool at all, since a
        # pool without the transport does nothing (#830).
        provenance["vllmCommon.kvTransfer.enabled"] = (
            "implied by prefill pod count; P/D requires a KV transfer backend"
        )
        provenance["vllmCommon.kvTransfer.connector"] = (
            "framework default, stated explicitly (llm-d-benchmark defaults.yaml:56)"
        )
        provenance["vllmCommon.kvTransfer.role"] = (
            "framework default, stated explicitly; kv_both is deprecated for "
            "NixlConnector but per-role values are not expressible (see #845)"
        )

    return scenario, provenance


# ---------------------------------------------------------------------------
# YAML output with provenance comments
# ---------------------------------------------------------------------------

def write_provenance_yaml(
    scenario: dict, provenance: dict[str, str], out_path: str, dry_run: bool = False
):
    """Write scenario YAML with inline provenance comments."""
    lines = []
    lines.append("scenario:")
    lines.append(f"- name: {scenario['name']}")
    lines.append("")
    lines.append("  model:")
    lines.append(f"    name: {scenario['model']['name']}  # {provenance['model.name']}")
    lines.append(f"    shortName: {scenario['model']['shortName']}  # {provenance['model.shortName']}")
    lines.append(f"    path: {scenario['model']['path']}  # {provenance['model.path']}")
    lines.append(f"    huggingfaceId: {scenario['model']['huggingfaceId']}  # {provenance['model.huggingfaceId']}")
    lines.append(f"    size: {scenario['model']['size']}  # {provenance['model.size']}")
    lines.append(f"    maxModelLen: {scenario['model']['maxModelLen']}  # {provenance['model.maxModelLen']}")
    lines.append(f"    blockSize: {scenario['model']['blockSize']}  # {provenance['model.blockSize']}")
    lines.append(f"    gpuMemoryUtilization: {scenario['model']['gpuMemoryUtilization']}  # {provenance['model.gpuMemoryUtilization']}")

    # This emitter is hand-rolled, so every key under vllmCommon needs a branch
    # here or it is silently dropped from the output no matter what the scenario
    # dict says. `flags` and `kvTransfer` are independent: enforce_eager sets the
    # first, a prefill pool sets the second, and either can appear alone.
    if "vllmCommon" in scenario:
        lines.append("")
        lines.append("  vllmCommon:")
        if "flags" in scenario["vllmCommon"]:
            source = "config.md row \"enforce_eager\""
            if "enforce_eager" in provenance:
                source = provenance["enforce_eager"]
            lines.append("    flags:")
            lines.append(f"      enforceEager: false  # {source}")
        if "kvTransfer" in scenario["vllmCommon"]:
            kv = scenario["vllmCommon"]["kvTransfer"]
            lines.append("    # Required for the prefill pool to do anything: the")
            lines.append("    # --kv-transfer-config flag is gated on `enabled`, which")
            lines.append("    # defaults to false, so without this the prefill pod is")
            lines.append("    # never routed to and decode prefills its own requests.")
            lines.append("    kvTransfer:")
            lines.append(
                f"      enabled: {str(kv['enabled']).lower()}  "
                f"# {provenance['vllmCommon.kvTransfer.enabled']}"
            )
            lines.append(
                f"      connector: {kv['connector']}  "
                f"# {provenance['vllmCommon.kvTransfer.connector']}"
            )
            lines.append(
                f"      role: {kv['role']}  "
                f"# {provenance['vllmCommon.kvTransfer.role']}"
            )

    lines.append("")
    lines.append("  decode:")
    lines.append(f"    replicas: {scenario['decode']['replicas']}  # {provenance['decode.replicas']}")
    lines.append("    acceleratorType:")
    lines.append("      labelKey: nvidia.com/gpu.product")
    lines.append(f"      labelValue: {scenario['decode']['acceleratorType']['labelValue']}  # {provenance['decode.acceleratorType.labelValue']}")

    if "parallelism" in scenario["decode"]:
        p = scenario["decode"]["parallelism"]
        lines.append("    parallelism:")
        lines.append(f"      data: {p['data']}  # {provenance['decode.parallelism.data']}")
        lines.append(f"      dataLocal: {p['dataLocal']}  # {provenance['decode.parallelism.data']}")
        lines.append(f"      tensor: {p['tensor']}  # {provenance['decode.parallelism.tensor']}")
        lines.append(f"      workers: {p['workers']}  # {provenance['decode.parallelism.tensor']}")

    if "vllm" in scenario["decode"]:
        lines.append("    vllm:")
        lines.append("      additionalFlags:")
        for flag, source in scenario["decode"]["vllm"]["additionalFlags"]:
            lines.append(f'      - "{flag}"  # {source}')

    # Prefill role, emitted only when config.md named a prefill pod count. Placed
    # after decode so the decode bytes above are untouched when it is absent.
    if "prefill" in scenario:
        p_role = scenario["prefill"]
        lines.append("")
        lines.append("  prefill:")
        lines.append(
            "    enabled: true  # required: capacity planning defaults prefill to "
            "disabled with 0 replicas (pipeline/lib/capacity.py)"
        )
        lines.append(f"    replicas: {p_role['replicas']}  # {provenance['prefill.replicas']}")
        lines.append("    acceleratorType:")
        lines.append("      labelKey: nvidia.com/gpu.product")
        lines.append(
            f"      labelValue: {p_role['acceleratorType']['labelValue']}"
            f"  # {provenance['prefill.acceleratorType.labelValue']}"
        )
        if "parallelism" in p_role:
            pp = p_role["parallelism"]
            lines.append("    parallelism:")
            lines.append(f"      data: {pp['data']}  # {provenance['prefill.parallelism.data']}")
            lines.append(f"      dataLocal: {pp['dataLocal']}  # {provenance['prefill.parallelism.data']}")
            lines.append(f"      tensor: {pp['tensor']}  # {provenance['prefill.parallelism.tensor']}")
            lines.append(f"      workers: {pp['workers']}  # {provenance['prefill.parallelism.tensor']}")
        if "vllm" in p_role:
            lines.append("    vllm:")
            lines.append("      additionalFlags:")
            for flag, source in p_role["vllm"]["additionalFlags"]:
                lines.append(f'      - "{flag}"  # {source}')

    lines.append("")

    output = "\n".join(lines) + "\n"

    if dry_run:
        print(output)
    else:
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w") as f:
            f.write(output)
        print(f"  wrote {out_path}")


# ---------------------------------------------------------------------------
# blis observe parsing and rendering (issue #403)
# ---------------------------------------------------------------------------

def parse_observe_block(config_md_text: str) -> dict[str, str]:
    """Extract flags from the `blis observe \\ ... \\` command in config.md.

    Returns a dict keyed by transfer.yaml key. Keys are present only when the
    block contained the corresponding flag. Each flag is validated against
    `blis observe`'s namespace: modeled tuning flags map to their key, other
    real observe flags are passed through verbatim in `extraArgs`
    (whitespace-joined, source order), and flags that are not observe flags
    (replay-only, sim-world, unknown) are dropped with a stderr warning rather
    than transcribed (issue #602). Both `--flag value` and `--flag=value` forms
    are accepted. Absent block → {}.
    """
    # Locate the first line that starts a `blis observe` invocation. We accept
    # optional leading whitespace so the block can live inside a code fence.
    lines = config_md_text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(r"^\s*blis\s+observe\b", line):
            start = i
            break
    if start is None:
        return {}

    # Collect the invocation line plus continuation lines. A continuation is
    # any line whose predecessor's stripped form ends with '\'. Stop at the
    # first line without a trailing continuation.
    collected = [lines[start]]
    idx = start
    while collected[-1].rstrip().endswith("\\"):
        idx += 1
        if idx >= len(lines):
            break
        collected.append(lines[idx])

    # Flatten to a single string, strip trailing backslashes and code-fence
    # boundaries, then tokenize on whitespace.
    joined = " ".join(ln.rstrip("\\").strip() for ln in collected)
    # Drop leading "blis observe" tokens.
    tokens = joined.split()
    # Skip until we see the first token starting with '--'.
    flag_tokens = []
    seen_flag = False
    for tok in tokens:
        if tok.startswith("--"):
            seen_flag = True
        if seen_flag:
            flag_tokens.append(tok)

    parsed: dict[str, str] = {}
    extra_pieces: list[str] = []
    i = 0
    while i < len(flag_tokens):
        tok = flag_tokens[i]
        if not tok.startswith("--"):
            # Stray value with no preceding flag — skip.
            i += 1
            continue
        # Support both "--flag value" and "--flag=value" forms. Classification
        # keys on the flag NAME; the value is inline (after '=') or, failing
        # that, the next token when it is not itself a flag.
        if "=" in tok:
            flag_name, inline_value = tok.split("=", 1)
        else:
            flag_name, inline_value = tok, None
        has_next_value = (
            inline_value is None
            and i + 1 < len(flag_tokens)
            and not flag_tokens[i + 1].startswith("--")
        )
        # Tokens this flag consumes: itself, plus a separate value token if any.
        step = 2 if has_next_value else 1

        if flag_name in OBSERVE_TUNING_FLAGS:
            value = inline_value if inline_value is not None else (
                flag_tokens[i + 1] if has_next_value else None
            )
            if value:
                parsed[OBSERVE_TUNING_FLAGS[flag_name]] = value
            else:
                # Recognized tuning flag but no usable value (`--timeout` with
                # no arg, or `--timeout=`). Drop it and warn — consistent with
                # the other drop paths — so the sim2real-bootstrap default
                # applies visibly instead of an empty value leaking into
                # transfer.yaml tagged as config.md-sourced (issue #602 review).
                print(
                    f"WARNING: dropping '{flag_name}' from the blis observe "
                    f"block (recognized flag with no value); the "
                    f"sim2real-bootstrap default will apply.",
                    file=sys.stderr,
                )
        elif flag_name in OBSERVE_PIPELINE_INJECTED_FLAGS:
            pass  # Drop entirely — the Tekton task supplies these.
        elif flag_name in OBSERVE_VALID_FLAGS:
            # A real blis observe flag with no first-class key — pass it
            # through verbatim (preserving --flag=value vs --flag value) so the
            # operator can tune it in transfer.yaml.
            extra_pieces.append(tok)
            if has_next_value:
                extra_pieces.append(flag_tokens[i + 1])
        else:
            # Not a blis observe flag. Refuse to transcribe it into extraArgs
            # (it would abort observe at runtime, e.g. the transcribed
            # `blis replay` flag "unknown flag: --session-mode"). Warn and drop
            # so a valid invocation still emits; the operator can add it back
            # in transfer.yaml if it is legitimate (issue #602).
            reason = (
                "replay/sim flag with no blis observe equivalent"
                if flag_name in OBSERVE_REPLAY_ONLY_FLAGS
                else "not a blis observe flag"
            )
            print(
                f"WARNING: dropping '{flag_name}' from the blis observe block "
                f"({reason}); add it to transfer.yaml if intended.",
                file=sys.stderr,
            )
        i += step

    if extra_pieces:
        parsed["extraArgs"] = " ".join(extra_pieces)
    return parsed


def render_blis_observe_yaml(parsed: dict[str, str]) -> str:
    """Render a `blis_observe:` YAML block with provenance comments.

    Emits all 5 keys in canonical order. Keys present in `parsed` are marked
    `# source: config.md`; keys absent are defaulted from OBSERVE_DEFAULTS
    and marked `# source: sim2real-bootstrap default`. Numeric-string values
    (all-digit) emit as bare YAML integers; other values emit as double-
    quoted YAML strings so a bare `60s` round-trips cleanly.
    """
    lines = ["blis_observe:"]
    for key, default in OBSERVE_DEFAULTS.items():
        if key in parsed:
            value = parsed[key]
            source = "config.md"
        else:
            value = default
            source = "sim2real-bootstrap default"
        # Emit as bare int when the value is purely digits (no leading zero
        # edge case: '0' is fine as int, '007' would still parse fine).
        if value.isdigit():
            rendered = value
        else:
            # Escape embedded double quotes.
            rendered = '"' + value.replace('"', '\\"') + '"'
        lines.append(f"  {key}: {rendered}  # source: {source}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate baseline scenario YAML from config.md"
    )
    parser.add_argument(
        "config", nargs="?", default="./config.md", help="Path to config.md"
    )
    parser.add_argument(
        "-o", "--output-dir", default="./baselines", help="Output directory"
    )
    parser.add_argument(
        "-n", "--name",
        help=(
            "Override the inner scenario `- name:` label inside the emitted "
            "YAML (default: derived from folder). The output filename is "
            "always baseline.yaml — see issue #544."
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print YAML to stdout, don't write file"
    )
    parser.add_argument(
        "--emit-observe-yaml",
        action="store_true",
        help=(
            "Emit only a `blis_observe:` YAML fragment (parsed from the "
            "`blis observe \\ ... \\` block in config.md) to stdout, then "
            "exit 0. Skips scenario YAML generation. If config.md is "
            "missing, emits an all-defaults fragment."
        ),
    )
    args = parser.parse_args()

    if args.emit_observe_yaml:
        config_path = args.config
        if os.path.isfile(config_path):
            with open(config_path) as f:
                text = f.read()
            parsed = parse_observe_block(text)
        else:
            parsed = {}
        sys.stdout.write(render_blis_observe_yaml(parsed))
        return

    config_path = args.config
    if not os.path.isfile(config_path):
        print(f"ERROR: config file not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Reading: {config_path}")

    with open(config_path) as f:
        lines = f.readlines()

    # Parse all tables
    tables = parse_md_tables(lines)
    if not tables:
        print("ERROR: no markdown tables found in config.md", file=sys.stderr)
        sys.exit(1)

    # Find the vLLM configuration table
    vllm_table = find_vllm_table(tables)
    if vllm_table is None:
        print("ERROR: could not find vLLM configuration table in config.md", file=sys.stderr)
        print(f"  searched {len(tables)} table(s) with headings: {[t.heading for t in tables]}", file=sys.stderr)
        sys.exit(1)

    print(f"  found table under: \"{vllm_table.heading}\" ({len(vllm_table.rows)} rows)")

    # Extract fields
    fields = extract_fields(vllm_table)
    if not fields:
        print("ERROR: no recognized fields extracted from table", file=sys.stderr)
        sys.exit(1)

    # A per-role row stated ONLY in some other table is invisible to the parser.
    # Say so rather than emitting a decode-only baseline in silence (issue #824
    # review). Runs after extraction so a value correctly present in the vLLM table
    # and merely documented elsewhere draws no warning.
    warn_role_rows_outside_vllm_table(tables, vllm_table, set(fields))

    print(f"  extracted {len(fields)} field(s): {list(fields.keys())}")

    # Derive the inner scenario `- name:` label (used inside the YAML doc,
    # not as the filename — see issue #544).
    scenario_name = derive_scenario_name(config_path, args.name)
    print(f"  scenario label: {scenario_name}")

    # Build scenario
    scenario, provenance = build_scenario(fields, scenario_name)

    # Write output. The filename is always ``baseline.yaml`` — the baseline
    # identifier in transfer.yaml is hardcoded to the literal string
    # ``baseline`` (issue #544), so downstream filenames stay consistent
    # across experiments.
    out_path = os.path.join(args.output_dir, "baseline.yaml")
    write_provenance_yaml(scenario, provenance, out_path, dry_run=args.dry_run)

    # Validate output parses as YAML
    if not args.dry_run:
        try:
            import yaml
            with open(out_path) as f:
                yaml.safe_load(f)
            print("  validated: output is valid YAML")
        except ImportError:
            print("  note: PyYAML not available, skipping validation")
        except Exception as e:
            print(f"  WARNING: output YAML validation failed: {e}", file=sys.stderr)

    print("Done.")


if __name__ == "__main__":
    main()

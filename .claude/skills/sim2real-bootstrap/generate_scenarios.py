#!/usr/bin/env python3
"""Generate llm-d-benchmark scenario candidates from top3_selection.json.

Writes one file per candidate (``<group>-<i>.yaml``, e.g. ``top3-1.yaml``).
The operator or bootstrap skill picks one candidate and renames it to
``baseline.yaml`` before ``transfer.yaml`` is written (issue #544 — the
baseline identifier in transfer.yaml is always ``baseline`` and the file
lives at ``baselines/baseline.yaml``).
"""

import json
import os
import re
import sys
from pathlib import Path

import pd_plumbing as pdp

# See #831: `workers` is the LeaderWorkerSet group size (pods per replica), not a
# parallelism degree, and no input field states it -- so it is a stated default.
_WORKERS_COMMENT = "single-node default (LWS pods per replica, not a parallelism degree)"

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
}

HARDWARE_LABELS = {
    "H100_SXM_80GB": "NVIDIA-H100-80GB-HBM3",
    "A100_SXM_80GB": "NVIDIA-A100-SXM4-80GB",
    "A100_PCIE_40GB": "NVIDIA-A100-PCIE-40GB",
}

# Separators an author might use to name several GPU types in one value
# (issue #824). Kept in sync with generate_from_config.py's equivalent.
_GPU_LIST_SPLIT_RE = re.compile(r"\s*(?:,|/|\+|\band\b)\s*", re.IGNORECASE)


def split_hardware_value(raw: str) -> list[str]:
    """Split a hardware value into the types it names. Single type -> one element."""
    cleaned = str(raw).strip()
    if not cleaned:
        return []
    return [part for part in _GPU_LIST_SPLIT_RE.split(cleaned) if part]


def resolve_role_hardware(raw: str, role: str) -> str:
    """Resolve one role's accelerator label, warning if the value names several.

    A role is one Deployment, so it carries one node selector and its replicas are
    fungible -- replica 0 and replica 1 cannot be pinned to different GPU types.
    When a value names more than one, say what was dropped rather than emitting a
    permissive `labelValues` list that would look like support for heterogeneity
    while allowing a homogeneous placement (issue #824).
    """
    types = split_hardware_value(raw)
    if len(types) > 1:
        print(
            f"  WARNING: {role} names {len(types)} GPU types ({', '.join(types)}); "
            f"a Deployment carries one node selector, so '{types[0]}' was used and "
            f"the {role} block is HOMOGENEOUS in the generated scenario. "
            f"Heterogeneity within one role is not expressible on the target -- "
            f"see issue #824.",
            file=sys.stderr,
        )
    chosen = types[0] if types else str(raw)
    label = HARDWARE_LABELS.get(chosen)
    if label is None:
        # Passing an unmapped value straight through makes it the node-selector
        # labelValue, so a typo or a new SKU becomes a selector that matches no
        # node and the pods sit Pending with nothing said.
        #
        # The fallback VALUE differs from generate_from_config.py's on purpose:
        # that one synthesizes `NVIDIA-{key}` while this one returns the input
        # verbatim, and each preserves its own generator's long-standing output.
        # Only the fact that a diagnostic is printed at all is now shared.
        print(
            f"  warning: hardware '{chosen}' not in HARDWARE_LABELS, using it "
            f"verbatim as the {role} node-selector value",
            file=sys.stderr,
        )
        return chosen
    return label


# ---------------------------------------------------------------------------
# Field registry: declares which fields we handle vs intentionally ignore.
# Any field not in either set triggers a warning.
# ---------------------------------------------------------------------------

KNOWN_FIELDS = {
    "workload": {
        "mapped": {"model", "hardware", "prefill_hardware"},
        "ignored": {
            "preset", "num_requests", "isl_mean", "isl_max",
            "osl_mean", "osl_max", "arrival_pattern",
            "slo_ttft_mean_ms", "seed", "trace_file",
        },
    },
    "vllm_args": {
        "mapped": {
            "tensor_parallel_size", "pipeline_parallel_size",
            "num_instances", "data_parallel_size", "prefill_instances",
            "max_num_seqs", "max_num_batched_tokens",
            "enable_chunked_prefill", "block_size",
            "gpu_memory_utilization", "dtype", "kv_cache_dtype",
            "enable_prefix_caching", "enforce_eager", "swap_space",
        },
        "ignored": set(),
    },
    "routing_config": {
        "mapped": {"strategy"},
        "ignored": {"scorers", "picker"},
    },
    "tool_config": {
        "mapped": set(),
        "ignored": {
            "scheduler", "admission_policy", "preemption_policy",
            "max_concurrency", "vidur_scheduler_type",
        },
    },
}

# Top-level keys in each entry
KNOWN_TOP_LEVEL = {"tool", "workload", "vllm_args", "routing_config", "tool_config", "results", "metadata"}


def check_unknown_fields(entry: dict, entry_name: str) -> list[str]:
    """Check for fields not in the known registry. Returns list of warnings."""
    warnings = []

    # Check top-level keys
    for key in entry:
        if key not in KNOWN_TOP_LEVEL:
            warnings.append(f"[{entry_name}] unknown top-level key: '{key}'")

    # Check each section
    for section_name, registry in KNOWN_FIELDS.items():
        section = entry.get(section_name)
        if section is None:
            continue
        all_known = registry["mapped"] | registry["ignored"]
        for key in section:
            if key not in all_known:
                warnings.append(
                    f"[{entry_name}] unknown field in {section_name}: '{key}' "
                    f"— may need mapping"
                )

    return warnings


def build_additional_flags(vllm_args: dict) -> list[str]:
    """Convert vllm_args into a list of --flag strings for additionalFlags."""
    flags = []

    if vllm_args.get("max_num_seqs") is not None:
        flags.append(f"--max-num-seqs={vllm_args['max_num_seqs']}")

    if vllm_args.get("max_num_batched_tokens") is not None:
        flags.append(f"--max-num-batched-tokens={vllm_args['max_num_batched_tokens']}")

    if vllm_args.get("enable_chunked_prefill"):
        flags.append("--enable-chunked-prefill")

    if not vllm_args.get("enable_prefix_caching", True):
        flags.append("--no-enable-prefix-caching")

    if vllm_args.get("dtype") and vllm_args["dtype"] != "auto":
        flags.append(f"--dtype={vllm_args['dtype']}")

    if vllm_args.get("swap_space") is not None and vllm_args["swap_space"] != 4:
        flags.append(f"--swap-space={vllm_args['swap_space']}")

    if vllm_args.get("pipeline_parallel_size", 1) > 1:
        flags.append(f"--pipeline-parallel-size={vllm_args['pipeline_parallel_size']}")

    return flags


def build_scenario(entry: dict, name: str) -> dict:
    """Build a scenario YAML dict from a single top3_selection entry."""
    workload = entry["workload"]
    vllm_args = entry["vllm_args"]

    model_name = workload["model"]
    hardware = workload["hardware"]

    meta = MODEL_METADATA.get(model_name, {})
    hw_label = resolve_role_hardware(hardware, "decode") if hardware else hardware

    tp = vllm_args.get("tensor_parallel_size", 1)
    replicas = vllm_args.get("num_instances", 1)
    dp = vllm_args.get("data_parallel_size", 1)

    scenario = {"name": name}

    # Model
    scenario["model"] = {
        "name": model_name,
        "shortName": meta.get("shortName", model_name.replace("/", "-").lower()),
        "path": meta.get("path", f"models/{model_name}"),
        "huggingfaceId": model_name,
        "size": meta.get("size", "1Ti"),
        "maxModelLen": meta.get("maxModelLen", 16384),
        "blockSize": vllm_args.get("block_size", 16),
        "gpuMemoryUtilization": vllm_args.get("gpu_memory_utilization", 0.9),
    }

    # Decode
    decode = {"replicas": replicas}

    if hw_label:
        decode["acceleratorType"] = {
            "labelKey": "nvidia.com/gpu.product",
            "labelValue": hw_label,
        }

    if tp > 1 or dp > 1:
        decode["parallelism"] = {
            "data": dp,
            "dataLocal": dp,
            "tensor": tp,
            # LWS group size (pods per replica), NOT a parallelism degree. A
            # single pod holding `tensor` GPUs is workers: 1. Multi-pod model
            # instances need an input field that does not exist yet (#843).
            "workers": 1,
        }

    flags = build_additional_flags(vllm_args)
    if flags:
        decode["vllm"] = {"additionalFlags": flags}

    # enforce_eager: defaults.yaml sets it true; only override if false
    enforce_eager = vllm_args.get("enforce_eager", True)
    if not enforce_eager:
        scenario["vllmCommon"] = {"flags": {"enforceEager": False}}

    scenario["decode"] = decode

    # --- Prefill role (issue #824) ---
    # Only when the entry names a prefill pod count, so entries without one
    # produce exactly the single-`decode:` scenario they always have.
    #
    # `enabled: true` is load-bearing: pipeline/lib/capacity.py defaults prefill
    # to disabled with 0 replicas, so a prefill block without it plans zero
    # prefill GPUs while reading as disaggregated.
    prefill_replicas = vllm_args.get("prefill_instances")
    if prefill_replicas:
        prefill = {"enabled": True, "replicas": prefill_replicas}
        prefill_hw = workload.get("prefill_hardware") or hardware
        if prefill_hw:
            prefill["acceleratorType"] = {
                "labelKey": "nvidia.com/gpu.product",
                "labelValue": resolve_role_hardware(prefill_hw, "prefill"),
            }
        # Parallelism and flags are shared, not per-role -- no input states them
        # per role, so inventing per-role keys would add unsourced vocabulary.
        if tp > 1 or dp > 1:
            prefill["parallelism"] = {
                "data": dp,
                "dataLocal": dp,
                "tensor": tp,
                "workers": 1,
            }
        if flags:
            prefill["vllm"] = {"additionalFlags": flags}
        scenario["prefill"] = prefill

        # KV transfer is what makes the prefill pool actually do anything (#830).
        # vllmCommon.kvTransfer.enabled defaults to false upstream
        # (llm-d-benchmark config/templates/values/defaults.yaml:725-726) and the
        # --kv-transfer-config flag is gated on it (_macros.j2:103). A prefill pool
        # without this block reads as disaggregated and is not: no KV connector is
        # instantiated, the prefill pod is never routed to, and decode prefills its
        # own requests. Nothing errors.
        #
        # Same failure class as `enabled: true` above, one layer down -- that one is
        # guarded at the capacity-planning layer, this is the model-server layer.
        #
        # `role: kv_both` is deprecated for NixlConnector, which wants kv_producer
        # on prefill and kv_consumer on decode; vllmCommon is shared by both roles
        # so per-role values are not expressible today. Tracked as #845.
        #
        # setdefault, NOT assignment: the enforce_eager override above may already
        # have created scenario["vllmCommon"].
        scenario.setdefault("vllmCommon", {})["kvTransfer"] = {
            "enabled": True,
            # Engine half of the connector decision; routing.connector is the
            # sidecar half. Both come from pd_plumbing so they cannot drift (#848).
            "connector": pdp.KV_CONNECTOR_ENGINE,
            "role": "kv_both",
        }
    elif workload.get("prefill_hardware"):
        # Declared in KNOWN_FIELDS so check_unknown_fields stays quiet, but unused
        # without a count -- a recognized input silently discarded (issue #824
        # review). Same hole as generate_from_config.py's.
        print(
            "  WARNING: workload.prefill_hardware was given but no "
            "vllm_args.prefill_instances, so no prefill pool is emitted and the "
            "field has NO effect. Set prefill_instances for it to apply.",
            file=sys.stderr,
        )

    # --- Pod plumbing (issue #848) ---
    # Same two gates and the same fragments as generate_from_config.py -- see
    # pd_plumbing's module docstring for why both generators must agree here.
    #
    # `prefill_instances` is absent-or-int (unlike the config.md path, which
    # normalises to 0 upstream), so it is coerced before the gate sees it.
    kv_plumbing = pdp.needs_kv_plumbing(prefill_replicas or 0)
    multigpu_plumbing = pdp.needs_multigpu_plumbing(tp, dp)

    if kv_plumbing or multigpu_plumbing:
        vc = scenario.setdefault("vllmCommon", {})
        vc["volumes"] = True
        vc["volumeMounts"] = True
        for role in ("decode", "prefill"):
            if role in scenario:
                scenario[role]["extraEnvVars"] = True

    if kv_plumbing:
        scenario.setdefault("vllmCommon", {})["preprocessScript"] = True
        for role in ("decode", "prefill"):
            if role in scenario:
                scenario[role]["initContainers"] = True
        scenario["routing"] = {"connector": pdp.KV_CONNECTOR_SIDECAR}

    # Internal channel to the emitter; never printed. See generate_from_config.py.
    scenario["_gates"] = {"kv": kv_plumbing, "multigpu": multigpu_plumbing}

    return scenario


def write_commented_yaml(scenario: dict, entry: dict, out_path: str):
    """Write scenario YAML with comments explaining the source of each field."""
    # Pod-plumbing gates (#848), computed by build_scenario. Defaulted rather than
    # indexed so a caller that hand-builds a scenario dict still emits.
    gates = scenario.get("_gates", {"kv": False, "multigpu": False})

    lines = []
    lines.append("scenario:")
    lines.append(f"- name: {scenario['name']}")
    lines.append("")
    lines.append("  model:")
    lines.append(f"    name: {scenario['model']['name']}  # from workload.model")
    lines.append(f"    shortName: {scenario['model']['shortName']}  # derived from model name (lookup table)")
    lines.append(f"    path: {scenario['model']['path']}  # derived from model name (lookup table)")
    lines.append(f"    huggingfaceId: {scenario['model']['huggingfaceId']}  # from workload.model")
    lines.append(f"    size: {scenario['model']['size']}  # lookup table (storage estimate)")
    lines.append(f"    maxModelLen: {scenario['model']['maxModelLen']}  # lookup table (model's max context window)")
    lines.append(f"    blockSize: {scenario['model']['blockSize']}  # from vllm_args.block_size")
    lines.append(f"    gpuMemoryUtilization: {scenario['model']['gpuMemoryUtilization']}  # from vllm_args.gpu_memory_utilization")

    # Hand-rolled emitter: every key under vllmCommon needs a branch here or it is
    # silently dropped from the output. `flags` and `kvTransfer` are independent --
    # enforce_eager sets the first, a prefill pool sets the second, and either can
    # appear alone, so neither may be accessed unconditionally.
    if "vllmCommon" in scenario:
        lines.append("")
        lines.append("  vllmCommon:")
        if "flags" in scenario["vllmCommon"]:
            lines.append("    flags:")
            lines.append(f"      enforceEager: {str(scenario['vllmCommon']['flags']['enforceEager']).lower()}  # from vllm_args.enforce_eager")
        if "kvTransfer" in scenario["vllmCommon"]:
            kv = scenario["vllmCommon"]["kvTransfer"]
            lines.append("    # Required for the prefill pool to do anything: the")
            lines.append("    # --kv-transfer-config flag is gated on `enabled`, which")
            lines.append("    # defaults to false, so without this the prefill pod is")
            lines.append("    # never routed to and decode prefills its own requests.")
            lines.append("    kvTransfer:")
            lines.append(f"      enabled: {str(kv['enabled']).lower()}  # implied by prefill_instances; P/D requires a KV transfer backend")
            lines.append(f"      connector: {kv['connector']}  # framework default, stated explicitly")
            lines.append(f"      role: {kv['role']}  # framework default, stated explicitly; kv_both is deprecated for NixlConnector (see #845)")
        # Plumbing (#848). Both gates write volumes/volumeMounts, so those are
        # rendered in one call rather than appended per gate.
        if gates["kv"]:
            lines.extend(pdp.preprocess_script_lines())
        lines.extend(
            pdp.volume_lines(shared_config=gates["kv"], dshm=gates["multigpu"])
        )

    lines.append("")
    lines.append("  decode:")
    lines.append(f"    replicas: {scenario['decode']['replicas']}  # from vllm_args.num_instances")

    if "acceleratorType" in scenario["decode"]:
        lines.append("    acceleratorType:")
        lines.append(f"      labelKey: {scenario['decode']['acceleratorType']['labelKey']}")
        lines.append(f"      labelValue: {scenario['decode']['acceleratorType']['labelValue']}  # from workload.hardware (lookup table)")

    if "parallelism" in scenario["decode"]:
        p = scenario["decode"]["parallelism"]
        lines.append("    parallelism:")
        lines.append(f"      data: {p['data']}  # from vllm_args.data_parallel_size")
        lines.append(f"      dataLocal: {p['dataLocal']}  # from vllm_args.data_parallel_size")
        lines.append(f"      tensor: {p['tensor']}  # from vllm_args.tensor_parallel_size")
        lines.append(f"      workers: {p['workers']}  # {_WORKERS_COMMENT}")

    if "vllm" in scenario["decode"]:
        lines.append("    vllm:")
        lines.append("      additionalFlags:")
        for flag in scenario["decode"]["vllm"]["additionalFlags"]:
            source = _flag_source(flag)
            lines.append(f"      - \"{flag}\"  # from vllm_args.{source}")

    # Per-role plumbing (#848). Each role is a separate pod, so each needs its own
    # init container and env vars -- there is no vllmCommon form for either.
    if gates["kv"]:
        lines.extend(pdp.init_container_lines())
    lines.extend(
        pdp.extra_env_var_lines(nixl=gates["kv"], multigpu=gates["multigpu"])
    )

    # Prefill role, emitted only when the entry named a prefill pod count. Placed
    # after decode so the decode bytes above are untouched when it is absent.
    if "prefill" in scenario:
        p_role = scenario["prefill"]
        lines.append("")
        lines.append("  prefill:")
        lines.append(
            "    enabled: true  # required: capacity planning defaults prefill to "
            "disabled with 0 replicas (pipeline/lib/capacity.py)"
        )
        lines.append(f"    replicas: {p_role['replicas']}  # from vllm_args.prefill_instances")
        if "acceleratorType" in p_role:
            # Name the key the value actually came from: prefill_hardware is
            # optional and falls back to workload.hardware, so citing it
            # unconditionally would attribute the value to a key the input may
            # not contain.
            prefill_hw_source = (
                "workload.prefill_hardware (lookup table)"
                if entry.get("workload", {}).get("prefill_hardware")
                else "workload.hardware (lookup table; no prefill_hardware given)"
            )
            lines.append("    acceleratorType:")
            lines.append(f"      labelKey: {p_role['acceleratorType']['labelKey']}")
            lines.append(
                f"      labelValue: {p_role['acceleratorType']['labelValue']}"
                f"  # from {prefill_hw_source}"
            )
        if "parallelism" in p_role:
            pp = p_role["parallelism"]
            lines.append("    parallelism:")
            lines.append(f"      data: {pp['data']}  # from vllm_args.data_parallel_size")
            lines.append(f"      dataLocal: {pp['dataLocal']}  # from vllm_args.data_parallel_size")
            lines.append(f"      tensor: {pp['tensor']}  # from vllm_args.tensor_parallel_size")
            lines.append(f"      workers: {pp['workers']}  # {_WORKERS_COMMENT}")
        if "vllm" in p_role:
            lines.append("    vllm:")
            lines.append("      additionalFlags:")
            for flag in p_role["vllm"]["additionalFlags"]:
                source = _flag_source(flag)
                lines.append(f"      - \"{flag}\"  # from vllm_args.{source}")

        # Same per-role plumbing as decode above (#848).
        if gates["kv"]:
            lines.extend(pdp.init_container_lines())
        lines.extend(
            pdp.extra_env_var_lines(nixl=gates["kv"], multigpu=gates["multigpu"])
        )

    # Scenario-level plumbing (#848), last so every block above keeps its bytes.
    if gates["kv"]:
        lines.append("")
        lines.extend(pdp.routing_lines())

    lines.append("")

    with open(out_path, "w") as f:
        f.write("\n".join(lines) + "\n")


def _flag_source(flag: str) -> str:
    """Map a --flag back to its vllm_args field name."""
    mapping = {
        "--max-num-seqs": "max_num_seqs",
        "--max-num-batched-tokens": "max_num_batched_tokens",
        "--enable-chunked-prefill": "enable_chunked_prefill",
        "--no-enable-prefix-caching": "enable_prefix_caching",
        "--dtype": "dtype",
        "--swap-space": "swap_space",
        "--pipeline-parallel-size": "pipeline_parallel_size",
    }
    for prefix, source in mapping.items():
        if flag.startswith(prefix):
            return source
    return "unknown"


def generate(input_path: str, output_dir: str):
    with open(input_path) as f:
        data = json.load(f)

    os.makedirs(output_dir, exist_ok=True)

    all_warnings = []

    for group_name, entries in data.items():
        for i, entry in enumerate(entries):
            scenario_name = f"{group_name}-{i+1}"

            warnings = check_unknown_fields(entry, scenario_name)
            all_warnings.extend(warnings)

            scenario = build_scenario(entry, scenario_name)

            filename = f"{scenario_name}.yaml"
            out_path = os.path.join(output_dir, filename)
            write_commented_yaml(scenario, entry, out_path)

            print(f"  wrote {out_path}")

    if all_warnings:
        print(f"\n⚠ {len(all_warnings)} warning(s) — unknown fields detected:")
        for w in all_warnings:
            print(f"  {w}")
        print("\nUpdate KNOWN_FIELDS in the script to classify these as 'mapped' or 'ignored'.")


if __name__ == "__main__":
    script_dir = Path(__file__).parent

    input_file = sys.argv[1] if len(sys.argv) > 1 else str(script_dir / "top3_selection.json")
    output_dir = sys.argv[2] if len(sys.argv) > 2 else str(script_dir / "generated_scenarios")

    print(f"Reading: {input_file}")
    print(f"Output:  {output_dir}")
    generate(input_file, output_dir)
    print("Done.")

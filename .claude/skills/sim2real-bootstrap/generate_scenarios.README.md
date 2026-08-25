# generate_scenarios.py — Assumptions & Decisions

## Purpose

Converts entries from `top3_selection.json` into llm-d-benchmark scenario YAML
files (overrides on top of `defaults.yaml`).

## Input fields used

Only system-under-test fields are mapped:

| Source section | Fields used |
|---|---|
| `workload` | `model`, `hardware` |
| `vllm_args` | all fields |
| `routing_config` | `strategy` (currently not emitted — see below) |

Ignored sections: `tool`, `tool_config`, `results`, `metadata`, and workload
traffic parameters (`num_requests`, `isl_*`, `osl_*`, `arrival_pattern`,
`slo_*`, `seed`, `trace_file`).

## Omission rules (when a field is NOT emitted)

These assume `defaults.yaml` from llm-d-benchmark provides the base values.

| Field | Omitted when | Rationale |
|---|---|---|
| `decode.parallelism` | `tensor_parallel_size == 1` AND `data_parallel_size == 1` | Matches the `parallelism_single` default |
| `swap_space` | value == 4 | 4 is vLLM's built-in default |
| `enforce_eager` | value == true | `defaults.yaml` sets `enforceEager: true` |
| `dtype` | value == "auto" | vLLM auto-selects dtype by default |
| `kv_cache_dtype` | value == "auto" | vLLM default; never emitted currently |
| `enable_chunked_prefill` | value == false | Only emitted as `--enable-chunked-prefill` when true |
| `pipeline_parallel_size` | value == 1 | Single-pipeline is the default |

## Lookup tables (values not in the input JSON)

### MODEL_METADATA

Maps model name → fields needed by the scenario but absent from `top3_selection.json`.

| Field | Source | Example |
|---|---|---|
| `shortName` | Derived: lowercase, slashes → hyphens | `meta-llama-llama-3-1-8b` |
| `path` | Convention: `models/<model_name>` | `models/meta-llama/Llama-3.1-8B` |
| `size` | Hardcoded estimate (PVC size hint) | `1Ti` |
| `maxModelLen` | Hardcoded from model spec (max context window) | `131072` for Llama-3.1-8B |

### HARDWARE_LABELS

Maps simulation hardware identifiers → Kubernetes node selector label values.

| Input | Output |
|---|---|
| `H100_SXM_80GB` | `NVIDIA-H100-80GB-HBM3` |
| `A100_SXM_80GB` | `NVIDIA-A100-SXM4-80GB` |
| `A100_PCIE_40GB` | `NVIDIA-A100-PCIE-40GB` |

## Field mappings

### Direct mappings (1:1)

| Input | Output location |
|---|---|
| `workload.model` | `model.name`, `model.huggingfaceId` |
| `workload.hardware` | `decode.acceleratorType.labelValue` |
| `workload.prefill_hardware` | `prefill.acceleratorType.labelValue` (optional; falls back to `workload.hardware`) |
| `vllm_args.num_instances` | `decode.replicas` |
| `vllm_args.prefill_instances` | `prefill.replicas` (optional; omitted or `0` produces no `prefill:` block) |
| `vllm_args.tensor_parallel_size` | `decode.parallelism.tensor` |
| `vllm_args.data_parallel_size` | `decode.parallelism.data`, `decode.parallelism.dataLocal` |
| _(no input)_ | `decode.parallelism.workers` — always `1`; LWS pods per replica, not a parallelism degree (#831). Multi-pod model instances tracked by #843 |
| `vllm_args.block_size` | `model.blockSize` |
| `vllm_args.gpu_memory_utilization` | `model.gpuMemoryUtilization` |
| `vllm_args.enforce_eager` | `vllmCommon.flags.enforceEager` |

### Disaggregation (issue #824)

`prefill.enabled: true` is emitted with every `prefill:` block. It is required,
not decorative: `pipeline/lib/capacity.py` defaults prefill to disabled with 0
replicas, so a block without it reads as disaggregated while planning no prefill
GPUs.

`parallelism` and `vllm.additionalFlags` are shared, not per-role — the input has
no per-role form for them, so both roles receive the same values.

`vllmCommon.kvTransfer` is emitted with every `prefill:` block too (issue #830).
`kvTransfer.enabled` defaults to `false` upstream and vLLM's
`--kv-transfer-config` is gated on it, so a prefill pool without it gets no KV
connector: the prefill pod is never routed to and the decode pods prefill their
own requests. Nothing errors — the run completes and is silently not P/D.

So is the pod plumbing that makes KV transfer actually initialise (issue #848),
on two gates. Both come from `pd_plumbing.py`, shared with
`generate_from_config.py` so the two paths cannot drift:

| Gate | Condition | Emitted |
|---|---|---|
| 1 | `vllm_args.prefill_instances > 0` | `preprocess` init container on both roles, `shared-config` emptyDir + mount, `vllmCommon.preprocessScript`, `NIXL_LOG_LEVEL`, `routing.connector` |
| 2 | `tensor_parallel_size > 1` or `data_parallel_size > 1` | `dshm` tmpfs at `/dev/shm` + mount, `NCCL_DEBUG`, `NVSHMEM_DEBUG` |

Gate 1's first three are one unit — the init container writes
`/shared-config/llmdbench_env.sh`, `preprocessScript` sources it, the volume is
the handoff — so any one missing makes the other two inert. Gate 2 keys on either
parallelism degree because `dataLocal` equals `dp`, so either puts more than one
GPU in a pod. Neither gate fires for a single-GPU aggregated entry, so those
regenerate byte-identically.

One GPU type per role. A hardware value naming several types (`"H100, A100"`)
emits the first and warns, naming every type found and the one used: a role is
one Deployment, so it carries one node selector and its replicas cannot be split
across types. `labelValues` (the permissive plural form) is deliberately never
emitted — it would read as heterogeneity support while allowing a homogeneous
placement. Heterogeneity within one role is an llm-d-side concern.

### Mapped to additionalFlags

| Input | Flag |
|---|---|
| `vllm_args.max_num_seqs` | `--max-num-seqs=N` |
| `vllm_args.max_num_batched_tokens` | `--max-num-batched-tokens=N` |
| `vllm_args.enable_chunked_prefill` | `--enable-chunked-prefill` |
| `vllm_args.enable_prefix_caching` | `--no-enable-prefix-caching` (when false) |
| `vllm_args.dtype` | `--dtype=X` |
| `vllm_args.swap_space` | `--swap-space=N` |
| `vllm_args.pipeline_parallel_size` | `--pipeline-parallel-size=N` |

## Not yet mapped

| Field | Reason |
|---|---|
| `routing_config.strategy` | "round-robin" is likely default EPP behavior; custom strategies would need `inferenceExtension.pluginsCustomConfig` |
| `routing_config.scorers` | Always null in current data |
| `routing_config.picker` | Always null in current data |

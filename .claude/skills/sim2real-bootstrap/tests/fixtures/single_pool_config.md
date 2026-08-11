# Configuration

Representative single-pool bundle, shaped after the four known-good experiment
repos (admission-control-pf, exponential-ceiling, quartic, soft-reflective).
Used as a golden input: regenerating it must produce byte-identical output
forever, because those bundles are already deployed against it.

## vLLM Pod Configuration

| Parameter | Value | Notes |
|---|---|---|
| Model | `Qwen/Qwen3-14B` | |
| GPU | H100-SXM-80GB | |
| `tensor_parallel_size` | 1 | |
| `max_num_seqs` | 256 | Max concurrent requests per pod |
| `max_num_batched_tokens` | 2048 | Chunked prefill budget |
| `block_size` | 16 | KV cache block size in tokens |
| `gpu_memory_utilization` | 0.9 | |
| `max_model_len` | 40960 | |
| `enable_chunked_prefill` | True | |
| Pods per node | 1 | informational ratio, not a fleet size |
| Number of pods | 4 | |

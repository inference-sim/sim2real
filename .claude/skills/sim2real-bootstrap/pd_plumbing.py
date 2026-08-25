#!/usr/bin/env python3
"""Pod plumbing that makes KV transfer and multi-GPU pods actually work (#848).

#846 taught both generators to emit `vllmCommon.kvTransfer.enabled: true` when a
prefill pool exists. That flag alone crashloops: the worker dies during
"Initializing NIXL wrapper" because nothing set up the network and GPU-routing
state NIXL needs. This module holds the fragments that do, on two gates.

Why a shared module when the rest of this skill duplicates freely: the fragments
below are ~60 lines of *identical literal YAML text* needed by two hand-rolled
emitters. Duplicated literal text is the highest-drift-risk kind there is (#842
was a "sync the drifted templates" commit), and #846's review found
generate_scenarios.py had no coverage for the branches it had just grown. One
copy, one test file.

Gate 1 -- a prefill pool exists (the same gate #846 uses for kvTransfer):
  the `preprocess` init container COMPUTES environment and GPU-routing values and
  writes them to /shared-config/llmdbench_env.sh; `preprocessScript` SOURCES them;
  the shared-config volume is the handoff. These three are one unit -- any one of
  them missing makes the other two inert. `routing.connector` is the sidecar half
  of the connector decision the engine half already makes.

Gate 2 -- more than one GPU per pod:
  a tmpfs /dev/shm (the K8s default 64 MB is a jitter and hang risk for
  multi-GPU collectives) plus the NCCL/NVSHMEM variables, which only do anything
  once collectives exist.

Both gates are additive and independent. When neither fires, every helper here
returns [] and the generators emit exactly the bytes they emitted before.

All values are copied verbatim from llm-d-benchmark@76473d0
(config/scenarios/guides/). Indentation contract: scenario-level keys at 2
spaces, children at 4 -- matching a scenario rendered as a list item under
`scenario:`. Callers own blank-line separation.
"""

# ---------------------------------------------------------------------------
# One connector decision, two spellings
# ---------------------------------------------------------------------------
# The engine (vLLM's --kv-transfer-config kv_connector) and the routing sidecar
# name the same connector in different vocabularies. Emitting one without the
# other is a half-configuration of exactly #830's shape, so both spellings live
# here and neither generator carries a literal. Switching connectors is a
# one-place edit.
#
# Upstream anchors at the pin: defaults.yaml:56 (&kv_connector NixlConnector)
# and defaults.yaml:694 (routing.connector: nixlv2). Stated rather than
# inherited so the value is this bundle's decision, not a downstream fallback --
# the same principle #846 applied to kvTransfer.
KV_CONNECTOR_ENGINE = "NixlConnector"
KV_CONNECTOR_SIDECAR = "nixlv2"

# Where the init container writes the env file and where every consumer reads it.
_ENV_FILE = "/shared-config/llmdbench_env.sh"
_SHARED_CONFIG_MOUNT = "/shared-config"

# 16Gi is what 5 of the 7 upstream guides that set a limit use (20Gi and 32Gi are
# the outliers). Nothing in config.md states it, so it is a stated default.
_DSHM_SIZE_LIMIT = "16Gi"


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------

def needs_kv_plumbing(prefill_replicas: int) -> bool:
    """Gate 1: a prefill pool exists, so KV transfer is on and needs its plumbing.

    Deliberately the same predicate #846 uses for `vllmCommon.kvTransfer`, so the
    flag and the plumbing that makes it work can never be emitted apart.
    """
    return bool(prefill_replicas) and prefill_replicas > 0


def needs_multigpu_plumbing(tp: int, dp: int) -> bool:
    """Gate 2: more than one GPU in a pod, so collectives and /dev/shm matter.

    Issue #848 words this gate as `tensor_parallel_size > 1`. It is implemented as
    `tp > 1 or dp > 1` because GPUs-per-pod is tensor x dataLocal and both
    generators set `dataLocal: dp` -- so dp>1 with tp=1 is also a multi-GPU pod
    running the same collectives against the same /dev/shm. This is also the exact
    predicate the adjacent `parallelism` block already uses in both generators, so
    the plumbing appears precisely when a `parallelism` block appears rather than
    on a third gate spelling that has to be kept in sync. Strict superset of the
    issue's wording: it never emits less.
    """
    return tp > 1 or dp > 1


# ---------------------------------------------------------------------------
# Emitters
# ---------------------------------------------------------------------------

def routing_lines() -> list[str]:
    """`routing.connector` -- the sidecar half of the connector decision."""
    return [
        "  # Sidecar half of the connector decision the engine makes in",
        "  # vllmCommon.kvTransfer.connector. Both spellings come from one value",
        "  # (pd_plumbing.KV_CONNECTOR_*) so they cannot drift apart (#848).",
        "  routing:",
        f"    connector: {KV_CONNECTOR_SIDECAR}"
        "  # framework default, stated explicitly",
    ]


def preprocess_script_lines() -> list[str]:
    """`vllmCommon.preprocessScript` -- libcuda prologue, then source the env file.

    Emitted under `vllmCommon:`; the caller has already printed that key.

    NOT `vllm.customCommand`, which upstream uses for the same prologue:
    customCommand replaces the ENTIRE launch command, so every flag would have to
    be hand-written and nothing would track config.md any more. preprocessScript
    is prepended to the generated command instead (_macros.j2:90-95, :112), which
    keeps command generation intact.
    """
    return [
        "    # Sources the env file the preprocess init container writes. Without",
        "    # this the init container's work is discarded and NIXL initialises",
        "    # with no network or GPU-routing configuration -- the crashloop #848",
        "    # describes. The libcuda prologue must run before the source line.",
        "    preprocessScript: |",
        "      export LD_LIBRARY_PATH=$(find / -name libcuda.so.1 -printf '%h\\n' "
        "2>/dev/null | sed ':a; N; $!ba; s/\\n/:/g'):${LD_LIBRARY_PATH}",
        "      export LIBRARY_PATH=$(find / -name libcuda.so.1 -printf '%h\\n' "
        "2>/dev/null | head -1):${LIBRARY_PATH}",
        f"      . {_ENV_FILE}",
    ]


def volume_lines(shared_config: bool, dshm: bool) -> list[str]:
    """`vllmCommon.volumes` + `volumeMounts`, accumulated across both gates.

    Emitted under `vllmCommon:`; the caller has already printed that key. Returns
    [] when neither gate fires, which is what keeps existing bundles
    byte-identical. Both gates write the same two keys, so they are rendered
    together in one pass rather than appended by each gate independently.

    Upstream defaults both keys to [] (defaults.yaml:803-804, with a comment
    saying scenarios must define their own), so nothing is inherited here.
    """
    if not shared_config and not dshm:
        return []

    volumes: list[str] = []
    mounts: list[str] = []

    if shared_config:
        volumes += [
            "    # Handoff between the preprocess init container (writes) and the",
            "    # vLLM container's preprocessScript (reads). Plain emptyDir, not",
            "    # memory-backed -- a few KB of shell exports.",
            "    - name: shared-config",
            "      type: emptyDir",
            "      emptyDir: {}",
        ]
        mounts += [
            "    - name: shared-config",
            f"      mountPath: {_SHARED_CONFIG_MOUNT}",
        ]

    if dshm:
        volumes += [
            "    # Multi-GPU collectives use shared memory. The K8s default 64 MB",
            "    # /dev/shm is a latency-jitter and hang risk for a multi-GPU pod.",
            "    # medium: Memory is tmpfs, so this charges against the pod's",
            "    # memory limit -- see #850 before setting one below this size.",
            "    - name: dshm",
            "      type: emptyDir",
            "      emptyDir:",
            "        medium: Memory",
            f"        sizeLimit: {_DSHM_SIZE_LIMIT}",
        ]
        mounts += [
            "    - name: dshm",
            "      mountPath: /dev/shm",
        ]

    return ["    volumes:"] + volumes + ["    volumeMounts:"] + mounts


def init_container_lines() -> list[str]:
    """The `preprocess` init container, for `decode.initContainers` /
    `prefill.initContainers`.

    Emitted inside a role block (`decode:` / `prefill:`), which the caller has
    already printed.

    Declares its own volumeMounts because render_init_container (_macros.j2:52-54)
    emits only `ic.volumeMounts` -- there is no inheritance from
    vllmCommon.volumeMounts, and without the mount this container cannot write the
    env file.

    Declares NO `env`, deliberately: _macros.j2:28-31 substitutes
    build_ms_env_vars(mode) only for an init container that declares none, and
    set_llmdbench_environment.py reads NVSHMEM_DEBUG from its own environment
    (:539-541). Adding an `env` block here would suppress that inheritance.

    Declares NO `resources`, and must not: pipeline/lib/capacity.py:98-99 excludes
    initContainers from GPU accounting on the stated assumption that llm-d
    workloads have no GPU-requesting init containers. Giving this container a GPU
    request would silently under-count demand in the capacity probe.
    """
    return [
        "    # Computes network and GPU-routing values and writes them to the",
        "    # shared-config volume for preprocessScript to source. One unit with",
        "    # those two -- any one missing makes the other two inert (#848).",
        "    initContainers:",
        "    - name: preprocess",
        "      imageKey: benchmark",
        "      imagePullPolicy: Always",
        '      command: ["set_llmdbench_environment.py", "-e", '
        f'"{_ENV_FILE}", "-i"]',
        "      # Required explicitly: init containers do not inherit",
        "      # vllmCommon.volumeMounts (_macros.j2:52-54).",
        "      volumeMounts:",
        "      - name: shared-config",
        f"        mountPath: {_SHARED_CONFIG_MOUNT}",
    ]


def extra_env_var_lines(nixl: bool, multigpu: bool) -> list[str]:
    """`extraEnvVars` for one role block, accumulated across both gates.

    Emitted inside a role block (`decode:` / `prefill:`), which the caller has
    already printed. Per-role because upstream has no vllmCommon.extraEnvVars --
    only decode (defaults.yaml:897), prefill (:1034) and standalone (:1223).

    These reach the init container too: an init container declaring no `env`
    inherits build_ms_env_vars(mode) (_macros.j2:28-31), which emits
    config.extraEnvVars (:307-313). That inheritance is why NVSHMEM_DEBUG works.
    """
    if not nixl and not multigpu:
        return []

    lines = ["    extraEnvVars:"]

    if nixl:
        lines += [
            "    # Diagnostics only -- NIXL init failures are otherwise silent in",
            "    # the worker log. Safe to drop if log volume becomes a problem.",
            "    - name: NIXL_LOG_LEVEL",
            "      value: debug",
        ]

    if multigpu:
        lines += [
            "    - name: NCCL_DEBUG",
            '      value: "INFO"',
            "    # NOT just a log level: set_llmdbench_environment.py:539-541 adds",
            "    # NVSHMEM_HCA_LIST to the generated env file only when this is not",
            '    # "none". Dropping it silently removes that entry.',
            "    - name: NVSHMEM_DEBUG",
            '      value: "INFO"',
        ]

    return lines

# P/D and Multi-GPU Pod Plumbing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make bootstrap emit the pod plumbing that lets NIXL actually initialise, on the same gates that already emit `vllmCommon.kvTransfer`, so a bootstrapped P/D bundle stops crashlooping.

**Architecture:** A new shared module `pd_plumbing.py` holds one connector decision (two spellings), the gate predicates, and line-emitter helpers that render each YAML fragment. Both hand-rolled generators (`generate_from_config.py`, `generate_scenarios.py`) import it and call the same emitters, so the two paths cannot drift. Both gates are conditional and additive: when neither fires, output is byte-identical to today.

**Tech Stack:** Python 3.10+, pytest, PyYAML. No new dependencies.

**Spec:** GitHub issue #848 (`bootstrap: emit the P/D and multi-GPU pod plumbing on the same gates as #846, or kvTransfer:true crashloops`). Vetted against HEAD `bc9c7a9`; submodule pin `llm-d-benchmark@76473d0`.

> **Superseded in one place.** Gate 2's predicate changed after review, from
> `needs_multigpu_plumbing(tp, dp) -> tp > 1 or dp > 1` to
> `needs_multigpu_plumbing(tensor, data_local) -> tensor * data_local > 1`. See
> **D1-revised** below for why. The Task 1 / 2 / 3 code blocks in this plan still
> show the original signature and the `(tp, dp)` call sites; `pd_plumbing.py` and
> the two generators are the current truth. Everything else in this plan shipped
> as written.

## Global Constraints

- Python >= 3.10. No new third-party dependencies.
- CI must pass: `ruff check pipeline/ .claude/skills/ --select F`, then pytest with `--cov=pipeline --cov-fail-under=90`.
- **Byte-identity is the primary regression contract.** A `config.md` with no prefill pod count and `tensor_parallel_size <= 1` (and `data_parallel_size <= 1`) must regenerate byte-for-byte identical output. Every new key is emitted only inside a gate.
- **Both generators, always.** `generate_from_config.py` (config.md path) and `generate_scenarios.py` (top3_selection.json path) each emit their own prefill block and each hand-roll their own YAML. #846's commit message records that fixing only one leaves half of bootstrap broken. Every behavior change in this plan lands in both.
- Both emitters are hand-rolled line appenders. **A key added to the scenario dict is silently dropped unless an emitter branch prints it.** Tests must assert on emitted text, not on the intermediate dict.
- New keys are appended *after* existing keys within their block, so unchanged bytes stay unchanged.
- Exact upstream values, copied verbatim from `llm-d-benchmark@76473d0`:
  - `NIXL_LOG_LEVEL: debug` (`config/scenarios/guides/pd-disaggregation.yaml`)
  - `NCCL_DEBUG: "INFO"` (`config/scenarios/guides/wide-ep-lws.yaml`)
  - `NVSHMEM_DEBUG: "INFO"` (both of the above)
  - `dshm` emptyDir `medium: Memory`, `sizeLimit: 16Gi` (5 of the 7 guides that set a limit; 20Gi and 32Gi are outliers)
  - init container command: `["set_llmdbench_environment.py", "-e", "/shared-config/llmdbench_env.sh", "-i"]`, `imageKey: benchmark`
  - `routing.connector: nixlv2`

## Design decisions locked before implementation

**D1 — Gate 2 is `tensor × dataLocal > 1`, not `tp > 1`.** The issue says
`tensor_parallel_size > 1`.

This decision was **revised after review** (see D1-revised below); the original
form is kept here because the revision is the interesting part.

*Originally implemented as `tp > 1 or dp > 1`*, reasoned as: GPUs-per-pod is
`tensor × dataLocal`, both generators set `dataLocal: dp`, so `dp>1, tp=1` is also
a multi-GPU pod. That reasoning states the formula correctly and then gates on the
wrong variable — `dp` is the deployment-wide DP degree, `dataLocal` is the per-pod
one.

**D1-revised — the gate consults `dataLocal`, and is expressed as the product.**
Upstream resolves the accelerator count itself at `13_ms-values.yaml.j2:269-271`:
`accelerator.count if explicit, else tensor * dataLocal`, commented "each DP-local
rank needs its own GPU". Bootstrap never emits `accelerator.count`, only
`acceleratorType`, so for generated scenarios the product *is* the count.

`data` counts ranks across the whole deployment; `dataLocal` counts the ones in
this pod. A `data: 8, dataLocal: 1, tensor: 1` scenario is a single-GPU pod whose
siblings communicate over the network — no intra-pod shared-memory collectives, so
no `/dev/shm` pressure and nothing for NCCL/NVSHMEM to do locally. Gating on `data`
would emit a 16Gi tmpfs (charged against the pod memory limit, #850) plus dead env
vars for a pod that needs none.

Latent, not live: both generators feed `dataLocal` from the single
`data_parallel_size` input, so `data == dataLocal` today and all 18 tp×dp×prefill
combinations emit byte-identically under either predicate (verified by diff). It
goes live with #843's multinode/LWS per-pod split — precisely when a gate on the
wrong quantity would begin silently over-emitting.

Still a deliberate superset of the issue's literal wording: it also fires for an
intra-pod data-parallel pod (`dataLocal > 1, tensor == 1`), which is multi-GPU by
the same mechanism the issue argues from. The gap between the issue's wording and
this gate is now just that case, rather than every `dp > 1` deployment.

Expressed as `tensor * data_local > 1` rather than the equivalent disjunction so it
reads as the same quantity upstream computes. (A control fault-injection confirms
the two forms are behaviourally identical for integers ≥ 1, so this is a
readability choice, not a behavioural one.)

**D2 — One connector decision, two spellings.** The issue requires that `kvTransfer.connector` (engine) and `routing.connector` (sidecar) "come from one 'which connector' value". Implemented as two module constants in one place with a comment binding them, and the existing `"NixlConnector"` literals in both generators replaced by the constant. Changing connectors is then a one-place edit.

**D3 — Shared module rather than copy-paste.** The two generators currently duplicate `_WORKERS_COMMENT`, `resolve_role_hardware`, `MODEL_METADATA`, and `build_additional_flags`, so duplication is the established local convention. This change would add ~60 lines of *identical literal YAML text* to each — the highest-drift-risk kind of duplication, and #842 was already a "sync the drifted templates" commit. The fragments go in one module. Import works in both contexts: running `python generate_*.py` puts the script's dir on `sys.path[0]`, and the tests already do `sys.path.insert(0, Path(__file__).parents[1])`.

**D4 — `extraEnvVars` is per-role only.** `defaults.yaml` has `extraEnvVars` under `decode:` (:897), `prefill:` (:1034), and `standalone:` (:1223). There is no `vllmCommon.extraEnvVars`. So the env vars are emitted into each role block that exists. Verified they do reach the init container: `_macros.j2:28-31` (`render_init_container`) falls back to `build_ms_env_vars(mode)` when the init container declares no `env`, and `build_ms_env_vars` emits `config.extraEnvVars` (`_macros.j2:307-313`). This matters because `set_llmdbench_environment.py:539-541` reads `NVSHMEM_DEBUG` from the *init container's* environment.

**D5 — The init container declares its own `volumeMounts`.** `render_init_container` (`_macros.j2:52-54`) emits only `ic.volumeMounts`; there is no inheritance from `vllmCommon.volumeMounts`. Without an explicit mount the init container cannot write the env file and Gate 1 silently does nothing.

**D6 — `NVSHMEM_DEBUG` is load-bearing, not a log knob.** `set_llmdbench_environment.py:539-541` appends `NVSHMEM_HCA_LIST` to the generated env file only when `NVSHMEM_DEBUG != "none"`. This is the real justification for emitting it and belongs in the provenance comment. `NIXL_LOG_LEVEL` has no such mechanism — it is diagnostics only, and its comment must say so honestly.

---

## File Structure

| File | Responsibility |
|---|---|
| `.claude/skills/sim2real-bootstrap/pd_plumbing.py` | **Create.** Connector constants, gate predicates, and one line-emitter per YAML fragment. The single source of truth for the emitted text. |
| `.claude/skills/sim2real-bootstrap/generate_from_config.py` | **Modify.** `build_scenario` populates the new keys; `write_provenance_yaml` emits them via `pd_plumbing`. |
| `.claude/skills/sim2real-bootstrap/generate_scenarios.py` | **Modify.** `build_scenario` and `write_commented_yaml`, same shape. |
| `.claude/skills/sim2real-bootstrap/tests/test_pd_plumbing.py` | **Create.** Unit tests for the emitter helpers in isolation. |
| `.claude/skills/sim2real-bootstrap/tests/test_generate_from_config_prefill.py` | **Modify.** Gate behavior + byte-identity through the config.md path. |
| `.claude/skills/sim2real-bootstrap/tests/test_generate_scenarios.py` | **Modify.** Same gates through the JSON path. |
| `.claude/skills/sim2real-bootstrap/SKILL.md` | **Modify.** Document both gates and the emitted keys. |

Emitted key placement (all appended after existing content in their block):

```
scenario:
- name: ...
  model: ...
  vllmCommon:
    flags: ...              # existing
    kvTransfer: ...         # existing (#846)
    preprocessScript: ...   # NEW  Gate 1
    volumes: ...            # NEW  Gate 1 (shared-config) + Gate 2 (dshm)
    volumeMounts: ...       # NEW  same
  decode:
    ... existing ...
    initContainers: ...     # NEW  Gate 1
    extraEnvVars: ...       # NEW  Gate 1 (NIXL) + Gate 2 (NCCL/NVSHMEM)
  prefill:                  # existing, Gate 1 by construction
    ... existing ...
    initContainers: ...     # NEW  Gate 1
    extraEnvVars: ...       # NEW  Gate 1 + Gate 2
  routing:                  # NEW  Gate 1
    connector: nixlv2
```

---

### Task 1: `pd_plumbing.py` — constants, gates, and emitters

**Files:**
- Create: `.claude/skills/sim2real-bootstrap/pd_plumbing.py`
- Test: `.claude/skills/sim2real-bootstrap/tests/test_pd_plumbing.py`

**Interfaces:**
- Consumes: nothing (leaf module, stdlib only).
- Produces, relied on by Tasks 2 and 3:
  - `KV_CONNECTOR_ENGINE: str` == `"NixlConnector"`
  - `KV_CONNECTOR_SIDECAR: str` == `"nixlv2"`
  - `needs_kv_plumbing(prefill_replicas: int) -> bool`
  - `needs_multigpu_plumbing(tp: int, dp: int) -> bool`
  - `routing_lines() -> list[str]`
  - `preprocess_script_lines() -> list[str]`
  - `volume_lines(shared_config: bool, dshm: bool) -> list[str]`
  - `init_container_lines() -> list[str]`
  - `extra_env_var_lines(nixl: bool, multigpu: bool) -> list[str]`

  Every `*_lines` helper returns YAML lines already indented for a scenario emitted as a
  list item under `scenario:` — i.e. scenario-level keys at 2 spaces, their children at 4.
  Helpers return `[]` when they have nothing to emit. None append a trailing blank line;
  callers own blank-line separation.

- [ ] **Step 1: Write the failing tests**

Create `.claude/skills/sim2real-bootstrap/tests/test_pd_plumbing.py`:

```python
"""Tests for pd_plumbing.py — the shared P/D and multi-GPU pod-plumbing fragments.

Covers issue #848. These are the fragments that make NIXL actually initialise;
#846 emitted `kvTransfer.enabled: true` without them, which crashloops.

The emitters are tested in isolation here. The gates that decide *when* they are
called live in the two generators and are tested in their own files.
"""
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parents[1]))
import pd_plumbing as pdp


def parse_fragment(lines: list[str]) -> dict:
    """Parse emitter output as the body of a scenario list item.

    The helpers emit scenario-level keys at 2-space indent, matching a scenario
    rendered as `- name: ...` under `scenario:`. Wrapping them in the same
    envelope the real emitters use is what makes the indentation contract
    testable rather than assumed.
    """
    doc = "scenario:\n- name: t\n" + "\n".join(lines) + "\n"
    return yaml.safe_load(doc)["scenario"][0]


# --- One connector decision, two spellings (issue #848) --------------------

def test_connector_constants_are_the_two_spellings():
    assert pdp.KV_CONNECTOR_ENGINE == "NixlConnector"
    assert pdp.KV_CONNECTOR_SIDECAR == "nixlv2"


def test_routing_connector_uses_the_sidecar_constant():
    """The engine and sidecar halves must not be able to drift apart."""
    parsed = parse_fragment(pdp.routing_lines())
    assert parsed["routing"]["connector"] == pdp.KV_CONNECTOR_SIDECAR


# --- Gate predicates -------------------------------------------------------

@pytest.mark.parametrize("replicas,expected", [(0, False), (1, True), (8, True)])
def test_needs_kv_plumbing_gates_on_prefill_count(replicas, expected):
    assert pdp.needs_kv_plumbing(replicas) is expected


@pytest.mark.parametrize(
    "tp,dp,expected",
    [
        (1, 1, False),
        (2, 1, True),
        # dataLocal == dp in both generators, so dp>1 also means several GPUs in
        # one pod -- same collectives, same /dev/shm pressure. Issue #848 says
        # "tensor_parallel_size > 1"; this is the deliberate superset (plan D1).
        (1, 2, True),
        (2, 2, True),
    ],
)
def test_needs_multigpu_plumbing_gates_on_either_degree(tp, dp, expected):
    assert pdp.needs_multigpu_plumbing(tp, dp) is expected


# --- preprocessScript ------------------------------------------------------

def test_preprocess_script_sources_the_env_file():
    parsed = parse_fragment(pdp.preprocess_script_lines())
    script = parsed["vllmCommon"]["preprocessScript"]
    assert ". /shared-config/llmdbench_env.sh" in script


def test_preprocess_script_carries_the_libcuda_prologue():
    parsed = parse_fragment(pdp.preprocess_script_lines())
    script = parsed["vllmCommon"]["preprocessScript"]
    assert "LD_LIBRARY_PATH" in script
    assert "LIBRARY_PATH" in script
    assert "libcuda.so.1" in script


def test_preprocess_script_prologue_precedes_the_source():
    """The prologue exports paths the sourced file's consumers need; order matters."""
    script = parse_fragment(pdp.preprocess_script_lines())["vllmCommon"]["preprocessScript"]
    assert script.index("libcuda.so.1") < script.index("llmdbench_env.sh")


def test_preprocess_script_is_not_a_custom_command():
    """`vllm.customCommand` replaces the whole launch command, dropping every
    generated flag. preprocessScript is prepended instead (issue #848)."""
    text = "\n".join(pdp.preprocess_script_lines())
    assert "customCommand" not in text
    assert "vllm serve" not in text


# --- volumes / volumeMounts ------------------------------------------------

def test_volume_lines_empty_when_neither_gate_fires():
    assert pdp.volume_lines(shared_config=False, dshm=False) == []


def test_shared_config_volume_only():
    parsed = parse_fragment(pdp.volume_lines(shared_config=True, dshm=False))
    names = [v["name"] for v in parsed["vllmCommon"]["volumes"]]
    mounts = {m["name"]: m["mountPath"] for m in parsed["vllmCommon"]["volumeMounts"]}
    assert names == ["shared-config"]
    assert mounts == {"shared-config": "/shared-config"}


def test_dshm_volume_only():
    parsed = parse_fragment(pdp.volume_lines(shared_config=False, dshm=True))
    vols = parsed["vllmCommon"]["volumes"]
    mounts = {m["name"]: m["mountPath"] for m in parsed["vllmCommon"]["volumeMounts"]}
    assert [v["name"] for v in vols] == ["dshm"]
    assert vols[0]["emptyDir"]["medium"] == "Memory"
    assert vols[0]["emptyDir"]["sizeLimit"] == "16Gi"
    assert mounts == {"dshm": "/dev/shm"}


def test_both_volumes_accumulate_into_one_list():
    """The two gates contribute to the same key; a second gate must not clobber
    the first."""
    parsed = parse_fragment(pdp.volume_lines(shared_config=True, dshm=True))
    assert [v["name"] for v in parsed["vllmCommon"]["volumes"]] == [
        "shared-config",
        "dshm",
    ]
    assert [m["name"] for m in parsed["vllmCommon"]["volumeMounts"]] == [
        "shared-config",
        "dshm",
    ]


def test_shared_config_is_a_plain_emptydir_not_memory_backed():
    """dshm is tmpfs; shared-config is a plain emptyDir. Making shared-config
    memory-backed would charge the env file against the pod memory limit."""
    parsed = parse_fragment(pdp.volume_lines(shared_config=True, dshm=False))
    assert parsed["vllmCommon"]["volumes"][0]["emptyDir"] == {}


# --- init container --------------------------------------------------------

def test_init_container_runs_the_env_generator():
    parsed = parse_fragment(pdp.init_container_lines())
    ic = parsed["initContainers"][0]
    assert ic["name"] == "preprocess"
    assert ic["command"] == [
        "set_llmdbench_environment.py",
        "-e",
        "/shared-config/llmdbench_env.sh",
        "-i",
    ]


def test_init_container_declares_its_own_shared_config_mount():
    """render_init_container (_macros.j2:52-54) emits only ic.volumeMounts --
    there is no inheritance from vllmCommon.volumeMounts, so without this the
    init container cannot write the env file and Gate 1 does nothing."""
    ic = parse_fragment(pdp.init_container_lines())["initContainers"][0]
    mounts = {m["name"]: m["mountPath"] for m in ic["volumeMounts"]}
    assert mounts == {"shared-config": "/shared-config"}


def test_init_container_uses_the_benchmark_image_key():
    """imageKey resolves through images.* upstream; a literal image would pin a
    tag this bundle has no way to track."""
    ic = parse_fragment(pdp.init_container_lines())["initContainers"][0]
    assert ic["imageKey"] == "benchmark"
    assert "image" not in ic


def test_init_container_declares_no_env_so_it_inherits_extra_env_vars():
    """_macros.j2:28-31 falls back to build_ms_env_vars(mode) only when the init
    container declares no `env`. set_llmdbench_environment.py:539-541 reads
    NVSHMEM_DEBUG from this container's environment, so the fallback is required."""
    ic = parse_fragment(pdp.init_container_lines())["initContainers"][0]
    assert "env" not in ic


# --- extraEnvVars ----------------------------------------------------------

def test_extra_env_vars_empty_when_neither_gate_fires():
    assert pdp.extra_env_var_lines(nixl=False, multigpu=False) == []


def _env_map(lines):
    parsed = parse_fragment(lines)
    return {e["name"]: e["value"] for e in parsed["extraEnvVars"]}


def test_nixl_gate_emits_only_the_nixl_var():
    assert _env_map(pdp.extra_env_var_lines(nixl=True, multigpu=False)) == {
        "NIXL_LOG_LEVEL": "debug"
    }


def test_multigpu_gate_emits_the_collective_vars():
    assert _env_map(pdp.extra_env_var_lines(nixl=False, multigpu=True)) == {
        "NCCL_DEBUG": "INFO",
        "NVSHMEM_DEBUG": "INFO",
    }


def test_both_gates_accumulate_into_one_list():
    assert _env_map(pdp.extra_env_var_lines(nixl=True, multigpu=True)) == {
        "NIXL_LOG_LEVEL": "debug",
        "NCCL_DEBUG": "INFO",
        "NVSHMEM_DEBUG": "INFO",
    }


def test_nvshmem_debug_justification_cites_the_env_file_gate():
    """NVSHMEM_DEBUG is load-bearing, not diagnostics: it gates NVSHMEM_HCA_LIST
    into the generated env file (set_llmdbench_environment.py:539-541). A reader
    who thinks it is only a log level will delete it."""
    text = "\n".join(pdp.extra_env_var_lines(nixl=False, multigpu=True))
    assert "NVSHMEM_HCA_LIST" in text


def test_every_fragment_is_parseable_yaml_at_scenario_indent():
    """Indentation is the whole contract for a hand-rolled emitter."""
    for lines in (
        pdp.routing_lines(),
        pdp.preprocess_script_lines(),
        pdp.volume_lines(shared_config=True, dshm=True),
        pdp.init_container_lines(),
        pdp.extra_env_var_lines(nixl=True, multigpu=True),
    ):
        assert parse_fragment(lines)  # raises on malformed YAML
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest .claude/skills/sim2real-bootstrap/tests/test_pd_plumbing.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'pd_plumbing'`.

- [ ] **Step 3: Write the implementation**

Create `.claude/skills/sim2real-bootstrap/pd_plumbing.py`:

```python
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


# NOTE: this signature and predicate were REVISED after review -- see D1-revised.
# The shipped version takes (tensor, data_local) and returns
# `tensor * data_local > 1`. The docstring below is superseded by the one in
# pd_plumbing.py; read that file, not this block, for the current rationale.
def needs_multigpu_plumbing(tensor: int, data_local: int) -> bool:
    """Gate 2: more than one GPU in a pod, so collectives and /dev/shm matter.

    GPUs-per-pod is `tensor x dataLocal` -- upstream's own arithmetic at
    13_ms-values.yaml.j2:269-271. `dataLocal`, NOT `data`: `data` is the
    deployment-wide DP degree, `dataLocal` is the ranks in THIS pod, and only the
    latter implies intra-pod shared-memory collectives.
    """
    return tensor * data_local > 1


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
    """
    return [
        "    # Computes network and GPU-routing values and writes them to the",
        "    # shared-config volume for preprocessScript to source. One unit with",
        "    # those two -- any one missing makes the other two inert (#848).",
        "    initContainers:",
        "    - name: preprocess",
        "      imageKey: benchmark",
        "      imagePullPolicy: Always",
        "      command: [\"set_llmdbench_environment.py\", \"-e\", "
        f"\"{_ENV_FILE}\", \"-i\"]",
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
            "      value: \"INFO\"",
            "    # NOT just a log level: set_llmdbench_environment.py:539-541 adds",
            "    # NVSHMEM_HCA_LIST to the generated env file only when this is not",
            "    # \"none\". Dropping it silently removes that entry.",
            "    - name: NVSHMEM_DEBUG",
            "      value: \"INFO\"",
        ]

    return lines
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest .claude/skills/sim2real-bootstrap/tests/test_pd_plumbing.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint**

Run: `ruff check .claude/skills/sim2real-bootstrap/ --select F`
Expected: no findings.

- [ ] **Step 6: Commit**

```bash
git add .claude/skills/sim2real-bootstrap/pd_plumbing.py \
        .claude/skills/sim2real-bootstrap/tests/test_pd_plumbing.py
git commit -m "feat(bootstrap): add pd_plumbing module for P/D and multi-GPU pod fragments (#848)"
```

---

### Task 2: Wire `generate_from_config.py` (the config.md path)

**Files:**
- Modify: `.claude/skills/sim2real-bootstrap/generate_from_config.py` — `build_scenario` (~:763-935), `write_provenance_yaml` (~:939-1045)
- Test: `.claude/skills/sim2real-bootstrap/tests/test_generate_from_config_prefill.py`

**Interfaces:**
- Consumes: every symbol listed in Task 1's Produces block, via `import pd_plumbing as pdp`.
- Produces: no new public symbols. `build_scenario`'s returned dict grows optional keys
  `scenario["routing"]`, `scenario["vllmCommon"]["preprocessScript"]`,
  `scenario["vllmCommon"]["volumes"]`, `scenario["vllmCommon"]["volumeMounts"]`,
  `scenario[role]["initContainers"]`, `scenario[role]["extraEnvVars"]`.

  **These dict keys are markers only — the hand-rolled emitter prints from the gate
  booleans, not by walking the dict.** Tests must assert on emitted text.

- [ ] **Step 1: Write the failing tests**

Append to `.claude/skills/sim2real-bootstrap/tests/test_generate_from_config_prefill.py`:

```python
# ---------------------------------------------------------------------------
# Pod plumbing gates (issue #848)
# ---------------------------------------------------------------------------
# #846 emits kvTransfer.enabled: true on a prefill pool. Without the plumbing
# below that flag crashloops the worker during "Initializing NIXL wrapper".
# Assertions are on emitted TEXT: both emitters are hand-rolled line appenders,
# so a key present in the scenario dict proves nothing about the output.


def test_no_gates_emits_no_plumbing(tmp_path):
    """The byte-identity contract: no prefill pool and a single-GPU pod must
    regenerate exactly what it did before #848."""
    scenario, prov = build([])
    text = emit(scenario, prov, tmp_path)
    for key in (
        "initContainers",
        "preprocessScript",
        "volumes:",
        "volumeMounts:",
        "extraEnvVars",
        "routing:",
        "shared-config",
        "dshm",
        "NIXL_LOG_LEVEL",
        "NCCL_DEBUG",
        "NVSHMEM_DEBUG",
    ):
        assert key not in text, f"{key} leaked into an ungated scenario"


def test_gate1_emits_the_full_kv_unit(tmp_path):
    scenario, prov = build([row("Number of prefill pods", "1")])
    text = emit(scenario, prov, tmp_path)
    parsed = yaml.safe_load(text)["scenario"][0]

    # init container on BOTH roles -- each role is a separate pod
    for role in ("decode", "prefill"):
        ics = parsed[role]["initContainers"]
        assert [ic["name"] for ic in ics] == ["preprocess"]
        assert ics[0]["command"][0] == "set_llmdbench_environment.py"

    assert ". /shared-config/llmdbench_env.sh" in parsed["vllmCommon"]["preprocessScript"]
    assert [v["name"] for v in parsed["vllmCommon"]["volumes"]] == ["shared-config"]
    assert parsed["routing"]["connector"] == "nixlv2"

    for role in ("decode", "prefill"):
        names = {e["name"] for e in parsed[role]["extraEnvVars"]}
        assert names == {"NIXL_LOG_LEVEL"}


def test_gate1_does_not_emit_gate2_pieces(tmp_path):
    """A single-GPU P/D pod needs no tmpfs and runs no collectives."""
    scenario, prov = build([row("Number of prefill pods", "1")])
    text = emit(scenario, prov, tmp_path)
    assert "dshm" not in text
    assert "NCCL_DEBUG" not in text
    assert "NVSHMEM_DEBUG" not in text


def test_gate2_emits_dshm_and_collective_vars_without_a_prefill_pool(tmp_path):
    """Gate 2 is independent of Gate 1: an aggregated multi-GPU pod gets the
    tmpfs and the collective variables and nothing else."""
    scenario, prov = build([row("tensor_parallel_size", "4")])
    text = emit(scenario, prov, tmp_path)
    parsed = yaml.safe_load(text)["scenario"][0]

    vols = parsed["vllmCommon"]["volumes"]
    assert [v["name"] for v in vols] == ["dshm"]
    assert vols[0]["emptyDir"]["medium"] == "Memory"
    assert {m["mountPath"] for m in parsed["vllmCommon"]["volumeMounts"]} == {"/dev/shm"}
    assert {e["name"] for e in parsed["decode"]["extraEnvVars"]} == {
        "NCCL_DEBUG",
        "NVSHMEM_DEBUG",
    }

    # Gate 1 pieces stay absent
    assert "prefill" not in parsed
    assert "initContainers" not in parsed["decode"]
    assert "preprocessScript" not in parsed["vllmCommon"]
    assert "routing" not in parsed


def test_gate2_fires_on_data_parallel_alone(tmp_path):
    """Plan D1: dataLocal == dp, so dp>1 is also several GPUs in one pod."""
    scenario, prov = build([row("data_parallel_size", "2")])
    parsed = yaml.safe_load(emit(scenario, prov, tmp_path))["scenario"][0]
    assert [v["name"] for v in parsed["vllmCommon"]["volumes"]] == ["dshm"]


def test_both_gates_accumulate_volumes_and_env(tmp_path):
    """The regression this shape invites: two gates writing the same two keys,
    the second clobbering the first."""
    scenario, prov = build(
        [row("Number of prefill pods", "2"), row("tensor_parallel_size", "4")]
    )
    parsed = yaml.safe_load(emit(scenario, prov, tmp_path))["scenario"][0]

    assert [v["name"] for v in parsed["vllmCommon"]["volumes"]] == [
        "shared-config",
        "dshm",
    ]
    assert [m["mountPath"] for m in parsed["vllmCommon"]["volumeMounts"]] == [
        "/shared-config",
        "/dev/shm",
    ]
    for role in ("decode", "prefill"):
        assert {e["name"] for e in parsed[role]["extraEnvVars"]} == {
            "NIXL_LOG_LEVEL",
            "NCCL_DEBUG",
            "NVSHMEM_DEBUG",
        }


def test_kv_transfer_and_routing_connector_agree(tmp_path):
    """The issue's requirement: both halves from one connector value. A bundle
    with the engine on NixlConnector and the sidecar on something else is the
    half-configuration #830 was."""
    import pd_plumbing as pdp

    scenario, prov = build([row("Number of prefill pods", "1")])
    parsed = yaml.safe_load(emit(scenario, prov, tmp_path))["scenario"][0]
    assert parsed["vllmCommon"]["kvTransfer"]["connector"] == pdp.KV_CONNECTOR_ENGINE
    assert parsed["routing"]["connector"] == pdp.KV_CONNECTOR_SIDECAR


def test_plumbing_coexists_with_enforce_eager_override(tmp_path):
    """vllmCommon now has four independent sub-keys. enforce_eager creates the
    dict first; a plain assignment anywhere downstream would drop it."""
    scenario, prov = build(
        [row("enforce_eager", "false"), row("Number of prefill pods", "1")]
    )
    parsed = yaml.safe_load(emit(scenario, prov, tmp_path))["scenario"][0]
    vc = parsed["vllmCommon"]
    assert vc["flags"]["enforceEager"] is False
    assert vc["kvTransfer"]["enabled"] is True
    assert "preprocessScript" in vc
    assert [v["name"] for v in vc["volumes"]] == ["shared-config"]


def test_emitted_plumbing_is_valid_yaml_in_every_gate_combination(tmp_path):
    """Hand-rolled emitter: indentation is the contract."""
    combos = [
        [],
        [row("Number of prefill pods", "1")],
        [row("tensor_parallel_size", "4")],
        [row("Number of prefill pods", "2"), row("tensor_parallel_size", "4")],
    ]
    for i, extra in enumerate(combos):
        scenario, prov = build(extra)
        parsed = yaml.safe_load(emit(scenario, prov, tmp_path / f"c{i}"))
        assert parsed["scenario"][0]["name"] == "test"
```

These use the helpers already defined at the top of this file — do not redefine them:

- `build(extra_rows: list[dict], name="test") -> (scenario, provenance)`
- `emit(scenario, provenance, tmp_path: Path) -> str`
- `row(param: str, value: str) -> dict`

`emit` writes `tmp_path / "baseline.yaml"`, so the last test passes a distinct
subdirectory per combination (`tmp_path / f"c{i}"`) to avoid overwriting. `pytest`
creates it lazily via `write_provenance_yaml`'s `os.makedirs`, so no `mkdir` is
needed.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest .claude/skills/sim2real-bootstrap/tests/test_generate_from_config_prefill.py -v -k "gate or plumbing or connector_agree"`
Expected: FAIL — `KeyError: 'initContainers'`, missing `routing`, etc. `test_no_gates_emits_no_plumbing` should PASS already (nothing is emitted yet); that is the byte-identity guard, and it must keep passing.

- [ ] **Step 3: Add the import and populate the scenario dict**

In the import block (~:18-23) add, after `from pathlib import Path`:

```python
import pd_plumbing as pdp
```

In `build_scenario`, replace the `"connector": "NixlConnector",` literal inside the
`scenario.setdefault("vllmCommon", {})["kvTransfer"] = {...}` assignment (~:875-879) with:

```python
            "connector": pdp.KV_CONNECTOR_ENGINE,
```

Then, immediately after the `prefill`/`elif "prefill_hardware"` block closes and
**before** the `# --- Build provenance map ---` comment (~:893), insert:

```python
    # --- Pod plumbing (issue #848) ---
    # #846 turns kvTransfer on with a prefill pool. On its own that crashloops the
    # worker during "Initializing NIXL wrapper", because nothing sets up the
    # network and GPU-routing state NIXL needs. Two gates, both additive; the
    # emitter reads these booleans, so they are computed once here.
    #
    # These dict keys are markers for the emitter and for tests that inspect the
    # scenario -- the hand-rolled emitter prints from the gate booleans below, not
    # by walking the dict.
    kv_plumbing = pdp.needs_kv_plumbing(prefill_replicas)
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
```

Then return the gate booleans alongside the existing values. Change the final
`return scenario, provenance` (~:935) to:

```python
    scenario["_gates"] = {"kv": kv_plumbing, "multigpu": multigpu_plumbing}

    return scenario, provenance
```

`_gates` is consumed and removed by the emitter; the leading underscore marks it
as not part of the emitted schema.

- [ ] **Step 4: Emit the new keys**

In `write_provenance_yaml`, read the gates at the top of the function, right after
`lines = []`:

```python
    gates = scenario.get("_gates", {"kv": False, "multigpu": False})
```

Inside the existing `if "vllmCommon" in scenario:` block, **after** the `kvTransfer`
branch closes, append:

```python
        if gates["kv"]:
            lines.extend(pdp.preprocess_script_lines())
        lines.extend(
            pdp.volume_lines(shared_config=gates["kv"], dshm=gates["multigpu"])
        )
```

Then, at the end of the decode block — after the `if "vllm" in scenario["decode"]:`
branch and **before** the `# Prefill role` comment:

```python
    if gates["kv"]:
        lines.extend(pdp.init_container_lines())
    lines.extend(
        pdp.extra_env_var_lines(nixl=gates["kv"], multigpu=gates["multigpu"])
    )
```

And the same pair at the end of the `if "prefill" in scenario:` block, after its
`if "vllm" in p_role:` branch (note this is inside the prefill `if`, so it is
reached only when a prefill block exists):

```python
        if gates["kv"]:
            lines.extend(pdp.init_container_lines())
        lines.extend(
            pdp.extra_env_var_lines(nixl=gates["kv"], multigpu=gates["multigpu"])
        )
```

Finally, after the prefill block and before the trailing `lines.append("")`:

```python
    if gates["kv"]:
        lines.append("")
        lines.extend(pdp.routing_lines())
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest .claude/skills/sim2real-bootstrap/tests/test_generate_from_config_prefill.py .claude/skills/sim2real-bootstrap/tests/test_generate_from_config.py -v`
Expected: all PASS, including the pre-existing tests and `test_no_gates_emits_no_plumbing`.

- [ ] **Step 6: Prove the byte-identity contract mechanically**

`_gates` is a new dict key on every returned scenario. Confirm it never reaches
output, and that an ungated scenario is byte-for-byte what `main` produced:

```bash
python - <<'PY'
import subprocess, sys, pathlib, tempfile
sys.path.insert(0, ".claude/skills/sim2real-bootstrap")
import generate_from_config as gfc
rows = [{"Parameter": "Model", "Value": "Qwen/Qwen3-14B", "Notes": ""},
        {"Parameter": "GPU", "Value": "H100_SXM_80GB", "Notes": ""}]
t = gfc.TableSection(heading="vLLM Pod Configuration", rows=rows, line_number=0)
s, p = gfc.build_scenario(gfc.extract_fields(t), "test")
out = pathlib.Path(tempfile.mkdtemp()) / "baseline.yaml"
gfc.write_provenance_yaml(s, p, str(out))
text = out.read_text()
assert "_gates" not in text, "internal gate marker leaked into output"
print(text)
PY
```

Then diff the same output against `main`'s:

```bash
git stash && python - <<'PY' > /tmp/848-before.yaml
import sys, pathlib, tempfile
sys.path.insert(0, ".claude/skills/sim2real-bootstrap")
import generate_from_config as gfc
rows = [{"Parameter": "Model", "Value": "Qwen/Qwen3-14B", "Notes": ""},
        {"Parameter": "GPU", "Value": "H100_SXM_80GB", "Notes": ""}]
t = gfc.TableSection(heading="vLLM Pod Configuration", rows=rows, line_number=0)
s, p = gfc.build_scenario(gfc.extract_fields(t), "test")
out = pathlib.Path(tempfile.mkdtemp()) / "b.yaml"
gfc.write_provenance_yaml(s, p, str(out)); print(out.read_text(), end="")
PY
git stash pop
```

Regenerate the same scenario with the change applied into `/tmp/848-after.yaml`
using the identical snippet, then `diff /tmp/848-before.yaml /tmp/848-after.yaml`.
Expected: **no output.** Any diff is a byte-identity regression — stop and fix.

- [ ] **Step 7: Commit**

```bash
git add .claude/skills/sim2real-bootstrap/generate_from_config.py \
        .claude/skills/sim2real-bootstrap/tests/test_generate_from_config_prefill.py
git commit -m "fix(bootstrap): emit P/D and multi-GPU pod plumbing from config.md (#848)"
```

---

### Task 3: Wire `generate_scenarios.py` (the top3_selection.json path)

**Files:**
- Modify: `.claude/skills/sim2real-bootstrap/generate_scenarios.py` — `build_scenario` (~:194-313), `write_commented_yaml` (~:316-420)
- Test: `.claude/skills/sim2real-bootstrap/tests/test_generate_scenarios.py`

**Interfaces:**
- Consumes: the same `pd_plumbing` symbols as Task 2, so the two paths emit identical text.
- Produces: no new public symbols; same optional dict keys and the same `_gates` marker.

This is the generator the issue does not mention. It has its own prefill block with
the identical hole — #846's commit message records that fixing only one generator
"would have left half of bootstrap emitting an inert pool."

- [ ] **Step 1: Write the failing tests**

Append to `.claude/skills/sim2real-bootstrap/tests/test_generate_scenarios.py`. It
already defines these at the top — use them, do not redefine them:

- `entry(workload_extra: dict | None = None, vllm_extra: dict | None = None) -> dict`
  (note the argument order: workload first. Defaults include `num_instances: 2` and
  `max_num_seqs: 256`.)
- `emit(scenario: dict, src: dict, tmp_path: Path) -> str` — takes the **built
  scenario**, not the entry, and writes `tmp_path / "cand.yaml"`.
- `gs.build_scenario(entry: dict, name: str) -> dict`

Add one local helper that chains them, since every test below needs the same
three-step build-then-emit:

```python
def build_and_emit(tmp_path, **vllm_extra):
    """entry -> build_scenario -> write_commented_yaml -> text."""
    e = entry(vllm_extra=vllm_extra)
    return emit(gs.build_scenario(e, "test"), e, tmp_path)
```

```python
# ---------------------------------------------------------------------------
# Pod plumbing gates (issue #848) -- parity with generate_from_config.py
# ---------------------------------------------------------------------------
# This generator has its own prefill block and its own hand-rolled emitter. #846
# had to fix both; so does #848. Any drift between the two paths means half of
# bootstrap emits an inert or crashlooping bundle.


def build_and_emit(tmp_path, **vllm_extra):
    """entry -> build_scenario -> write_commented_yaml -> text."""
    e = entry(vllm_extra=vllm_extra)
    return emit(gs.build_scenario(e, "test"), e, tmp_path)


def test_no_gates_emits_no_plumbing_json_path(tmp_path):
    text = build_and_emit(tmp_path)
    for key in ("initContainers", "preprocessScript", "volumes:", "extraEnvVars",
                "routing:", "shared-config", "dshm", "NIXL_LOG_LEVEL",
                "NCCL_DEBUG", "NVSHMEM_DEBUG"):
        assert key not in text, f"{key} leaked into an ungated scenario"


def test_gate1_emits_the_full_kv_unit_json_path(tmp_path):
    text = build_and_emit(tmp_path, prefill_instances=1)
    parsed = yaml.safe_load(text)["scenario"][0]
    for role in ("decode", "prefill"):
        assert parsed[role]["initContainers"][0]["name"] == "preprocess"
        assert {e["name"] for e in parsed[role]["extraEnvVars"]} == {"NIXL_LOG_LEVEL"}
    assert ". /shared-config/llmdbench_env.sh" in parsed["vllmCommon"]["preprocessScript"]
    assert [v["name"] for v in parsed["vllmCommon"]["volumes"]] == ["shared-config"]
    assert parsed["routing"]["connector"] == "nixlv2"


def test_gate2_emits_dshm_without_prefill_json_path(tmp_path):
    text = build_and_emit(tmp_path, tensor_parallel_size=4)
    parsed = yaml.safe_load(text)["scenario"][0]
    assert [v["name"] for v in parsed["vllmCommon"]["volumes"]] == ["dshm"]
    assert {e["name"] for e in parsed["decode"]["extraEnvVars"]} == {
        "NCCL_DEBUG", "NVSHMEM_DEBUG"}
    assert "routing" not in parsed
    assert "prefill" not in parsed


def test_gate2_fires_on_data_parallel_alone_json_path(tmp_path):
    text = build_and_emit(tmp_path, data_parallel_size=2)
    parsed = yaml.safe_load(text)["scenario"][0]
    assert [v["name"] for v in parsed["vllmCommon"]["volumes"]] == ["dshm"]


def test_both_gates_accumulate_json_path(tmp_path):
    text = build_and_emit(tmp_path, prefill_instances=2, tensor_parallel_size=4)
    parsed = yaml.safe_load(text)["scenario"][0]
    assert [v["name"] for v in parsed["vllmCommon"]["volumes"]] == [
        "shared-config", "dshm"]
    for role in ("decode", "prefill"):
        assert {e["name"] for e in parsed[role]["extraEnvVars"]} == {
            "NIXL_LOG_LEVEL", "NCCL_DEBUG", "NVSHMEM_DEBUG"}


def test_plumbing_coexists_with_enforce_eager_json_path(tmp_path):
    text = build_and_emit(tmp_path, prefill_instances=1, enforce_eager=False)
    vc = yaml.safe_load(text)["scenario"][0]["vllmCommon"]
    assert vc["flags"]["enforceEager"] is False
    assert vc["kvTransfer"]["enabled"] is True
    assert "preprocessScript" in vc


def test_both_generators_emit_identical_plumbing_text(tmp_path):
    """The anti-drift assertion. Two hand-rolled emitters, one set of fragments:
    the plumbing lines must match character-for-character, or one path is wrong."""
    import pd_plumbing as pdp
    import generate_from_config as gfc

    rows = [
        {"Parameter": "Model", "Value": "Qwen/Qwen3-14B", "Notes": ""},
        {"Parameter": "GPU", "Value": "H100_SXM_80GB", "Notes": ""},
        {"Parameter": "Number of prefill pods", "Value": "2", "Notes": ""},
        {"Parameter": "tensor_parallel_size", "Value": "4", "Notes": ""},
    ]
    table = gfc.TableSection(
        heading="vLLM Pod Configuration", rows=rows, line_number=0)
    s, p = gfc.build_scenario(gfc.extract_fields(table), "test")
    out = tmp_path / "from_config.yaml"
    gfc.write_provenance_yaml(s, p, str(out))
    from_config_text = out.read_text()

    # Same directory is safe: gfc writes baseline.yaml, gs writes cand.yaml.
    # Do NOT pass a subdirectory here -- write_commented_yaml does not makedirs
    # (unlike write_provenance_yaml, which does).
    json_text = build_and_emit(tmp_path, prefill_instances=2, tensor_parallel_size=4)

    for fragment_lines in (
        pdp.routing_lines(),
        pdp.preprocess_script_lines(),
        pdp.volume_lines(shared_config=True, dshm=True),
        pdp.init_container_lines(),
        pdp.extra_env_var_lines(nixl=True, multigpu=True),
    ):
        block = "\n".join(fragment_lines)
        assert block in from_config_text, "config.md path drifted from pd_plumbing"
        assert block in json_text, "json path drifted from pd_plumbing"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest .claude/skills/sim2real-bootstrap/tests/test_generate_scenarios.py -v -k "gate or plumbing or identical"`
Expected: FAIL on the gate tests; `test_no_gates_emits_no_plumbing_json_path` should PASS already.

- [ ] **Step 3: Add the import and populate the scenario dict**

In the import block (~:11-15) add after `from pathlib import Path`:

```python
import pd_plumbing as pdp
```

Replace the `"connector": "NixlConnector",` literal in the `kvTransfer` assignment
(~:299-303) with:

```python
            "connector": pdp.KV_CONNECTOR_ENGINE,
```

Then, immediately before `return scenario` at the end of `build_scenario` (~:313),
insert — note `prefill_replicas` here is `vllm_args.get("prefill_instances")`, which
may be `None`, so it is normalised:

```python
    # --- Pod plumbing (issue #848) ---
    # Same two gates and the same fragments as generate_from_config.py. Both
    # generators must emit this identically; see pd_plumbing's module docstring.
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

    scenario["_gates"] = {"kv": kv_plumbing, "multigpu": multigpu_plumbing}

    return scenario
```

- [ ] **Step 4: Emit the new keys**

In `write_commented_yaml`, after `lines = []`:

```python
    gates = scenario.get("_gates", {"kv": False, "multigpu": False})
```

Inside `if "vllmCommon" in scenario:`, after the `kvTransfer` branch:

```python
        if gates["kv"]:
            lines.extend(pdp.preprocess_script_lines())
        lines.extend(
            pdp.volume_lines(shared_config=gates["kv"], dshm=gates["multigpu"])
        )
```

At the end of the decode block, after `if "vllm" in scenario["decode"]:` and before
the `# Prefill role` comment:

```python
    if gates["kv"]:
        lines.extend(pdp.init_container_lines())
    lines.extend(
        pdp.extra_env_var_lines(nixl=gates["kv"], multigpu=gates["multigpu"])
    )
```

The same pair at the end of the `if "prefill" in scenario:` block, after its
`if "vllm" in p_role:` branch:

```python
        if gates["kv"]:
            lines.extend(pdp.init_container_lines())
        lines.extend(
            pdp.extra_env_var_lines(nixl=gates["kv"], multigpu=gates["multigpu"])
        )
```

And after the prefill block, before the trailing `lines.append("")`:

```python
    if gates["kv"]:
        lines.append("")
        lines.extend(pdp.routing_lines())
```

- [ ] **Step 5: Run the full skill test suite**

Run: `python -m pytest .claude/skills/sim2real-bootstrap/tests/ -v`
Expected: all PASS.

- [ ] **Step 6: Verify `_gates` does not leak, and check the fault-injection guard**

```bash
grep -rn "_gates" .claude/skills/sim2real-bootstrap/tests/ | grep -c "not in" || true
python -m pytest .claude/skills/sim2real-bootstrap/tests/ -q -k "no_gates"
```

Both `test_no_gates_emits_no_plumbing*` tests must pass. Then prove the anti-drift
test has teeth: temporarily change one character in a `pd_plumbing` fragment (e.g.
`/dev/shm` → `/dev/shmm`), re-run
`python -m pytest .claude/skills/sim2real-bootstrap/tests/ -q`, confirm tests FAIL,
then revert. A guard that cannot fail is not a guard.

- [ ] **Step 7: Commit**

```bash
git add .claude/skills/sim2real-bootstrap/generate_scenarios.py \
        .claude/skills/sim2real-bootstrap/tests/test_generate_scenarios.py
git commit -m "fix(bootstrap): emit the same pod plumbing from the json path (#848)"
```

---

### Task 4: Document the gates in SKILL.md, sweep for stale references

**Files:**
- Modify: `.claude/skills/sim2real-bootstrap/SKILL.md` — the disaggregation section (~:288-320)

**Interfaces:**
- Consumes: the emitted key set from Tasks 2 and 3.
- Produces: no code symbols; documentation only.

- [ ] **Step 1: Extend the emitted-YAML example**

In the fenced example that currently ends at `role: kv_both` (~:290-297), add the
new keys so the documented shape matches what the generators now emit:

```yaml
  # Also emitted with a prefill pool, and only then (issues #830, #848).
  vllmCommon:
    kvTransfer:
      enabled: true
      connector: NixlConnector
      role: kv_both
    # Sources the env file the preprocess init container writes (#848).
    preprocessScript: |
      export LD_LIBRARY_PATH=...libcuda.so.1 discovery...
      . /shared-config/llmdbench_env.sh
    volumes:
    - name: shared-config      # prefill pool  (Gate 1)
      type: emptyDir
      emptyDir: {}
    - name: dshm               # >1 GPU per pod (Gate 2)
      type: emptyDir
      emptyDir:
        medium: Memory
        sizeLimit: 16Gi
    volumeMounts:
    - name: shared-config
      mountPath: /shared-config
    - name: dshm
      mountPath: /dev/shm
  decode:                      # and prefill: -- both roles get both
    initContainers:            # Gate 1
    - name: preprocess
      imageKey: benchmark
      command: ["set_llmdbench_environment.py", "-e", "/shared-config/llmdbench_env.sh", "-i"]
      volumeMounts:
      - name: shared-config
        mountPath: /shared-config
    extraEnvVars:
    - name: NIXL_LOG_LEVEL     # Gate 1
      value: debug
    - name: NCCL_DEBUG         # Gate 2
      value: "INFO"
    - name: NVSHMEM_DEBUG      # Gate 2
      value: "INFO"
  routing:                     # Gate 1
    connector: nixlv2
```

- [ ] **Step 2: Add the prose that explains the gates**

Immediately after the existing `vllmCommon.kvTransfer is equally required (issue
#830)` bullet, add:

```markdown
- **The plumbing that makes `kvTransfer` work is emitted on the same gate (issue
  #848).** `kvTransfer.enabled: true` on its own is worse than leaving it off: the
  worker dies during "Initializing NIXL wrapper" because nothing configured the
  network and GPU-routing state. Two gates, both additive, both derived from rows
  `config.md` already declares:
  - **Gate 1, a prefill pool exists** — the `preprocess` init container (on *both*
    roles), the `shared-config` emptyDir, `vllmCommon.preprocessScript`,
    `NIXL_LOG_LEVEL`, and `routing.connector`. The first three are one unit: the
    init container *computes* the values and writes them to
    `/shared-config/llmdbench_env.sh`, `preprocessScript` *sources* them, and the
    volume is the handoff — any one missing makes the other two inert, which is
    why they are emitted together rather than split across the fragment layer.
    `routing.connector` is the sidecar half of the connector decision
    `kvTransfer.connector` makes engine-side; both spellings come from
    `pd_plumbing.KV_CONNECTOR_*` so they cannot drift.
  - **Gate 2, more than one GPU per pod** (`tensor_parallel_size > 1` or
    `data_parallel_size > 1` — `dataLocal` equals `dp`, so either puts several
    GPUs in one pod) — the `dshm` tmpfs at `/dev/shm` and `NCCL_DEBUG` /
    `NVSHMEM_DEBUG`. The K8s default 64 MB `/dev/shm` is a jitter and hang risk
    for multi-GPU collectives. `NVSHMEM_DEBUG` is load-bearing rather than
    diagnostic: `set_llmdbench_environment.py` adds `NVSHMEM_HCA_LIST` to the
    generated env file only when it is not `"none"`.

  Neither gate fires for a single-GPU aggregated bundle, so existing bundles
  regenerate byte-identically. Both generators emit the fragments from
  `pd_plumbing.py`, so the two paths cannot drift.
```

- [ ] **Step 3: Sweep for stale references**

The change adds a module and new emitted keys; it renames nothing and changes no
existing key, so path-greps should come back clean. Verify rather than assume:

```bash
grep -rn "kvTransfer" --include=*.md . | grep -v llm-d-benchmark
grep -rn "generate_from_config\|generate_scenarios" --include=*.md . | grep -v llm-d-benchmark
grep -rn "sim2real-bootstrap" CLAUDE.md pipeline/README.md
```

For each hit decide: stale (update here), accurate (leave), unrelated (leave).
Specifically check whether `CLAUDE.md`'s `/sim2real-bootstrap` paragraph or
`pipeline/README.md` enumerate the bootstrap module list — if either lists the
skill's files, add `pd_plumbing.py`.

Known gap in this sweep, per implement-issue Step 6: a path-grep cannot catch a
consumer whose *implicit schema* changed without any path or symbol changing. The
one at risk here is `/sim2real-check`, which compares resolved scenarios against
simulation config. Check whether it enumerates expected scenario keys and would
now see unexpected ones:

```bash
grep -rn "vllmCommon\|volumes\|initContainers\|extraEnvVars\|routing" \
  .claude/skills/sim2real-check/ | grep -v tests/
```

If it validates against a closed key set, add the new keys there in this PR.

- [ ] **Step 4: Run the full CI gate locally**

```bash
ruff check pipeline/ .claude/skills/ --select F
python -m pytest pipeline/ \
  .claude/skills/sim2real-analyze/tests/ \
  .claude/skills/sim2real-bootstrap/tests/ \
  .claude/skills/sim2real-translate/tests/ \
  .claude/skills/sim2real-check/tests/ \
  --cov=pipeline --cov-report=term-missing --cov-fail-under=90 -q
```

Expected: lint clean, all tests pass, coverage gate holds. (`--cov=pipeline` does
not measure the skill directory, so this change cannot move the coverage number;
confirm it did not drop for an unrelated reason.)

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/sim2real-bootstrap/SKILL.md
git commit -m "docs(bootstrap): document the two pod-plumbing gates (#848)"
```

---

## Acceptance criteria

Traced from the issue:

| Issue requirement | Where satisfied |
|---|---|
| Gate 1: `decode`/`prefill` `initContainers` running `set_llmdbench_environment.py -e /shared-config/llmdbench_env.sh -i` | `pd_plumbing.init_container_lines`; Task 2 Step 4, Task 3 Step 4 |
| Gate 1: `shared-config` emptyDir at `/shared-config` in `vllmCommon.volumes`/`volumeMounts` | `pd_plumbing.volume_lines(shared_config=True, ...)` |
| Gate 1: `vllmCommon.preprocessScript` = libcuda prologue then `. /shared-config/llmdbench_env.sh` | `pd_plumbing.preprocess_script_lines` |
| Gate 1: `NIXL_LOG_LEVEL` | `pd_plumbing.extra_env_var_lines(nixl=True, ...)` |
| Gate 1: `routing.connector: nixlv2` | `pd_plumbing.routing_lines` |
| Gate 1 items emitted as one unit, not split across layers | All five come from one module on one boolean; `test_gate1_emits_the_full_kv_unit` asserts them together |
| Both connector keys from one "which connector" value | `KV_CONNECTOR_ENGINE` / `KV_CONNECTOR_SIDECAR`; `test_kv_transfer_and_routing_connector_agree` |
| Gate 2: `dshm` emptyDir `medium: Memory` at `/dev/shm` | `pd_plumbing.volume_lines(..., dshm=True)` |
| Gate 2: `NCCL_DEBUG`, `NVSHMEM_DEBUG` | `pd_plumbing.extra_env_var_lines(..., multigpu=True)` |
| `preprocessScript`, not `vllm.customCommand` | `test_preprocess_script_is_not_a_custom_command` |
| Out of scope: pod capabilities, NIC exclusion list, RDMA reservation (#840) | Not emitted; nothing in this plan touches the fragment layer |

Additional criteria this plan adds:

- Existing single-GPU aggregated bundles regenerate **byte-identically** (`test_no_gates_emits_no_plumbing*`, plus the mechanical diff in Task 2 Step 6).
- Both generators emit character-identical plumbing (`test_both_generators_emit_identical_plumbing_text`).
- Both gates accumulate into shared keys without clobbering (`test_both_gates_accumulate*`).
- Every gate combination emits parseable YAML (`test_emitted_plumbing_is_valid_yaml_in_every_gate_combination`).

## Risks

- **Byte-identity.** Both emitters are hand-rolled; a stray `lines.append("")` changes output for every existing bundle. Mitigated by the diff-against-`main` step and the no-gates tests.
- **Indentation.** The fragments are literal strings with hardcoded indentation. A wrong level produces YAML that parses into the wrong nesting rather than failing loudly. Mitigated by parsing emitted text in every test rather than grepping it.
- **`_gates` leakage.** A new dict key on every scenario. It must never appear in output and must not confuse `/sim2real-check`. Asserted explicitly in Task 2 Step 6.
- **Gate 2 breadth (D1).** Emits plumbing for `dp>1, tp=1`, which the issue's literal wording would not. Deliberate, argued above, and to be surfaced in the PR body for the reviewer to accept or reject.
- **`sizeLimit: 16Gi` interacts with #850.** A `medium: Memory` emptyDir charges against the pod's memory limit. When #850 starts emitting memory limits, a limit at or below 16Gi makes a full `/dev/shm` an OOM. Flagged in the emitted comment and to be noted on #850.

## Gotchas found while writing this plan

Recorded here because each one would otherwise cost an executor a failed test run:

- **The two emitters differ on directory creation.** `write_provenance_yaml`
  (`generate_from_config.py:1048`) calls `os.makedirs`; `write_commented_yaml`
  (`generate_scenarios.py`) does not — it opens the path directly. Tests that pass a
  `tmp_path` subdirectory work for the first and raise `FileNotFoundError` for the
  second.
- **The two test files' `emit` helpers have different signatures.**
  `test_generate_from_config_prefill.py`'s takes `(scenario, provenance, tmp_path)`
  and writes `baseline.yaml`; `test_generate_scenarios.py`'s takes
  `(scenario, src_entry, tmp_path)` and writes `cand.yaml`.
- **`entry()`'s argument order is `(workload_extra, vllm_extra)`** — workload first.
  Passing vLLM args positionally puts them in the workload dict, where they are
  silently ignored and every gate assertion fails for a reason that looks unrelated.
- **`vllmCommon` can now be created by three independent things** (an
  `enforce_eager: false` override, a prefill pool, or either gate). Every populator
  must use `setdefault`, and the emitter must branch on each sub-key independently —
  this is exactly the bug #846's review caught by fault injection.

"""Tests for pd_plumbing.py — the shared P/D and multi-GPU pod-plumbing fragments.

Covers issue #848. These are the fragments that make NIXL actually initialise;
#846 emitted `kvTransfer.enabled: true` without them, which crashloops the worker
during "Initializing NIXL wrapper".

The emitters are tested in isolation here. The gates that decide *when* they are
called live in the two generators and are tested in their own files.
"""
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parents[1]))
import pd_plumbing as pdp


def parse_fragment(lines: list[str], parent: str | None = None) -> dict:
    """Parse emitter output inside the envelope the real emitters supply.

    A scenario is rendered as a list item under `scenario:`, so its own keys sit
    at 2-space indent and their children at 4. `routing_lines` emits a
    scenario-level key and needs no parent; the rest emit *children* of a key the
    caller has already printed (`vllmCommon:` or a role block), so the test has to
    supply that parent — exactly as the generators do. Reconstructing the real
    envelope is what makes the indentation contract testable rather than assumed.
    """
    body = [f"  {parent}:"] if parent else []
    doc = "scenario:\n- name: t\n" + "\n".join(body + lines) + "\n"
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
    "tensor,data_local,expected",
    [
        (1, 1, False),
        (2, 1, True),
        # Intra-pod data parallelism: dataLocal>1 with tensor==1 still puts several
        # GPUs in one pod. Issue #848 says "tensor_parallel_size > 1"; this is the
        # deliberate superset (plan D1).
        (1, 2, True),
        (2, 2, True),
        (8, 4, True),
    ],
)
def test_needs_multigpu_plumbing_gates_on_gpus_per_pod(tensor, data_local, expected):
    assert pdp.needs_multigpu_plumbing(tensor, data_local) is expected


def test_gate2_measures_data_local_not_deployment_wide_data():
    """The gate must key on GPUs *in this pod*, which is tensor x dataLocal
    (13_ms-values.yaml.j2:269-271), not on the deployment-wide `data` degree.

    A scenario with data: 8, dataLocal: 1, tensor: 1 is a SINGLE-GPU pod -- the
    other seven DP ranks live in other pods and talk over the network, so there are
    no intra-pod shared-memory collectives and nothing for /dev/shm or
    NCCL/NVSHMEM to do locally. A gate fed the deployment-wide degree would emit a
    16Gi tmpfs (charged against the pod memory limit, #850) plus dead env vars.

    Both generators feed dataLocal from the single data_parallel_size input today,
    so this is latent rather than live -- it goes live with #843's multinode split.
    """
    deployment_wide_data = 8
    assert pdp.needs_multigpu_plumbing(1, 1) is False
    # Sanity: the value that must NOT be what the gate consults would flip it.
    assert pdp.needs_multigpu_plumbing(1, deployment_wide_data) is True


# --- preprocessScript ------------------------------------------------------

def test_preprocess_script_sources_the_env_file():
    parsed = parse_fragment(pdp.preprocess_script_lines(), "vllmCommon")
    script = parsed["vllmCommon"]["preprocessScript"]
    assert ". /shared-config/llmdbench_env.sh" in script


def test_preprocess_script_carries_the_libcuda_prologue():
    parsed = parse_fragment(pdp.preprocess_script_lines(), "vllmCommon")
    script = parsed["vllmCommon"]["preprocessScript"]
    assert "LD_LIBRARY_PATH" in script
    assert "LIBRARY_PATH" in script
    assert "libcuda.so.1" in script


def test_preprocess_script_prologue_precedes_the_source():
    """The prologue exports paths the sourced file's consumers need; order matters."""
    script = parse_fragment(pdp.preprocess_script_lines(), "vllmCommon")["vllmCommon"]["preprocessScript"]
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
    parsed = parse_fragment(pdp.volume_lines(shared_config=True, dshm=False), "vllmCommon")
    names = [v["name"] for v in parsed["vllmCommon"]["volumes"]]
    mounts = {m["name"]: m["mountPath"] for m in parsed["vllmCommon"]["volumeMounts"]}
    assert names == ["shared-config"]
    assert mounts == {"shared-config": "/shared-config"}


def test_dshm_volume_only():
    parsed = parse_fragment(pdp.volume_lines(shared_config=False, dshm=True), "vllmCommon")
    vols = parsed["vllmCommon"]["volumes"]
    mounts = {m["name"]: m["mountPath"] for m in parsed["vllmCommon"]["volumeMounts"]}
    assert [v["name"] for v in vols] == ["dshm"]
    assert vols[0]["emptyDir"]["medium"] == "Memory"
    assert vols[0]["emptyDir"]["sizeLimit"] == "16Gi"
    assert mounts == {"dshm": "/dev/shm"}


def test_both_volumes_accumulate_into_one_list():
    """The two gates contribute to the same key; a second gate must not clobber
    the first."""
    parsed = parse_fragment(pdp.volume_lines(shared_config=True, dshm=True), "vllmCommon")
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
    parsed = parse_fragment(pdp.volume_lines(shared_config=True, dshm=False), "vllmCommon")
    assert parsed["vllmCommon"]["volumes"][0]["emptyDir"] == {}


# --- init container --------------------------------------------------------

def test_init_container_runs_the_env_generator():
    parsed = parse_fragment(pdp.init_container_lines(), "decode")
    ic = parsed["decode"]["initContainers"][0]
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
    ic = parse_fragment(pdp.init_container_lines(), "decode")["decode"]["initContainers"][0]
    mounts = {m["name"]: m["mountPath"] for m in ic["volumeMounts"]}
    assert mounts == {"shared-config": "/shared-config"}


def test_init_container_uses_the_benchmark_image_key():
    """imageKey resolves through images.* upstream; a literal image would pin a
    tag this bundle has no way to track."""
    ic = parse_fragment(pdp.init_container_lines(), "decode")["decode"]["initContainers"][0]
    assert ic["imageKey"] == "benchmark"
    assert "image" not in ic


def test_init_container_declares_no_env_so_it_inherits_extra_env_vars():
    """_macros.j2:28-31 falls back to build_ms_env_vars(mode) only when the init
    container declares no `env`. set_llmdbench_environment.py:539-541 reads
    NVSHMEM_DEBUG from this container's environment, so the fallback is required."""
    ic = parse_fragment(pdp.init_container_lines(), "decode")["decode"]["initContainers"][0]
    assert "env" not in ic


# --- extraEnvVars ----------------------------------------------------------

def test_extra_env_vars_empty_when_neither_gate_fires():
    assert pdp.extra_env_var_lines(nixl=False, multigpu=False) == []


def _env_map(lines):
    parsed = parse_fragment(lines, "decode")
    return {e["name"]: e["value"] for e in parsed["decode"]["extraEnvVars"]}


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


@pytest.mark.parametrize(
    "lines,parent,key",
    [
        (pdp.routing_lines(), None, "routing"),
        (pdp.preprocess_script_lines(), "vllmCommon", "preprocessScript"),
        (pdp.volume_lines(shared_config=True, dshm=True), "vllmCommon", "volumes"),
        (pdp.init_container_lines(), "decode", "initContainers"),
        (pdp.extra_env_var_lines(nixl=True, multigpu=True), "decode", "extraEnvVars"),
    ],
)
def test_every_fragment_parses_to_its_key_under_the_right_parent(lines, parent, key):
    """Indentation is the whole contract for a hand-rolled emitter, and a wrong
    level nests silently rather than failing loudly -- so assert the key lands
    where it belongs, not merely that the document parses."""
    parsed = parse_fragment(lines, parent)
    container = parsed[parent] if parent else parsed
    assert key in container


def test_all_fragments_coexist_in_one_document():
    """The gates can all fire at once; the full combination must still parse."""
    doc = (
        "scenario:\n- name: t\n  vllmCommon:\n"
        + "\n".join(
            pdp.preprocess_script_lines()
            + pdp.volume_lines(shared_config=True, dshm=True)
        )
        + "\n  decode:\n"
        + "\n".join(
            pdp.init_container_lines()
            + pdp.extra_env_var_lines(nixl=True, multigpu=True)
        )
        + "\n"
        + "\n".join(pdp.routing_lines())
        + "\n"
    )
    parsed = yaml.safe_load(doc)["scenario"][0]
    assert set(parsed["vllmCommon"]) == {"preprocessScript", "volumes", "volumeMounts"}
    assert set(parsed["decode"]) == {"initContainers", "extraEnvVars"}
    assert parsed["routing"]["connector"] == "nixlv2"

# Step 0 design — Foundation: cluster provisioning + workspace plumbing

Epic: [#416 — Step 0 — Foundation: workspace + cluster provisioning](https://github.com/inference-sim/sim2real/issues/416)

Base branch: `refactor/v2`

This document is the design contract for Step 0 of the sim2real v2 refactor. It is the output of the interactive design pass against the addendum's open questions and the surrounding proposals. Several of those open questions resolved to "no Step 0 work" or "defer to a later step"; what remains is captured below.

---

## Scope

Step 0 produces:

1. **A new library module** `pipeline/lib/cluster_ops.py` with idempotent primitives for cluster-side setup: `provision_namespace`, `apply_cluster_resources`, and `read/write/update_cluster_config`.
2. **A new entry point** `pipeline/cluster.py` with a `provision` subcommand that orchestrates the library helpers to bootstrap a cluster's namespaces, RBAC, secrets, PVCs, and Tekton tasks. Writes a per-cluster `cluster_config.json`.
3. **A path-helper module** `pipeline/lib/layout.py` for the new workspace layout, providing functions like `clusters_dir()`, `cluster_dir(id)`, `runs_dir()`, etc.
4. **A transfer.yaml slicer module** `pipeline/lib/slicer.py` that, given a loaded v3 `transfer.yaml`, partitions it into translation slice and assembly slice, and computes `translation_hash`.
5. **Edits to `pipeline/setup.py`** to stop writing cluster-scoped fields and the cruft fields; `setup.py` continues to write the workspace-scoped fields it owns.
6. **Code porting** of consumers of cluster-scoped fields (in `deploy.py`, `prepare.py`, `run.py`, `lib/remote.py`, `lib/run_manager.py`) from `setup_config.json` reads to `cluster_config.json` reads via the layout module.
7. **Removal** of cruft fields and any consumers that exist.

Step 0 does **not**:

- Change `transfer.yaml` (no v4, no schema change; slicer is pure code).
- Introduce per-run `.state.json` or `workspace/state.json` (current_run stays in `setup_config.json`).
- Implement `run_metadata.json`, `translation_output.json`, `manifest.assembly.yaml`, or `registered.json` (each is its owning step's deliverable).
- Add drift detection or `params_hash`.
- Add operator-flow commands (no `assemble`, `translate`, `build`, `use`, `list` yet).

---

## Operator getting-started flow (post-Step-0)

This is the user-visible shape of the new flow. It's a Step 0 deliverable (documentation), and it's the source-of-truth that `pipeline/README.md` and `CLAUDE.md` should document going forward.

From a clean checkout, an operator runs:

**Step A — One-time per cluster** (the new command introduced by this epic):

```bash
python pipeline/cluster.py provision <cluster_id> \
    --namespaces NS1,NS2,NS3
    # Plus secret value flags or env vars (HF_TOKEN, REGISTRY_USER+REGISTRY_TOKEN, GITHUB_TOKEN)
    # OpenShift is auto-detected; no flag needed.
```

Bootstraps cluster-side resources (namespaces, RBAC, secrets, PVCs, Tekton tasks). Writes `workspace/clusters/<cluster_id>/cluster_config.json`. Idempotent — safe to re-run.

**Step B — One-time per workspace** (today's setup.py, now slimmer):

```bash
python pipeline/setup.py \
    --registry ghcr.io/myorg \
    --repo-name llm-d-router \
    --pipeline-yaml pipeline/pipeline.yaml \
    --orchestrator-image ghcr.io/inference-sim/sim2real/orchestrator:latest \
    --experiment-root ../my-experiment
```

Writes `workspace/setup_config.json` with workspace-scoped fields. No cluster-side flags (those moved to Step A). Operators with an existing setup_config.json keep it; re-run updates workspace fields. Idempotent.

**Steps C–F — Per-run cycle** (unchanged by Step 0; these are today's commands, with cluster-field reads ported under the hood):

```bash
python pipeline/prepare.py --experiment-root ../my-experiment   # phases 1–6
# [/sim2real-translate skill at the translation checkpoint, if applicable]
python pipeline/deploy.py run --run <run-name>
python pipeline/deploy.py collect --run <run-name>
```

These read what they need: cluster-scoped fields (namespaces, is_openshift, etc.) from `cluster_config.json` via the new layout module; workspace-scoped fields (registry, current_run, etc.) from `setup_config.json` unchanged.

**Why two setup commands instead of one**: the 3D model separates cluster identity from workspace identity. Multiple workspaces can target the same cluster; one workspace can later target multiple clusters (Step 0 enforces single-cluster but the model supports it). Two commands reflect that separation.

**An alternative deferred to future refinement**: the 3D proposal mentions "lazy workspace init" — let each command prompt for missing workspace fields on first use, eliminating Step B. Cleaner long-term but more invasive. Not Step 0's scope.

---

## Non-goals (explicit)

These are addressed elsewhere or deliberately deferred:

- **Experiment-repo migration.** No `transfer.yaml` schema change means existing experiment repos (admission-control, etc.) work unchanged against the post-Step-0 code path.
- **Per-cluster registries.** `registry` and `repo_name` are workspace-scoped, stay in `setup_config.json`. 3D's "intentionally deferred" stance holds.
- **Kubeconfig context validation.** Operator-managed kube context. No auto-switching or refusal on mismatch.
- **`sim2real list clusters` / `list translations` commands.** Listing-shaped surface deferred to whichever step makes sense.
- **Issue #377 (slots add/remove CLI verbs).** Step 0 provides the library substrate (`provision_namespace`, `update_cluster_config`) so issue #377 lands as a clean later-step consumer. The `publish_slot_pool` helper (ConfigMap + live propagation) and the `deploy.py slots ...` CLI verbs are issue #377's own deliverables, not Step 0's.

---

## Architecture

Three layers, with clear scope boundaries:

```
pipeline/cluster.py                   ← entry-point ("orchestrator" for cluster bootstrap)
                                        first consumer of cluster_ops
        │
        ▼
pipeline/lib/cluster_ops.py          ← library helpers, idempotent primitives
                                        - provision_namespace(ns, cluster_config)
                                        - apply_cluster_resources(cluster_id)
                                        - read_cluster_config(cluster_id)
                                        - write_cluster_config(cluster_id, config)
                                        - update_cluster_config(cluster_id, **updates)
        │
        ▼
pipeline/lib/layout.py               ← path helpers, no I/O
                                        - workspace_dir() → Path
                                        - clusters_dir() → Path
                                        - cluster_dir(cluster_id) → Path
                                        - cluster_config_path(cluster_id) → Path
                                        - runs_dir(), translations_dir(), etc.

pipeline/lib/slicer.py               ← transfer.yaml field categorizer
                                        - translation_slice(manifest_dict) → dict
                                        - assembly_slice(manifest_dict) → dict
                                        - translation_hash(manifest_dict) → str
```

**Layer rules**:

- `layout.py` deals in paths only. Filesystem-aware operations (Path existence checks, directory enumeration) are allowed; content reads (JSON parsing, YAML parsing) are not. No imports of cluster_ops.
- `cluster_ops.py` uses `layout.py` for paths but doesn't import `cluster.py`.
- `cluster.py` uses both, plus argparse + Kubernetes interaction code carved from today's `setup.py`.
- `slicer.py` is independent of the cluster stack. Consumed by Steps 2+ (when `translate` is implemented); Step 0 ships it as a ready substrate.

**Why this shape**: Issue #377 calls these out as library helpers because slot add/remove also need them. Step 0 produces the substrate; Issue #377 later builds on it without re-architecting.

---

## `cluster_config.json` schema

One file per provisioned cluster, at `workspace/clusters/<cluster_id>/cluster_config.json`.

```json
{
  "cluster_id": "ocp-east",
  "namespaces": ["kalantar-0", "kalantar-1", "kalantar-2", "kalantar-3"],
  "is_openshift": true,
  "storage_class": "",
  "secret_names": {
    "hf_token": "hf-secret",
    "registry_creds": "registry-creds",
    "github_token": "github-token",
    "dockerhub_creds": ""
  },
  "workspaces": {
    "data-storage": { "persistentVolumeClaim": { "claimName": "data-pvc" } },
    "source": { "persistentVolumeClaim": { "claimName": "source-pvc" } }
  },
  "created_at": "2026-06-29T22:00:00Z"
}
```

Field semantics:

| Field | Source | Notes |
|---|---|---|
| `cluster_id` | operator-provided (positional arg to `cluster.py provision`) | Matches the directory name. Slug-style: lowercase, hyphens, no slashes. |
| `namespaces` | operator-provided (`--namespaces`) or stable across runs | The slot list. Order is significant for legacy `namespaces[0]`-as-primary pattern. |
| `is_openshift` | auto-detected by `cluster.py provision` (same `detect_openshift()` logic as today's setup.py; no operator flag) | Cached so commands don't re-probe per invocation. |
| `storage_class` | operator-provided (`--storage-class`); empty means cluster default | PVC storage class. Same semantics as today. |
| `secret_names` | hardcoded to today's conventional names; not operator-tunable in Step 0 | Names of k8s secrets installed in each namespace. Recorded so issue #377's future tooling can inspect them. Values are NOT stored here. |
| `secret_names.hf_token` | `hf-secret` | HuggingFace token for model pulls. |
| `secret_names.registry_creds` | `registry-creds` | Pull/push credentials for the image registry. |
| `secret_names.github_token` | `github-token` | Git credentials for Tekton task checkouts. |
| `secret_names.dockerhub_creds` | `dockerhub-creds` when `--dockerhub-user`+`--dockerhub-token` provided; `""` (empty) otherwise | Optional Docker Hub pull credentials. When non-empty, pods include it in `imagePullSecrets`. |
| `workspaces` | hardcoded to today's defaults: `data-storage=data-pvc`, `source=source-pvc` | Tekton workspace bindings. Keys match Pipeline definition's `workspaces:` declarations. |
| `created_at` | auto-set by `cluster.py provision` on first write | ISO-8601 UTC. Not updated on re-provision. |

**Schema discipline**:

- Top-level keys are stable; adding new top-level keys is permitted (additive).
- Adding keys inside `secret_names` is permitted (additive). Older consumers ignore unknown keys.
- No top-level `version` field. The shape is documented; readers tolerate unknown fields.
- Updates use atomic write (tmpfile + rename).

---

## `cluster_ops.py` library API

```python
@dataclass
class ProvisionResult:
    """Structured outcome of provision_namespace, per issue #377's
    'surface every divergence' convention."""
    namespace: str
    steps_ok:   list[str]                  # e.g. ["namespace", "rbac", "secrets", "pvc", "tekton"]
    steps_skipped: list[tuple[str, str]]    # (step, reason)
    steps_failed:  list[tuple[str, str]]    # (step, error_message)

    @property
    def diverged(self) -> bool:
        return bool(self.steps_skipped or self.steps_failed)


def provision_namespace(
    namespace: str,
    cluster_config: dict,
    *,
    secret_values: dict | None = None,
    skip: list[str] = (),
) -> ProvisionResult:
    """Provision ONE namespace's cluster-side resources. Idempotent.

    Sub-steps, in order:
      1. Create namespace (oc new-project on OpenShift; kubectl create ns elsewhere).
      2. Apply RBAC for that namespace.
      3. For each entry in cluster_config['secret_names'] with a non-empty name,
         look up its value in secret_values (keyed by the same key — e.g.
         secret_values['hf_token']). If the value is present, create or update
         the k8s Secret in the namespace with the configured name. If the value
         is absent and a Secret with that name already exists in the namespace,
         leave it untouched (idempotent reuse of pre-installed secrets). If
         absent AND no existing Secret, surface a structured 'skipped' entry
         saying the secret value was not provided.
      4. Create PVCs per cluster_config['workspaces'].
      5. Apply Tekton task bindings for that namespace.

    Each step is idempotent: a step that's already correct is a no-op. A step
    that diverges (already exists with different content for which content is
    given) returns a structured failure entry rather than silently succeeding.

    `secret_values` is a plain dict like {'hf_token': '...', 'registry_creds':
    {'user': '...', 'token': '...'}, 'github_token': '...', 'dockerhub_creds':
    {'user': '...', 'token': '...'}}. Resolution from CLI flags / env vars /
    interactive prompts happens in cluster.py provision (the caller); the
    library function takes resolved values. Keeps env-var dependence out of
    pipeline/lib/ for testability.

    The `skip` arg suppresses individual sub-steps by name. Issue #377's
    `slots add --no-provision` translates to `provision_namespace(ns, cfg,
    skip=["namespace", "rbac", "secrets", "pvc", "tekton"])` — i.e., the
    operator is asserting they've already done the cluster setup out-of-band
    and just wants to add the slot to the pool. Step 0 doesn't surface this
    flag in cluster.py provision; it exists so issue #377's later command
    has the substrate it needs without re-shaping the API.
    """


def apply_cluster_resources(cluster_id: str) -> None:
    """Apply cluster-wide resources that are not per-namespace. Idempotent.

    Today this is essentially the Tekton Pipeline definition (cluster-scoped CRD).
    Future cluster-wide resources (e.g. ClusterRoles) live here.
    """


def read_cluster_config(cluster_id: str) -> dict:
    """Read clusters/<id>/cluster_config.json. Returns {} if absent."""


def write_cluster_config(cluster_id: str, config: dict) -> None:
    """Atomic write (tmpfile + rename) of clusters/<id>/cluster_config.json.
    Creates the cluster directory if absent.
    """


def update_cluster_config(cluster_id: str, **updates) -> dict:
    """Read-modify-write of clusters/<id>/cluster_config.json. Atomic.

    Returns the new config. Used for partial mutations like appending to the
    namespaces list (issue #377's slots add) or rotating a secret name.

    Note: keys inside `secret_names` and `workspaces` are deep-merged, not
    replaced wholesale, unless the caller passes the new dict explicitly.
    """
```

**Error-handling convention**:

- Idempotent kubectl/oc applies on Step success: silent.
- Idempotent applies that hit a name collision with different content: structured failure in the result, surfaced to operator at `warn` level by the caller.
- Hard failures (auth, network): exceptions, propagate to caller; `cluster.py provision` translates to a clean error message.

This matches issue #377's "surface every divergence" stance.

---

## `cluster.py provision` orchestrator

```
python pipeline/cluster.py provision <cluster_id> \
    --namespaces NS1,NS2,NS3 \
    [--storage-class SC] \
    [--hf-token TOKEN]        # or HF_TOKEN env, or prompt
    [--github-token TOKEN]    # or GITHUB_TOKEN env, or prompt
    [--registry-user USER]    # or REGISTRY_USER env, or prompt
    [--registry-token TOKEN]  # or REGISTRY_TOKEN env, or prompt
    [--dockerhub-user USER]   # or DOCKERHUB_USER env (optional; empty = skip dockerhub secret)
    [--dockerhub-token TOKEN] # or DOCKERHUB_TOKEN env
    [--experiment-root PATH]
```

**Flag set vs today's `setup.py`**: every flag above except `<cluster_id>`, `--dockerhub-user`, and `--dockerhub-token` already exists in today's `setup.py` with identical semantics. The two new dockerhub flags address today's image-pull-rate-limit gap (no current handling). The positional `<cluster_id>` is new because the cluster-id concept itself is new.

**Flags from today's `setup.py` that are NOT carried over** to `cluster.py provision`:

- `--namespace` (singular): redundant — `--namespaces` always wins.
- `--no-cluster`: cluster.py provision's purpose IS to touch the cluster; the no-cluster mode doesn't apply.
- `--redeploy-tasks`: cluster.py provision is fully idempotent — re-running it has the same effect, no special flag needed.
- `--test-push` / `--test-push-tag`: registry credential testing is workspace-level, not cluster-level (registry URL lives in `setup_config.json`). Stays with `setup.py` (or retires separately); not a cluster.py provision concern.
- All workspace-only flags (`--registry`, `--repo-name`, `--run`, `--pipeline-yaml`, `--orchestrator-image`): stay with `setup.py`.

**Defaults match today's setup.py**. Specifically:

- `is_openshift` is auto-detected from the cluster (same `detect_openshift()` logic as today; no operator flag).
- Secret names are hardcoded to the conventional values setup.py uses today: `hf-secret`, `registry-creds`, `github-token`, plus `dockerhub-creds` (the new optional one). These are recorded in `cluster_config.json:secret_names` for future tooling (e.g. issue #377's `slots ...` commands need to know what to mount) but are not operator-tunable in Step 0. Rename support is an additive future flag if ever needed.
- Workspace bindings are hardcoded to today's defaults: `data-storage → data-pvc` and `source → source-pvc`. Recorded in `cluster_config.json:workspaces`.
- Storage class default is empty string (means "cluster default storage class") — same as today.

**Secret values are NOT in `cluster_config.json`** — only the (hardcoded-conventional) names are. Values flow in via flags / env vars / prompts and become k8s Secret resources in each namespace. The four secret value sources, in priority order: command-line flag, env var, interactive prompt. If a value is absent AND the named Secret already exists in the namespace, the existing Secret is reused (idempotent); if absent AND no existing Secret, that secret's sub-step is recorded as "skipped" in the ProvisionResult (per the `provision_namespace` contract).

**Behavior**:

1. Resolve flags (read existing `cluster_config.json` for defaults if present; flags override).
2. Detect OpenShift via the same `detect_openshift()` logic as today's setup.py. Cache the result for inclusion in `cluster_config.json:is_openshift`.
3. **Resolve secret values** from flags, env vars, or interactive prompts (matching today's `setup.py` resolution order: flag > env var > prompt). Build a `secret_values` dict keyed by the same keys used in `cluster_config['secret_names']`. Empty values (e.g. dockerhub not provided) are absent from the dict — `provision_namespace` will skip those secrets per its contract.
4. Call `apply_cluster_resources(cluster_id)`.
5. For each namespace in `--namespaces`:
   - Call `provision_namespace(ns, cluster_config_in_progress, secret_values=secret_values)`.
   - Accumulate `ProvisionResult` for end-of-run summary.
6. Compute the final `cluster_config` dict (combining flags, detected values, defaults, and secret_names).
7. Call `write_cluster_config(cluster_id, config)`.
8. Print a summary line per namespace: "ok" or "diverged: <reason>".
9. Exit non-zero if any `provision_namespace` had failed steps.

Idempotent: safe to re-run. Re-running with the same flags is a no-op (everything's already in the right state, the file is rewritten with the same content).

Re-running with different flags: this is the substrate issue #377 uses for `slots add` (call `provision_namespace` for a new namespace + `update_cluster_config` to append it). `cluster.py provision` itself doesn't do incremental adds — operators run it once per cluster; incremental adds come via `deploy.py slots add` in a later step.

---

## `setup.py` changes

The cluster fields move OUT of `setup_config.json` entirely. The cruft fields are deleted. Workspace fields stay.

**Fields removed from `setup_config.json`** (no longer written; consumers are ported in this same change):

| Field | Replacement |
|---|---|
| `namespaces` | `cluster_config.json:namespaces` |
| `namespace` (singular) | derived from `namespaces[0]` (the "primary" namespace pattern) |
| `is_openshift` | `cluster_config.json:is_openshift` |
| `storage_class` | `cluster_config.json:storage_class` |
| `hf_secret_name` | `cluster_config.json:secret_names.hf_token` |
| `workspaces` | `cluster_config.json:workspaces` |
| `tektonc_dir` | removed entirely (confirmed write-only across all files: only setup.py:724 writes; no reader anywhere) |
| `sim2real_root` | **stays in `setup_config.json` for now.** Read by `sim2real-translate/SKILL.md:92`. Retires when sim2real-translate is rewritten in Step 2 (the skill update naturally drops this consumer). |
| `container_runtime` | the persisted FIELD is removed (no functional reader; only test assertions). If `setup.py` keeps `step_test_push`, the in-memory detection (`_detect_container_runtime()`) stays in setup.py and the result is passed in-process to the test — no need to persist. |
| `setup_timestamp` | removed entirely (confirmed write-only across all files) |

**Fields that stay in `setup_config.json`**:

- `registry`
- `repo_name`
- `pipeline_yaml`
- `orchestrator_image`
- `current_run`

`setup.py` continues to manage these (via its prompt + write flow). The intent is that `setup.py` shrinks gradually as later steps replace its consumers; Step 0 just removes the cluster-scoped responsibility.

**`setup.py`'s cluster-side work is removed**:

- The namespace-creation, RBAC, secret, PVC, and Tekton-task steps are deleted from `setup.py`. They live in `cluster_ops.py` now.
- `setup.py` continues to handle the workspace-scoped configuration prompts (registry, repo_name, pipeline_yaml, orchestrator_image, current_run) and write them to `setup_config.json`.
- **`step_test_push` (today's step 5, Registry Credential Test) stays in `setup.py`** if it stays at all. Registry config is workspace-scoped (`setup_config.json:registry`), so credential validation is a workspace-level concern, not a per-cluster one. `cluster.py provision` deliberately does NOT do registry testing — bundling it into a cluster-bootstrap command would test workspace config from the wrong layer. Whether to keep `step_test_push` in setup.py, retire it (operator runs `podman push` manually), or move it to a separate utility command is a separate cleanup, not a Step 0 deliverable.
- Because `cluster.py provision` does not need a local container runtime (no image build / push), `container_runtime` does NOT live in `cluster_config.json`. If `setup.py` keeps `step_test_push`, it keeps detecting and using `container_runtime` for that purpose — that detection happens at command time (today's `_detect_container_runtime()`) and is internal to setup.py. No `container_runtime` field is recorded in `cluster_config.json`.

Operator-facing implication: existing `python pipeline/setup.py --namespace ...` invocations behave differently — they no longer provision the cluster. Operators run `python pipeline/cluster.py provision ...` for the cluster bits. `setup.py` is then a workspace-config writer (and possibly a registry-creds validator).

**This is a breaking change to `setup.py`'s CLI.** The flags `--namespace`, `--namespaces`, `--storage-class`, `--hf-token`, `--no-cluster`, `--redeploy-tasks` are removed or repurposed. The change is gated on `refactor/v2` — main is unaffected.

---

## Code porting

Consumers of `setup_config.json` cluster fields are ported to `cluster_config.json` reads via `cluster_ops.read_cluster_config()` in the same PR.

Identified callsites (from grepping, not exhaustive — to be confirmed during implementation):

| File:Line | Field | Port pattern |
|---|---|---|
| `pipeline/deploy.py:1398` | `setup_config.get("namespace", ...)` | `cluster_config["namespaces"][0]` |
| `pipeline/deploy.py:2366` | `setup_config.get("namespaces")` fallback chain | `cluster_config["namespaces"]` |
| `pipeline/deploy.py:3137` | same | same |
| `pipeline/deploy.py:3389, 3407, 3427` | same | same |
| `pipeline/prepare.py:659` | `setup_config.get("namespace", "default")` | `cluster_config["namespaces"][0]` |
| `pipeline/setup.py` (various) | cluster-side flow | removed (logic moves to `cluster_ops.py`) |

**Identifying the cluster context at read time**: callsites need to know which cluster's config to load. Options:

1. **Single-cluster assumption (Step 0 default)**: there's exactly one cluster registered. `cluster_ops.read_cluster_config()` with no args looks for the lone `clusters/*/` directory; errors if zero or >1.
2. **Cluster-id from current_run**: Step 1+ once `run_metadata.json` exists with `cluster_id`, callsites in run-aware commands read it from there.
3. **Explicit `--cluster` flag on each command**: deferred; not added in Step 0.

Step 0 picks option 1 to keep things simple. Step 1+ extends to option 2 as `run_metadata.json` lands. Option 3 is a later refinement.

**Single-cluster assumption — error semantics**:

- Zero clusters registered (`workspace/clusters/` empty or absent): `cluster_ops.read_cluster_config()` raises a clean error: `"No cluster registered. Run 'python pipeline/cluster.py provision <cluster_id>' first."`
- More than one cluster registered: raises `"Multiple clusters registered: [<ids>]. Step 0 supports only a single cluster; specify which with --cluster (not yet implemented)."` Step 1+ resolves this by reading `cluster_id` from `run_metadata.json` when run-aware commands have a run context.

---

## `layout.py` path helpers

```python
# Paths only, no I/O. Pure functions.

def workspace_dir() -> Path:
    """The workspace root. Today: <experiment-root>/workspace/.
    Derivation matches existing convention (--experiment-root flag or cwd).
    """

def clusters_dir() -> Path:
    """workspace/clusters/"""

def cluster_dir(cluster_id: str) -> Path:
    """workspace/clusters/<cluster_id>/"""

def cluster_config_path(cluster_id: str) -> Path:
    """workspace/clusters/<cluster_id>/cluster_config.json"""

def list_cluster_ids() -> list[str]:
    """Enumerate subdirectories of workspace/clusters/."""

def runs_dir() -> Path:
    """workspace/runs/ — directory may be empty at end of Step 0."""

def translations_dir() -> Path:
    """workspace/translations/ — directory may be empty at end of Step 0."""

def setup_config_path() -> Path:
    """workspace/setup_config.json — legacy file, still in use for workspace
    fields after Step 0."""
```

**Implementation notes**:

- Module is small (~80 lines). Stays under `pipeline/lib/`.
- Existing `EXPERIMENT_ROOT` resolution logic (`--experiment-root` flag, cwd fallback) is preserved and centralized here. Today it lives in `setup.py`; `layout.py` consolidates it so other modules don't reinvent.
- No state. Every call recomputes from the current `EXPERIMENT_ROOT`.

---

## `slicer.py` transfer.yaml partitioner

Pure code, no YAML structure changes. Operates on the loaded v3 manifest dict.

```python
TRANSLATION_FIELDS = [
    "scenario",
    "component",
    "context",
    "algorithms[*].source",
    # algorithms[*].config is referenced in the 3D proposal but does NOT exist
    # in v3. Slicer does not include it. If a real consumer in Step 2+ surfaces
    # the need, it gets added as an additive schema extension at that time.
]

ASSEMBLY_FIELDS = [
    "workloads",
    "baselines",
    "algorithms[*].defaults",          # the baseline-name reference
    "defaults.disable",
    # All other fields default to assembly slice (additive default).
]


def translation_slice(manifest: dict) -> dict:
    """Extract the translation-slice fields from a loaded manifest.
    Returns a dict containing only those fields, in canonical order
    (sorted keys at every level) for deterministic hashing.
    """


def assembly_slice(manifest: dict) -> dict:
    """Extract the assembly-slice fields. Same canonicalization."""


def translation_hash(manifest: dict) -> str:
    """SHA-256 over translation_slice(manifest), serialized as canonical JSON.
    Used by Step 2's `translate` (when implemented) to determine whether
    a translation needs re-running.
    """
```

**Notes**:

- The slice membership list is the design lock. Future additive fields default to `ASSEMBLY_FIELDS` unless explicitly added to `TRANSLATION_FIELDS`.
- Canonical serialization (sorted keys) ensures the hash is stable across formatter / writer differences.
- Step 0 ships and tests the slicer. The first consumer is Step 2's `translate`. Step 1's `assemble` may consume `assembly_slice()` to compute its own provenance, but that's a Step 1 decision.

---

## Testing approach

Per-module unit tests under `pipeline/tests/`:

- `test_layout.py` — path computation, EXPERIMENT_ROOT resolution.
- `test_cluster_ops.py` — `read/write/update_cluster_config` with tmpdir; `provision_namespace` and `apply_cluster_resources` mock kubectl/oc calls.
- `test_slicer.py` — slice extraction on representative v3 manifests; hash stability; canonicalization.

Integration tests:

- `test_cluster_py.py` — end-to-end `cluster.py provision` against a mocked cluster, verifies the produced `cluster_config.json` matches expectations for various flag combinations.

CI updates:

- `.github/workflows/test.yml` adds the new test files to the pytest invocation.
- `ruff check pipeline/lib/cluster_ops.py pipeline/lib/layout.py pipeline/lib/slicer.py pipeline/cluster.py --select F` is included in the lint step.

Manual / cluster verification at end-of-phase (see Demo).

---

## Dead code and test cleanup

Step 0's port and CLI changes leave concrete dead code and obsolete tests in their wake. Each child PR in Step 0's implementation MUST include — as an acceptance criterion — a cleanup of the dead code and tests it creates. Plus a final sweep at the end of the epic to catch anything missed.

### Expected dead code

| Item | Today's location | After Step 0 |
|---|---|---|
| `detect_openshift()` | `setup.py:177` | Relocates to `cluster_ops.py`. Copy in setup.py dies. |
| `check_cluster_reachable()` | `setup.py:183` | Relocates to `cluster_ops.py`. Copy in setup.py dies. |
| `secret_exists()` | `setup.py:225` | Relocates to `cluster_ops.py`. |
| `step_namespace()` | `setup.py:338` | Decomposed into `cluster_ops.provision_namespace()`. Original dies. |
| `step_rbac()` | `setup.py:371` | Same — decomposed into provision_namespace. |
| `step_secrets()` | `setup.py:444` | Same. |
| `step_pvcs()` | `setup.py:621` | Same. |
| `step_tekton()` | `setup.py:661` | Decomposed across `cluster_ops.apply_cluster_resources` (cluster-wide bits) and `cluster_ops.provision_namespace` (per-namespace bits). |
| `step_test_push()` | `setup.py:574` | Stays IF the registry credential test stays in setup.py; dies if registry test is retired (separate decision, not Step 0's). |
| `_detect_container_runtime()` | `setup.py:144` | Stays IF `step_test_push` stays; dies otherwise. |
| `step_config_output()` cluster-field writing | `setup.py:703` | Partial: workspace-field writing stays in setup.py; cluster-field writing dies. |
| Fallback `or [setup_config.get("namespace", "")]` | `deploy.py:2366, 3137, 3407` | Dies. Each callsite simplifies to `cluster_config["namespaces"]`. |
| Setup-time pipeline.yaml application | inside `step_tekton` | Moves to `cluster_ops.apply_cluster_resources`. |

### Obsolete tests in `pipeline/tests/test_setup_pipeline.py`

| Test | Status |
|---|---|
| `test_skipped_when_no_cluster` (line 69) | DELETE — `--no-cluster` flag removed in Step 0. |
| `test_redeploy_applies_pipeline` (284) | DELETE — `--redeploy-tasks` removed; idempotent re-run replaces it. |
| `test_redeploy_custom_pipeline_missing_warns` (305) | DELETE — same reason. |
| `test_redeploy_default_pipeline_missing_errors` (323) | DELETE — same reason. |
| `test_config_output_includes_hf_secret_name` (191) | MOVE — equivalent assertion lives in `test_cluster_py.py` for `cluster_config.json`. Delete the setup.py version. |
| `test_refreshes_setup_owned_fields_on_rerun` (401) | UPDATE — the test enumerates setup-owned fields including `container_runtime` and `namespace`. After Step 0 those leave setup.py's scope; the test's field list shrinks (registry, repo_name, pipeline_yaml, orchestrator_image remain). |
| Tests asserting `tektonc_dir` / `sim2real_root` / `setup_timestamp` get written (if any) | DELETE — these fields are removed from setup_config.json. |
| Tests for storage_class flag/default | MOVE to `test_cluster_py.py` — `--storage-class` is now cluster.py provision's flag. |
| `test_first_run_creates_full_metadata` (422) | UPDATE — the "full metadata" shape changes; cluster-side fields move out. |

Anything else in `test_setup_pipeline.py` not enumerated above stays as-is (tests for `--pipeline-yaml`, `--orchestrator-image`, and `run_metadata.json` preservation tests for workspace fields).

### Per-PR cleanup acceptance criteria

Each implementation child PR includes in its acceptance:

- "No dead code left in the files this PR modifies." Reviewer checks for: leftover imports, unreachable branches, functions whose only callers were removed in this PR.
- "No obsolete tests left in the test files this PR modifies." Reviewer checks for: tests whose feature was removed, tests asserting fields that no longer exist, mocks of helpers that no longer get called.

### Final sweep child issue

The last child issue in Step 0 is a dedicated cleanup pass:

- Grep across the whole worktree for references to removed identifiers (`setup_config.get("namespace"`, `tektonc_dir`, `setup_timestamp`, `container_runtime` as a persisted field, `--no-cluster`, `--redeploy-tasks`, `--test-push`, `--test-push-tag`). Each hit is either a missed cleanup (fix in this PR) or an intentional reference (justify in PR description).
- `ruff check` + `python -m pytest pipeline/ -v` clean.
- Verify the demo's jq check (line in the Demo section) actually passes: the removed fields are absent from `setup_config.json`.

This final sweep is the formal end of Step 0.

---

## Documentation updates

Concrete touchpoints, verified by grep against the current tree. Each item below is a real change required as part of Step 0's PRs.

### Narrative-level update (both `pipeline/README.md` and `CLAUDE.md`)

The line-level patches enumerated below are necessary but not sufficient. Today both docs have a "here's how to get started" narrative built around one command (`setup.py` doing everything). Step 0 splits that into two commands with separated concerns (cluster.py provision for cluster bootstrap; setup.py shrunk to workspace config). The narrative needs to be rewritten, not just patched.

The "Operator getting-started flow" section above is the source-of-truth for the new narrative. Both documents should adopt structurally equivalent content:

- A "Getting started" / "Quick start" section opens with **Step A** (cluster.py provision) — including a note that it's idempotent and only re-run when adding/changing cluster slots — followed by **Step B** (setup.py for workspace).
- A "Per-run cycle" subsection describes Steps C–F (today's commands), explicitly noting that they NOW read cluster-scoped fields from `cluster_config.json` instead of `setup_config.json`.
- The pipeline-flow diagram is updated to show cluster.py provision as a separate one-time-per-cluster prerequisite, distinct from the per-run cycle.

### `CLAUDE.md` (project root)

Plus the following eight line-level references (in addition to the narrative rewrite above):

| Line | Today's content | Update |
|---|---|---|
| 11 | "`pipeline/pipeline.yaml` — Static Tekton Pipeline definition (applied by `setup.py`)" | Change "applied by `setup.py`" → "applied by `cluster.py provision`" |
| 27 | Pipeline flow `setup.py → prepare.py → ...` | Add a separate one-time-per-cluster step: `cluster.py provision` (executed once before the run-cycle flow) |
| 33 | Invocation example uses `python pipeline/setup.py --experiment-root ...` | Add `python pipeline/cluster.py provision <cluster_id> --namespaces ... --experiment-root ...` as the cluster-bootstrap command |
| 42 | "**`pipeline/setup.py`** — One-time cluster bootstrap..." | Revise: setup.py is now a workspace-config writer; `cluster.py provision` does the cluster bootstrap |
| 57 | deploy.py description: "driven by workspace files and `setup_config.json`" | Append "and `clusters/<id>/cluster_config.json`" |
| 81 | Workspace artifact table row `setup_config.json` | Add a new row for `clusters/<id>/cluster_config.json` (Written by: `cluster.py provision`; Read by: `deploy.py`, future commands). Update the `setup_config.json` row to reflect that it now holds workspace fields only. |
| 83 | `run_metadata.json` row references setup.py as writer | Note that with Step 0, the cluster-side write by setup.py is gone (cluster.py provision doesn't write run_metadata.json — that's Step 1+) |
| 134 | Second pipeline-flow mention (Stage Contracts section) | Same as line 27 |

### `pipeline/README.md`

Narrative rewrite per "Narrative-level update" above, plus a significant rewrite of the `## setup.py` section, plus a new `## cluster.py` section. Specific line-level touchpoints:

| Lines | Change |
|---|---|
| 6 | Pipeline flow diagram — add cluster.py provision as one-time-per-cluster prep |
| 19 | Invocation example block — add cluster.py provision example |
| 31 | "`pipeline.yaml` ... applied by `setup.py`" → "applied by `cluster.py provision`" |
| 35–66 | Entire `## setup.py` section — trim cluster-side flags (`--namespaces`, `--no-cluster`, `--redeploy-tasks`, etc.); document the workspace-only flag set; note that cluster-side work moved to `cluster.py provision` |
| 60 | `--no-cluster` flag mention — remove (flag is gone with the cluster-side carve-out) |
| 66 | "Writes `workspace/setup_config.json` and `workspace/runs/<run>/run_metadata.json`" | setup.py no longer writes run_metadata.json (no cluster-side responsibility). Note that cluster.py provision writes cluster_config.json. |
| 68 | "`setup_config.json` includes workspace bindings for two PVCs..." | These move to `cluster_config.json:workspaces`. Update the reference. |
| 116, 126 | deploy.py references `setup_config.json` for namespaces | Update to reference `cluster_config.json:namespaces` |
| 177 | "The safe way to add a slot is `setup.py --namespaces NS1,NS2,NS3` (provisions before publishing the change)." | Update to: "The safe way to add a slot is `cluster.py provision` (re-run with the new full list). When issue #377 lands, `deploy.py slots add NS` is the operator-friendly form." |
| 185 | "Requires `orchestrator_image` in `setup_config.json`" | Unchanged — orchestrator_image is workspace-scoped and stays |
| 261 | "**`switch`** ... updates `setup_config.json`" | Unchanged — switch updates `current_run` (workspace-scoped) |
| 337 | Top-level architecture summary references `setup.py --namespaces` | Replace with `cluster.py provision` for the namespace provisioning sentence |
| New | Add a new `## cluster.py` section between the project overview and `## setup.py`, documenting flags, behavior, output (`cluster_config.json`), and idempotency |
| New | Update `pipeline/lib/` module table to include `cluster_ops.py`, `layout.py`, `slicer.py` |
| New | Add `clusters/<id>/cluster_config.json` to the Workspace Artifacts table |

### `docs/troubleshooting.md`

Verified zero matches for `setup_config`, `setup.py`, `namespaces`, `cluster_config`. No updates required.

### Skill prompt files (`.claude/skills/sim2real-*/SKILL.md`)

**No Step 0 updates required.** Verified consumers:

- `sim2real-analyze/SKILL.md:31-33` reads `current_run` (workspace field — stays).
- `sim2real-translate/SKILL.md:92, 119` reads `sim2real_root` (workspace field — stays) and `current_run` (workspace field — stays).
- `sim2real-bootstrap/SKILL.md`, `sim2real-check/SKILL.md`: no reads of cluster-scoped fields confirmed by grep.

Skill paths reorganize in later steps (Step 2 rewrites sim2real-translate; Step 3 rewrites sim2real-check; Step 4 rewrites sim2real-bootstrap; etc.) — those updates are owned by their respective epics.

### CI workflow (`.github/workflows/test.yml`)

Add the three new test files to the pytest invocation:

```yaml
python -m pytest pipeline/ \
  pipeline/tests/test_layout.py \
  pipeline/tests/test_cluster_ops.py \
  pipeline/tests/test_cluster_py.py \
  pipeline/tests/test_slicer.py \
  .claude/skills/sim2real-analyze/tests/ \
  .claude/skills/sim2real-bootstrap/tests/ \
  .claude/skills/sim2real-translate/tests/ \
  -v
```

(Lint step `ruff check pipeline/ .claude/skills/ --select F` automatically picks up the new modules since they're under `pipeline/lib/` and `pipeline/cluster.py`.)

---

## Demo (acceptance criteria)

End-of-phase verification on a real cluster:

```bash
# 1. Provision a cluster
# (Assuming HF_TOKEN, REGISTRY_USER, REGISTRY_TOKEN, GITHUB_TOKEN are in the env;
#  cluster.py provision reads them, creates the k8s secrets, records the names.)
python pipeline/cluster.py provision ocp-east \
    --namespaces kalantar-0,kalantar-1,kalantar-2,kalantar-3

# Equivalent with explicit value flags:
#   --hf-token "$HF_TOKEN" \
#   --registry-user "$REGISTRY_USER" \
#   --registry-token "$REGISTRY_TOKEN" \
#   --github-token "$GITHUB_TOKEN"

# 2. Verify cluster state
kubectl get ns kalantar-0 kalantar-1 kalantar-2 kalantar-3
kubectl get rolebinding -n kalantar-0
kubectl get pvc -n kalantar-0
kubectl get tasks -n kalantar-0     # Tekton tasks applied

# 3. Verify config artifact
cat workspace/clusters/ocp-east/cluster_config.json
# Expected: well-formed JSON with the schema above.
# secret_names.dockerhub_creds is empty unless --dockerhub-creds-secret was passed.

# 4. Verify setup_config.json no longer has cluster fields
jq 'has("namespaces") or has("is_openshift") or has("storage_class") or has("hf_secret_name") or has("workspaces") or has("namespace") or has("tektonc_dir") or has("container_runtime") or has("setup_timestamp")' workspace/setup_config.json
# Expected: false
# Note: sim2real_root is NOT in the check — it stays in setup_config.json until
# sim2real-translate is rewritten in Step 2.

# 5. Verify existing commands still work after the field move
python pipeline/setup.py --registry ghcr.io/myorg --repo-name router    # workspace-only flags
python pipeline/deploy.py run --run sr-mk                                # reads namespaces from cluster_config.json
```

Demo passes if:

- All four namespaces are provisioned and `kubectl get ns,role,rolebinding,pvc,tasks` is clean.
- `cluster_config.json` matches the documented schema.
- `setup_config.json` contains only workspace fields (no cluster fields, no cruft fields).
- `deploy.py run` works against the new `cluster_config.json` source (i.e. the porting is complete).

---

## Downstream schema intent (design contracts for later steps)

Step 0 doesn't implement these, but pins their shape so later steps inherit consistent contracts.

### `run_metadata.json` (Step 1 — `assemble`)

```json
{
  "run_name": "R1",
  "translation_hash": "abc123…",
  "cluster_id": "ocp-east",
  "image_tag": "ghcr.io/myorg/sim2real-scorer:abc123abcd",
  "assembled_at": "2026-06-29T22:00:00Z"
}
```

No `params_hash` (drift detection out per the design discussion: assemble overwrites).

### `translation_output.json` (Step 1 BYO register / Step 2 skill translate)

```json
{
  "algorithms": [{"name": "sim2real-ac", ...}],
  "source": "byo" | "skill",
  "image_ref": "ghcr.io/...:abc123abcd"      // for BYO; computed for skill
}
```

### `manifest.assembly.yaml` (Step 5 — assemble with replicas)

YAML snapshot of the assembly slice at assemble time + `replicas: N` field.

```yaml
replicas: 3
workloads: [chat-low, chat-mid]
baselines: [...]
defaults: {disable: [...]}
algorithms: [{name: sim2real-ac, defaults: baseline-a}]
```

Pair-key suffix (`|iN` vs `|rN`) is an open question deferred to Step 5's design-epic.

### `registered.json` (Step 1 — BYO register)

```json
{
  "algorithm_name": "sim2real-ac",
  "image_ref": "ghcr.io/...:user-built",
  "image_digest": "sha256:...",
  "source": "byo",
  "registered_at": "..."
}
```

Image-tag mutability enforcement is an open question deferred to Step 1's design-epic.

---

## Open questions (deferred, surfaced for downstream design)

| Q | Owning step | Surfaced where |
|---|---|---|
| Pair-key suffix `\|iN` vs `\|rN` | Step 5 | `manifest.assembly.yaml` schema |
| Image-tag mutability enforcement for BYO | Step 1 | `registered.json` schema |
| Cluster context (single-cluster vs multi-cluster read patterns) | Step 1+ | Once `run_metadata.json` exists, callsites can use `cluster_id` from it instead of "the lone cluster" assumption |

---

## Risks

- **Porting breadth.** ~15 callsites read cluster fields from `setup_config.json` today. Missing one means a regression after the port. Mitigated by: (a) grep-driven inventory at the start of implementation; (b) the demo's Step 5 (existing commands still work) exercises the ported paths.
- **`setup.py`'s CLI break.** Operators with scripts that call `setup.py --namespaces ...` need to switch to `cluster.py provision`. Communicated via README + changelog; gated on `refactor/v2` so main is unaffected until the umbrella merges.
- **Idempotency edge cases in `provision_namespace`.** Re-running against a namespace that's been manually edited (e.g. RBAC drift) produces structured failure entries. Surfaces at warn level; operator decides.
- **OpenShift detection.** Today's `detect_openshift()` works against a live cluster. If `cluster.py provision` is run against an unreachable cluster, detection fails before any other work. Currently the same failure mode as today's `setup.py`. Acceptable for Step 0.

---

## Out-of-scope, explicitly

- `assemble`, `translate`, `build`, `use`, `list runs`, `list clusters`, `list translations`, `collect` — all Step 1+.
- BYO flow (`translation register`) — Step 1.
- Skill-driven translate flow — Step 2.
- `sim2real-check` skill — Step 3.
- Bootstrap skill — Step 4.
- Replicas — Step 5.
- Validate/execute pattern — Step 6.
- Issue #377's `deploy.py slots add/remove/list` — later step that consumes Step 0's substrate.
- `publish_slot_pool` helper (ConfigMap + `kubectl cp` live propagation) — issue #377's own deliverable.
- Per-cluster registries, kubeconfig context validation, per-cluster overlays — 3D-deferred.
- Migration of existing experiment repos — none needed (transfer.yaml schema unchanged).
- Documentation updates — enumerated explicitly in the Documentation Updates section above; in scope, not handwaved.

---

## Implementation order suggestion (for split-epic)

Approximate child-issue breakdown (final list comes from split-epic):

1. **Layout module + tests** — pure path code. Smallest first.
2. **Slicer module + tests** — pure transfer.yaml code, no cluster dependence.
3. **`cluster_ops.py` read/write/update_cluster_config + tests** — file-system only, no cluster.
4. **`cluster_ops.py` provision_namespace + apply_cluster_resources** — cluster-side primitives. Tested against mocked kubectl/oc.
5. **`cluster.py provision` + tests** — orchestrator. End-to-end test.
6. **Port `deploy.py` consumers** of cluster fields. Includes test fixture rewrites.
7. **Port `prepare.py`, `run.py`, `lib/remote.py`, `lib/run_manager.py` consumers**.
8. **Edit `setup.py`** to stop writing cluster fields and cruft fields. Removes cluster-side flow code.
9. **CI + docs update** — `.github/workflows/test.yml`, `CLAUDE.md`, `pipeline/README.md` reflect the new module structure (narrative rewrite + line-level patches per Documentation Updates section).
10. **Final cleanup sweep** — grep-based audit of removed identifiers, deletion of any missed dead code and obsolete tests (per Dead code and test cleanup section). `ruff check` and `pytest` clean. Closes the epic.

Each child is one PR. Ordering is dependency-driven; 1–5 land first, then 6–8 port consumers, 9 finishes the docs/CI, 10 is the final sweep. Per-PR cleanup acceptance criteria apply to children 6–8 specifically.

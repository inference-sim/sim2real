"""Cluster-config file primitives + kubectl/oc primitives for per-cluster setup.

Step 0 carves cluster-side responsibilities out of ``pipeline/setup.py`` into
a new ``pipeline/cluster.py`` entry point backed by this library. The module
is split into two slices:

* File-system primitives — :func:`read_cluster_config`,
  :func:`write_cluster_config`, :func:`update_cluster_config`. Atomic writes
  (tmpfile + ``Path.replace``); path resolution delegated to
  :mod:`pipeline.lib.layout`.
* kubectl/oc primitives — :func:`provision_namespace` (per-namespace
  resources: namespace, RBAC, secrets, PVCs, Tekton tasks) and
  :func:`apply_cluster_resources` (Tekton Pipeline definition). Each
  primitive is idempotent in the ``kubectl apply``/``oc new-project``
  sense: applying the same manifest twice converges on the same cluster
  state, and an already-existing object is not an error. Note that
  ``kubectl apply`` server-side merges — this module does NOT compare
  the live object's contents to the supplied manifest, so drift between
  a cluster-mutated resource and the source-of-truth manifest is
  reconciled silently. Per-step outcomes are reported via
  :class:`ProvisionResult` (see "outcome reporting" below).

Outcome reporting:

* Successful sub-step (apply returned zero, or pre-check said
  already-exists): recorded on ``ProvisionResult.steps_ok``.
* Intentional suppression (caller-supplied ``skip``) or operator
  prerequisites not provided (e.g. absent secret value + no
  pre-installed Secret) or best-effort YAMLs that hit a tolerated
  permission error (cluster-scoped RBAC Forbidden): recorded on
  ``ProvisionResult.steps_skipped``. Soft divergence — flips
  :attr:`ProvisionResult.diverged` to True without anything in
  ``steps_failed``.
* Non-zero exit from kubectl/oc, missing required YAML, or malformed
  config: recorded on ``ProvisionResult.steps_failed``. Hard divergence.

Hard failures (auth, network) that prevent reasoning about per-step
outcomes raise instead — :class:`ClusterUnreachableError` for
connectivity issues (from :func:`check_cluster_reachable`), and
``subprocess.CalledProcessError`` from :func:`apply_cluster_resources`'s
per-namespace applies. No exception ever escapes
:func:`provision_namespace`: every sub-step lands in exactly one of
``steps_ok`` / ``steps_skipped`` / ``steps_failed``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from pipeline.lib import layout
from pipeline.lib.log import err, info, ok, warn
from pipeline.lib.values import deep_merge


# Top-level keys in cluster_config.json whose contents are merged into the
# existing value rather than wholesale-replaced. Per design (see
# docs/epics/step-0/design.md, "cluster_config.json schema"): ``secret_names``
# is a flat string→string dict; ``workspaces`` is a dict of Tekton workspace
# bindings. Partial updates to either should preserve untouched keys.
_DEEP_MERGE_KEYS = ("secret_names", "workspaces")


# Repo-relative paths used by the kubectl/oc primitives. ``cluster_ops.py``
# lives at ``pipeline/lib/cluster_ops.py``, so the repo root is two parents up.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_TEKTONC_DIR = _REPO_ROOT / "tektonc-data-collection"
_PIPELINE_YAML = _REPO_ROOT / "pipeline" / "pipeline.yaml"


# PVC defaults — today's setup.py hardcodes these. ``cluster_config['workspaces']``
# only stores the Tekton binding (claim name); size + access mode are not part
# of the schema. If a future scenario needs per-PVC sizing, extend the schema
# and surface here.
_DEFAULT_PVC_SIZE = "50Gi"
_DEFAULT_PVC_ACCESS_MODE = "ReadWriteMany"


# Sub-steps for provision_namespace, in execution order. ``skip`` arg values
# must be drawn from this set.
_PROVISION_STEPS = ("namespace", "rbac", "secrets", "pvc", "tekton")


class ClusterUnreachableError(RuntimeError):
    """Raised by :func:`check_cluster_reachable` when the API server cannot
    be contacted. Carries an actionable hint as ``args[1]`` when available.
    """


def read_cluster_config(cluster_id: str) -> dict:
    """Read ``clusters/<cluster_id>/cluster_config.json``.

    Returns the parsed JSON object as a dict. Returns ``{}`` if the file does
    not exist — callers that need a not-yet-provisioned cluster to fail loudly
    should check the result themselves.
    """
    path = layout.cluster_config_path(cluster_id)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_cluster_config(cluster_id: str, config: dict) -> None:
    """Atomically write ``clusters/<cluster_id>/cluster_config.json``.

    Creates the cluster directory if it does not exist. Uses tmpfile +
    ``Path.replace`` so a concurrent reader never observes a partially
    written file: the rename is atomic on POSIX (and Windows) for paths on
    the same filesystem, and the tmpfile is created in the same directory.
    """
    path = layout.cluster_config_path(cluster_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".cluster_config-", suffix=".json.tmp")
    try:
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, sort_keys=True)
            f.write("\n")
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def update_cluster_config(cluster_id: str, /, **updates) -> dict:
    """Read-modify-write of ``cluster_config.json``. Returns the new config.

    Update semantics, per design:

    - Keys ``secret_names`` and ``workspaces`` are **deep-merged** into the
      existing values when the caller passes a dict. Keys not mentioned in
      the update are preserved.
    - All other top-level keys are **fully replaced** by the supplied value.

    Examples::

        # Replace the namespaces list wholesale.
        update_cluster_config("ocp-east", namespaces=["a", "b"])

        # Merge a new secret name in; existing entries are preserved.
        update_cluster_config("ocp-east", secret_names={"hf_token": "hf-2"})

        # Add a workspace binding; existing bindings are preserved.
        update_cluster_config(
            "ocp-east",
            workspaces={"new-wks": {"persistentVolumeClaim": {"claimName": "new-pvc"}}},
        )

    Operates on the read result of ``read_cluster_config`` — an absent file
    is treated as ``{}``, so this function can be used to initialise a
    cluster_config from scratch as well as to mutate an existing one.

    The write step is atomic; the read-modify-write sequence as a whole is
    not (no inter-process locking). Concurrent updaters are not expected.
    """
    cfg = read_cluster_config(cluster_id)
    for key, value in updates.items():
        if key in _DEEP_MERGE_KEYS and isinstance(value, dict):
            existing = cfg.get(key)
            if isinstance(existing, dict):
                cfg[key] = deep_merge(existing, value)
                continue
        cfg[key] = value
    write_cluster_config(cluster_id, cfg)
    return cfg


# ── Slot-pool live propagation ───────────────────────────────────────


# CM name where the run-inputs are packaged. Kept in lockstep with
# ``pipeline.lib.remote.CONFIGMAP_NAME`` — that module is the CM's
# authoring authority; we only mutate the cluster_config key here when
# a slot is added or removed. Duplicated as a bare string so this
# module doesn't take a dependency on ``pipeline.lib.remote`` (which
# would introduce a cluster_ops ↔ remote cycle at import time).
_RUN_INPUTS_CONFIGMAP_NAME = "sim2real-run-inputs"

# CM key that carries the cluster_config JSON blob. Matches
# ``_CLUSTER_CONFIG_KEY_PREFIX`` in ``pipeline.lib.remote``. Kept in
# lockstep for the same reason as ``_RUN_INPUTS_CONFIGMAP_NAME``.
_CLUSTER_CONFIG_KEY_PREFIX = "cluster_config--"


def publish_slot_pool(cluster_id: str) -> None:
    """Propagate the on-disk cluster_config.json to a live run-inputs CM.

    Called after any mutation to ``cluster_config.json:namespaces`` — from
    ``cluster init``, ``cluster slot add``, ``cluster slot remove``. Steps:

    1. Read the current cluster_config from disk. Determine primary from
       ``namespaces[0]``.
    2. Probe whether the ``sim2real-run-inputs`` ConfigMap exists in
       ``<primary>``. Three outcomes:

       - **Probe returns non-zero** (cluster unreachable, RBAC denied,
         malformed args): warn with kubectl stderr and skip the patch.
         The on-disk change is still written; live propagation just
         did not happen. Distinct from "CM absent" so the operator
         sees an auth/network problem rather than a misleading "no CM
         found" info line.
       - **Probe returns empty stdout** (CM absent): info-log the skip.
         The on-disk change will be picked up on the next
         ``deploy.py run --remote``'s assemble step.
       - **Probe returns the CM name**: patch its
         ``cluster_config--<cluster_id>`` key with the JSON serialization
         of the current config. Patch failures (TOCTOU CM deletion,
         mid-command RBAC change, immutable CM) are also non-fatal —
         warn with kubectl stderr and return. The on-disk change is
         still authoritative.

    Never creates the CM (that is ``build_run_inputs_configmap``'s
    responsibility, called from the run-start path). Never touches Pods:
    the running orchestrator's runtime container mounts the CM key
    directly, so kubelet propagates the update within ~60s and the
    orchestrator's per-cycle ``_refresh_namespaces`` picks it up.

    Idempotent — patching with the same value converges to the same
    final state (kubectl patch --type=merge unconditionally advances
    resourceVersion, so this DOES re-trigger watchers, but the
    ConfigMap contents are unchanged). Safe to call from every
    mutation path.
    """
    cfg = read_cluster_config(cluster_id)
    namespaces = cfg.get("namespaces") or []
    if not namespaces:
        info(
            f"publish_slot_pool: cluster {cluster_id!r} has no namespaces; "
            f"skipping ConfigMap patch"
        )
        return
    primary = namespaces[0]

    # Existence probe. With --ignore-not-found, kubectl exits 0 whether
    # the CM is present or absent; a non-zero exit therefore signals a
    # real failure (cluster unreachable, RBAC denial, malformed args)
    # that would be quietly swallowed if we folded it into the "skip"
    # branch. Split the two so the operator sees the auth/network
    # problem instead of the misleading "no CM found" info line.
    probe = _run(
        [
            "kubectl", "get", "configmap", _RUN_INPUTS_CONFIGMAP_NAME,
            f"-n={primary}", "--ignore-not-found", "-o=name",
        ],
        check=False, capture=True,
    )
    if probe.returncode != 0:
        stderr = (probe.stderr or probe.stdout or "").strip()
        warn(
            f"publish_slot_pool: probe for {_RUN_INPUTS_CONFIGMAP_NAME!r} in "
            f"{primary!r} failed (rc={probe.returncode}): {stderr}. "
            f"On-disk change is written; live orchestrator (if any) "
            f"will NOT see it until the probe succeeds on a later re-run."
        )
        return
    if not (probe.stdout or "").strip():
        info(
            f"publish_slot_pool: no {_RUN_INPUTS_CONFIGMAP_NAME!r} ConfigMap in "
            f"{primary!r}; on-disk change will be picked up on next "
            f"'deploy.py run --remote' from the assemble step"
        )
        return

    key = f"{_CLUSTER_CONFIG_KEY_PREFIX}{cluster_id}"
    payload = json.dumps({"data": {key: json.dumps(cfg, indent=2)}})
    # Patch is non-fatal: publish_slot_pool's contract is that the
    # on-disk mutation has already committed by the time we get here,
    # and the CM patch is best-effort live propagation. A raise from
    # this call would surface as an uncaught traceback in cmd_init /
    # cmd_slot_add / cmd_slot_remove AFTER their on-disk mutation has
    # already succeeded — misleading the operator into thinking the
    # command failed. The narrow window that matters here: TOCTOU CM
    # deletion between probe and patch, mid-command RBAC change, or a
    # CM that has been made immutable. Warn and continue so the
    # operator sees the problem without discarding the on-disk change.
    patch_result = _run(
        [
            "kubectl", "patch", "configmap", _RUN_INPUTS_CONFIGMAP_NAME,
            f"-n={primary}", "--type=merge", "-p", payload,
        ],
        check=False, capture=True,
    )
    if patch_result.returncode != 0:
        stderr = (patch_result.stderr or patch_result.stdout or "").strip()
        warn(
            f"publish_slot_pool: patch of data key {key!r} in ConfigMap "
            f"{_RUN_INPUTS_CONFIGMAP_NAME!r} (namespace {primary!r}) failed "
            f"(rc={patch_result.returncode}): {stderr}. "
            f"On-disk change is written; live orchestrator (if any) will NOT "
            f"see it until the patch succeeds on a later re-run."
        )
        return
    info(
        f"publish_slot_pool: patched data key {key!r} in ConfigMap "
        f"{_RUN_INPUTS_CONFIGMAP_NAME!r} (namespace {primary!r}); "
        f"running orchestrator (if any) will see the change within ~60s "
        f"(kubelet ConfigMap propagation window)"
    )


# ── Subprocess + tool discovery helpers ──────────────────────────────


def _run(
    cmd: list[str],
    *,
    check: bool = True,
    capture: bool = False,
    input: str | None = None,
) -> subprocess.CompletedProcess:
    """Thin wrapper over ``subprocess.run``. Tests monkeypatch this to
    intercept every kubectl/oc invocation.
    """
    return subprocess.run(cmd, check=check, text=True, capture_output=capture, input=input)


def _which(cmd: str) -> bool:
    return shutil.which(cmd) is not None


# ── Relocated support helpers (from setup.py) ────────────────────────


def detect_openshift() -> bool:
    """Return True if the ``oc`` CLI is installed and the user is logged in.

    Mirrors today's ``pipeline/setup.py:177`` logic. ``cluster.py provision``
    calls this once at startup and records the result on
    ``cluster_config['is_openshift']``; :func:`provision_namespace` then
    reads that field rather than re-probing the cluster per namespace.
    """
    if not _which("oc"):
        return False
    return _run(["oc", "whoami"], check=False, capture=True).returncode == 0


def check_cluster_reachable() -> None:
    """Verify the cluster is reachable via ``kubectl cluster-info``.

    Raises :class:`ClusterUnreachableError` with a classified reason + hint on
    failure. A 403 "forbidden" reply is treated as reachable (the API server
    answered and authenticated us; the user simply lacks permission for
    whatever ``cluster-info`` probed).

    Library convention: this primitive raises rather than calling
    ``sys.exit`` — the operator-facing CLI (:mod:`pipeline.cluster`) is
    responsible for translating into a clean exit message.
    """
    result = _run(["kubectl", "cluster-info"], check=False, capture=True)
    if result.returncode == 0:
        return

    combined = ((result.stdout or "") + (result.stderr or "")).lower()
    if "forbidden" in combined:
        return

    if any(s in combined for s in ("no such host", "name resolution", "lookup")):
        reason = "DNS resolution failed — the cluster hostname is not reachable."
        hint = "Check your VPN, network connection, or kubeconfig server address."
    elif "connection refused" in combined:
        reason = "Connection refused — nothing is listening on the cluster API endpoint."
        hint = "Ensure the cluster is running and the API server port is accessible."
    elif "i/o timeout" in combined or "timeout" in combined:
        reason = "Connection timed out — the cluster API server did not respond."
        hint = "Check your VPN or firewall; the cluster may be stopped or unreachable."
    elif "unauthorized" in combined:
        reason = "Authentication failed — your credentials or kubeconfig may be expired."
        hint = "Try: kubectl config view  or re-run your cluster login command."
    elif "no configuration" in combined or "no such file" in combined:
        reason = "No kubeconfig found — kubectl has no cluster to connect to."
        hint = "Set KUBECONFIG or run your cluster login command first."
    else:
        reason = "kubectl cluster-info failed."
        hint = (result.stderr or "").strip() or (result.stdout or "").strip()

    raise ClusterUnreachableError(reason, hint)


def secret_exists(name: str, namespace: str) -> bool:
    """Return True if the named Kubernetes secret exists in the namespace."""
    return _run(
        ["kubectl", "get", "secret", name, f"-n={namespace}"],
        check=False, capture=True,
    ).returncode == 0


def namespace_provisioned(namespace: str) -> bool:
    """Return True if the namespace looks provisioned for sim2real use.

    Best-effort probe used by ``cluster slot list`` — checks for the
    presence of the ``sim2real-runner`` ServiceAccount, which every
    :func:`provision_namespace` invocation creates as part of its RBAC
    step. Presence of the SA is a proxy for "this namespace has been
    through :func:`provision_namespace`"; it does not verify that every
    sub-resource is intact.

    A False result means "operator has not (successfully) provisioned
    this namespace via cluster init / slot add"; the namespace may
    still exist as a k8s namespace object without being sim2real-ready.
    """
    return _run(
        ["kubectl", "get", "serviceaccount", "sim2real-runner", f"-n={namespace}"],
        check=False, capture=True,
    ).returncode == 0


def deprovision_metrics_rbac(namespace: str) -> tuple[bool, str]:
    """Delete the per-namespace metrics-reader RBAC + Secret provisioned
    by :func:`provision_namespace`'s ``_step_rbac`` from roles-cluster.yaml
    and roles-ns.yaml (see sim2real#579).

    Two resources:

    - ClusterRoleBinding ``sim2real-metrics-reader-<namespace>`` — cluster
      scoped; without this cleanup, every removed slot leaves a dangling
      binding that outlives the namespace.
    - Secret ``inference-gateway-sa-metrics-reader-secret`` in
      ``<namespace>`` — a long-lived SA token; would go away with the
      namespace, but we're not deleting the namespace on slot remove so
      it needs an explicit delete.

    Both deletes use ``--ignore-not-found`` so a partially-provisioned
    slot is fine to remove. Called by ``cluster.py slot remove`` before
    updating the pool config on disk.

    Returns ``(True, "")`` on success or ``(False, err)`` on the first
    kubectl failure encountered. Callers surface failures but treat them
    as non-fatal — pool config still gets updated so on-disk state
    catches up.
    """
    crb_name = f"sim2real-metrics-reader-{namespace}"
    proc = _run(
        ["kubectl", "delete", "clusterrolebinding", crb_name,
         "--ignore-not-found"],
        check=False, capture=True,
    )
    if proc.returncode != 0:
        return (False, f"clusterrolebinding/{crb_name}: "
                       f"{(proc.stderr or proc.stdout).strip()}")

    secret_name = "inference-gateway-sa-metrics-reader-secret"
    proc = _run(
        ["kubectl", "delete", "secret", secret_name,
         f"-n={namespace}", "--ignore-not-found"],
        check=False, capture=True,
    )
    if proc.returncode != 0:
        return (False, f"secret/{secret_name} in {namespace}: "
                       f"{(proc.stderr or proc.stdout).strip()}")

    return (True, "")


# ── ProvisionResult + provision_namespace ────────────────────────────


@dataclass
class ProvisionResult:
    """Structured outcome of :func:`provision_namespace`.

    Follows issue #377's "surface every divergence" convention: every
    sub-step lands in exactly one of ``steps_ok``, ``steps_skipped``, or
    ``steps_failed``. ``diverged`` is shorthand for "either skipped or
    failed any sub-step" — operators use it as a one-line summary signal.
    """

    namespace: str
    steps_ok: list[str] = field(default_factory=list)
    steps_skipped: list[tuple[str, str]] = field(default_factory=list)
    steps_failed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def diverged(self) -> bool:
        return bool(self.steps_skipped or self.steps_failed)


def provision_namespace(
    namespace: str,
    cluster_config: dict,
    *,
    secret_values: dict | None = None,
    skip: list[str] | tuple[str, ...] = (),
) -> ProvisionResult:
    """Provision ONE namespace's cluster-side resources. Idempotent.

    Sub-steps, in order (``skip`` suppresses by name):

    1. ``namespace`` — create via ``oc new-project`` on OpenShift, else
       ``kubectl create ns``. Already-exists is treated as success.
    2. ``rbac`` — apply the RBAC YAML bundle (tektonc + pipeline
       roles-ns / sim2real-runner). Cluster-scoped augments are
       best-effort: a kubectl Forbidden on a ClusterRole/ClusterRoleBinding
       is recorded as a structured ``skipped`` entry, not a failure.
    3. ``secrets`` — for each entry in ``cluster_config['secret_names']``,
       look up its value in ``secret_values`` (same key). Value present →
       create/update via ``kubectl apply``. Value absent but Secret exists
       → leave untouched (idempotent reuse of pre-installed Secrets).
       Value absent and no existing Secret → structured ``skipped``.
    4. ``pvc`` — create PVCs per ``cluster_config['workspaces']``. Already
       existing = success. Size hardcoded to 50Gi, access mode
       ``ReadWriteMany`` (matches today's setup.py).
    5. ``tekton`` — apply Tekton step/task YAMLs (tektonc/tekton/steps,
       tektonc/tekton/tasks). The cluster-wide Pipeline definition is NOT
       applied here; see :func:`apply_cluster_resources`.

    Args:
        namespace: target namespace.
        cluster_config: full cluster config dict. Reads ``is_openshift``,
            ``secret_names``, ``workspaces``, ``namespaces`` (for the
            "primary namespace" RBAC envsubst), ``storage_class``.
        secret_values: resolved secret payloads keyed by the same key used
            in ``cluster_config['secret_names']``. Shapes:
              * ``hf_token`` / ``github_token``: str
              * ``registry_creds`` / ``dockerhub_creds``:
                ``{"server": str, "user": str, "token": str}``
            Resolution from CLI flags / env vars / interactive prompts
            happens in the caller (``cluster.py provision``); this library
            takes resolved values to keep env-var dependence out of
            ``pipeline/lib/`` for testability.
        skip: sub-step names to suppress. Unknown names are ignored
            (no-op for forward compat).

    Returns:
        :class:`ProvisionResult` with every attempted sub-step recorded.
    """
    skip_set = set(skip or ())
    result = ProvisionResult(namespace=namespace)
    secret_values = secret_values or {}

    handlers = {
        "namespace": _step_namespace,
        "rbac": _step_rbac,
        "secrets": _step_secrets,
        "pvc": _step_pvc,
        "tekton": _step_tekton,
    }

    info(f"provisioning namespace: {namespace}")
    total = len(_PROVISION_STEPS)
    for i, step in enumerate(_PROVISION_STEPS, start=1):
        if step in skip_set:
            warn(f"  [{i}/{total}] {step}: suppressed by skip arg")
            result.steps_skipped.append((step, "suppressed by skip arg"))
            continue
        info(f"  [{i}/{total}] {step}...")
        status, reason = handlers[step](namespace, cluster_config, secret_values)
        if status == "ok":
            ok(f"  [{i}/{total}] {step}: {reason}")
            result.steps_ok.append(step)
        elif status == "skipped":
            warn(f"  [{i}/{total}] {step} skipped: {reason}")
            result.steps_skipped.append((step, reason))
        else:
            err(f"  [{i}/{total}] {step} failed: {reason}")
            result.steps_failed.append((step, reason))
    return result


def _step_namespace(
    namespace: str,
    cluster_config: dict,
    _secret_values: dict,
) -> tuple[str, str]:
    is_openshift = bool(cluster_config.get("is_openshift"))
    # Pre-check: already exists?
    if is_openshift:
        exists = _run(
            ["oc", "get", "project", namespace],
            check=False, capture=True,
        ).returncode == 0
    else:
        exists = _run(
            ["kubectl", "get", "ns", namespace],
            check=False, capture=True,
        ).returncode == 0
    if exists:
        return ("ok", "already exists")

    if is_openshift:
        proc = _run(["oc", "new-project", namespace], check=False, capture=True)
    else:
        proc = _run(["kubectl", "create", "ns", namespace], check=False, capture=True)
    if proc.returncode == 0:
        return ("ok", "created")
    # Race: created in parallel between our pre-check and create — accept.
    combined = ((proc.stdout or "") + (proc.stderr or "")).lower()
    if "alreadyexists" in combined or "already exists" in combined:
        return ("ok", "already exists (race)")
    return ("failed", (proc.stderr or proc.stdout or "create failed").strip())


def _step_rbac(
    namespace: str,
    cluster_config: dict,
    _secret_values: dict,
) -> tuple[str, str]:
    namespaces = cluster_config.get("namespaces") or [namespace]
    primary_ns = namespaces[0]
    env = {"NAMESPACE": namespace, "PRIMARY_NAMESPACE": primary_ns}

    # Matches today's setup.py:399-406 — same bundle, same required flags.
    yaml_paths: list[tuple[Path, bool]] = [
        (_TEKTONC_DIR / "tekton" / "roles-ns.yaml", True),
        (_TEKTONC_DIR / "tekton" / "roles-cluster.yaml", False),
        (_TEKTONC_DIR / "tekton" / "rbac" / "sim2real-runner.yaml", True),
        (_REPO_ROOT / "pipeline" / "rbac" / "sim2real-runner-ns.yaml", True),
        (_REPO_ROOT / "pipeline" / "rbac" / "sim2real-runner-cluster.yaml", False),
    ]
    skipped_names: list[str] = []
    for yaml_path, required in yaml_paths:
        if not yaml_path.exists():
            if required:
                return ("failed", f"{yaml_path} not found (submodule init?)")
            continue
        try:
            text = yaml_path.read_text()
        except OSError as e:
            return ("failed", f"{yaml_path.name}: read failed: {e}")
        except UnicodeDecodeError as e:
            return ("failed", f"{yaml_path.name}: not UTF-8 ({e.reason})")
        subst = _envsubst(text, env)
        info(f"    applying {yaml_path.name}")
        proc = _run(
            ["kubectl", "apply", "-f", "-"],
            input=subst, check=False, capture=True,
        )
        if proc.returncode == 0:
            continue
        combined = ((proc.stdout or "") + (proc.stderr or "")).lower()
        # Narrow predicate (matches setup.py:425-429): only skip a
        # best-effort YAML when the failure is a Forbidden on cluster-scoped
        # RBAC. Other Forbidden reasons (SCC, webhook, quota) abort.
        if (not required) and "forbidden" in combined and "clusterrole" in combined:
            skipped_names.append(yaml_path.name)
            continue
        return ("failed", f"{yaml_path.name}: {(proc.stderr or proc.stdout).strip()}")
    if skipped_names:
        return ("skipped", f"cluster-scoped RBAC unavailable: {', '.join(skipped_names)}")
    return ("ok", "applied")


def _step_secrets(
    namespace: str,
    cluster_config: dict,
    secret_values: dict,
) -> tuple[str, str]:
    secret_names = cluster_config.get("secret_names") or {}
    if not secret_names:
        return ("skipped", "no secret_names configured")

    reused: list[str] = []
    skipped: list[str] = []
    for key, name in secret_names.items():
        if not name:
            continue
        value = secret_values.get(key)
        if value in (None, "", {}):
            # No value provided. Reuse if a Secret with that name already
            # exists; otherwise surface as skipped (operator must supply).
            if secret_exists(name, namespace):
                reused.append(f"{key}({name})")
            else:
                skipped.append(f"{key}({name})")
            continue
        manifest, reason = _build_secret_manifest(key, name, namespace, value)
        if manifest is None:
            return ("failed", f"secret '{key}' ({name}): {reason}")
        info(f"    applying secret {name} ({key})")
        proc = _run(
            ["kubectl", "apply", "-f", "-"],
            input=manifest, check=False, capture=True,
        )
        if proc.returncode != 0:
            return ("failed", f"{name}: {(proc.stderr or proc.stdout).strip()}")

    if skipped:
        return ("skipped", f"no value provided for: {', '.join(skipped)}")
    return ("ok", "applied" + (f" (reused: {', '.join(reused)})" if reused else ""))


def _build_secret_manifest(
    key: str,
    name: str,
    namespace: str,
    value: object,
) -> tuple[str | None, str | None]:
    """Render a Kubernetes Secret manifest YAML via ``kubectl create
    secret ... --dry-run=client -o yaml``.

    Returns ``(manifest, None)`` on success and ``(None, reason)`` on any
    failure — unknown key, incomplete docker-creds dict, or a non-zero
    ``kubectl create --dry-run`` exit. The caller surfaces ``reason`` on
    ``ProvisionResult.steps_failed`` so distinct failure modes get
    distinct operator-facing messages.

    The four conventional keys are:

    * ``hf_token`` / ``github_token`` — generic Secret with a single
      string field (``HF_TOKEN`` / ``token``).
    * ``registry_creds`` / ``dockerhub_creds`` — docker-registry Secret;
      value is a dict with ``server``, ``user``, ``token``.

    All ``_run`` calls pass ``check=False`` so a non-zero dry-run is
    reported through the return value rather than raised — provision_namespace
    must never let a sub-step bypass :class:`ProvisionResult`.
    """
    if key == "hf_token":
        proc = _run(
            ["kubectl", "create", "secret", "generic", name,
             f"--namespace={namespace}",
             f"--from-literal=HF_TOKEN={value}",
             "--dry-run=client", "-o", "yaml"],
            check=False, capture=True,
        )
        if proc.returncode != 0:
            return None, f"kubectl create --dry-run failed: {(proc.stderr or proc.stdout).strip()}"
        return proc.stdout, None
    if key == "github_token":
        proc = _run(
            ["kubectl", "create", "secret", "generic", name,
             f"--namespace={namespace}",
             f"--from-literal=token={value}",
             "--dry-run=client", "-o", "yaml"],
            check=False, capture=True,
        )
        if proc.returncode != 0:
            return None, f"kubectl create --dry-run failed: {(proc.stderr or proc.stdout).strip()}"
        return proc.stdout, None
    if key in ("registry_creds", "dockerhub_creds"):
        if not isinstance(value, dict):
            return None, f"value is not a dict (expected {{server, user, token}}, got {type(value).__name__})"
        missing = [field for field in ("server", "user", "token") if not value.get(field)]
        if missing:
            return None, f"value is missing required fields: {', '.join(missing)}"
        proc = _run(
            ["kubectl", "create", "secret", "docker-registry", name,
             f"--namespace={namespace}",
             f"--docker-server={value['server']}",
             f"--docker-username={value['user']}",
             f"--docker-password={value['token']}",
             "--dry-run=client", "-o", "yaml"],
            check=False, capture=True,
        )
        if proc.returncode != 0:
            return None, f"kubectl create --dry-run failed: {(proc.stderr or proc.stdout).strip()}"
        return proc.stdout, None
    return None, f"unknown secret key '{key}' — no manifest builder"


def _step_pvc(
    namespace: str,
    cluster_config: dict,
    _secret_values: dict,
) -> tuple[str, str]:
    workspaces = cluster_config.get("workspaces") or {}
    if not workspaces:
        return ("skipped", "no workspaces configured")

    storage_class = cluster_config.get("storage_class") or ""
    sc_line = f"  storageClassName: {storage_class}\n" if storage_class else ""

    for ws_name, binding in workspaces.items():
        claim = (binding or {}).get("persistentVolumeClaim", {}).get("claimName")
        if not claim:
            return ("failed", f"workspace '{ws_name}' has no persistentVolumeClaim.claimName")
        # Already exists?
        exists = _run(
            ["kubectl", "get", "pvc", claim, f"-n={namespace}"],
            check=False, capture=True,
        ).returncode == 0
        if exists:
            info(f"    PVC {claim} already exists")
            continue
        manifest = (
            f"apiVersion: v1\nkind: PersistentVolumeClaim\n"
            f"metadata:\n  name: {claim}\n  namespace: {namespace}\n"
            f"spec:\n{sc_line}"
            f"  accessModes:\n    - {_DEFAULT_PVC_ACCESS_MODE}\n"
            f"  resources:\n    requests:\n      storage: {_DEFAULT_PVC_SIZE}\n"
        )
        info(f"    creating PVC {claim} ({_DEFAULT_PVC_SIZE}, {_DEFAULT_PVC_ACCESS_MODE})")
        proc = _run(
            ["kubectl", "apply", "-f", "-"],
            input=manifest, check=False, capture=True,
        )
        if proc.returncode != 0:
            return ("failed", f"PVC {claim}: {(proc.stderr or proc.stdout).strip()}")
    return ("ok", "applied")


def _step_tekton(
    namespace: str,
    _cluster_config: dict,
    _secret_values: dict,
) -> tuple[str, str]:
    # Apply step/task YAMLs from tektonc-data-collection. Pipeline definition
    # is cluster-wide and lives in apply_cluster_resources.
    applied = 0
    for subdir in ("steps", "tasks"):
        tekton_dir = _TEKTONC_DIR / "tekton" / subdir
        if not tekton_dir.exists():
            continue
        try:
            yaml_files = sorted(tekton_dir.glob("*.yaml"))
        except OSError as e:
            return ("failed", f"cannot list {tekton_dir}: {e}")
        if yaml_files:
            info(f"    applying {len(yaml_files)} {subdir}/*.yaml")
        for yaml_file in yaml_files:
            proc = _run(
                ["kubectl", "apply", "-f", str(yaml_file), f"-n={namespace}"],
                check=False, capture=True,
            )
            if proc.returncode != 0:
                return ("failed", f"{yaml_file.name}: {(proc.stderr or proc.stdout).strip()}")
            applied += 1
    if applied == 0:
        return ("skipped", "no tekton step/task YAMLs found")
    return ("ok", f"applied {applied} task/step YAML(s)")


def _envsubst(text: str, env: dict[str, str]) -> str:
    """Substitute ``$VAR`` and ``${VAR}`` from ``env`` in ``text``.

    Pure-Python replacement for the ``envsubst`` shell tool so this library
    doesn't depend on a system binary. Unset / missing keys are left as
    ``$VAR`` (matches ``envsubst`` default-without-flags behavior).
    """
    import re

    def _sub(match: re.Match) -> str:
        name = match.group(1) or match.group(2)
        return env.get(name, match.group(0))

    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)", _sub, text)


# ── apply_cluster_resources ──────────────────────────────────────────


def _resolve_pipeline_yaml(cluster_id: str) -> Path:
    """Return the Pipeline YAML path for ``cluster_id``.

    Uses ``cluster_config['pipeline_yaml']`` when set, else the default
    ``pipeline/pipeline.yaml`` bundled with the framework. Raises
    ``FileNotFoundError`` if the resolved path does not exist so callers
    fail loudly before invoking kubectl.
    """
    cfg = read_cluster_config(cluster_id)
    override = cfg.get("pipeline_yaml")
    pipeline_yaml = Path(override) if override else _PIPELINE_YAML
    if not pipeline_yaml.exists():
        raise FileNotFoundError(f"Pipeline YAML not found at {pipeline_yaml}")
    return pipeline_yaml


def apply_pipeline_to_namespace(cluster_id: str, namespace: str) -> None:
    """Apply the cluster's Tekton Pipeline definition to one namespace.

    Tekton ``Pipeline`` is a namespace-scoped resource in upstream Tekton,
    so applying the "cluster-wide" Pipeline means applying the same
    manifest to every namespace registered for this cluster. Callers that
    only add a single namespace to the pool (``cluster slot add``) use
    this helper directly instead of re-applying to every namespace.

    Idempotent: ``kubectl apply`` no-ops when the live object matches the
    supplied manifest. Raises ``subprocess.CalledProcessError`` on
    non-zero apply so the caller can surface the first failure.
    Raises ``FileNotFoundError`` if the resolved Pipeline YAML is missing.
    """
    pipeline_yaml = _resolve_pipeline_yaml(cluster_id)
    info(f"    applying {pipeline_yaml.name} to {namespace}")
    _run(
        ["kubectl", "apply", "-f", str(pipeline_yaml), f"-n={namespace}"],
        check=True, capture=True,
    )


def apply_cluster_resources(cluster_id: str) -> None:
    """Apply cluster-wide Tekton resources (the Pipeline definition).

    Reads ``cluster_config['namespaces']`` and applies the Tekton Pipeline
    manifest to each via :func:`apply_pipeline_to_namespace`. Tekton
    ``Pipeline`` is a namespaced resource in upstream Tekton, so
    "cluster-wide" here means "the same definition across every namespace
    registered for this cluster" — it is NOT part of the per-namespace
    flow because the source YAML is one cluster-level artifact, not a
    per-namespace one.

    The manifest path is ``cluster_config['pipeline_yaml']`` when set,
    otherwise the default ``pipeline/pipeline.yaml`` bundled with the
    framework. Operators pick the override at init time via
    ``cluster.py init <cluster_id> <primary> --pipeline-yaml PATH``
    (or ``cluster.py provision <cluster_id> --namespaces … --pipeline-yaml PATH``
    on a fresh cluster). Against an already-initialized cluster,
    ``--pipeline-yaml`` on the sugar path warns and is ignored — the
    override is fixed at init time.

    Idempotent: ``kubectl apply`` is a no-op when the live object matches
    the supplied manifest; subsequent invocations succeed silently.

    Raises ``FileNotFoundError`` if the resolved Pipeline YAML is missing.
    Per-namespace apply failures raise ``subprocess.CalledProcessError`` so
    the caller can surface the first failure rather than silently
    continuing.
    """
    pipeline_yaml = _resolve_pipeline_yaml(cluster_id)
    namespaces = read_cluster_config(cluster_id).get("namespaces") or []
    if namespaces:
        info(f"applying cluster-wide Pipeline ({pipeline_yaml.name}) "
             f"to {len(namespaces)} namespace(s)")
    for ns in namespaces:
        apply_pipeline_to_namespace(cluster_id, ns)
    if namespaces:
        ok(f"{pipeline_yaml.name} applied to {len(namespaces)} namespace(s)")

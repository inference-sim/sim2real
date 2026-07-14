#!/usr/bin/env python3
"""sim2real cluster bootstrap — first consumer of pipeline.lib.cluster_ops.

Subcommands:

* ``init`` — first-time bootstrap of a cluster: cluster identity,
  cluster-wide config, per-namespace resources for the primary namespace,
  and cluster-wide Tekton Pipeline application.
* ``slot add / remove / list`` — grow, shrink, or inspect the pool of
  namespaces the orchestrator dispatches to. ``slot add`` is idempotent:
  re-adding an existing namespace is a no-op at every layer. ``slot
  remove`` is drain-only; cluster-side resources (PVCs, secrets, Tekton
  tasks) stay so ``deploy.py collect`` continues to work and the slot can
  be re-added later without re-provisioning (issue #571).
* ``provision`` — backwards-compatible sugar over ``init`` + ``slot add``.
  Preserves the pre-#571 CLI so existing scripts and the
  ``sim2real-bootstrap`` skill do not break.

Every state-changing subcommand calls
:func:`cluster_ops.publish_slot_pool` after mutating
``cluster_config.json``. When a ``sim2real-run-inputs`` ConfigMap exists
in the primary namespace (i.e. a ``--remote`` orchestrator has been
assembled), the mutation is patched into the CM so the running
orchestrator picks it up via its live-cluster-config mount on the next
``_refresh_namespaces`` cycle. Otherwise the on-disk change is
sufficient and picked up on the next ``deploy.py run --remote``'s
assemble step.

Idempotent — safe to re-run every subcommand; pre-existing cluster-side
resources reconcile via ``kubectl apply``.
"""

from __future__ import annotations

import argparse
import getpass
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure repo root is on sys.path when run as a script (python pipeline/cluster.py)
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pipeline.lib import cluster_ops, layout  # noqa: E402 — must follow sys.path guard


# ── Argparse ──────────────────────────────────────────────────────────


def _add_credential_flags(p: argparse.ArgumentParser) -> None:
    """Attach the shared secret-plumbing flags to *p*.

    Used by ``init`` (primary namespace's secrets), ``slot add`` (new
    namespace's secrets), and ``provision`` (backwards-compat sugar). All
    three resolve values via flag > env > prompt through
    :func:`_resolve_secret_values`, which is why the flag surface must
    match exactly.
    """
    p.add_argument("--hf-token", metavar="TOKEN", default=None,
                   help="HuggingFace API token (env: HF_TOKEN; else prompt)")
    p.add_argument("--github-token", metavar="TOKEN", default=None,
                   help="GitHub token (env: GITHUB_TOKEN; optional)")
    p.add_argument("--registry-user", metavar="USER", default=None,
                   help="Registry username (env: REGISTRY_USER; else prompt)")
    p.add_argument("--registry-token", metavar="TOKEN", default=None,
                   help="Registry token (env: REGISTRY_TOKEN; else prompt)")
    p.add_argument("--dockerhub-user", metavar="USER", default=None,
                   help="Docker Hub username (env: DOCKERHUB_USER; optional)")
    p.add_argument("--dockerhub-token", metavar="TOKEN", default=None,
                   help="Docker Hub token (env: DOCKERHUB_TOKEN; optional)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipeline/cluster.py",
        description="Cluster-side bootstrap for the sim2real pipeline. "
                    "Idempotent — safe to re-run.",
    )
    parser.add_argument("--experiment-root", metavar="PATH", default=None,
                        help="Root of the experiment repo (default: cwd)")
    sub = parser.add_subparsers(dest="command", required=True)

    # ── cluster init ──────────────────────────────────────────────────
    init_p = sub.add_parser(
        "init",
        help="First-time bootstrap: cluster identity + cluster-wide "
             "config + primary namespace",
    )
    init_p.add_argument("cluster_id",
                        help="Cluster identifier (slug; matches workspace/clusters/<id>/)")
    init_p.add_argument("primary_namespace",
                        help="Primary namespace — pinned for the cluster's lifetime; "
                             "holds the run-inputs / progress ConfigMaps")
    init_p.add_argument("--storage-class", metavar="SC", default=None,
                        help="PVC storage class (empty → cluster default)")
    init_p.add_argument("--pipeline-yaml", metavar="PATH", default=None,
                        help="Path to Tekton Pipeline YAML to apply "
                             "(default: <repo-root>/pipeline/pipeline.yaml)")
    _add_credential_flags(init_p)

    # ── cluster slot {add,remove,list} ────────────────────────────────
    slot_p = sub.add_parser(
        "slot",
        help="Grow, shrink, or inspect the slot pool (add/remove/list)",
    )
    slot_sub = slot_p.add_subparsers(dest="slot_command", required=True)

    add_p = slot_sub.add_parser(
        "add",
        help="Provision a namespace and add it to the pool (idempotent)",
    )
    add_p.add_argument("cluster_id",
                       help="Cluster identifier (must already be initialized)")
    add_p.add_argument("namespace", help="Namespace to add to the slot pool")
    _add_credential_flags(add_p)

    rm_p = slot_sub.add_parser(
        "remove",
        help="Remove a namespace from the pool (drain-only; no cluster-side cleanup)",
    )
    rm_p.add_argument("cluster_id",
                      help="Cluster identifier (must already be initialized)")
    rm_p.add_argument("namespace",
                      help="Namespace to remove from the slot pool (primary refused)")

    ls_p = slot_sub.add_parser(
        "list",
        help="List the slot pool and per-slot provisioned state",
    )
    ls_p.add_argument("cluster_id", help="Cluster identifier")

    # ── cluster provision (backwards-compat sugar) ────────────────────
    pv = sub.add_parser(
        "provision",
        help="Backwards-compat sugar: equivalent to 'init <NS1>' + "
             "'slot add <NS2..>' for a fresh cluster, or 'slot add' per "
             "namespace for an existing one",
    )
    pv.add_argument("cluster_id",
                    help="Cluster identifier (slug; matches workspace/clusters/<id>/)")
    pv.add_argument("--namespaces", required=True, metavar="NS1,NS2,...",
                    help="Comma-separated namespaces to provision")
    pv.add_argument("--storage-class", metavar="SC", default=None,
                    help="PVC storage class (empty → cluster default). "
                         "Applied only when initializing a new cluster; "
                         "ignored (with warning) when the cluster already exists.")
    pv.add_argument("--pipeline-yaml", metavar="PATH", default=None,
                    help="Path to Tekton Pipeline YAML to apply "
                         "(default: <repo-root>/pipeline/pipeline.yaml). "
                         "Applied only when initializing a new cluster.")
    _add_credential_flags(pv)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    layout.set_experiment_root(args.experiment_root)

    if args.command == "init":
        return cmd_init(args)
    if args.command == "slot":
        if args.slot_command == "add":
            return cmd_slot_add(args)
        if args.slot_command == "remove":
            return cmd_slot_remove(args)
        if args.slot_command == "list":
            return cmd_slot_list(args)
        return 2  # unreachable: slot_command is required
    if args.command == "provision":
        return cmd_provision(args)
    return 2  # unreachable: subparser is required


# ── Hardcoded defaults (match today's setup.py behavior) ──────────────


_DEFAULT_SECRET_NAMES = {
    "hf_token": "hf-secret",
    "registry_creds": "registry-creds",
    "github_token": "github-token",
    "dockerhub_creds": "",  # filled in when --dockerhub-user/--dockerhub-token both provided
}

_DEFAULT_WORKSPACES = {
    "data-storage": {"persistentVolumeClaim": {"claimName": "data-pvc"}},
    "source":       {"persistentVolumeClaim": {"claimName": "source-pvc"}},
}


# ── Argument resolution helpers ──────────────────────────────────────


def _parse_namespaces(raw: str) -> list[str]:
    """Split ``--namespaces`` CSV into a non-empty list.

    Whitespace around each entry is stripped; empty / whitespace-only
    entries are dropped. Raises ``ValueError`` if the result is empty so the
    caller exits with a clean message rather than letting ``provision_namespace``
    iterate over an empty list.
    """
    items = [n.strip() for n in (raw or "").split(",") if n.strip()]
    if not items:
        raise ValueError("--namespaces resolved to an empty list")
    return items


def _build_cluster_config_dict(
    cluster_id: str,
    namespaces: list[str],
    *,
    is_openshift: bool,
    storage_class: str,
    has_dockerhub: bool,
    pipeline_yaml: str | None = None,
    existing: dict | None = None,
) -> dict:
    """Compose the cluster_config dict to be written to disk.

    Pure — no I/O. ``existing`` is the prior on-disk config (or empty)
    used only to preserve ``created_at``; everything else comes from the
    current command-line invocation.

    ``pipeline_yaml`` — optional override path for the Tekton Pipeline
    definition applied by :func:`cluster_ops.apply_cluster_resources`.
    ``None`` means "use the built-in default". The key is only written
    when set, so cluster_config.json stays minimal for the common case.
    """
    secret_names = dict(_DEFAULT_SECRET_NAMES)
    if has_dockerhub:
        secret_names["dockerhub_creds"] = "dockerhub-creds"
    cfg = {
        "cluster_id": cluster_id,
        "namespaces": list(namespaces),
        "is_openshift": bool(is_openshift),
        "storage_class": storage_class or "",
        "secret_names": secret_names,
        "workspaces": dict(_DEFAULT_WORKSPACES),
    }
    if pipeline_yaml:
        cfg["pipeline_yaml"] = pipeline_yaml
    prior_created = (existing or {}).get("created_at")
    if prior_created:
        cfg["created_at"] = prior_created
    return cfg


def _default_plain_prompter(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    raw = input(f"{label}{suffix}: ").strip()
    return raw or default


def _default_secret_prompter(label: str) -> str:
    return getpass.getpass(f"{label}: ").strip()


def _resolve_secret_values(
    args,
    *,
    env: dict[str, str],
    prompter,
    secret_prompter,
) -> tuple[dict, bool]:
    """Resolve the four secret payloads using flag > env > prompt.

    Returns ``(secret_values, has_dockerhub)``. The dict shape matches
    ``pipeline.lib.cluster_ops._build_secret_manifest`` expectations:

      * ``hf_token`` / ``github_token`` → ``str``
      * ``registry_creds`` / ``dockerhub_creds`` → ``{"server", "user", "token"}``

    Resolution rules:

    * Required secrets (``hf_token``, ``registry_creds``) prompt the
      operator when neither flag nor env var supplies a value.
    * Optional secrets (``github_token``, ``dockerhub_creds``) never
      prompt — when no flag/env is set, the key is omitted entirely so
      :func:`cluster_ops.provision_namespace` records a structured skip
      (or reuses a pre-installed Secret).
    * Registry/dockerhub ``server`` fields default to ``ghcr.io`` and
      ``docker.io`` respectively; overridable by ``REGISTRY_SERVER`` /
      ``DOCKERHUB_SERVER`` env vars. No flag, per design.
    """
    values: dict[str, object] = {}

    # hf_token — required; prompt if absent.
    hf = args.hf_token or env.get("HF_TOKEN") or secret_prompter("HuggingFace token")
    if hf:
        values["hf_token"] = hf

    # github_token — optional; never prompt.
    gh = args.github_token or env.get("GITHUB_TOKEN")
    if gh:
        values["github_token"] = gh

    # registry_creds — user + token required together; prompt if missing.
    reg_user = args.registry_user or env.get("REGISTRY_USER") \
        or prompter("Registry username")
    reg_token = args.registry_token or env.get("REGISTRY_TOKEN") \
        or (secret_prompter("Registry token") if reg_user else "")
    if reg_user and reg_token:
        values["registry_creds"] = {
            "server": env.get("REGISTRY_SERVER") or "ghcr.io",
            "user": reg_user,
            "token": reg_token,
        }

    # dockerhub_creds — optional; never prompt; both fields required.
    dh_user = args.dockerhub_user or env.get("DOCKERHUB_USER")
    dh_token = args.dockerhub_token or env.get("DOCKERHUB_TOKEN")
    has_dockerhub = bool(dh_user and dh_token)
    if has_dockerhub:
        values["dockerhub_creds"] = {
            "server": env.get("DOCKERHUB_SERVER") or "docker.io",
            "user": dh_user,
            "token": dh_token,
        }

    return values, has_dockerhub


def _format_summary_line(result) -> str:
    """One-line per-namespace summary for stdout printing.

    Failed items come first because they block the exit code (steps_skipped
    is soft divergence; steps_failed is the only thing that flips the exit
    to non-zero — operators scanning a multi-namespace summary care about
    the failures first).
    """
    if not result.diverged:
        return f"{result.namespace}: ok"
    parts: list[str] = []
    for step, reason in result.steps_failed:
        parts.append(f"failed={step}({reason})")
    for step, reason in result.steps_skipped:
        parts.append(f"skipped={step}({reason})")
    return f"{result.namespace}: diverged: " + ", ".join(parts)


# ── Command handlers ──────────────────────────────────────────────────


def _resolve_secrets_from_args(args) -> tuple[dict, bool]:
    """Wrap :func:`_resolve_secret_values` with the default prompters + env.

    Isolated so the CLI handlers stay short and so tests can monkeypatch
    just this seam instead of stubbing four prompters on every call.
    """
    return _resolve_secret_values(
        args,
        env=os.environ,
        prompter=_default_plain_prompter,
        secret_prompter=_default_secret_prompter,
    )


def cmd_init(args: argparse.Namespace) -> int:
    """First-time cluster bootstrap. Refuses if the cluster already exists."""
    cluster_id = args.cluster_id
    primary = args.primary_namespace

    existing = cluster_ops.read_cluster_config(cluster_id)
    if existing:
        print(
            f"error: cluster {cluster_id!r} already initialized "
            f"(namespaces={existing.get('namespaces') or []}); use "
            f"'cluster.py slot add {cluster_id} <namespace>' to grow the pool",
            file=sys.stderr,
        )
        return 1

    is_openshift = cluster_ops.detect_openshift()
    secret_values, has_dockerhub = _resolve_secrets_from_args(args)

    cluster_config = _build_cluster_config_dict(
        cluster_id, [primary],
        is_openshift=is_openshift,
        storage_class=args.storage_class or "",
        has_dockerhub=has_dockerhub,
        pipeline_yaml=args.pipeline_yaml,
        existing=None,
    )
    cluster_config["created_at"] = datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    # Persist BEFORE apply_cluster_resources (it reads cluster_config from disk).
    cluster_ops.write_cluster_config(cluster_id, cluster_config)

    try:
        cluster_ops.apply_cluster_resources(cluster_id)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        msg = (exc.stderr or exc.stdout or str(exc)).strip()
        print(f"error: cluster-wide apply failed: {msg}", file=sys.stderr)
        return 1

    result = cluster_ops.provision_namespace(
        primary, cluster_config, secret_values=secret_values,
    )
    print(_format_summary_line(result))

    # Publish is almost always a no-op skip at init time (no CM yet), but
    # covers the edge case where the operator re-runs init after a
    # prior 'deploy.py run --remote' left a dangling ConfigMap.
    cluster_ops.publish_slot_pool(cluster_id)

    return 1 if result.steps_failed else 0


def cmd_slot_add(args: argparse.Namespace) -> int:
    """Provision a namespace and add it to the pool. Idempotent."""
    cluster_id = args.cluster_id
    namespace = args.namespace

    cluster_config = cluster_ops.read_cluster_config(cluster_id)
    if not cluster_config:
        print(
            f"error: cluster {cluster_id!r} not initialized; run "
            f"'cluster.py init {cluster_id} <primary-namespace>' first",
            file=sys.stderr,
        )
        return 1

    secret_values, _ = _resolve_secrets_from_args(args)

    result = cluster_ops.provision_namespace(
        namespace, cluster_config, secret_values=secret_values,
    )
    print(_format_summary_line(result))
    if result.steps_failed:
        # Do not append a namespace to the pool when its provisioning
        # diverged in a way that flips the exit code. Publish would just
        # advertise a slot that isn't usable.
        return 1

    current = list(cluster_config.get("namespaces") or [])
    if namespace not in current:
        current.append(namespace)
        cluster_ops.update_cluster_config(cluster_id, namespaces=current)

    # Apply the Pipeline definition to the (possibly newly-provisioned)
    # namespace. Idempotent kubectl apply — no-op if the Pipeline is
    # already there. Uses the per-namespace helper so we don't re-apply
    # to every namespace in the pool.
    try:
        cluster_ops.apply_pipeline_to_namespace(cluster_id, namespace)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        msg = (exc.stderr or exc.stdout or str(exc)).strip()
        print(f"error: pipeline apply failed for {namespace}: {msg}", file=sys.stderr)
        return 1

    cluster_ops.publish_slot_pool(cluster_id)
    return 0


def cmd_slot_remove(args: argparse.Namespace) -> int:
    """Remove a namespace from the pool. Drain-only; no cluster-side changes."""
    cluster_id = args.cluster_id
    namespace = args.namespace

    cluster_config = cluster_ops.read_cluster_config(cluster_id)
    if not cluster_config:
        print(
            f"error: cluster {cluster_id!r} not initialized; nothing to remove",
            file=sys.stderr,
        )
        return 1

    namespaces = list(cluster_config.get("namespaces") or [])
    if not namespaces:
        print(
            f"error: cluster {cluster_id!r} has no namespaces in the pool",
            file=sys.stderr,
        )
        return 1
    if namespace == namespaces[0]:
        print(
            f"error: cannot remove primary namespace {namespace!r}; "
            f"primary is pinned for the cluster's lifetime. To repurpose the "
            f"cluster, initialize a new one under a different cluster_id.",
            file=sys.stderr,
        )
        return 1
    if namespace not in namespaces:
        print(
            f"error: namespace {namespace!r} not in pool for cluster "
            f"{cluster_id!r} (current pool: {namespaces})",
            file=sys.stderr,
        )
        return 1

    new_namespaces = [ns for ns in namespaces if ns != namespace]
    cluster_ops.update_cluster_config(cluster_id, namespaces=new_namespaces)
    print(f"{namespace}: removed from pool (cluster-side resources preserved)")

    cluster_ops.publish_slot_pool(cluster_id)
    return 0


def cmd_slot_list(args: argparse.Namespace) -> int:
    """List slot pool with per-slot provisioned probe. Read-only."""
    cluster_id = args.cluster_id
    cluster_config = cluster_ops.read_cluster_config(cluster_id)
    if not cluster_config:
        print(
            f"error: cluster {cluster_id!r} not initialized",
            file=sys.stderr,
        )
        return 1

    namespaces = list(cluster_config.get("namespaces") or [])
    if not namespaces:
        print(f"cluster {cluster_id!r} has no namespaces in the pool")
        return 0

    print(f"cluster {cluster_id!r} pool ({len(namespaces)} namespace(s)):")
    for i, ns in enumerate(namespaces):
        marker = " (primary)" if i == 0 else ""
        provisioned = cluster_ops.namespace_provisioned(ns)
        state = "provisioned" if provisioned else "not provisioned"
        print(f"  {ns}{marker}: {state}")
    return 0


def cmd_provision(args: argparse.Namespace) -> int:
    """Backwards-compat sugar over init + slot add.

    Behavior branches on whether ``cluster_config.json`` already exists:

    * **Fresh cluster** (no existing config): ``init`` with
      ``--namespaces[0]`` as primary, then ``slot add`` for each of
      ``--namespaces[1:]``. ``--storage-class`` / ``--pipeline-yaml``
      apply to the init call.

    * **Existing cluster**: ``slot add`` for each of ``--namespaces``
      that is not already in the pool. ``--storage-class`` /
      ``--pipeline-yaml`` are ignored (with a warn) because those are
      fixed at init time.

    Preserves exact behavior of pre-#571 ``cluster.py provision`` for
    both first-run and re-run invocations.
    """
    cluster_id = args.cluster_id
    try:
        namespaces = _parse_namespaces(args.namespaces)
    except ValueError as exc:
        print(f"error: --namespaces: {exc}", file=sys.stderr)
        return 2

    existing = cluster_ops.read_cluster_config(cluster_id)

    if not existing:
        # Fresh cluster — init on the first namespace, then slot-add the rest.
        primary = namespaces[0]
        init_args = argparse.Namespace(
            cluster_id=cluster_id,
            primary_namespace=primary,
            storage_class=args.storage_class,
            pipeline_yaml=args.pipeline_yaml,
            hf_token=args.hf_token,
            github_token=args.github_token,
            registry_user=args.registry_user,
            registry_token=args.registry_token,
            dockerhub_user=args.dockerhub_user,
            dockerhub_token=args.dockerhub_token,
            experiment_root=getattr(args, "experiment_root", None),
        )
        rc = cmd_init(init_args)
        if rc != 0:
            return rc
        remaining = namespaces[1:]
    else:
        if args.storage_class is not None or args.pipeline_yaml is not None:
            print(
                f"warn: cluster {cluster_id!r} already initialized; "
                f"ignoring --storage-class / --pipeline-yaml "
                f"(cluster-wide config is fixed at init time)",
                file=sys.stderr,
            )
        remaining = namespaces

    any_failed = False
    for ns in remaining:
        slot_args = argparse.Namespace(
            cluster_id=cluster_id,
            namespace=ns,
            hf_token=args.hf_token,
            github_token=args.github_token,
            registry_user=args.registry_user,
            registry_token=args.registry_token,
            dockerhub_user=args.dockerhub_user,
            dockerhub_token=args.dockerhub_token,
            experiment_root=getattr(args, "experiment_root", None),
        )
        rc = cmd_slot_add(slot_args)
        if rc != 0:
            any_failed = True

    return 1 if any_failed else 0


if __name__ == "__main__":
    sys.exit(main())

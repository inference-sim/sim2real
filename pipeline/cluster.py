#!/usr/bin/env python3
"""sim2real cluster bootstrap — first consumer of pipeline.lib.cluster_ops.

The ``provision`` subcommand replaces today's cluster-side responsibilities
in ``pipeline/setup.py``: namespace creation, RBAC, Secrets, PVCs, and
Tekton task bindings, plus the cluster-wide Pipeline definition. Writes
``workspace/clusters/<cluster_id>/cluster_config.json`` for downstream
commands to read.

Idempotent — safe to re-run; pre-existing resources reconcile via
``kubectl apply``.
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipeline/cluster.py",
        description="Cluster-side bootstrap for the sim2real pipeline. "
                    "Idempotent — safe to re-run.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    pv = sub.add_parser("provision", help="Provision cluster-side resources")
    pv.add_argument("cluster_id", help="Cluster identifier (slug; matches workspace/clusters/<id>/)")
    pv.add_argument("--namespaces", required=True, metavar="NS1,NS2,...",
                    help="Comma-separated namespaces to provision")
    pv.add_argument("--storage-class", metavar="SC", default=None,
                    help="PVC storage class (empty → cluster default)")
    pv.add_argument("--hf-token", metavar="TOKEN", default=None,
                    help="HuggingFace API token (env: HF_TOKEN; else prompt)")
    pv.add_argument("--github-token", metavar="TOKEN", default=None,
                    help="GitHub token (env: GITHUB_TOKEN; optional)")
    pv.add_argument("--registry-user", metavar="USER", default=None,
                    help="Registry username (env: REGISTRY_USER; else prompt)")
    pv.add_argument("--registry-token", metavar="TOKEN", default=None,
                    help="Registry token (env: REGISTRY_TOKEN; else prompt)")
    pv.add_argument("--dockerhub-user", metavar="USER", default=None,
                    help="Docker Hub username (env: DOCKERHUB_USER; optional)")
    pv.add_argument("--dockerhub-token", metavar="TOKEN", default=None,
                    help="Docker Hub token (env: DOCKERHUB_TOKEN; optional)")
    pv.add_argument("--experiment-root", metavar="PATH", default=None,
                    help="Root of the experiment repo (default: cwd)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
    existing: dict | None = None,
) -> dict:
    """Compose the cluster_config dict to be written to disk.

    Pure — no I/O. ``existing`` is the prior on-disk config (or empty)
    used only to preserve ``created_at``; everything else comes from the
    current command-line invocation.
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


def cmd_provision(args: argparse.Namespace) -> int:
    layout.set_experiment_root(args.experiment_root)
    cluster_id = args.cluster_id

    # 1. Parse namespaces — fail fast on empty.
    try:
        namespaces = _parse_namespaces(args.namespaces)
    except ValueError as exc:
        print(f"error: --namespaces: {exc}", file=sys.stderr)
        return 2

    # 2. Existing config (for created_at preservation).
    existing = cluster_ops.read_cluster_config(cluster_id)

    # 3. Detect OpenShift once.
    is_openshift = cluster_ops.detect_openshift()

    # 4. Resolve secret values from flag > env > prompt.
    secret_values, has_dockerhub = _resolve_secret_values(
        args,
        env=os.environ,
        prompter=_default_plain_prompter,
        secret_prompter=_default_secret_prompter,
    )

    # 5. Build cluster_config; stamp created_at if first-time write.
    cluster_config = _build_cluster_config_dict(
        cluster_id, namespaces,
        is_openshift=is_openshift,
        storage_class=args.storage_class or "",
        has_dockerhub=has_dockerhub,
        existing=existing,
    )
    if "created_at" not in cluster_config:
        cluster_config["created_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 6. Persist BEFORE apply_cluster_resources (it reads cluster_config from disk).
    cluster_ops.write_cluster_config(cluster_id, cluster_config)

    # 7. Cluster-wide resources.
    try:
        cluster_ops.apply_cluster_resources(cluster_id)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        msg = (exc.stderr or exc.stdout or str(exc)).strip()
        print(f"error: cluster-wide apply failed: {msg}", file=sys.stderr)
        return 1

    # 8. Provision each namespace; print one summary line each.
    any_failed = False
    for ns in namespaces:
        result = cluster_ops.provision_namespace(
            ns, cluster_config, secret_values=secret_values,
        )
        print(_format_summary_line(result))
        if result.steps_failed:
            any_failed = True

    return 1 if any_failed else 0


if __name__ == "__main__":
    sys.exit(main())

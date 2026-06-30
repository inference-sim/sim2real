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
import sys


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
                    help="GitHub token (env: GITHUB_TOKEN; else prompt)")
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


def cmd_provision(args: argparse.Namespace) -> int:
    raise NotImplementedError("filled in by subsequent tasks")


if __name__ == "__main__":
    sys.exit(main())

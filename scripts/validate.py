#!/usr/bin/env python3
"""sim2real validate — pre-deploy, post-deploy, and post-collection validation."""

from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

_tty = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _tty else text


def _print_report(report) -> None:
    """Print human-readable report to stdout."""
    print(f"\n{'━'*60}")
    print(f" Validation: {report.phase}  run={report.run}")
    print(f"{'━'*60}")
    for group_name, group in report.checks.items():
        status = _c("32", "[PASS]") if group.passed else _c("31", "[FAIL]")
        print(f"  {status} {group_name}")
        for item in group.items:
            if item.passed:
                continue
            icon = _c("33", "[WARN]") if item.severity == "warn" else _c("31", "[FAIL]")
            for note in item.notes:
                print(f"         {icon} {item.name}: {note}")
    overall_color = "32" if report.overall == "PASS" else "31"
    print(f"\n  Overall: {_c(overall_color, report.overall)}\n")


def _cmd_pre_deploy(args: argparse.Namespace) -> int:
    from lib.validate_checks import run_pre_deploy_checks
    run_dir = Path(args.run_dir)
    repo_root = Path(args.repo_root) if args.repo_root else None
    try:
        report = run_pre_deploy_checks(run_dir, repo_root=repo_root)
    except FileNotFoundError as e:
        print(_c("31", f"[ERROR] {e}"), file=sys.stderr)
        return 2
    except Exception as e:
        print(_c("31", f"[ERROR] Unexpected error: {e}"), file=sys.stderr)
        return 2

    out_path = run_dir / "validate_pre_deploy.json"
    out_path.write_text(json.dumps(report.to_dict(), indent=2))
    _print_report(report)
    print(f"  Report: {out_path}")
    return 1 if report.failed else 0


def _cmd_post_deploy(args: argparse.Namespace) -> int:
    from lib.validate_checks import run_post_deploy_checks
    run_dir = Path(args.run_dir)
    try:
        report = run_post_deploy_checks(
            run_dir, namespace=args.namespace, phase=args.phase,
            prometheus_url=args.prometheus_url,
        )
    except FileNotFoundError as e:
        print(_c("31", f"[ERROR] {e}"), file=sys.stderr)
        return 2
    except RuntimeError as e:
        print(_c("31", f"[ERROR] {e}"), file=sys.stderr)
        return 2
    except Exception as e:
        print(_c("31", f"[ERROR] Unexpected error: {e}"), file=sys.stderr)
        return 2

    out_path = run_dir / f"validate_post_deploy_{args.phase}.json"
    out_path.write_text(json.dumps(report.to_dict(), indent=2))
    _print_report(report)
    print(f"  Report: {out_path}")
    return 1 if report.failed else 0


def _cmd_post_collection(args: argparse.Namespace) -> int:
    from lib.validate_checks import run_post_collection_checks
    run_dir = Path(args.run_dir)
    try:
        report = run_post_collection_checks(phase=args.phase, run_dir=run_dir)
    except FileNotFoundError as e:
        print(_c("31", f"[ERROR] {e}"), file=sys.stderr)
        return 2
    except Exception as e:
        print(_c("31", f"[ERROR] Unexpected error: {e}"), file=sys.stderr)
        return 2

    out_path = run_dir / f"validate_post_collection_{args.phase}.json"
    out_path.write_text(json.dumps(report.to_dict(), indent=2))
    _print_report(report)
    print(f"  Report: {out_path}")
    return 1 if report.failed else 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="validate.py",
        description="sim2real run validation — pre-deploy, post-deploy, post-collection",
    )
    sub = p.add_subparsers(dest="subcommand", required=True)

    # ── pre-deploy ──
    pre = sub.add_parser("pre-deploy", help="Static artifact checks before deployment")
    pre.add_argument("--run-dir", required=True, metavar="PATH",
                     help="workspace/runs/{name}/ directory")
    pre.add_argument("--repo-root", metavar="PATH",
                     help="Repo root (default: inferred from script location)")

    # ── post-deploy ──
    post = sub.add_parser("post-deploy", help="Live cluster probes against a running stack")
    post.add_argument("--run-dir", required=True, metavar="PATH")
    post.add_argument("--namespace", required=True, metavar="NS")
    post.add_argument("--phase", required=True, choices=["baseline", "treatment"])
    post.add_argument("--prometheus-url", metavar="URL",
                      help="Prometheus base URL (default: auto-resolve from cluster)")

    # ── post-collection ──
    col = sub.add_parser("post-collection", help="Trace CSV audit after benchmark completes")
    col.add_argument("--run-dir", required=True, metavar="PATH")
    col.add_argument("--phase", required=True, choices=["baseline", "treatment"])

    return p


def main_with_args(argv: list[str] | None = None) -> int:
    p = _build_parser()
    args = p.parse_args(argv)
    dispatch = {
        "pre-deploy":      _cmd_pre_deploy,
        "post-deploy":     _cmd_post_deploy,
        "post-collection": _cmd_post_collection,
    }
    return dispatch[args.subcommand](args)


def main() -> int:
    return main_with_args()


if __name__ == "__main__":
    sys.exit(main())

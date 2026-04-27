#!/usr/bin/env python3
"""sim2real deploy monitor — watches active slots and diagnoses pod failures."""
from __future__ import annotations

import argparse
import datetime
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    import anthropic
except ImportError:
    anthropic = None  # type: ignore[assignment]

from pipeline.lib.health import (
    RemediationTracker, get_pods, get_events, get_pod_logs,
    delete_pod, triage_pod, _kubectl,
)
from pipeline.lib.progress import LocalProgressStore

# ── Color helpers (mirrors deploy.py) ────────────────────────────────────────
_tty = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _tty else text


def info(msg: str)  -> None: print(_c("34", "[INFO]  ") + msg)
def ok(msg: str)    -> None: print(_c("32", "[OK]    ") + msg)
def warn(msg: str)  -> None: print(_c("33", "[WARN]  ") + msg)
def err(msg: str)   -> None: print(_c("31", "[ERROR] ") + msg, file=sys.stderr)


# ── Health report ─────────────────────────────────────────────────────────────
@dataclass
class _Finding:
    timestamp: str
    namespace: str
    pair_key: str
    pod_name: str
    signal: str
    action_taken: str
    diagnosis: str
    suggestion: str
    tier: int


class HealthReport:
    """Manages health_report.md. Regenerated on every write; prior session
    content preserved as an opaque block."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._findings: list[_Finding] = []
        self._prior_text = ""
        if self._path.exists():
            raw = self._path.read_text()
            sep = "\n---\n\n## Prior session findings\n\n"
            self._prior_text = raw.split(sep)[0] if sep in raw else raw

    def add_finding(
        self,
        timestamp: str,
        namespace: str,
        pair_key: str,
        pod_name: str,
        signal: str,
        action_taken: str,
        diagnosis: str,
        suggestion: str,
        tier: int,
    ) -> None:
        self._findings.append(_Finding(
            timestamp=timestamp, namespace=namespace, pair_key=pair_key,
            pod_name=pod_name, signal=signal, action_taken=action_taken,
            diagnosis=diagnosis, suggestion=suggestion, tier=tier,
        ))
        self._write()

    def _write(self) -> None:
        n = len(self._findings)
        tier_counts: dict[int, int] = {}
        for f in self._findings:
            tier_counts[f.tier] = tier_counts.get(f.tier, 0) + 1
        summary_parts = " | ".join(
            f"tier-{t}: {c}" for t, c in sorted(tier_counts.items())
        )
        lines = [
            "# Deploy Monitor Health Report\n\n",
            f"**{n} finding{'s' if n != 1 else ''} this session**",
            f" — {summary_parts}\n\n---\n",
        ]
        for f in self._findings:
            lines.append(f"\n## {f.timestamp}  {f.namespace} / {f.pair_key}\n")
            lines.append(f"\n**Signal:** {f.signal}")
            lines.append(f"\n**Pod:** {f.pod_name}")
            lines.append(f"\n**Action taken:** {f.action_taken}")
            if f.diagnosis:
                lines.append(f"\n\n**Diagnosis (Claude):**\n{f.diagnosis}")
            if f.suggestion:
                lines.append(f"\n\n**Suggested fix:**\n  {f.suggestion}")
            lines.append("\n")
        if self._prior_text:
            lines.append("\n---\n\n## Prior session findings\n\n")
            lines.append(self._prior_text)
        self._path.write_text("".join(lines))


# ── Slot discovery ────────────────────────────────────────────────────────────

def _resolve_active_slots(progress: dict) -> dict[str, list[str]]:
    """Return {namespace: [pair_key, ...]} for all running pairs."""
    slots: dict[str, list[str]] = {}
    for key, entry in progress.items():
        if entry.get("status") == "running" and entry.get("namespace"):
            ns = entry["namespace"]
            slots.setdefault(ns, []).append(key)
    return slots


def _work_remaining(progress: dict) -> bool:
    return any(v.get("status") == "running" for v in progress.values())


# ── Setup config ──────────────────────────────────────────────────────────────

def _load_setup_config(experiment_root: Path) -> dict:
    for p in [
        experiment_root / "workspace" / "setup_config.json",
        _REPO_ROOT / "workspace" / "setup_config.json",
    ]:
        if p.exists():
            return json.loads(p.read_text())
    return {}


# ── One poll cycle ────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _poll_once(
    progress: dict,
    experiment_id: str,
    tracker: RemediationTracker,
    report: HealthReport,
    log_lines: int,
) -> None:
    """Run one health-check pass over all active slots."""
    slots = _resolve_active_slots(progress)
    for ns, pair_keys in slots.items():
        events = get_events(ns)
        pods = get_pods(ns, experiment_id)
        for pod in pods:
            result = triage_pod(pod, events, tracker)
            if result is None:
                if pod.ready:
                    tracker.reset(pod.name)
                continue

            ts = _now()
            # Each running pair gets its own namespace in sim2real; pair_keys[0]
            # is the correct label unless the user deliberately shares a namespace.
            pair_label = pair_keys[0]
            action_taken = "none"

            if result.tier == 1:
                if result.action == "delete_pod":
                    tracker.record(pod.name)
                    success = delete_pod(ns, pod.name)
                    action_taken = "deleted pod" if success else "delete failed"
                warn(f"{ns} / {pair_label}: {result.message}")
                report.add_finding(
                    timestamp=ts, namespace=ns, pair_key=pair_label,
                    pod_name=pod.name, signal=result.message,
                    action_taken=action_taken,
                    diagnosis="", suggestion="", tier=1,
                )

            elif result.tier == 2:
                warn(f"{ns} / {pair_label}: {result.message}")
                report.add_finding(
                    timestamp=ts, namespace=ns, pair_key=pair_label,
                    pod_name=pod.name, signal=result.message,
                    action_taken=action_taken,
                    diagnosis="", suggestion=result.suggestion, tier=2,
                )

            elif result.tier == 3:
                err(f"{ns} / {pair_label}: {result.message}")
                logs = ""
                if result.needs_logs:
                    logs = get_pod_logs(ns, pod.name, tail=log_lines)
                    if pod.restart_count > 0:
                        prev = get_pod_logs(ns, pod.name, tail=100, previous=True)
                        if prev:
                            logs = (f"=== previous container ===\n{prev}\n"
                                    f"=== current ===\n{logs}")
                _, describe_out = _kubectl("describe", "pod", pod.name, f"-n={ns}")
                events_summary = "\n".join(
                    f"{e.reason}: {e.message}"
                    for e in events if e.involved_object == pod.name
                )
                diagnosis = _diagnose_with_api(
                    pod_name=pod.name, namespace=ns, signal=result.message,
                    describe_output=describe_out, logs=logs,
                    events_summary=events_summary, log_lines=log_lines,
                )
                report.add_finding(
                    timestamp=ts, namespace=ns, pair_key=pair_label,
                    pod_name=pod.name, signal=result.message,
                    action_taken=action_taken,
                    diagnosis=diagnosis, suggestion=result.suggestion, tier=3,
                )
                info(f"{ns}: diagnosis written to {report._path.name}")


# ── Anthropic API diagnosis (stub — implemented in Task 7) ───────────────────

def _diagnose_with_api(
    pod_name: str,
    namespace: str,
    signal: str,
    describe_output: str,
    logs: str,
    events_summary: str,
    log_lines: int = 200,
) -> str:
    return "(API diagnosis not yet configured)"


# ── CLI ───────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="monitor.py",
        description="sim2real deploy monitor — watches active slots for pod failures",
    )
    p.add_argument("--experiment-root", metavar="PATH", dest="experiment_root",
                   help="Root of the experiment repo (default: cwd)")
    p.add_argument("--run", metavar="NAME",
                   help="Run name (default: current_run from setup_config.json)")
    p.add_argument("--interval", type=int, default=30, metavar="SECONDS",
                   help="Poll interval in seconds [30]")
    p.add_argument("--log-lines", type=int, default=200, dest="log_lines",
                   help="Tail depth for pod logs sent to API [200]")
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    print(_c("36", "\n━━━ sim2real-monitor ━━━\n"))

    experiment_root = (Path(args.experiment_root).resolve()
                       if args.experiment_root else Path.cwd())
    setup_config = _load_setup_config(experiment_root)
    run_name = args.run or setup_config.get("current_run", "")
    if not run_name:
        err("No run name. Use --run NAME or set current_run in setup_config.json.")
        sys.exit(1)

    run_dir = experiment_root / "workspace" / "runs" / run_name
    if not run_dir.exists():
        err(f"Run directory not found: {run_dir}")
        sys.exit(1)

    report = HealthReport(run_dir / "health_report.md")
    tracker = RemediationTracker()
    store = LocalProgressStore(run_dir / "progress.json")

    info(f"Monitoring run '{run_name}' (interval: {args.interval}s)")
    info(f"Report: {run_dir}/health_report.md")

    while True:
        progress = store.load()
        if not _work_remaining(progress):
            info("No active pairs remaining — exiting.")
            break
        _poll_once(progress, run_name, tracker, report, args.log_lines)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()

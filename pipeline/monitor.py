#!/usr/bin/env python3
"""sim2real deploy monitor — watches active slots and diagnoses pod failures."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    import anthropic
except ImportError:
    anthropic = None  # type: ignore[assignment]

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
            self._prior_text = self._path.read_text()

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

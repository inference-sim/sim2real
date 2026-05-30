"""Shadow GPU reservation ledger for deploy.py orchestrator."""
from __future__ import annotations

import time


class ShadowLedger:
    """Tracks GPU reservations not yet reflected in the cluster probe.

    Each record is a (gpu_cost, timestamp) tuple. Entries older than TTL
    are pruned on every read. With ttl=0, tracking is disabled.
    """

    def __init__(self, ttl: int) -> None:
        self._ttl = ttl
        self._entries: list[tuple[int, float]] = []

    def record(self, gpu_cost: int) -> None:
        if self._ttl <= 0:
            return
        self._entries.append((gpu_cost, time.time()))

    def reserved(self) -> int:
        if self._ttl <= 0:
            return 0
        self._prune()
        return sum(cost for cost, _ in self._entries)

    def effective_free(self, probed_free: int) -> int:
        return max(0, probed_free - self.reserved())

    def _prune(self) -> None:
        cutoff = time.time() - self._ttl
        self._entries = [(c, t) for c, t in self._entries if t > cutoff]

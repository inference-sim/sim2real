"""Exponential backoff controller for the deploy orchestrator."""
from __future__ import annotations

import datetime as _dt


class BackoffController:
    """Manages poll-interval backoff during sustained GPU scarcity."""

    def __init__(self, base_interval: int, max_backoff: int) -> None:
        self._base = base_interval
        self._max = max_backoff
        self.state: str = "normal"
        self.backoff_level: int = 0
        self.last_scarcity_time: str | None = None
        self.last_probe_free_gpus: int | None = None

    @property
    def effective_interval(self) -> int:
        if self.state == "normal":
            return self._base
        raw = self._base * (2 ** self.backoff_level)
        return min(raw, self._max)

    def signal_scarcity(self, *, free_gpus: int, min_cost: int) -> None:
        if free_gpus >= min_cost:
            return
        self.state = "backing_off"
        self.backoff_level += 1
        raw = self._base * (2 ** self.backoff_level)
        if raw > self._max:
            self.backoff_level = self._level_for_max()
        self.last_scarcity_time = _dt.datetime.now(_dt.timezone.utc).isoformat()
        self.last_probe_free_gpus = free_gpus

    def signal_capacity(self, *, free_gpus: int, max_cost: int) -> None:
        if free_gpus >= max_cost:
            self._reset()
        self.last_probe_free_gpus = free_gpus

    def signal_scheduling_success(self) -> None:
        self._reset()

    def should_dispatch(self, *, free_gpus: int, min_cost: int) -> bool:
        if self.state == "normal":
            return True
        return free_gpus >= min_cost

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "backoff_level": self.backoff_level,
            "last_scarcity_time": self.last_scarcity_time,
            "last_probe_free_gpus": self.last_probe_free_gpus,
        }

    @classmethod
    def from_dict(cls, data: dict, *, base_interval: int, max_backoff: int) -> BackoffController:
        bc = cls(base_interval=base_interval, max_backoff=max_backoff)
        bc.state = data.get("state", "normal")
        bc.backoff_level = data.get("backoff_level", 0)
        bc.last_scarcity_time = data.get("last_scarcity_time")
        bc.last_probe_free_gpus = data.get("last_probe_free_gpus")
        return bc

    def _reset(self) -> None:
        self.state = "normal"
        self.backoff_level = 0

    def _level_for_max(self) -> int:
        """Return the smallest level where effective_interval == max_backoff."""
        level = 0
        while self._base * (2 ** level) < self._max:
            level += 1
        return level

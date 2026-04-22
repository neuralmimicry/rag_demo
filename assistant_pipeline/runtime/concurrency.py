"""Owner-aware concurrency primitives for multi-user Refiner execution."""

from __future__ import annotations

import threading
import time
from typing import Dict


class OwnerAwareCapacityLimiter:
    """Bound concurrent execution globally and per owner.

    The limiter keeps a hard global ceiling while also reserving capacity for
    other active users by capping how many slots one owner may hold at once.
    """

    def __init__(self, max_concurrent: int, *, max_concurrent_per_owner: int):
        self.max_concurrent = max(1, int(max_concurrent))
        self.max_concurrent_per_owner = max(1, min(int(max_concurrent_per_owner), self.max_concurrent))
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._inflight_total = 0
        self._inflight_by_owner: Dict[str, int] = {}

    def _owner_key(self, owner: str) -> str:
        cleaned = str(owner or "").strip()
        return cleaned or "__anonymous__"

    def acquire_for(self, owner: str, timeout: float = 0.0) -> bool:
        owner_key = self._owner_key(owner)
        timeout = max(0.0, float(timeout or 0.0))
        deadline = time.monotonic() + timeout if timeout > 0 else None
        with self._condition:
            while True:
                owner_inflight = int(self._inflight_by_owner.get(owner_key) or 0)
                if self._inflight_total < self.max_concurrent and owner_inflight < self.max_concurrent_per_owner:
                    self._inflight_total += 1
                    self._inflight_by_owner[owner_key] = owner_inflight + 1
                    return True
                if deadline is None:
                    return False
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(timeout=remaining)

    def release_for(self, owner: str) -> None:
        owner_key = self._owner_key(owner)
        with self._condition:
            owner_inflight = int(self._inflight_by_owner.get(owner_key) or 0)
            if owner_inflight <= 1:
                self._inflight_by_owner.pop(owner_key, None)
            else:
                self._inflight_by_owner[owner_key] = owner_inflight - 1
            if self._inflight_total > 0:
                self._inflight_total -= 1
            self._condition.notify_all()

    def acquire(self, blocking: bool = True, timeout: float | None = None) -> bool:
        """Compatibility adapter for legacy semaphore-style callers."""

        if not blocking:
            return self.acquire_for("__anonymous__", timeout=0.0)
        return self.acquire_for("__anonymous__", timeout=0.0 if timeout is None else float(timeout))

    def release(self) -> None:
        """Compatibility adapter for legacy semaphore-style callers."""

        self.release_for("__anonymous__")

    def snapshot(self) -> Dict[str, int]:
        with self._lock:
            return {
                "max_concurrent": self.max_concurrent,
                "max_concurrent_per_owner": self.max_concurrent_per_owner,
                "inflight_total": self._inflight_total,
                "active_owner_count": len(self._inflight_by_owner),
            }

from __future__ import annotations

import time


class RateLimiter:
    def __init__(
        self,
        requests_per_minute: int = 60,
        max_concurrent: int = 5,
    ) -> None:
        self._requests_per_minute = requests_per_minute
        self._max_concurrent = max_concurrent
        self._requests: dict[str, list[float]] = {}
        self._concurrent_count = 0

    def allow(self, client_id: str) -> bool:
        self._cleanup(client_id)

        if len(self._requests.get(client_id, [])) >= self._requests_per_minute:
            return False

        if self._concurrent_count >= self._max_concurrent:
            return False

        self._requests.setdefault(client_id, []).append(time.time())
        return True

    def increment_concurrent(self) -> None:
        self._concurrent_count += 1

    def decrement_concurrent(self) -> None:
        self._concurrent_count = max(0, self._concurrent_count - 1)

    def _cleanup(self, client_id: str) -> None:
        timestamps = self._requests.get(client_id)
        if not timestamps:
            return
        threshold = time.time() - 60
        self._requests[client_id] = [ts for ts in timestamps if ts > threshold]

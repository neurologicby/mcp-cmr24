"""Small in-process metrics registry with Prometheus text export."""

from __future__ import annotations

import threading
from collections import defaultdict


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = (
            defaultdict(float)
        )

    def inc(self, name: str, value: float = 1, **labels: str) -> None:
        key = (name, tuple(sorted((k, str(v)) for k, v in labels.items())))
        with self._lock:
            self._counters[key] += value

    def snapshot(self) -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
        with self._lock:
            return dict(self._counters)

    def prometheus(self) -> str:
        lines: list[str] = []
        for (name, labels), value in sorted(self.snapshot().items()):
            suffix = ""
            if labels:
                suffix = "{" + ",".join(f'{key}="{val}"' for key, val in labels) + "}"
            lines.append(f"cmr24_{name}{suffix} {value:g}")
        return "\n".join(lines) + "\n"


metrics = Metrics()

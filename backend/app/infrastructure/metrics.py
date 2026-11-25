"""In-process counters for the local Observability page."""

from collections import Counter


class Metrics:
    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()
        self.latencies_ms: list[float] = []

    def inc(self, name: str) -> None:
        self.counts[name] += 1

    def observe(self, elapsed_ms: float) -> None:
        self.latencies_ms.append(round(elapsed_ms, 1))
        self.latencies_ms = self.latencies_ms[-200:]

    def snapshot(self) -> dict:
        recent = self.latencies_ms[-50:]
        average = round(sum(recent) / len(recent), 1) if recent else 0
        return {
            "counts": dict(self.counts),
            "average_turn_ms": average,
            "latencies_ms": recent,
        }


metrics = Metrics()

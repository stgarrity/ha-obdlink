"""Arrival detection driven by fresh advertisements, not periodic OBD polling."""

from dataclasses import dataclass


@dataclass
class ArrivalGate:
    away_seconds: float = 120
    last_seen: float | None = None

    def observe(self, now: float) -> bool:
        if self.last_seen is not None and now <= self.last_seen:
            return False
        arrived = self.last_seen is None or now - self.last_seen >= self.away_seconds
        self.last_seen = now
        return arrived

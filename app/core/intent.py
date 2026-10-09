from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Intent:
    action: str = "WAIT"
    target_index: int | None = None
    confidence: float = 0.0

    @classmethod
    def from_vl(cls, data: dict) -> "Intent":
        raw = data.get("intent") or {}
        return cls(
            action=str(raw.get("action", "WAIT")).upper(),
            target_index=raw.get("target_index"),
            confidence=max(0.0, min(1.0, float(raw.get("confidence", 0.0)))),
        )

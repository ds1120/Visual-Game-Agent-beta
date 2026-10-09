from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class HUDGeometry:
    """Normalized 0..1000 HUD geometry proposed by Qwen and validated by OpenCV."""
    hud_bbox: list[int] | None = None
    health_bbox: list[int] | None = None
    buff_bbox: list[int] | None = None
    confidence: float = 0.0
    source: str = "unknown"
    validated: bool = False
    extras: dict = field(default_factory=dict)

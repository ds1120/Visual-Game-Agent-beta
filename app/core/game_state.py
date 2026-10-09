from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.intent import Intent


@dataclass
class HUDState:
    health: float | None = None
    mp: float | None = None
    sp: float | None = None
    health_valid: bool = False
    mp_valid: bool = False
    sp_valid: bool = False
    shield_valid: bool = False
    buffs: list[str] = field(default_factory=list)
    regions: dict = field(default_factory=dict)


@dataclass
class VisualObject:
    object_id: int
    object_type: str
    bbox: tuple[int, int, int, int]
    confidence: float
    detector_type: str = "unknown"
    relation: str = "unknown"
    semantic_confidence: float = 0.0
    track_id: int | None = None
    memory_id: int | None = None
    status: str = "unknown"
    name: str = ""
    enemy_health: float | None = None
    enemy_health_valid: bool = False
    enemy_bar_confirmed: bool = False


@dataclass
class GenericGameState:
    source: str = ""
    scene_type: str = "unknown"
    scene_confidence: float = 0.0
    hud: HUDState = field(default_factory=HUDState)
    objects: list[VisualObject] = field(default_factory=list)
    intent: Intent = field(default_factory=Intent)
    vl_elapsed_ms: float = 0.0

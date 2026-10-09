from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Detection:
    class_id: int
    class_name: str
    confidence: float

    x1: int
    y1: int
    x2: int
    y2: int

    center_x: int
    center_y: int

    width: int
    height: int
from __future__ import annotations

import math
from typing import Sequence

from app.core.game_state import VisualObject


class TargetSelector:
    """Deterministic combat target selector with short visual target stickiness."""

    def __init__(self, sticky_iou: float = 0.25) -> None:
        self.sticky_iou = float(sticky_iou)
        self._last_bbox: tuple[int, int, int, int] | None = None

    @staticmethod
    def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
        inter = iw * ih
        if inter <= 0:
            return 0.0
        aa = max(1, (ax2 - ax1) * (ay2 - ay1))
        ab = max(1, (bx2 - bx1) * (by2 - by1))
        return inter / float(aa + ab - inter)

    def clear(self) -> None:
        self._last_bbox = None

    def select(
        self,
        objects: Sequence[VisualObject],
        candidate_indices: Sequence[int],
        frame_shape: tuple[int, ...],
    ) -> int | None:
        if not candidate_indices:
            self.clear()
            return None

        h, w = int(frame_shape[0]), int(frame_shape[1])
        cx, cy = w * 0.5, h * 0.5
        max_dist = max(1.0, math.hypot(cx, cy))

        best_index: int | None = None
        best_score = -1.0

        for i in candidate_indices:
            if i < 0 or i >= len(objects):
                continue
            obj = objects[i]
            x1, y1, x2, y2 = obj.bbox
            ox, oy = (x1 + x2) * 0.5, (y1 + y2) * 0.5
            center_score = 1.0 - min(1.0, math.hypot(ox - cx, oy - cy) / max_dist)

            semantic_score = max(0.0, min(1.0, float(obj.semantic_confidence)))
            detector_score = max(0.0, min(1.0, float(obj.confidence)))
            sticky = 0.0
            if self._last_bbox is not None:
                sticky = min(1.0, self._iou(obj.bbox, self._last_bbox) / max(0.01, self.sticky_iou))

            # Semantic truth first, then practical screen position, then detector confidence.
            # Stickiness prevents rapid target hopping between nearby monsters.
            score = (
                semantic_score * 0.45
                + center_score * 0.30
                + detector_score * 0.10
                + sticky * 0.15
            )
            if score > best_score:
                best_score = score
                best_index = i

        if best_index is not None:
            self._last_bbox = objects[best_index].bbox
        return best_index

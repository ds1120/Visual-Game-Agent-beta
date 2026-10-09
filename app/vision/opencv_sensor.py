"""Profile-calibrated HUD measurements; inference-free after ROI discovery."""

from __future__ import annotations
import cv2
import numpy as np
from app.core.game_state import HUDState


class OpenCVSensor:
    DEFAULT_HSV = {
        "health": [[[0, 65, 40], [15, 255, 255]], [[165, 65, 40], [179, 255, 255]]],
        "mp": [[[80, 50, 35], [140, 255, 255]]],
        "sp": [[[20, 50, 40], [85, 255, 255]]],
    }

    def __init__(self):
        self._regions = {}
        self.health_valid = False
        self.resource_valid = False

    def set_regions(self, regions):
        self._regions = dict(regions or {})
        self.health_valid = False
        self.resource_valid = False

    @staticmethod
    def _roi(frame, item):
        if not item or not item.get("visible") or not item.get("bbox"):
            return None
        h, w = frame.shape[:2]
        if item.get("pixel_resolution") == [w, h] and item.get("pixel_bbox"):
            x1, y1, x2, y2 = item["pixel_bbox"]
            return frame[y1:y2, x1:x2]
        x1, y1, x2, y2 = item["bbox"]
        return frame[
            max(0, round(y1 * h / 1000)) : min(h, round(y2 * h / 1000)),
            max(0, round(x1 * w / 1000)) : min(w, round(x2 * w / 1000)),
        ]

    @classmethod
    def measure(cls, roi, item, kind):
        if roi is None or roi.size == 0 or min(roi.shape[:2]) < 3:
            return None
        inset = item.get("inset_fraction", [0, 0])
        h, w = roi.shape[:2]
        ix, iy = round(w*inset[0]), round(h*inset[1])
        roi = roi[iy:h-iy, ix:w-ix]
        if roi.size == 0: return None
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        mask = np.zeros(roi.shape[:2], dtype=np.uint8)
        for lo, hi in item.get("hsv_ranges", cls.DEFAULT_HSV[kind]):
            mask = cv2.bitwise_or(mask, cv2.inRange(hsv, tuple(lo), tuple(hi)))
        active = mask > 0
        axis = item.get("axis", "x")
        if item.get("shape") == "orb":
            h, w = active.shape
            yy, xx = np.ogrid[:h, :w]
            interior = ((xx - (w - 1) / 2) / (w * 0.43)) ** 2 + (
                (yy - (h - 1) / 2) / (h * 0.43)
            ) ** 2 <= 1
            active &= interior
            counts = active.sum(axis=1 if axis == "y" else 0)
            width = interior.sum(axis=1 if axis == "y" else 0)
            occupied = (width > 0) & (counts / np.maximum(1, width) > 0.12)
            eligible = np.flatnonzero(width > 0)
            if not len(eligible):
                return None
            occupied = occupied[eligible[0] : eligible[-1] + 1]
        else:
            occupied = active.mean(axis=1 if axis == "y" else 0) > 0.08
        indices = np.flatnonzero(occupied)
        if not len(indices):
            if item.get("allow_empty") and item.get("tracked_stack") and np.mean(hsv[:,:,2] < 45) > .9:
                return 0.0  # Locator verified all three framed bars in this same frame.
            return None  # lost HUD or unverified empty meter
        fill = (
            (len(occupied) - indices[0]) / len(occupied)
            if item.get("fill_from", "start") == "end"
            else (indices[-1] + 1) / len(occupied)
        )
        return float(np.clip(fill * 100, 0, 100))

    def validate_regions(self, frame):
        state = self.analyze(frame)
        self.health_valid = state.health_valid
        self.resource_valid = state.mp_valid
        return self.health_valid, self.resource_valid

    def analyze(self, frame):
        values = {}
        for name in ("health", "mp", "sp"):
            item = self._regions.get(name, {})
            values[name] = self.measure(self._roi(frame, item), item, name)
        return HUDState(
            health=values["health"],
            mp=values["mp"],
            sp=values["sp"],
            health_valid=values["health"] is not None,
            mp_valid=values["mp"] is not None,
            sp_valid=values["sp"] is not None,
            regions=self._regions.copy(),
        )

from __future__ import annotations

from pathlib import Path
import cv2
import numpy as np


class ProfileHUDSensor:
    """Reusable OpenCV helpers for HUD references supplied with each game profile."""

    @staticmethod
    def crop_normalized(frame: np.ndarray, roi) -> np.ndarray | None:
        if roi is None:
            return None
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = roi.pixels(w, h)
        x1, x2 = max(0, x1), min(w, x2)
        y1, y2 = max(0, y1), min(h, y2)
        if x2 <= x1 or y2 <= y1:
            return None
        return frame[y1:y2, x1:x2]

    @staticmethod
    def load_reference(path: Path | None) -> np.ndarray | None:
        if path is None or not path.exists():
            return None
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        return image if image is not None and image.size else None

    @staticmethod
    def template_score(frame_roi: np.ndarray | None, reference: np.ndarray | None) -> float:
        if frame_roi is None or reference is None:
            return 0.0
        if reference.shape[0] > frame_roi.shape[0] or reference.shape[1] > frame_roi.shape[1]:
            reference = cv2.resize(reference, (frame_roi.shape[1], frame_roi.shape[0]))
        a = cv2.cvtColor(frame_roi, cv2.COLOR_BGR2GRAY)
        b = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY)
        if a.shape != b.shape:
            b = cv2.resize(b, (a.shape[1], a.shape[0]))
        result = cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED)
        return float(cv2.minMaxLoc(result)[1])

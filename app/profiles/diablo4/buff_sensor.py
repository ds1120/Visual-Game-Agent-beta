from __future__ import annotations

from pathlib import Path
import cv2
import numpy as np


class DiabloBuffSensor:
    """Qwen proposes a coarse ROI; OpenCV confirms/locks it using known buff templates."""

    def __init__(self, match_threshold: float = 0.78, search_expand: float = 1.0) -> None:
        self.candidate_bbox: list[int] | None = None
        self.locked_bbox: list[int] | None = None
        self.match_threshold = float(match_threshold)
        self.search_expand = float(search_expand)
        self.reference_dir = Path(__file__).resolve().parent / "assets" / "buffs"
        self.references = self._load_references()
        self.last_matches: list[tuple[str, float, list[int]]] = []

    def _load_references(self) -> dict[str, np.ndarray]:
        refs: dict[str, np.ndarray] = {}
        if self.reference_dir.exists():
            for path in sorted(self.reference_dir.glob("*.png")):
                image = cv2.imread(str(path), cv2.IMREAD_COLOR)
                if image is not None and image.size:
                    refs[path.stem] = image
        return refs

    def reload_references(self) -> None:
        self.references = self._load_references()

    def set_bbox(self, bbox: list[int] | None) -> None:
        self.candidate_bbox = bbox
        self.locked_bbox = None
        self.last_matches = []

    @staticmethod
    def _to_pixels(bbox: list[int], frame: np.ndarray) -> list[int]:
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = bbox
        return [
            max(0, round(w*x1/1000)), max(0, round(h*y1/1000)),
            min(w, round(w*x2/1000)), min(h, round(h*y2/1000)),
        ]

    @staticmethod
    def _to_normalized(bbox: list[int], frame: np.ndarray) -> list[int]:
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = bbox
        return [
            round(x1/w*1000), round(y1/h*1000),
            round(x2/w*1000), round(y2/h*1000),
        ]

    def _expanded_pixels(self, frame: np.ndarray) -> list[int] | None:
        if not self.candidate_bbox:
            return None
        x1, y1, x2, y2 = self._to_pixels(self.candidate_bbox, frame)
        h, w = frame.shape[:2]
        bw, bh = max(1, x2-x1), max(1, y2-y1)
        ex, ey = round(bw*self.search_expand), round(bh*self.search_expand)
        return [max(0,x1-ex), max(0,y1-ey), min(w,x2+ex), min(h,y2+ey)]

    @staticmethod
    def _gray(image: np.ndarray) -> np.ndarray:
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    def confirm_candidate(self, frame: np.ndarray) -> bool:
        """Search around Qwen's candidate and lock ROI only if a known buff icon is found."""
        area = self._expanded_pixels(frame)
        if area is None or not self.references:
            return False
        ax1, ay1, ax2, ay2 = area
        roi = frame[ay1:ay2, ax1:ax2]
        if roi.size == 0:
            return False
        roi_gray = self._gray(roi)

        matches: list[tuple[str, float, list[int]]] = []
        for name, template in self.references.items():
            th, tw = template.shape[:2]
            if th > roi.shape[0] or tw > roi.shape[1]:
                continue
            result = cv2.matchTemplate(roi_gray, self._gray(template), cv2.TM_CCOEFF_NORMED)
            _, score, _, loc = cv2.minMaxLoc(result)
            if score >= self.match_threshold:
                x, y = loc
                matches.append((name, float(score), [ax1+x, ay1+y, ax1+x+tw, ay1+y+th]))

        if not matches:
            self.last_matches = []
            return False

        self.last_matches = sorted(matches, key=lambda m: m[1], reverse=True)
        # Lock a practical band around confirmed icons, with room for adjacent buff slots.
        boxes = [m[2] for m in self.last_matches]
        x1 = min(b[0] for b in boxes); y1 = min(b[1] for b in boxes)
        x2 = max(b[2] for b in boxes); y2 = max(b[3] for b in boxes)
        icon_h = max(1, y2-y1)
        pad_x, pad_y = icon_h*4, max(4, icon_h//2)
        h, w = frame.shape[:2]
        locked_px = [max(0,x1-pad_x), max(0,y1-pad_y), min(w,x2+pad_x), min(h,y2+pad_y)]
        self.locked_bbox = self._to_normalized(locked_px, frame)
        return True

    def detect(self, frame: np.ndarray) -> list[str]:
        bbox = self.locked_bbox
        if not bbox or not self.references:
            return []
        x1,y1,x2,y2 = self._to_pixels(bbox, frame)
        roi = frame[y1:y2, x1:x2]
        if roi.size == 0:
            return []
        gray = self._gray(roi)
        found: list[str] = []
        for name, template in self.references.items():
            th, tw = template.shape[:2]
            if th > roi.shape[0] or tw > roi.shape[1]:
                continue
            result = cv2.matchTemplate(gray, self._gray(template), cv2.TM_CCOEFF_NORMED)
            _, score, _, _ = cv2.minMaxLoc(result)
            if score >= self.match_threshold:
                found.append(name)
        return found

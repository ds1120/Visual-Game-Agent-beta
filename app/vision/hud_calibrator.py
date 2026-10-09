from __future__ import annotations

from pathlib import Path
import cv2
import numpy as np

from app.vision.hud_geometry import HUDGeometry


class HUDCalibrator:
    """Qwen proposes HUD geometry; OpenCV validates/refines it against a profile screenshot."""

    def __init__(self, reference_path: Path, threshold: float = 0.42) -> None:
        self.reference_path = reference_path
        self.threshold = float(threshold)
        self.reference = cv2.imread(str(reference_path), cv2.IMREAD_COLOR)
        self.geometry: HUDGeometry | None = None

    @staticmethod
    def _px(bbox: list[int], frame: np.ndarray) -> tuple[int, int, int, int]:
        h, w = frame.shape[:2]
        x1,y1,x2,y2 = bbox
        return (
            max(0, round(w*x1/1000)), max(0, round(h*y1/1000)),
            min(w, round(w*x2/1000)), min(h, round(h*y2/1000)),
        )

    @staticmethod
    def _norm(box: tuple[int,int,int,int], frame: np.ndarray) -> list[int]:
        h,w = frame.shape[:2]
        x1,y1,x2,y2 = box
        return [round(x1/w*1000), round(y1/h*1000), round(x2/w*1000), round(y2/h*1000)]

    def validate_and_refine(self, frame: np.ndarray, proposed: HUDGeometry) -> HUDGeometry:
        if self.reference is None or proposed.hud_bbox is None:
            proposed.validated = False
            self.geometry = proposed
            return proposed

        x1,y1,x2,y2 = self._px(proposed.hud_bbox, frame)
        # Expand Qwen's coarse proposal substantially; OpenCV does the precise alignment.
        bw,bh = max(1,x2-x1), max(1,y2-y1)
        h,w = frame.shape[:2]
        sx1,sy1 = max(0,x1-bw//2), max(0,y1-bh//2)
        sx2,sy2 = min(w,x2+bw//2), min(h,y2+bh//2)
        search = frame[sy1:sy2, sx1:sx2]
        if search.size == 0:
            proposed.validated = False
            self.geometry = proposed
            return proposed

        ref = self.reference
        # Search several scales so UI scaling/resolution differences do not require exact pixel size.
        best = (-1.0, None)
        for scale in (0.70,0.80,0.90,1.00,1.10,1.20,1.30):
            rw = max(16, round(ref.shape[1]*scale))
            rh = max(16, round(ref.shape[0]*scale))
            if rw > search.shape[1] or rh > search.shape[0]:
                continue
            templ = cv2.resize(ref, (rw,rh), interpolation=cv2.INTER_AREA)
            a = cv2.cvtColor(search, cv2.COLOR_BGR2GRAY)
            b = cv2.cvtColor(templ, cv2.COLOR_BGR2GRAY)
            result = cv2.matchTemplate(a,b,cv2.TM_CCOEFF_NORMED)
            _,score,_,loc = cv2.minMaxLoc(result)
            if score > best[0]:
                best = (float(score),(loc[0]+sx1,loc[1]+sy1,loc[0]+sx1+rw,loc[1]+sy1+rh))

        score, box = best
        if box is not None and score >= self.threshold:
            proposed.hud_bbox = self._norm(box, frame)
            proposed.confidence = max(proposed.confidence, score)
            proposed.source = "qwen+opencv"
            proposed.validated = True
        else:
            proposed.validated = False

        self.geometry = proposed
        return proposed

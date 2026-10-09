from __future__ import annotations

from pathlib import Path
import time
import cv2
import numpy as np


class DiabloOrbHealthSensor:
    """
    Diablo IV HP/Shield sensor.

    Important: the profile screenshot is a REFERENCE, not a fixed normalized ROI.
    Runtime first locates the actual circular HP orb in the current frame with
    OpenCV HoughCircles. HP/Shield are then measured relative to that detected
    circle, so resolution/aspect/UI-position changes do not distort the gauge.
    """

    def __init__(self) -> None:
        self.bbox: list[int] | None = None
        self.valid = False
        self._auto_locked = False
        self._last_locate = 0.0
        self._locate_interval = 2.0
        self._shield_smoothed: float | None = None

        base = Path(__file__).resolve().parent / "assets" / "hud"
        self._shield_on = cv2.imread(str(base / "hp_shield_100.png"), cv2.IMREAD_COLOR)
        self._shield_off = cv2.imread(str(base / "hp_shield_0.png"), cv2.IMREAD_COLOR)

    def set_bbox(self, bbox: list[int] | None) -> None:
        # Qwen/profile bbox is only a fallback search hint.
        if not self._auto_locked:
            self.bbox = bbox

    @staticmethod
    def _norm_box(box: tuple[int, int, int, int], frame: np.ndarray) -> list[int]:
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = box
        return [
            round(x1 / w * 1000), round(y1 / h * 1000),
            round(x2 / w * 1000), round(y2 / h * 1000),
        ]

    def _locate_orb(self, frame: np.ndarray) -> bool:
        """Find the actual HP orb from its circular HUD geometry."""
        h, w = frame.shape[:2]

        # Diablo player HUD lives in the lower-left, but do not assume an exact ROI.
        sx1, sy1 = 0, int(h * 0.52)
        sx2, sy2 = int(w * 0.36), h
        search = frame[sy1:sy2, sx1:sx2]
        gray = cv2.cvtColor(search, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (7, 7), 1.5)

        min_r = max(24, int(min(w, h) * 0.035))
        max_r = max(min_r + 4, int(min(w, h) * 0.105))

        circles = cv2.HoughCircles(
            gray,
            cv2.HOUGH_GRADIENT,
            dp=1.2,
            minDist=max(30, min_r),
            param1=110,
            param2=34,
            minRadius=min_r,
            maxRadius=max_r,
        )

        if circles is None:
            return False

        candidates = []
        for cx, cy, r in np.round(circles[0]).astype(int):
            gx, gy = cx + sx1, cy + sy1

            # HP orb is left-side and above the very bottom edge.
            if gx > w * 0.22 or gy < h * 0.58:
                continue

            # Prefer a circle containing substantial red/magenta game-gauge color.
            y1, y2 = max(0, cy-r), min(search.shape[0], cy+r)
            x1, x2 = max(0, cx-r), min(search.shape[1], cx+r)
            roi = search[y1:y2, x1:x2]
            if roi.size == 0:
                continue
            hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
            red = cv2.bitwise_or(
                cv2.inRange(hsv, (0, 55, 30), (18, 255, 255)),
                cv2.inRange(hsv, (155, 55, 30), (179, 255, 255)),
            )
            color_ratio = float(np.mean(red > 0))

            # Strongly prefer the leftmost red orb and a plausible HUD-circle size.
            score = color_ratio * 5.0 - (gx / max(1, w)) * 0.8 + (r / max(1, min(w, h)))
            candidates.append((score, gx, gy, r))

        if not candidates:
            return False

        _, cx, cy, r = max(candidates, key=lambda x: x[0])

        # Crop is defined by the detected circle itself, not by screenshot proportions.
        pad = int(round(r * 1.12))
        box = (
            max(0, cx-pad), max(0, cy-pad),
            min(w, cx+pad), min(h, cy+pad),
        )
        self.bbox = self._norm_box(box, frame)
        self._auto_locked = True
        self._last_locate = time.monotonic()
        return True

    def _ensure_orb(self, frame: np.ndarray) -> None:
        now = time.monotonic()
        if (not self._auto_locked) or (now - self._last_locate >= self._locate_interval):
            # Re-detect periodically so UI scale/resolution changes self-correct.
            self._locate_orb(frame)
            self._last_locate = now

    def _crop(self, frame: np.ndarray) -> np.ndarray | None:
        self._ensure_orb(frame)
        if not self.bbox:
            return None
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = self.bbox
        x1, x2 = round(w*x1/1000), round(w*x2/1000)
        y1, y2 = round(h*y1/1000), round(h*y2/1000)
        x1, x2 = max(0,x1), min(w,x2)
        y1, y2 = max(0,y1), min(h,y2)
        if x2-x1 < 30 or y2-y1 < 30:
            return None
        return frame[y1:y2, x1:x2]

    @staticmethod
    def _circle_mask(h: int, w: int, inner: float, outer: float) -> np.ndarray:
        yy, xx = np.ogrid[:h, :w]
        cx, cy = (w-1)/2.0, (h-1)/2.0
        r = min(w, h) * 0.5
        d = np.sqrt((xx-cx)**2 + (yy-cy)**2) / max(r, 1.0)
        return (d >= inner) & (d <= outer)

    @staticmethod
    def _red_mask(roi: np.ndarray) -> np.ndarray:
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        return cv2.bitwise_or(
            cv2.inRange(hsv, (0, 60, 30), (18, 255, 255)),
            cv2.inRange(hsv, (155, 60, 30), (179, 255, 255)),
        ) > 0

    def _hp_percent(self, roi: np.ndarray) -> float | None:
        red = self._red_mask(roi)
        h, w = red.shape

        # Ignore ring/decorations. For each row, measure red coverage across
        # the central liquid body, then find the sustained liquid boundary.
        body = self._circle_mask(h, w, 0.0, 0.68)
        row_red = np.zeros(h, dtype=np.float32)
        for y in range(h):
            valid = body[y]
            n = int(valid.sum())
            if n:
                row_red[y] = float((red[y] & valid).sum()) / n

        active = row_red >= 0.30
        # Require several consecutive active rows to reject combat-effect noise.
        run = 5
        boundary = None
        for y in range(0, h-run+1):
            if bool(np.all(active[y:y+run])):
                boundary = y
                break
        if boundary is None:
            return None

        cy = (h-1)/2.0
        radius = min(w, h)*0.5*0.68
        top, bottom = cy-radius, cy+radius
        return max(0.0, min(100.0, (bottom-boundary)/max(1.0, bottom-top)*100.0))

    @staticmethod
    def _chromatic_features(img: np.ndarray) -> np.ndarray:
        """
        Features deliberately insensitive to brightness changes.
        Diablo's shield ring animates/glows, so raw BGR distance is unstable.
        """
        f = img.astype(np.float32) + 1.0
        total = f.sum(axis=2, keepdims=True)
        chroma = f / total
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
        # Hue is circular; encode it with sin/cos. Saturation is useful, Value is omitted.
        angle = hsv[..., 0] * (2.0 * np.pi / 180.0)
        return np.dstack((
            chroma[..., 0], chroma[..., 1], chroma[..., 2],
            np.sin(angle), np.cos(angle), hsv[..., 1] / 255.0,
        ))

    def _shield_percent(self, roi: np.ndarray) -> float | None:
        if self._shield_on is None or self._shield_off is None:
            return None

        size = (roi.shape[1], roi.shape[0])
        on = cv2.resize(self._shield_on, size, interpolation=cv2.INTER_AREA)
        off = cv2.resize(self._shield_off, size, interpolation=cv2.INTER_AREA)
        h, w = roi.shape[:2]

        # Compare only the outer ring and use chromatic features instead of raw brightness.
        ring = self._circle_mask(h, w, 0.68, 1.02)
        fon = self._chromatic_features(on)
        foff = self._chromatic_features(off)
        flive = self._chromatic_features(roi)

        ref_delta = np.linalg.norm(fon-foff, axis=2)
        # Keep only pixels that genuinely distinguish shield ON from OFF.
        vals = ref_delta[ring]
        if vals.size < 20:
            return None
        cutoff = max(0.10, float(np.percentile(vals, 65)))
        characteristic = ring & (ref_delta >= cutoff)
        if int(characteristic.sum()) < 20:
            return None

        d_on = np.linalg.norm(flive-fon, axis=2)
        d_off = np.linalg.norm(flive-foff, axis=2)

        # Soft nearest-reference vote. Similar color counts even if glow/value changed.
        margin = d_off - d_on
        m = margin[characteristic]
        soft = 1.0 / (1.0 + np.exp(-m * 7.0))
        score = float(np.median(soft))

        # Temporal smoothing prevents animated glow from making shield jump frame-to-frame.
        raw = max(0.0, min(100.0, score * 100.0))
        prev = getattr(self, "_shield_smoothed", None)
        if prev is None:
            smoothed = raw
        else:
            # Rise somewhat faster than fall; brief dim animation should not erase shield.
            alpha = 0.35 if raw > prev else 0.12
            smoothed = prev + (raw-prev)*alpha
        self._shield_smoothed = smoothed

        # Similarity is intentionally fuzzy around endpoints.
        if smoothed >= 78.0:
            return 100.0
        if smoothed <= 22.0:
            return 0.0
        return smoothed

    def validate(self, frame: np.ndarray) -> bool:
        roi = self._crop(frame)
        if roi is None:
            self.valid = False
            return False
        hp = self._hp_percent(roi)
        self.valid = hp is not None
        return self.valid

    def analyze(self, frame: np.ndarray) -> tuple[float | None, float | None, bool]:
        roi = self._crop(frame)
        if roi is None:
            self.valid = False
            return None, None, False
        hp = self._hp_percent(roi)
        shield = self._shield_percent(roi)
        self.valid = hp is not None
        return hp, shield, self.valid

    @property
    def auto_locked(self) -> bool:
        return self._auto_locked

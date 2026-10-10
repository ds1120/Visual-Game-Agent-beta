"""Rotation-tolerant silhouette image matching, independent of arrow colour."""
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

TEMPLATE_PATH = Path(__file__).with_name('assets') / 'route_arrow.png'


@lru_cache(maxsize=1)
def templates():
    silhouette = cv2.imread(str(TEMPLATE_PATH), cv2.IMREAD_GRAYSCALE)
    if silhouette is None:
        raise RuntimeError(f'Arrow template missing: {TEMPLATE_PATH}')
    height, width = silhouette.shape
    bank = []
    for angle in range(0, 360, 10):
        rotated = cv2.warpAffine(silhouette, cv2.getRotationMatrix2D(
            (width / 2, height / 2), angle, 1), (width, height))
        ys, xs = np.nonzero(rotated > 100)
        crop = rotated[ys.min():ys.max()+1, xs.min():xs.max()+1]
        normalized = cv2.resize(crop, (48, 48))
        edge = cv2.Canny(np.pad(normalized, 4), 30, 90)
        bank.append(cv2.GaussianBlur(edge, (5, 5), 1))
    return bank


def match_arrow(frame, player=(.5, .5), excluded=(), previous_marker=None,
                preferred_heading=None):
    height, width = frame.shape[:2]
    # Keep native detail around the player; each candidate is normalized for scale.
    origin = np.array(player) * [width, height]
    radius = min(600, max(300, width * .32))
    left, top = np.maximum(origin-radius, [width*.06, height*.06]).astype(int)
    right, bottom = np.minimum(origin+radius, [width*.94, height*.86]).astype(int)
    if right <= left or bottom <= top:return None
    grey = cv2.cvtColor(frame[top:bottom, left:right], cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(grey, 15, 40)
    closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    scale = width/960
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if not (10*scale <= w <= 80*scale and 8*scale <= h <= 80*scale
                and max(w, h) >= 25*scale and .35 < w/h < 3.5):continue
        center = np.array([left+x+w/2, top+y+h/2])
        delta = center-origin
        distance = float(np.linalg.norm(delta))
        if not 25*scale < distance < radius:continue
        normalized_center = center/[width, height]
        if any(a <= normalized_center[0] <= c and b <= normalized_center[1] <= d
               for a,b,c,d in excluded):continue
        crop = grey[y:y+h, x:x+w]
        candidate = cv2.Canny(np.pad(cv2.resize(crop, (48, 48)), 4, mode='edge'), 15, 40)
        candidate = cv2.GaussianBlur(candidate, (5, 5), 1)
        score = max(float(cv2.matchTemplate(candidate, template,
                    cv2.TM_CCOEFF_NORMED)[0, 0]) for template in templates())
        if not np.isfinite(score) or score < .65:continue
        candidates.append((center, delta/distance, score))
    if preferred_heading is not None:
        forward = [c for c in candidates if float((c[0]-origin)@preferred_heading)>0]
        if forward:candidates=forward
    if not candidates:return None
    reference = np.array(previous_marker)*[width, height] if previous_marker is not None else origin
    point, heading, score = min(candidates, key=lambda c: np.linalg.norm(c[0]-reference))
    marker = tuple(float(v) for v in point/[width, height])
    return {'target':marker, 'marker':marker, 'arrow_tip':marker,
            'direction':tuple(heading), 'arrow_direction':tuple(heading),
            'source':'template_arrow', 'match_score':score, 'dots':[]}

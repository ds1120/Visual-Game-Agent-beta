"""Profile-local icon templates: active buffs are observed, never inferred from key presses."""
from pathlib import Path
import time
import cv2
import numpy as np
from app.vision.game_viewport import hud_region_bbox

class BuffMonitor:
    def __init__(self, profile_dir):
        self.directory = Path(profile_dir) / "assets" / "buffs"
        self.references = {}
        self.absence_seconds=None
        self.repeat_seconds=5
        self.once_per_absence=False
        self.require_all_absent=False
        self.threshold=.82
        self.stable_icon=False
        self.reload()

    def reload(self):
        self.references = {}
        self.seen = set()
        self.misses = {}
        self.expired = set()
        self.pending_recast = False
        self.absent_since={}
        self.last_recast=-1e9
        self.pending_icons=set()
        self.observable=set()
        if self.directory.exists():
            for path in sorted(self.directory.glob("*.png"))[:100]:
                # imdecode supports Korean Windows paths.
                try:
                    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
                    if image is not None and image.size and float(image.std()) > 1:
                        self.references[path.stem] = image
                except (OSError, ValueError):
                    continue

    def observe(self, active, now=None, observable=None):
        now=time.monotonic() if now is None else now
        active = set(active)
        if self.absence_seconds is not None:active.intersection_update(self.references)
        observed=set(self.references) if observable is None else set(observable)
        self.seen.update(active)
        if self.require_all_absent and (active or not set(self.references).issubset(observed)):
            self.pending_recast=False;self.pending_icons.clear();self.absent_since.clear();self.expired.clear()
            return
        if self.absence_seconds is not None:
            due=set()
            for name in self.references:
                if name in active:
                    self.absent_since.pop(name,None);self.expired.discard(name)
                elif name not in observed:self.absent_since.pop(name,None)
                else:
                    self.absent_since.setdefault(name,now)
                    if (now-self.absent_since[name]>=self.absence_seconds
                            and now-self.last_recast>=self.repeat_seconds
                            and (not self.once_per_absence or name not in self.expired)):due.add(name)
            self.pending_icons=due;self.pending_recast=bool(due)
            return
        for name in self.seen:
            if name in active:
                self.misses[name] = 0
                self.expired.discard(name)
            else:
                self.misses[name] = self.misses.get(name, 0) + 1
                if self.misses[name] >= 3 and name not in self.expired:
                    self.expired.add(name)
                    self.pending_recast = True

    def recast_sent(self,now=None):
        if self.once_per_absence:self.expired.update(self.pending_icons)
        self.pending_icons.clear()
        self.pending_recast = False
        self.last_recast=time.monotonic() if now is None else now
        if self.require_all_absent:self.absent_since.clear()

    @staticmethod
    def crop(frame, region):
        if frame is None or not region.get("visible") or not region.get("bbox"):
            return None
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = hud_region_bbox(frame, region)
        image = frame[max(0, round(y1*h/1000)):min(h, round(y2*h/1000)), max(0, round(x1*w/1000)):min(w, round(x2*w/1000))]
        return image if image.size else None

    def detect(self, frame, region):
        self.observable=set()
        roi = self.crop(frame, region)
        if roi is None:
            return []
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        shrink=min(1.,960/max(gray.shape))
        if shrink<1:gray=cv2.resize(gray,(round(gray.shape[1]*shrink),round(gray.shape[0]*shrink)),interpolation=cv2.INTER_AREA)
        found = []
        for name, template in self.references.items():
            best = -1.0
            for scale in (tuple(np.arange(.5,1.61,.05)) if self.stable_icon else (.8, 1.0, 1.2)):
                w, h = round(template.shape[1]*scale*shrink), round(template.shape[0]*scale*shrink)
                if min(w,h) < 3 or w > gray.shape[1] or h > gray.shape[0]:
                    continue
                t = cv2.cvtColor(cv2.resize(template, (w,h)), cv2.COLOR_BGR2GRAY)
                if float(t.std()) <= 1:
                    continue
                self.observable.add(name)
                variants=[t]
                # Exclude the changing countdown/stack number and outer border.
                if self.stable_icon and min(w,h)>=12:
                    variants.append(t[max(1,round(h*.08)):round(h*.70),max(1,round(w*.08)):round(w*.92)])
                for variant in variants:
                    if variant.size and float(variant.std())>1:
                        best=max(best,float(cv2.minMaxLoc(cv2.matchTemplate(gray,variant,cv2.TM_CCOEFF_NORMED))[1]))
            if np.isfinite(best) and best >= self.threshold:
                found.append(name)
        return found

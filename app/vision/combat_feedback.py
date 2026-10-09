"""Calibrated visual feedback; missing bars are unknown, never zero HP."""
from pathlib import Path

import time
import cv2
import numpy as np


class CombatFeedback:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self._templates = {}
        self._bar_tracks={}
        self._complete_boxes=set()

    def template(self, path):
        resolved = (self.root / path).resolve()
        if not resolved.is_relative_to(self.root):
            return None
        if resolved not in self._templates:
            try:
                self._templates[resolved] = cv2.imdecode(np.fromfile(resolved, dtype=np.uint8), cv2.IMREAD_COLOR)
            except (OSError, ValueError, cv2.error):
                return None
        return self._templates[resolved]

    @staticmethod
    def crop(frame, roi, box=None):
        h, w = frame.shape[:2]
        x, y, bw, bh = (0, 0, w, h) if box is None else (
            box[0], box[1], box[2] - box[0], box[3] - box[1]
        )
        x1, y1, x2, y2 = (round(x + roi[0] * bw), round(y + roi[1] * bh),
                          round(x + roi[2] * bw), round(y + roi[3] * bh))
        if not (0 <= x1 < x2 <= w and 0 <= y1 < y2 <= h):
            return None
        return frame[y1:y2, x1:x2]

    def matches(self, frame, config, box=None):
        crop = self.crop(frame, config['roi'], box)
        reference = self.template(config['template'])
        if crop is None or reference is None or crop.shape != reference.shape:
            return False
        # Absolute error also handles flat-color references safely.
        score = 1 - float(cv2.absdiff(crop, reference).mean()) / 255
        return score >= config.get('threshold', .95)

    def annotate(self, frame, objects, config):
        if not config or not config.get('enabled', False):
            return
        for obj in objects:
            if obj.status != 'confirmed' or obj.object_type != 'monster' or obj.relation != 'hostile':
                continue
            # A separately calibrated border/nameplate patch must be present
            # before reading the fill; a red effect alone cannot establish HP.
            if not self.matches(frame, config['presence'], obj.bbox):
                continue
            crop = self.crop(frame, config['fill_roi'], obj.bbox)
            if crop is None:
                continue
            hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
            for low, high in config['fill_hsv']:
                mask |= cv2.inRange(hsv, np.array(low), np.array(high))
            columns = (mask > 0).mean(axis=0) >= .5
            # Only a contiguous left-aligned fill is a valid measurement.
            edges = np.flatnonzero(~columns)
            width = int(edges[0]) if len(edges) else len(columns)
            if columns[width:].any():
                continue
            obj.enemy_health = 100 * width / len(columns)
            obj.enemy_health_valid = True

    def _bar_components(self,frame,config):
        roi=config.get('roi',[.15,.15,.85,.8]);crop=self.crop(frame,roi)
        if crop is None:return [],[],None
        h,w=frame.shape[:2];ox,oy=round(roi[0]*w),round(roi[1]*h)
        hsv=cv2.cvtColor(crop,cv2.COLOR_BGR2HSV);red=np.zeros(hsv.shape[:2],np.uint8)
        for low,high in config.get('hsv',[[[0,180,100],[10,255,255]],[[170,180,100],[179,255,255]]]):
            red|=cv2.inRange(hsv,np.array(low),np.array(high))
        def boxes(mask,tiny=False,density=.75):
            count,_,stats,_=cv2.connectedComponentsWithStats(mask)
            return [(int(ox+x),int(oy+y),int(ox+x+bw),int(oy+y+bh)) for x,y,bw,bh,area in stats[1:count]
                    if (1 if tiny else config.get('min_width',20))<=bw<=config.get('max_width',220)
                    and 1<=bh<=config.get('max_height',12) and area/(bw*bh)>=density
                    and (tiny or bw/bh>=5)]
        dark=cv2.inRange(hsv,(0,0,0),(179,255,55));surface=(dark|red)>0
        backgrounds=[d for d in boxes(dark,density=.1) if surface[d[1]-oy:d[3]-oy,d[0]-ox:d[2]-ox].mean()>=.9]
        return boxes(red,True),backgrounds,red

    def red_bar_boxes(self,frame,config):
        """Find the entire red/black bar; tiny residual red is not a new full bar."""
        self._complete_boxes=set()
        if not config or not config.get('enabled',False):return []
        reds,darks,_=self._bar_components(frame,config);result=[]
        for r in reds:
            linked=[]
            for d in darks:
                overlap=max(0,min(r[3],d[3])-max(r[1],d[1]))
                gap=max(d[0]-r[2],r[0]-d[2],0)
                if overlap>=min(r[3]-r[1],d[3]-d[1])*.5 and gap<=2:
                    box=(min(r[0],d[0]),min(r[1],d[1]),max(r[2],d[2]),max(r[3],d[3]))
                    if box[2]-box[0]<=config.get('max_width',220) and box[3]-box[1]<=config.get('max_height',12):linked.append(box)
            if len(linked)==1:
                box=linked[0];self._complete_boxes.add(box);result.append(box)
            elif not linked and r[2]-r[0]>=config.get('min_width',20) and r[3]-r[1]>=2 and (r[2]-r[0])/(r[3]-r[1])>=5:result.append(r)
        return list(dict.fromkeys(result))

    @staticmethod
    def _bar_overlap(a,b):
        inter=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
        return inter/max(1,(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-inter)

    def tracked_black_bars(self,frame,config):
        """A black rectangle is HP zero only when it matches a recently confirmed enemy bar."""
        if not config or not config.get('enabled',False):return []
        reds,darks,_=self._bar_components(frame,config);now=time.monotonic()
        self._bar_tracks={k:v for k,v in self._bar_tracks.items() if now-v[1]<1}
        result=[]
        for d in darks:
            if any(min(r[3],d[3])>max(r[1],d[1]) and r[0]<=d[2]+2 and r[2]>=d[0]-2 for r in reds):continue
            if any(self._bar_overlap(d,box)>.65 for box,at in self._bar_tracks.values()):
                result.append(d);self._complete_boxes.add(d)
        return result

    def confirm_red_bars(self, frame, objects, config, bars=None):
        """Associate single horizontal red bars with exactly one body candidate.

        A complete red-and-black rectangle provides the observed fill extent.
        Red-only detections confirm hostility without inventing health.
        """
        if not config or not config.get('enabled', False):
            return
        bars=self.red_bar_boxes(frame,config) if bars is None else bars
        links = {}
        ambiguous = set()
        for bx1,by1,bx2,by2 in bars:
            cx,cy=(bx1+bx2)/2,(by1+by2)/2
            candidates = []
            for obj in objects:
                if obj.detector_type not in {'monster', 'person', 'npc', 'unknown'}:
                    continue
                x1, y1, x2, y2 = obj.bbox
                body_h = max(1, y2 - y1)
                if x1 <= cx <= x2 and y1 - body_h * .6 <= cy <= y1 + body_h * .15:
                    candidates.append(obj)
            if len(candidates) == 1:
                obj = candidates[0]
                links.setdefault(id(obj), (obj, []))[1].append((bx1,by1,bx2,by2))
            elif len(candidates) > 1:
                ambiguous.update(id(obj) for obj in candidates)
        for obj, bars in links.values():
            # Multiple bars or a named/confirmed ally are ambiguous association.
            if id(obj) in ambiguous or len(bars) != 1 or obj.relation in {'friendly', 'neutral'}:
                continue
            obj.object_type, obj.relation, obj.status = 'monster', 'hostile', 'confirmed'
            obj.semantic_confidence = 1.0
            obj.enemy_bar_confirmed = True
            box=bars[0];self._bar_tracks[obj.track_id]=(box,time.monotonic())
            if box in self._complete_boxes:
                crop=frame[box[1]:box[3],box[0]:box[2]]
                hsv=cv2.cvtColor(crop,cv2.COLOR_BGR2HSV);red=np.zeros(hsv.shape[:2],np.uint8)
                for low,high in config.get('hsv',[[[0,180,100],[10,255,255]],[[170,180,100],[179,255,255]]]):red|=cv2.inRange(hsv,np.array(low),np.array(high))
                columns=np.flatnonzero((red>0).any(axis=0))
                obj.enemy_health=100*(int(columns[-1])+1)/crop.shape[1] if len(columns) else 0
                obj.enemy_health_valid=True

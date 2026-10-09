"""Selectable player bars; preserves legacy profile behaviour when classic is selected."""
import copy
import cv2
import numpy as np
from app.vision.opencv_sensor import OpenCVSensor
from app.vision.player_bar_locator import locate_player_bars
from app.core.game_state import HUDState

class BarHUDProfile:
    def __init__(self,profile):
        self.original=profile
        self.layout='classic'
        self.sensor=OpenCVSensor()
        self._reference_ranges={}
    def __getattr__(self,name):return getattr(self.original,name)
    @property
    def hud_prompt_context(self):
        if self.layout=='classic':return self.original.hud_prompt_context
        return ('Locate PLAYER horizontal HUD bars only, not monster nameplates or the old orbs. '
                'User reference order: HP/health BLUE, SP PINK/MAGENTA, MP PURPLE. '
                'They fill from left to right: shape=bar, axis=x, fill_from=start. '
                'The bars are a compact THREE ROW STACK directly below the player name above the player character near screen centre, not the bottom-left HUD. HP is top, SP middle, MP bottom. Return the full framed bar bounding box including its empty portion, never just the coloured fill. '
                'Return visible=false if uncertain. Do not locate buff icons. OpenCV measures values.')
    @property
    def hud_calibration_context(self):
        return self.original.hud_calibration_context if self.layout=='classic' else self.hud_prompt_context
    @property
    def full_reference_path(self):
        return self.original.full_reference_path if self.layout=='classic' else self.profile_dir/'assets/hud/player_bars_full.png'
    def prepare_regions(self,regions):
        result=copy.deepcopy(regions)
        if self.layout!='classic':
            for kind in ('health','sp','mp'):
                item=result.get(kind)
                if not item:continue
                # The locator verified all three framed rows in this frame.
                # Do not narrow its capture-tolerant ranges back to an asset's hue.
                ranges=copy.deepcopy(item.get('hsv_ranges')) if item.get('tracked_stack') else self._reference_ranges.get(kind)
                if ranges is None:
                    path=self.profile_dir/f'assets/hud/{kind}_bar.png'
                    ref=cv2.imdecode(np.fromfile(path,dtype=np.uint8),cv2.IMREAD_COLOR) if path.exists() else None
                    defaults={'health':[[[100,45,35],[119,255,255]]],'sp':[[[155,45,35],[179,255,255]]],'mp':[[[120,45,35],[150,255,255]]]}
                    ranges=defaults[kind]
                    if ref is not None:
                        hsv=cv2.cvtColor(ref,cv2.COLOR_BGR2HSV);pixels=hsv[(hsv[:,:,1]>45)&(hsv[:,:,2]>35)]
                        if len(pixels):
                            lo,hi=np.percentile(pixels[:,0],[2,98]);ranges=[[[max(0,int(lo)-5),40,30],[min(179,int(hi)+5),255,255]]]
                    self._reference_ranges[kind]=ranges
                item.update(shape='bar',axis='x',fill_from='start',hsv_ranges=ranges,inset_fraction=[.032,.25])
        return result
    def locate_bars(self,frame):
        if self.layout=='classic':return None
        regions=locate_player_bars(frame,self.sensor._regions)
        if regions:
            for name,item in self.sensor._regions.items():
                if name not in ('health','sp','mp'):regions[name]=copy.deepcopy(item)
        return regions
    def set_hud_regions(self,regions):
        if self.layout=='classic':self.original.set_hud_regions(regions)
        else:self.sensor.set_regions(self.prepare_regions(regions))
    def validate_hud(self,frame):
        return self.original.validate_hud(frame) if self.layout=='classic' else self.analyze_hud(frame).health_valid
    def analyze_hud(self,frame):
        if self.layout=='classic':return self.original.analyze_hud(frame)
        regions=self.locate_bars(frame)
        if regions:self.sensor.set_regions(self.prepare_regions(regions))
        elif (frame.shape[0]>=100 and frame.shape[1]>=150) or any(v.get('tracked_stack') for v in self.sensor._regions.values()):
            return HUDState(regions=copy.deepcopy(self.sensor._regions))  # Never read the old player location after tracking loss.
        return self.sensor.analyze(frame)
    def export_hud_regions(self):
        return self.original.export_hud_regions() if self.layout=='classic' else copy.deepcopy(self.sensor._regions)

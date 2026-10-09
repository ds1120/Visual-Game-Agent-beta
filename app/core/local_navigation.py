"""Short screen-space steps with object obstacles and optional OpenCV minimap."""

from __future__ import annotations
import math
import cv2
import numpy as np
from app.vision.game_viewport import minimap_region
from app.core.route_geometry import supercover_cells
from app.vision.orange_route import route_terrain_image


def minimap_mask(frame, settings):
    m = settings["minimap"]
    if not m["enabled"]:
        return None
    region = minimap_region(frame, m)
    if region is None:return None
    x1, y1, x2, y2 = region
    roi = frame[y1:y2,x1:x2]
    if roi.size == 0:
        return np.zeros((1, 1), dtype=np.uint8)
    if m.get("mapping",{}).get("mode","bright_floor") in {"bright_floor","diablo4_auto"}:
        roi=route_terrain_image(roi)
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    mask = np.zeros(roi.shape[:2], dtype=np.uint8)
    for lo, hi in m["walkable_hsv"]:
        mask = cv2.bitwise_or(mask, cv2.inRange(hsv, tuple(lo), tuple(hi)))
    return mask


class LocalNavigator:
    @staticmethod
    def click_is_clear(target,objects,shape,excluded_regions=(),origin=(.5,.5),minimum_distance=.08):
        x,y=target;h,w=shape[:2]
        if not (0<=x<=1 and 0<=y<=1) or math.hypot(x-origin[0],y-origin[1])<minimum_distance-1e-8:return False
        if any(x1<=x<=x2 and y1<=y<=y2 for x1,y1,x2,y2 in excluded_regions):return False
        for o in objects:
            if o.object_type not in {'player','npc','monster','unknown'} and o.detector_type not in {'player','npc','monster','person'}:continue
            x1,y1,x2,y2=o.bbox
            if x1/w-.02<=x<=x2/w+.02 and y1/h-.02<=y<=y2/h+.02:return False
        return True

    @staticmethod
    def _map_clear(direction, mask, m):
        if mask is None:
            return not m["enabled"]
        h, w = mask.shape[:2]
        a = math.radians(m["rotation_degrees"])
        dx, dy = direction
        dx, dy = dx * math.cos(a) - dy * math.sin(a), dx * math.sin(a) + dy * math.cos(a)
        px, py = m["player"]
        # Ignore the central player marker and sample a short corridor ahead.
        mapped=m.get('mapping',{}).get('enabled',False)
        lookahead=m.get('lookahead',.18)
        near=min(.025 if mapped else .05,lookahead)
        yscale=w if mapped or m.get('pixel_directions') else h
        verified=m.get('verified_route',False)
        offset=0 if verified else .5
        start=(px*w+dx*near*w+offset,py*h+dy*near*yscale+offset)
        end=(px*w+dx*lookahead*w+offset,py*h+dy*lookahead*yscale+offset)
        for x,y in supercover_cells(start,end):
            if (
                not (1 <= x < w - 1 and 1 <= y < h - 1)
                or (mapped and mask[y,x]==0)
                or (not verified and (mask[y - 1 : y + 2, x - 1 : x + 2] > 0).mean() < 0.6)
            ):
                return False
        return True

    def choose(self, direction, objects, shape, settings, mask=None, avoid_direction=None):
        if settings.get('strict_route') and settings.get('route_mask') is not None:
            mask=settings['route_mask']
        dx, dy = direction
        n = math.hypot(dx, dy)
        if n < 1e-8:
            return None
        dx, dy = dx / n, dy / n
        h, w = shape[:2]
        origin=settings.get('player_screen',(.5,.5))
        margin = settings["obstacle_margin"]
        # Unknown YOLO obstacle candidates are sufficient to avoid; never to attack.
        obstacles = [
            o
            for o in objects
            if (o.object_type == "obstacle" or o.detector_type == "obstacle")
            and o.confidence >= 0.4 and not settings.get('map_only')
        ]
        degrees_list=((90, -90, 135, -135, 180, 45, -45, 0) if avoid_direction else (0, 45, -45, 90, -90, 135, -135, 180))
        if settings.get('smooth_navigation') and not avoid_direction:
            degrees_list=(0,15,-15,30,-30,45,-45,60,-60,90,-90,135,-135,180)
            previous=getattr(self,'last_direction',None)
            if previous:
                def score(degrees):
                    a=math.radians(degrees);v=(dx*math.cos(a)-dy*math.sin(a),dx*math.sin(a)+dy*math.cos(a))
                    return abs(degrees)/90+.25*(1-v[0]*previous[0]-v[1]*previous[1])
                degrees_list=sorted(degrees_list,key=score)
        if settings.get('strict_route'):
            # The map planner already handled failed directions and wall clearance.
            # A stale screen recovery hint must not veto its newly verified route.
            degrees_list=(0,);avoid_direction=None
        for degrees in degrees_list:
            a = math.radians(degrees)
            vx, vy = dx * math.cos(a) - dy * math.sin(a), dx * math.sin(a) + dy * math.cos(a)
            if avoid_direction:
                length=math.hypot(*avoid_direction)
                if length and (vx*avoid_direction[0]+vy*avoid_direction[1])/length > .7: continue
            distance=settings['step_fraction'] if settings.get('map_screen_scale') or settings.get('strict_route') else max(.08,settings['step_fraction'])
            # Minimap vectors are pixel directions. Normalized X/Y scaling otherwise changes the heading on wide screens.
            scale_y=w/h if settings.get('projection')=='isotropic' else 1
            # Travel clicks must clear the character's near zone in screen
            # pixels. Extend a tiny waypoint only along a verified clear ray.
            minimum_pixels=settings.get('minimum_move_pixels',0)
            pixels_per_length=math.hypot(vx*w,vy*scale_y*h)
            minimum_length=minimum_pixels/max(pixels_per_length,1)
            if not settings.get('strict_route'):distance=max(distance,minimum_length)
            end=None
            for fraction in ((1,.8,.6,.4,.25,.125) if settings.get('strict_route') else (1,)):
                length=distance*fraction if settings.get('map_screen_scale') or settings.get('strict_route') else max(.08,distance*fraction)
                if length*pixels_per_length<minimum_pixels-1e-6:continue
                candidate=(origin[0]+vx*length,origin[1]+vy*length*scale_y)
                if self.click_is_clear(candidate,objects,shape,settings.get('excluded_regions',()),origin,settings.get('minimum_click_distance',.08)):
                    m=dict(settings['minimap'])
                    if settings.get('map_screen_scale') and mask is not None:
                        m['lookahead']=length*w/settings['map_screen_scale']/mask.shape[1]
                        allowed=settings['minimap'].get('lookahead',.18) if settings.get('strict_route') else max(settings['minimap'].get('lookahead',.18),minimum_length*w/settings['map_screen_scale']/mask.shape[1])
                        if m['lookahead']>allowed+1e-6:continue
                    if self._map_clear((vx,vy),mask,m):end=candidate;break
            if end is None:continue
            blocked = False
            for t in np.linspace(0.15, 1, 10):
                x, y = origin[0] + (end[0] - origin[0]) * t, origin[1] + (end[1] - origin[1]) * t
                for o in obstacles:
                    x1, y1, x2, y2 = o.bbox
                    if (
                        x1 / w - margin <= x <= x2 / w + margin
                        and y1 / h - margin <= y <= y2 / h + margin
                    ):
                        blocked = True
                        break
                if blocked:
                    break
            if not blocked:
                self.last_direction=(vx,vy)
                return (vx, vy), end
        return None

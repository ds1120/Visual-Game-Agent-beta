"""Conservative symmetric black-bar detection for minimap coordinate projection."""
import numpy as np


def game_viewport(frame):
    h, w = frame.shape[:2]
    step = max(1, max(h, w)//360)
    sampled = frame[::step, ::step].max(axis=2)
    top, left, bottom, right = 0, 0, h, w

    def trim(black, length):
        content = np.flatnonzero(~black)
        if not len(content):
            return 0, length
        start = int(content[0])*step
        end = min(length, (int(content[-1])+1)*step)
        a, b = start, length-end
        # Darkness inside the world is not enough: require near-black, symmetric borders.
        if .02*length <= min(a, b) and max(a, b) <= .25*length and abs(a-b) <= max(step*2, .025*length):
            return start, end
        return 0, length

    top, bottom = trim((sampled <= 4).mean(axis=1) >= .998, h)
    inner = sampled[top//step:max(top//step+1, bottom//step)]
    left, right = trim((inner <= 4).mean(axis=0) >= .998, w)
    return left, top, right, bottom


def minimap_region(frame, minimap):
    if not minimap.get('enabled') or not minimap.get('bbox'):
        return None
    h, w = frame.shape[:2]
    left, top, right, bottom = game_viewport(frame) if minimap.get('mapping', {}).get('crop_to_viewport', True) else (0, 0, w, h)
    a, b, c, d = minimap['bbox']
    return (left+round(a*(right-left)/1000), top+round(b*(bottom-top)/1000),
            left+round(c*(right-left)/1000), top+round(d*(bottom-top)/1000))


def hud_region_bbox(frame, region):
    """Project a viewport-relative HUD box into normalized full-capture coordinates."""
    box=region.get('bbox')
    if not box or frame is None or region.get('coordinate_space')!='game_viewport':return box
    h,w=frame.shape[:2];a,b,c,d=game_viewport(frame)
    x1,y1,x2,y2=box
    return [(a+x1*(c-a)/1000)*1000/w,(b+y1*(d-b)/1000)*1000/h,
            (a+x2*(c-a)/1000)*1000/w,(b+y2*(d-b)/1000)*1000/h]

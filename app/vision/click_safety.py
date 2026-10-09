"""Normalized HUD and player exclusions derived from the current capture geometry."""
from app.vision.game_viewport import game_viewport, minimap_region, hud_region_bbox

def click_exclusions(profile, hud, navigation, shape, frame=None):
    h,w=shape[:2];regions=hud.get('regions',{});zones=[]
    def add(box):
        if not box or len(box)!=4:return
        x1,y1,x2,y2=[max(0,min(1,v/1000)) for v in box]
        if 0<(x2-x1)*(y2-y1)<.35:zones.append((x1,y1,x2,y2))
    for region in regions.values():
        if region.get('visible'):add(hud_region_bbox(frame,region))
    add(hud.get('hud_bbox'))
    if navigation.get('minimap',{}).get('enabled'):add(navigation['minimap'].get('bbox'))
    if frame is not None:
        region=minimap_region(frame,navigation.get('minimap',{}))
        if region:
            a,b,c,d=region;zones.append((a/w,b/h,c/w,d/h))
        a,b,c,d=game_viewport(frame)
        if b:zones.append((0,0,1,b/h))
        if d<h:zones.append((0,d/h,1,1))
        if a:zones.append((0,0,a/w,1))
        if c<w:zones.append((c/w,0,1,1))
    if profile=='diablo4':zones.extend(((0,.80,1,1),(.78,0,1,.28)))
    zones.extend(tuple(box) for box in navigation.get('excluded_regions',[]))
    health=regions.get('health',{})
    if health.get('tracked_stack') and health.get('visible') and health.get('bbox'):
        box=health['bbox'];cx=(box[0]+box[2])/2000;bw=(box[2]-box[0])*w/1000
        bottom=max((r['bbox'][3] for k,r in regions.items() if k in {'health','sp','mp'} and r.get('visible') and r.get('bbox')),default=box[3])/1000
        half=max(24,bw*.95)/w;height=max(100,bw*4)/h
        zones.append((max(0,cx-half),max(0,bottom-12/h),min(1,cx+half),min(1,bottom+height)))
    return zones

def point_outside(target,zones):
    if target is None:return True
    x,y=target
    return 0<=x<=1 and 0<=y<=1 and not any(x1<=x<=x2 and y1<=y<=y2 for x1,y1,x2,y2 in zones)

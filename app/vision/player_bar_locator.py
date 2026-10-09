"""Find the player's compact HP/SP/MP stack at native capture resolution."""
import itertools,math
from pathlib import Path
from functools import lru_cache
import cv2
import numpy as np

# Hue may move with game effects/capture colour processing. Geometry and
# three-row order remain mandatory evidence; colour alone never selects HP.
RANGES={'health':[[[92,40,30],[127,255,255]]],'sp':[[[145,40,30],[179,255,255]]],'mp':[[[112,40,30],[160,255,255]]]}

def _locate_strict(frame,previous=None):
    h,w=frame.shape[:2]
    if h<100 or w<150:return None
    # Player stays around screen centre. This excludes party meters and corner HUD/minimap.
    sx,sy,ex,ey=round(w*.25),round(h*.12),round(w*.75),round(h*.68)
    if previous:
        anchor=previous.get('health',{})
        old=anchor.get('pixel_bbox')
        if old and anchor.get('pixel_resolution')==[w,h]:
            pad_x=max(w*.04,(old[2]-old[0])*3);pad_y=max(h*.06,(old[3]-old[1])*8)
            sx,ex=max(sx,round(old[0]-pad_x)),min(ex,round(old[2]+pad_x))
            sy,ey=max(sy,round(old[1]-pad_y)),min(ey,round(old[3]+pad_y))
    crop=frame[sy:ey,sx:ex];hsv=cv2.cvtColor(crop,cv2.COLOR_BGR2HSV)
    candidates={}
    for kind,ranges in RANGES.items():
        mask=np.zeros(crop.shape[:2],np.uint8)
        for lo,hi in ranges:mask|=cv2.inRange(hsv,tuple(lo),tuple(hi))
        n,_,stats,_=cv2.connectedComponentsWithStats(mask)
        candidates[kind]=[(int(x+sx),int(y+sy),int(bw),int(bh)) for x,y,bw,bh,area in stats[1:]
                          if 2<=bh<=max(12,h*.015) and 2<=bw<=w*.12 and bw>=bh and area/(bw*bh)>.65]
    # A previously verified stack may have only one non-empty resource. Its
    # current coloured row still anchors all three independently verified rails.
    if previous:
        old=previous.get('health',{})
        box=old.get('pixel_bbox')
        if box and old.get('pixel_resolution')==[w,h]:
            step=box[3]-box[1]
            real={k:list(v) for k,v in candidates.items()}
            for kind,rows in real.items():
                for x,y,bw,bh in rows:
                    for other in real:
                        if kind!=other:
                            oy=round(y+step*(('health','sp','mp').index(other)-('health','sp','mp').index(kind)))
                            candidates[other].append((x,oy,bw,bh))
    groups=[]
    # Any two meters anchor the exact same framed stack; the third may genuinely be empty.
    kinds=('health','sp','mp')
    for ka,kb in itertools.combinations(kinds,2):
        for a in candidates[ka]:
            for b in candidates[kb]:
                ax,ay,aw,ah=a;bx,by,bw,bh=b
                step=(by-ay)/(kinds.index(kb)-kinds.index(ka))
                if abs(ax-bx)>max(2,ah*.5) or not max(4,ah*1.4)<=step<=ah*3 or abs(ah-bh)>2:continue
                y0=round(ay-step*kinds.index(ka));left=min(ax,bx)
                boxes=[]
                for i in range(3):
                    fy=round(y0+step*i)
                    if fy-1<0 or fy>=h:break
                    # Dark rim spans filled AND empty portions; avoid measuring only coloured width.
                    row=cv2.cvtColor(frame[fy-1:fy,left-4:min(w,left+round(step*20))],cv2.COLOR_BGR2HSV)[0,:,2]
                    seed=4
                    if row[seed]>45:break
                    l=seed;r=seed
                    while l>0 and row[l-1]<45:l-=1
                    while r+1<len(row) and row[r+1]<45:r+=1
                    width=r-l+1
                    if not step*4<=width<=step*14:break
                    x1=left-4+l-1;x2=left-4+r+2
                    top=fy-2;bottom=top+round(step)
                    if min(x1,top)<0 or x2>w or bottom>h:break
                    # Grey outer rail and dark rim are independent evidence of a real framed meter.
                    edge=frame[max(0,top+1):bottom-1,x1:x1+1].mean()
                    if not 35<=edge<=150:break
                    boxes.append((x1,top,x2,bottom))
                if len(boxes)!=3:continue
                widths=[x2-x1 for x1,y1,x2,y2 in boxes]
                if max(widths)-min(widths)>3:continue
                if max(b[0] for b in boxes)-min(b[0] for b in boxes)>2:continue
                # Require the expected colour or truly empty interior in every meter.
                valid=True
                for kind,(x1,y1,x2,y2) in zip(kinds,boxes):
                    interior=frame[y1+2:y2-2,x1+2:x2-2]
                    if not interior.size:valid=False;break
                    ih=cv2.cvtColor(interior,cv2.COLOR_BGR2HSV)
                    expected=cv2.inRange(ih,tuple(RANGES[kind][0][0]),tuple(RANGES[kind][0][1]))
                    if expected.any():continue
                    if np.mean(ih[:,:,2]<45)<.9:valid=False;break
                if not valid:continue
                cx=(boxes[0][0]+boxes[0][2])/2;cy=boxes[0][1]
                score=math.hypot((cx-w*.5)/w,(cy-h*.3)/h)
                if previous:
                    old=previous.get('health',{}).get('pixel_bbox')
                    if old:score+=math.hypot((cx-(old[0]+old[2])/2)/w,(cy-old[1])/h)*.5
                groups.append((score,boxes))
    if not groups:return _locate_strict(frame) if previous else None
    groups.sort(key=lambda g:g[0])
    if len(groups)>1 and abs(groups[1][0]-groups[0][0])<.015 and groups[1][1]!=groups[0][1]:return None
    boxes=groups[0][1]
    return {kind:{'visible':True,'bbox':[round(x1/w*1000,3),round(y1/h*1000,3),round(x2/w*1000,3),round(y2/h*1000,3)],
                  'pixel_bbox':[x1,y1,x2,y2],'pixel_resolution':[w,h],'confidence':.95,'shape':'bar','axis':'x','fill_from':'start',
                  'hsv_ranges':RANGES[kind],'inset_fraction':[.032,.25],'allow_empty':True,'tracked_stack':True}
            for kind,(x1,y1,x2,y2) in zip(kinds,boxes)}

@lru_cache(maxsize=48)
def _rail_template(width, height):
    """Cached frame-only evidence: the changing fill is deliberately masked out."""
    path=Path(__file__).resolve().parents[1]/'profiles/generic/assets/hud/player_bar_stack.png'
    ref=cv2.imread(str(path))
    if ref is None:return None,None
    ref=cv2.resize(ref,(width,height),interpolation=cv2.INTER_LINEAR)
    mask=np.zeros((height,width),np.uint8)
    edge=max(1,round(height/12))
    mask[:,:edge]=255;mask[:,-edge:]=255
    for row in (0,1,2):
        y=round(row*height/3);mask[y:y+edge]=255
    mask[-1:]=255
    return cv2.cvtColor(ref,cv2.COLOR_BGR2GRAY),mask


def _regions(boxes,w,h,confidence):
    return {kind:{'visible':True,'bbox':[round(x1/w*1000,3),round(y1/h*1000,3),round(x2/w*1000,3),round(y2/h*1000,3)],
            'pixel_bbox':[x1,y1,x2,y2],'pixel_resolution':[w,h],'confidence':confidence,
            'shape':'bar','axis':'x','fill_from':'start','hsv_ranges':RANGES[kind],
            'inset_fraction':[.032,.25],'allow_empty':True,'tracked_stack':True}
            for kind,(x1,y1,x2,y2) in zip(('health','sp','mp'),boxes)}


def _locate_soft(frame,previous=None):
    """Colour anchors plus masked rail correlation tolerate capture resampling.

    Templates only run in small candidate neighbourhoods, never across the full
    game image. All three current-frame colours/empty interiors must agree.
    """
    h,w=frame.shape[:2]
    if h<100 or w<150:return None
    sx,sy,ex,ey=round(w*.25),round(h*.12),round(w*.75),round(h*.68)
    crop=frame[sy:ey,sx:ex];hsv=cv2.cvtColor(crop,cv2.COLOR_BGR2HSV)
    kinds=('health','sp','mp');rows={}
    for kind in kinds:
        lo,hi=RANGES[kind][0];lo=[lo[0],30,20]
        mask=cv2.inRange(hsv,tuple(lo),tuple(hi))
        mask=cv2.morphologyEx(mask,cv2.MORPH_CLOSE,np.ones((1,3),np.uint8))
        _,_,stats,centres=cv2.connectedComponentsWithStats(mask)
        items=[]
        for (x,y,bw,bh,area),(cx,cy) in zip(stats[1:],centres[1:]):
            if 1<=bh<=max(18,h*.02) and 4<=bw<=w*.12 and bw>=bh*2.5 and area/(bw*bh)>.45:
                items.append((x+sx,cy+sy,bw,bh,area))
        rows[kind]=sorted(items,key=lambda v:v[4],reverse=True)[:32]
    proposals=[]
    for ka,kb in itertools.combinations(kinds,2):
        for a in rows[ka]:
            for b in rows[kb]:
                step=(b[1]-a[1])/(kinds.index(kb)-kinds.index(ka))
                if not 4<=step<=min(32,h*.03) or abs(a[0]-b[0])>max(3,step*.6):continue
                if not .6<=step/max(a[3],b[3])<=4:continue
                cy=a[1]-kinds.index(ka)*step
                proposals.append((min(a[0],b[0]),cy,step))
    # A game can start with SP and MP empty. One coloured meter plus all
    # three strongly matching rails is sufficient; a lone nameplate is not.
    for i,kind in enumerate(kinds):
        for x,cy,bw,bh,area in rows[kind][:6]:
            for factor in (1,1.33,1.6,2,2.4):
                step=bh*factor
                if 4<=step<=min(32,h*.03):proposals.append((x,cy-i*step,step))
    # Existing geometry also permits one or all temporarily empty resource bars;
    # it is only a search proposal and never a cached numeric reading.
    if previous:
        old=previous.get('health',{});b=old.get('pixel_bbox')
        if b and old.get('tracked_stack') and old.get('pixel_resolution')==[w,h]:
            step=b[3]-b[1];proposals.insert(0,(b[0]+2,b[1]+(step-1)/2,step))
            for i,kind in enumerate(kinds):
                proposals[:0]=[(x,cy-i*step,step) for x,cy,*_ in rows[kind]]
    found=[];seen=set()
    for left,cy,step in proposals[:96]:
        for scale,offset in itertools.product((.9,1,1.1),(-1,0,1)):
            th=max(12,round(step*3*scale));tw=round(th*63/24)+offset
            key=(round(left/2),round(cy/2),tw,th)
            if key in seen:continue
            seen.add(key)
            pad=max(3,round(step*.7))
            x1=max(0,round(left-2*step));x2=min(w,round(left+tw+2*step))
            y1=max(0,round(cy-step/2-pad));y2=min(h,round(cy-step/2+th+pad))
            region=frame[y1:y2,x1:x2]
            if region.shape[0]<th or region.shape[1]<tw:continue
            tpl,mask=_rail_template(tw,th)
            if tpl is None:continue
            response=cv2.matchTemplate(cv2.cvtColor(region,cv2.COLOR_BGR2GRAY),tpl,cv2.TM_CCOEFF_NORMED,mask=mask)
            response=np.nan_to_num(response,nan=-1,posinf=-1,neginf=-1)
            _,quality,_,pos=cv2.minMaxLoc(response)
            if quality<.70:continue
            bx,by=x1+pos[0],y1+pos[1]
            boxes=[(bx,by+round(i*th/3),bx+tw,by+round((i+1)*th/3)) for i in range(3)]
            if not _valid_stack(frame,boxes,previous,allow_single=quality>=.90):continue
            distance=math.hypot(((bx+tw/2)-w*.5)/w,(by-h*.3)/h)
            if previous:
                old=previous.get('health',{}).get('pixel_bbox')
                if old:distance+=math.hypot((bx-old[0])/w,(by-old[1])/h)
            found.append((quality-distance*.2,boxes))
    if not found:return None
    found.sort(reverse=True)
    if len(found)>1:
        # Competing scales of the same player are fine; separate equivalent
        # stacks are ambiguous and must not select another player's HP.
        best,other=found[0],found[1]
        if abs(best[0]-other[0])<.015 and abs(best[1][0][0]-other[1][0][0])>best[1][0][2]-best[1][0][0]:return None
    return _regions(found[0][1],w,h,min(.95,found[0][0]))


def _valid_stack(frame,boxes,previous=None,allow_single=False):
    x1,y1,x2,_=boxes[0];bottom=boxes[-1][3]
    stack=cv2.cvtColor(frame[y1:bottom,x1:x2],cv2.COLOR_BGR2HSV)
    if any(np.mean(stack[:,edge,1]<100)<.65 for edge in (0,-1)):return False
    evidence=[]
    for kind,(lx,ty,rx,bt) in zip(('health','sp','mp'),boxes):
        ix=max(1,round((rx-lx)*.035));iy=max(1,round((bt-ty)*.25))
        ih=cv2.cvtColor(frame[ty+iy:bt-iy,lx+ix:rx-ix],cv2.COLOR_BGR2HSV)
        if not ih.size:return False
        lo,hi=RANGES[kind][0]
        match=cv2.inRange(ih,(lo[0],30,20),tuple(hi))>0
        colour=match[:,:max(2,round((rx-lx)*.12))].mean()>.20
        empty=np.mean(ih[:,:,2]<70)>.85
        evidence.append((colour,empty))
    return all(c or e for c,e in evidence) and (bool(previous) or sum(c for c,e in evidence)>=2 or allow_single and sum(c for c,e in evidence)==1)


def _locate_tracked(frame,previous):
    old=(previous or {}).get('health',{});box=old.get('pixel_bbox')
    h,w=frame.shape[:2]
    if not box or not old.get('tracked_stack') or old.get('pixel_resolution')!=[w,h]:return None
    x,y,x2,y2=box;tw=x2-x;th=(y2-y)*3
    # Scaling can round the rows differently. Use the full verified extent.
    mp=(previous or {}).get('mp',{}).get('pixel_bbox')
    if mp:th=mp[3]-y
    pad=max(24,tw)
    sx,sy=max(0,x-pad),max(0,y-pad);ex,ey=min(w,x+tw+pad),min(h,y+th+pad)
    tpl,mask=_rail_template(tw,th)
    if tpl is None or ey-sy<th or ex-sx<tw:return None
    response=cv2.matchTemplate(cv2.cvtColor(frame[sy:ey,sx:ex],cv2.COLOR_BGR2GRAY),tpl,cv2.TM_CCOEFF_NORMED,mask=mask)
    response=np.nan_to_num(response,nan=-1,posinf=-1,neginf=-1)
    _,quality,_,pos=cv2.minMaxLoc(response)
    if quality<.75:return None
    bx,by=sx+pos[0],sy+pos[1]
    boxes=[(bx,by+round(i*th/3),bx+tw,by+round((i+1)*th/3)) for i in range(3)]
    if not _valid_stack(frame,boxes,previous):return None
    return _regions(boxes,w,h,min(.95,quality))


def locate_player_bars(frame,previous=None):
    return _locate_tracked(frame,previous) or _locate_strict(frame,previous) or _locate_soft(frame,previous)

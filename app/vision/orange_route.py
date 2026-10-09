"""Extract a thin orange minimap route, excluding compact markers and map borders."""
import heapq
import cv2
import numpy as np


def orange_route_mask(roi):
    hsv=cv2.cvtColor(roi,cv2.COLOR_BGR2HSV)
    mask=cv2.inRange(hsv,np.array([5,110,140]),np.array([35,255,255]))
    # D4 also renders the guide as a dim red/brown line on parchment maps.
    # Require red channel contrast so beige floor does not become a route.
    dark=cv2.inRange(hsv,np.array([0,75,45]),np.array([18,255,200]))
    b,g,r=cv2.split(roi.astype(np.int16))
    mask|=dark & (((r-g)>=20)&((r-b)>=25)).astype(np.uint8)*255
    # Downscaled guides can be desaturated. Local dark-line contrast separates
    # those pixels from flat beige/brown terrain without lowering all thresholds.
    weak=cv2.inRange(hsv,np.array([0,35,35]),np.array([25,255,220]))
    contrast=cv2.morphologyEx(hsv[:,:,2],cv2.MORPH_BLACKHAT,np.ones((9,9),np.uint8))
    weak &= (((r-g)>=14)&((r-b)>=22)&(contrast>=18)).astype(np.uint8)*255
    mask|=weak
    # A guide touching a brown rock must not be rejected as one thick component.
    solid=cv2.morphologyEx(mask,cv2.MORPH_OPEN,np.ones((11,11),np.uint8))
    mask &= 255-cv2.dilate(solid,np.ones((3,3),np.uint8))
    mask[:4]=0;mask[-4:]=0;mask[:,:4]=0;mask[:,-4:]=0
    mask=cv2.morphologyEx(mask,cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))
    count,labels,stats,_=cv2.connectedComponentsWithStats(mask)
    result=np.zeros_like(mask)
    for i in range(1,count):
        _,_,w,h,area=stats[i]
        if max(w,h)<18 or area<20:continue
        if max(w,h)/max(1,min(w,h))<3 and area/(w*h)>.4:continue
        component=(labels==i).astype(np.uint8)*255
        thickness=cv2.distanceTransform(component,cv2.DIST_L2,5)
        if float(thickness[component>0].mean())>2.8:continue
        result[component>0]=255
    return result


def route_terrain_image(roi, guide=None):
    """Remove guide pixels and their blended edges before terrain classification."""
    guide=orange_route_mask(roi) if guide is None else guide
    if not np.any(guide):return roi
    # Scaling/antialiasing leaves dim, desaturated edges outside the color mask.
    overlay=cv2.dilate(guide,np.ones((7,7),np.uint8))
    return cv2.inpaint(roi,overlay,3,cv2.INPAINT_TELEA)


def orange_route_target(mask,player,origin,visits,cell=4,destination=None):
    """Follow the remaining connected guide; the pin only breaks equal-length ties."""
    ys,xs=np.where(mask>0)
    if not len(xs):return None
    points=np.column_stack((xs,ys));near=np.linalg.norm(points-player,axis=1)
    index=int(near.argmin())
    if near[index]>32:return None
    start=tuple(points[index]);distance={start:0.};parent={};queue=[(0.,start)]
    while queue:
        cost,node=heapq.heappop(queue)
        if cost>distance[node]:continue
        for dx,dy in ((1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)):
            x,y=node[0]+dx,node[1]+dy
            if not (0<=y<mask.shape[0] and 0<=x<mask.shape[1]) or not mask[y,x]:continue
            value=cost+float(np.hypot(dx,dy))
            if value<distance.get((x,y),float('inf')):
                distance[(x,y)]=value;parent[(x,y)]=node;heapq.heappush(queue,(value,(x,y)))
    candidates=[p for p,d in distance.items() if d>=8 and np.linalg.norm(np.array(p)-player)>=6]
    if not candidates:return None
    local_destination=None if destination is None else np.asarray(destination)-origin
    def score(point):
        key=tuple(np.floor((origin+point)/cell).astype(int))
        pin_distance=0 if local_destination is None else np.linalg.norm(np.array(point)-local_destination)
        # Passed guide segments disappear in-game. The far end of the remaining
        # connected guide is authoritative; an old pin cannot shorten/reverse it.
        return (distance[point],-visits.get(key,0),-pin_distance)
    end=max(candidates,key=score)
    # The map planner checks bends and limits click distance independently.
    # Keep the connected guide's far end as the destination.
    return np.array(end,float)


def minimap_pin(roi,player):
    """Find a pale elongated pin with a wide head and narrow stem."""
    hsv=cv2.cvtColor(roi,cv2.COLOR_BGR2HSV)
    white=cv2.inRange(hsv,np.array([0,0,170]),np.array([179,45,255]))
    white[:2]=0;white[-2:]=0;white[:,:2]=0;white[:,-2:]=0
    n,labels,stats,_=cv2.connectedComponentsWithStats(white)
    candidates=[]
    for i in range(1,n):
        x,y,w,h,area=stats[i]
        if not 5<=area<=220 or not 7<=max(w,h)<=35:continue
        yy,xx=np.where(labels==i);points=np.column_stack((xx,yy)).astype(float)
        center=points.mean(0)
        if np.linalg.norm(center-player)<10:continue
        values,vectors=np.linalg.eigh(np.cov(points.T))
        if values[-1]/max(.2,values[0])<2.4:continue
        along=(points-center)@vectors[:,-1];across=(points-center)@vectors[:,0]
        cuts=np.linspace(along.min(),along.max()+.01,4);widths=[]
        for lo,hi in zip(cuts[:-1],cuts[1:]):
            band=across[(along>=lo)&(along<hi)]
            widths.append(float(np.ptp(band))+1 if len(band) else 0)
        head=max(widths[0],widths[-1]);tail=min(widths[0],widths[-1])
        if head<tail*1.25 or head<widths[1]*1.1:continue
        # The narrow stem tip anchors the destination on the map, not the icon head.
        end=along.min() if widths[0]<widths[-1] else along.max()
        tip=center+vectors[:,-1]*end
        candidates.append((head/max(1,tail),tip))
    return max(candidates,key=lambda item:item[0])[1] if candidates else None

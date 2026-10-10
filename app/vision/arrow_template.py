"""Rotation-tolerant silhouette image matching, independent of arrow colour."""
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from app.vision.game_viewport import game_viewport

TEMPLATE_PATH = Path(__file__).with_name('assets') / 'route_arrow.png'


@lru_cache(maxsize=1)
def white_contours():
    silhouette=cv2.imread(str(TEMPLATE_PATH.with_name('route_arrow_white.png')),0)
    bank=[]
    for kernel in (1,2,3):
        variant=cv2.dilate(silhouette,np.ones((kernel,kernel),np.uint8))
        for angle in range(0,360,15):
            rotated=cv2.warpAffine(variant,cv2.getRotationMatrix2D((48,48),angle,1),(96,96))
            found,_=cv2.findContours(cv2.inRange(rotated,100,255),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
            bank.append(max(found,key=cv2.contourArea))
    for name in ('route_arrow_white_levels.png','route_arrow_white_bent_levels.png'):
        levels=cv2.imread(str(TEMPLATE_PATH.with_name(name)),0)
        for angle in range(0,360,15):
            rotated=cv2.warpAffine(levels,cv2.getRotationMatrix2D((48,48),angle,1),(96,96))
            for threshold in (110,120,140,160,200):
                found,_=cv2.findContours(cv2.inRange(rotated,threshold,255),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
                for contour in found:
                    _,_,w,h=cv2.boundingRect(contour)
                    if max(w,h)>=25 and .05<cv2.contourArea(contour)/(w*h)<.55:bank.append(contour)
    return bank


def contour_vector(contour):
    x,y,w,h=cv2.boundingRect(contour)
    silhouette=np.zeros((h,w),np.uint8)
    cv2.drawContours(silhouette,[contour-np.array([[[x,y]]])],-1,255,-1)
    normalized=np.pad(cv2.resize(silhouette,(32,32)),2)
    edges=cv2.Canny(normalized,30,90)
    vector=cv2.GaussianBlur(edges,(3,3),.8).astype(np.float32).ravel()
    vector-=vector.mean()
    vector/=max(float(np.linalg.norm(vector)),1e-6)
    return vector


@lru_cache(maxsize=1)
def white_shape_vectors():
    return np.stack([contour_vector(c) for c in white_contours()])


def white_arrow_candidates(grey,left,top,origin,width,height,radius,excluded,diagnostics):
    candidates=[];scale=width/960
    for threshold in (110,120,140,160,200):
        contours,_=cv2.findContours(cv2.inRange(grey,threshold,255),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            x,y,w,h=cv2.boundingRect(contour)
            if not (10*scale<=w<=80*scale and 8*scale<=h<=80*scale
                    and max(w,h)>=18*scale and .35<w/h<3.5):continue
            if not .05<cv2.contourArea(contour)/(w*h)<.55:continue
            if int(grey[y:y+h,x:x+w].max())<200:continue
            center=np.array([left+x+w/2,top+y+h/2]);delta=center-origin
            distance=float(np.linalg.norm(delta));normalized=center/[width,height]
            if not 25*scale<distance<radius:continue
            if any(a<=normalized[0]<=c and b<=normalized[1]<=d for a,b,c,d in excluded):continue
            if diagnostics is not None:diagnostics['proposed']=diagnostics.get('proposed',0)+1
            score=float(np.clip(np.einsum('ij,j->i',white_shape_vectors(),contour_vector(contour)).max(),-1.,1.))
            if diagnostics is not None:
                diagnostics['best_score']=max(diagnostics['best_score'],score)
            if score<.65:continue
            if diagnostics is not None:
                diagnostics['candidates']+=1
                diagnostics['best_score']=max(diagnostics['best_score'],score)
                diagnostics['method']='white_contour'
            candidates.append((center,delta/distance,score))
    return candidates


@lru_cache(maxsize=1)
def templates():
    bank = []
    for path in (TEMPLATE_PATH,TEMPLATE_PATH.with_name('route_arrow_bent.png'),
                 TEMPLATE_PATH.with_name('route_arrow_actual.png'),
                 TEMPLATE_PATH.with_name('route_arrow_white.png')):
        silhouette = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if silhouette is None:raise RuntimeError(f'Arrow template missing: {path}')
        height, width = silhouette.shape
        variants = [silhouette,
                    cv2.erode(silhouette, np.ones((3, 3), np.uint8)),
                    cv2.dilate(silhouette, np.ones((3, 3), np.uint8))]
        for variant, shear in ((v, s) for v in variants for s in (-.15,0.,.15)):
            warped=cv2.warpAffine(variant,np.array([[1,shear,-shear*height/2],[0,1,0]],np.float32),(width,height))
            for angle in range(0, 360, 5):
                rotated = cv2.warpAffine(warped, cv2.getRotationMatrix2D(
                    (width / 2, height / 2), angle, 1), (width, height))
                ys, xs = np.nonzero(rotated > 100)
                crop = rotated[ys.min():ys.max()+1, xs.min():xs.max()+1]
                normalized = cv2.resize(crop, (48, 48))
                edge = cv2.Canny(np.pad(normalized, 4), 30, 90)
                vector=cv2.GaussianBlur(edge,(5,5),1).astype(np.float32).ravel()
                vector-=vector.mean()
                vector/=max(float(np.linalg.norm(vector)),1e-6)
                bank.append(vector)
    return np.stack(bank)


def match_arrow(frame, player=(.5, .5), excluded=(), previous_marker=None,
                preferred_heading=None,diagnostics=None,*,fast_only=False):
    height, width = frame.shape[:2]
    # Keep native detail around the player; each candidate is normalized for scale.
    origin = np.array(player) * [width, height]
    radius = min(600, max(300, width * .32))
    _, viewport_top, _, viewport_bottom = game_viewport(frame)
    left, top = np.maximum(origin-radius, [width*.06, height*.06]).astype(int)
    right, bottom = np.minimum(origin+radius,
        [width*.94, viewport_top+(viewport_bottom-viewport_top)*.8]).astype(int)
    if right <= left or bottom <= top:return None
    grey = cv2.cvtColor(frame[top:bottom, left:right], cv2.COLOR_BGR2GRAY)
    if diagnostics is not None:diagnostics.update(candidates=0,best_score=0.,method='template')
    if previous_marker is not None:
        px,py=np.asarray(previous_marker)*[width,height]-[left,top]
        margin=100*width/960
        x1,y1=np.maximum([px-margin,py-margin],[0,0]).astype(int)
        x2,y2=np.minimum([px+margin,py+margin],[grey.shape[1],grey.shape[0]]).astype(int)
        if x2>x1 and y2>y1:
            tracked=white_arrow_candidates(grey[y1:y2,x1:x2],left+x1,top+y1,
                origin,width,height,radius,excluded,diagnostics)
            if tracked:
                if diagnostics is not None:diagnostics['method']='white_tracking'
                return select_arrow(tracked,origin,width,height,previous_marker,preferred_heading,'white_contour_arrow')
    fast=white_arrow_candidates(grey,left,top,origin,width,height,radius,excluded,diagnostics)
    if fast:
        return select_arrow(fast,origin,width,height,previous_marker,preferred_heading,'white_contour_arrow')
    if fast_only:
        if diagnostics is not None:diagnostics['method']='white_search'
        return None
    edges = cv2.Canny(grey, 15, 40)
    closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(closed, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    components=[]
    # Black markers can be only a few pixels thick. Adaptive thresholding
    # breaks them where the ground is dark, while Canny joins ground texture
    # to their border. Propose intact dark silhouettes as well; accept them
    # only after the same rotated image comparison as every other candidate.
    for threshold in (24, 48, 72):
        mask=cv2.inRange(grey,0,threshold)
        found,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        components.extend((contour,True) for contour in found)
    for threshold in (160, 200, 230):
        mask=cv2.inRange(grey,threshold,255)
        found,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        components.extend((contour,True) for contour in found)
    # Local brightness contrast isolates the silhouette from textured ground.
    # It only proposes crops; a rotated template must still match their shape.
    for kind in (cv2.THRESH_BINARY_INV,cv2.THRESH_BINARY):
        mask=cv2.adaptiveThreshold(grey,255,cv2.ADAPTIVE_THRESH_GAUSSIAN_C,kind,31,12)
        found,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        components.extend((contour,True) for contour in found)
    components.extend((contour,False) for contour in contours)
    if diagnostics is not None:diagnostics.update(candidates=0,best_score=0.)
    candidates = []
    scale = width/960
    for contour,isolated in components:
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
        if isolated:
            crop=np.zeros((h,w),np.uint8)
            cv2.drawContours(crop,[contour-np.array([[[x,y]]])],-1,255,-1)
            if not .05<cv2.contourArea(contour)/(w*h)<.7:continue
            padded=np.pad(cv2.resize(crop,(48,48)),4)
        else:padded=np.pad(cv2.resize(grey[y:y+h,x:x+w],(48,48)),4,mode='edge')
        candidate = cv2.Canny(padded, 15, 40)
        candidate = cv2.GaussianBlur(candidate, (5, 5), 1)
        # Batched normalized template correlation avoids hundreds of OpenCV calls.
        vector=candidate.astype(np.float32).ravel();vector-=vector.mean()
        vector/=max(float(np.linalg.norm(vector)),1e-6)
        score=float(np.clip(np.einsum('ij,j->i',templates(),vector).max(), -1., 1.))
        if diagnostics is not None:
            diagnostics['candidates']+=1
            diagnostics['best_score']=max(diagnostics['best_score'],score)
        if not np.isfinite(score) or score < .65:continue
        candidates.append((center, delta/distance, score))
    return select_arrow(candidates,origin,width,height,previous_marker,preferred_heading,'template_arrow')


def select_arrow(candidates,origin,width,height,previous_marker,preferred_heading,source):
    scale=width/960
    if preferred_heading is not None:
        forward = [c for c in candidates if float((c[0]-origin)@preferred_heading)>0]
        if forward:candidates=forward
    if not candidates:return None
    best_score=max(c[2] for c in candidates)
    candidates=[c for c in candidates if c[2]>=best_score-.06]
    reference = np.array(previous_marker)*[width, height] if previous_marker is not None else origin
    if previous_marker is not None:
        nearby=[c for c in candidates if np.linalg.norm(c[0]-reference)<120*scale]
        if nearby:candidates=nearby
    point, heading, score = min(candidates, key=lambda c: np.linalg.norm(c[0]-reference)+60*scale*(1-c[2]))
    marker = tuple(float(v) for v in point/[width, height])
    return {'target':marker, 'marker':marker, 'arrow_tip':marker,
            'direction':tuple(heading), 'arrow_direction':tuple(heading),
            'source':source, 'match_score':score, 'dots':[]}

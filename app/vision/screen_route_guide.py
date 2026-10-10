"""Find a directional arrow and a chain of variable world-space breadcrumbs."""
import cv2
import numpy as np
from app.vision.arrow_template import match_arrow


def screen_route_guide(frame, player=(.5,.5), excluded=(), *, with_presence=False,arrow_only=False,previous_marker=None,preferred_heading=None,diagnostics=None,fast_only=False):
    if arrow_only:
        guide=match_arrow(frame,player,excluded,previous_marker,preferred_heading,diagnostics,fast_only=fast_only)
        return (guide,guide is not None,False) if with_presence else guide
    h,w=frame.shape[:2]
    width=min(w,960)
    image=cv2.resize(frame,(width,max(1,round(h*width/w))))
    ih,iw=image.shape[:2]
    hsv=cv2.cvtColor(image,cv2.COLOR_BGR2HSV)
    visible=np.zeros((ih,iw),np.uint8)
    visible[round(ih*.06):round(ih*.86),round(iw*.06):round(iw*.94)]=255
    for left,top,right,bottom in excluded:
        cv2.rectangle(visible,(round(left*iw),round(top*ih)),(round(right*iw),round(bottom*ih)),0,-1)
    # Breadcrumbs deform and fade with animation, perspective and combat effects.
    # Use neutral colour and local brightness instead of requiring a white circle.
    value=hsv[:,:,2].astype(np.float32)
    background=cv2.GaussianBlur(value,(31,31),0)
    white=np.where((hsv[:,:,1]<=80) & (value>=120)
                   & (value-background>=20),255,0).astype(np.uint8)
    white=cv2.morphologyEx(white,cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))
    # Black #000000; tolerate dark antialiasing/compression at the edges.
    black=cv2.inRange(hsv,(0,0,0),(179,255,45))
    scale=iw/960
    origin=np.array([player[0]*iw,player[1]*ih])
    markers=[];dots=[];marker_boxes=[];arrow_tips={}
    for mask,kind in ((black,'arrow'),(white,'dot')):
        if arrow_only and kind=='dot':continue
        mask=cv2.bitwise_and(mask,visible)
        contours,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            x,y,cw,ch=cv2.boundingRect(contour)
            area=cv2.contourArea(contour)
            perimeter=cv2.arcLength(contour,True)
            if not perimeter or not area:continue
            point=np.array([x+cw/2,y+ch/2])
            distance=float(np.linalg.norm(point-origin))
            if kind!='dot':
                hull_area=cv2.contourArea(cv2.convexHull(contour))
                if (12*scale<=cw<=70*scale and 8*scale<=ch<=70*scale
                        and max(cw,ch)>=25*scale
                        and .4<cw/ch<3.5 and .08<area/(cw*ch)<.8
                        and hull_area and area/hull_area<.9 and 25*scale<distance<300*scale):
                    heading=point-origin
                    if kind=='arrow' and not arrow_only:
                        # The concave notch points from the wing opening toward
                        # the tip. Triangle edge lengths cannot determine polarity.
                        try:
                            hull=cv2.convexHull(contour,returnPoints=False)
                            if hull is None or len(hull)<3:continue
                            defects=cv2.convexityDefects(contour,hull)
                        except cv2.error:
                            # Effects can produce self-intersecting contours.
                            # Reject only this candidate, never the movement worker.
                            continue
                        if defects is None or not defects.size:continue
                        start,end,notch,depth=max(defects.reshape(-1,4),key=lambda d:d[3])
                        if depth/256<2*scale:continue
                        vertices=contour.reshape(-1,2).astype(float)
                        heading=vertices[notch]-(vertices[start]+vertices[end])/2
                    length=np.linalg.norm(heading)
                    if length<=1e-6:continue
                    heading=heading/length
                    vertices=contour.reshape(-1,2).astype(float)
                    projections=vertices@heading
                    tip=vertices[projections>=projections.max()-1].mean(axis=0)
                    if arrow_only:tip=point  # Position only; shape polarity is irrelevant.
                    arrow_tips[tuple(point)]=(tip,heading)
                    markers.append((point,heading,kind))
                    marker_boxes.append((x,y,x+cw,y+ch))
            elif (2*scale<=cw<=24*scale and 2*scale<=ch<=24*scale
                  and .25<cw/ch<4 and area>=5*scale*scale
                  and area/(cw*ch)>.16 and distance>18*scale):
                if any(left<=point[0]<=right and top<=point[1]<=bottom
                       for left,top,right,bottom in marker_boxes):continue
                moments=cv2.moments(contour)
                dots.append(np.array([moments['m10']/moments['m00'],
                                      moments['m01']/moments['m00']]))
    if arrow_only:
        guide=None
        if markers:
            if preferred_heading is not None:
                forward=[m for m in markers if float((m[0]-origin)@np.asarray(preferred_heading))>0]
                if forward:markers=forward
            reference=np.array(previous_marker)*np.array([iw,ih]) if previous_marker is not None else origin
            marker,heading,_=min(markers,key=lambda m:np.linalg.norm(m[0]-reference))
            tip,heading=arrow_tips[tuple(marker)]
            guide={'target':tuple(float(v) for v in tip/np.array([iw,ih])),
                   'direction':tuple(float(v) for v in heading),'arrow_direction':tuple(float(v) for v in heading),
                   'arrow_tip':tuple(float(v) for v in tip/np.array([iw,ih])),
                   'marker':tuple(float(v) for v in marker/np.array([iw,ih])),
                   'source':'black_arrow','dots':[]}
        return (guide,bool(markers),False) if with_presence else guide
    best=None;best_score=None
    white_markers=[m for m in markers if m[2]=='arrow']
    preferred_markers=white_markers
    # Without an arrow, connected breadcrumbs supply the direction themselves.
    candidates=preferred_markers or [(None,(p-origin)/np.linalg.norm(p-origin),'dots') for p in dots]
    for marker,heading,kind in candidates:
        forward=[p for p in dots if float((p-origin)@heading)>-10*scale]
        for first in forward:
            if np.linalg.norm(first-origin)>220*scale:continue
            chain=[first];remaining=[p for p in forward if p is not first]
            while remaining:
                candidates=[p for p in remaining if 12*scale<np.linalg.norm(p-chain[-1])<180*scale
                            and float((p-chain[-1])@heading)>5*scale]
                if not candidates:break
                next_point=min(candidates,key=lambda p:np.linalg.norm(p-chain[-1]))
                chain.append(next_point)
                remaining=[p for p in remaining if p is not next_point]
            if len(chain)<2 and marker is None:continue
            route=chain[-1]-origin
            alignment=float(route@heading)/max(1,float(np.linalg.norm(route)))
            if alignment<.5:continue
            # Only consider a connected chain consistent with the arrow direction.
            # Its outermost breadcrumb becomes the planned destination.
            aligned=[p for p in chain if float((p-origin)@heading)/max(1,float(np.linalg.norm(p-origin)))>=.5]
            if not aligned:continue
            edge=lambda p:min(p[0]/iw,1-p[0]/iw,p[1]/ih,1-p[1]/ih)
            target=min(aligned,key=edge)
            score=(-edge(target),min(len(chain),5),alignment)
            if best_score is None or score>best_score:
                direction=target-origin;direction/=np.linalg.norm(direction)
                best={'target':(float(target[0]/iw),float(target[1]/ih)),
                      'direction':tuple(float(v) for v in direction),
                      'marker':tuple(float(v) for v in marker/np.array([iw,ih])) if marker is not None else None,
                      'source':'black_arrow' if kind=='arrow' else 'white_dots',
                      'dots':[tuple(float(v) for v in p/np.array([iw,ih])) for p in chain]}
                best_score=score
    if best is None and preferred_markers:
        marker,heading,kind=min(preferred_markers,key=lambda m:np.linalg.norm(m[0]-origin))
        target=origin+heading*min(iw,ih)*.25
        best={'target':tuple(float(v) for v in target/np.array([iw,ih])),
              'direction':tuple(float(v) for v in heading),
              'marker':tuple(float(v) for v in marker/np.array([iw,ih])),
              'source':'black_arrow','dots':[]}
    elif best is None and dots:
        target=min(dots,key=lambda p:min(p[0]/iw,1-p[0]/iw,p[1]/ih,1-p[1]/ih))
        heading=target-origin;heading/=np.linalg.norm(heading)
        best={'target':tuple(float(v) for v in target/np.array([iw,ih])),
              'direction':tuple(float(v) for v in heading),'marker':None,
              'source':'white_dots','dots':[tuple(float(v) for v in target/np.array([iw,ih]))]}
    if best is not None and best['source']=='black_arrow':
        marker=np.array(best['marker'])*np.array([iw,ih])
        key=min(arrow_tips,key=lambda p:np.linalg.norm(np.array(p)-marker))
        tip,heading=arrow_tips[key]
        best['arrow_tip']=tuple(float(v) for v in tip/np.array([iw,ih]))
        best['arrow_direction']=tuple(float(v) for v in heading)
    return (best,bool(markers),bool(dots)) if with_presence else best

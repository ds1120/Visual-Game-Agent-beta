"""Detect the grey Space keycap with its dark U-shaped spacebar glyph."""
import cv2
import numpy as np
from app.vision.game_viewport import game_viewport


def space_prompt(frame,excluded=()):
    h,w=frame.shape[:2]
    # Keep native pixels: downscaling a small keycap erases the U glyph.
    iw,ih=w,h
    image=frame
    hsv=cv2.cvtColor(image,cv2.COLOR_BGR2HSV)
    grey=cv2.inRange(hsv,(0,0,95),(179,80,255))
    visible=np.zeros((ih,iw),np.uint8)
    # The bottom HUD contains a permanent evade keycap; it is never a prompt.
    left,top,right,bottom=game_viewport(frame)
    vw,vh=right-left,bottom-top
    visible[top+round(.08*vh):top+round(.78*vh),
            left+round(.08*vw):left+round(.92*vw)]=255
    for left,top,right,bottom in excluded:
        cv2.rectangle(visible,(round(left*iw),round(top*ih)),(round(right*iw),round(bottom*ih)),0,-1)
    contours,_=cv2.findContours(cv2.bitwise_and(grey,visible),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    candidates=[]
    for contour in contours:
        x,y,cw,ch=cv2.boundingRect(contour)
        if not (12<=cw<=120 and 7<=ch<=68
                and 1.25<cw/ch<3 and cv2.contourArea(contour)/(cw*ch)>.55):continue
        crop=hsv[y+max(1,round(ch*.2)):y+ch-max(1,round(ch*.12)),
                 x+max(1,round(cw*.15)):x+cw-max(1,round(cw*.15)),2]
        if crop.size<12:continue
        dark=(crop<100).astype(np.uint8)
        ys,xs=np.nonzero(dark)
        if len(xs)<5:continue
        glyph=dark[ys.min():ys.max()+1,xs.min():xs.max()+1]
        gh,gw=glyph.shape
        if gh<2 or gw<6 or not 1.3<gw/gh<7:continue
        side=max(1,gw//5);bottom=max(1,gh//3)
        if (glyph[-bottom:,:].mean()>.5 and glyph[:max(1,gh//2),side:-side].mean()<.35
                and glyph[:,:side].mean()>.35 and glyph[:,-side:].mean()>.35):
            candidates.append((x/iw,y/ih,(x+cw)/iw,(y+ch)/ih))
    return min(candidates,key=lambda box:abs((box[0]+box[2])/2-.5)+abs((box[1]+box[3])/2-.5)) if candidates else None

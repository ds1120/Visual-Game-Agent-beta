"""Remove compact, outlined minimap symbols before classifying terrain."""
import cv2
import numpy as np


def entity_icon_mask(image):
    scale=image.shape[1]/192
    hsv=cv2.cvtColor(image,cv2.COLOR_BGR2HSV)
    pale=(hsv[:,:,1]<=55)&(hsv[:,:,2]>=150)
    colored=(hsv[:,:,1]>=140)&(hsv[:,:,2]>=150)
    seeds=(pale|colored).astype(np.uint8)
    n,labels,stats,_=cv2.connectedComponentsWithStats(seeds)
    result=np.zeros(image.shape[:2],np.uint8)
    pad=max(2,round(4*scale));height,width=result.shape
    for i in range(1,n):
        x,y,w,h,area=stats[i]
        if not 2*scale*scale<=area<=260*scale*scale or not 3*scale<=max(w,h)<=24*scale:continue
        if min(w,h)<2*scale:continue
        a,b,c,d=max(0,x-pad),max(0,y-pad),min(width,x+w+pad),min(height,y+h+pad)
        patch=hsv[b:d,a:c,2]
        # Compact bright regions alone may be ordinary floor texture. Require
        # the dark outline characteristic of player/NPC map symbols.
        dark=patch<min(110,max(45,float(np.median(patch))*.6))
        if dark.sum()<max(4,round(4*scale*scale)):continue
        result[b:d,a:c]=255
    return result


def remove_entity_icons(image, mask=None):
    mask=entity_icon_mask(image) if mask is None else mask
    if not np.any(mask):return image
    return cv2.inpaint(image,mask,max(2,round(3*image.shape[1]/192)),cv2.INPAINT_TELEA)

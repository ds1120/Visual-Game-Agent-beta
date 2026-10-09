"""Movement progress from configured minimap; only successful MOVE reports count."""
import time,math
from collections import deque
import cv2
import numpy as np
from app.vision.game_viewport import minimap_region
class NavigationMonitor:
    def __init__(self):self.reset()
    def reset(self):
        self.moves=deque(maxlen=60);self.previous=None;self.still_since=None
        self.last_recovery=-1e9;self.failed_direction=None;self.stuck=False;self.last_change=None
    def on_input(self,c,status,error=None):
        if c.action_type=='MOVE' and status=='sent':self.moves.append((time.monotonic(),c.direction))
    def update(self,frame,settings,now=None):
        now=time.monotonic() if now is None else now;m=settings['minimap']
        if not m['enabled'] or not m.get('bbox'):self.reset();return None
        x1,y1,x2,y2=minimap_region(frame,m);roi=frame[y1:y2,x1:x2]
        if roi.size==0:return None
        player=list(m['player']);ranges=m.get('player_hsv',[])
        if ranges:
            hsv=cv2.cvtColor(roi,cv2.COLOR_BGR2HSV);mask=np.zeros(roi.shape[:2],np.uint8)
            for lo,hi in ranges:mask|=cv2.inRange(hsv,tuple(lo),tuple(hi))
            n,labels,stats,centers=cv2.connectedComponentsWithStats(mask)
            candidates=[(x/roi.shape[1],y/roi.shape[0]) for i,(x,y) in enumerate(centers) if i and 2<=stats[i,4]<=roi.size/60 and .1<=x/roi.shape[1]<=.9 and .1<=y/roi.shape[0]<=.9]
            if candidates:player=list(min(candidates,key=lambda p:math.hypot(p[0]-m['player'][0],p[1]-m['player'][1])))
        gray=cv2.cvtColor(cv2.resize(roi,(80,80)),cv2.COLOR_BGR2GRAY)
        # Exclude a player marker for scrolling maps; a moving marker still counts via player position.
        compare=gray.copy();px,py=m['player'];cv2.circle(compare,(round(px*80),round(py*80)),4,0,-1)
        change=None if self.previous is None else float(cv2.absdiff(compare,self.previous[0]).mean())
        moved=self.previous is not None and math.hypot(player[0]-self.previous[1][0],player[1]-self.previous[1][1])>.012
        self.last_change=change;self.previous=(compare,player)
        if change is None or change>m.get('stuck_threshold',1.5) or moved:self.still_since=now
        if self.still_since is None:self.still_since=now
        attempts=[v for v in self.moves if now-v[0]<4]
        self.stuck=len(attempts)>=4 and now-self.still_since>=m.get('stuck_seconds',3)
        if self.stuck and now-self.last_recovery>=m.get('stuck_seconds',3):
            self.failed_direction=attempts[-1][1];self.last_recovery=now;self.still_since=now
        # Improved error handling - return current player even if we lose some tracking
        if change is None:
            # If we can't detect change, still return current player position
            return player
        return player
    def snapshot(self):return {'stuck':self.stuck,'change':self.last_change,'failed_direction':self.failed_direction,'attempts':len(self.moves)}

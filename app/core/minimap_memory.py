"""Small scrolling minimap, session-local occupancy and verified movement history."""
from collections import deque
import heapq
import math
import threading
import time
import cv2
import numpy as np
from app.vision.game_viewport import minimap_region
from app.vision.orange_route import orange_route_mask, orange_route_target, minimap_pin, route_terrain_image
from app.vision.minimap_overlays import entity_icon_mask, remove_entity_icons
from app.core.route_geometry import corridor_clear, shortcut_cost, supercover_cells


class MinimapMemory:
    CELL = 4
    registration_reset_seconds = 30.

    def __init__(self):
        self.lock = threading.RLock()
        self.reset()

    def reset(self, preserve_atlas=False):
        with self.lock:
            if not preserve_atlas:self.archives=deque(maxlen=6)
            elif getattr(self,'cells',None):
                self.archives.append({'segment':self.segment,'cells':self.cells.copy(),'visits':self.visits.copy()})
            self.keyframes=deque(maxlen=16)
            self.last_correction=0
            self.atlas_cache=None;self.atlas_cache_at=-1e9
            self.cells = {}
            self.visits = {}
            self.travel_edges = set()
            self.completed_goals = deque(maxlen=32)
            self.screen_obstacles=[]
            self.failures = deque(maxlen=32)
            self.pending = deque(maxlen=20)
            self.origin = np.zeros(2, float)
            self.previous = None
            self.registration_failed_at = None
            self.position = None
            self.recorded_position = None
            self.mask = None
            self.orange_mask = None
            self.pin_world = None
            self.pin_candidate = None
            self.grid = None
            self.route = []
            self.clearance = None
            self.breadcrumbs = deque(maxlen=128)
            self.recovery_count = 0
            self.recovery_stage = 'none'
            self.last_failure_at = -1e9
            self.goal = None
            self.replan_goal = None
            self.goal_heading = None
            self.goal_until = 0
            self.blocked_goal_heading = None
            self.replan_requested = False
            self.valid = False
            self.reason = 'waiting'
            self.walkable_ratio = 0.
            self.player_walkable = False
            self.registration = 'new'
            self.segment = getattr(self, 'segment', 0) + 1
            self.last_update = 0
            self.last_plan = 0
            self.signature = None
            self.last_direction = None
            self.last_lookahead = .05
            self.stuck = False
            self.movement = 0.
            self.player_floor_sample = None

    def _floor_player(self, mask, player, now):
        """Recover a small marker/configuration offset without opening wall cells."""
        h,w=mask.shape
        grid=(mask[:h//self.CELL*self.CELL,:w//self.CELL*self.CELL]
              .reshape(h//self.CELL,self.CELL,w//self.CELL,self.CELL).mean(axis=(1,3))>=160)
        point=np.asarray(player)*[w,h]
        x,y=np.floor(point/self.CELL).astype(int)
        if not (0<=y<grid.shape[0] and 0<=x<grid.shape[1]):return player
        if grid[y,x]:
            self.player_floor_sample=None
            return player
        ys,xs=np.nonzero(grid)
        if not len(xs):return player
        centers=(np.column_stack((xs,ys))+.5)*self.CELL
        distances=np.linalg.norm(centers-point,axis=1)
        index=int(np.argmin(distances))
        if distances[index]>self.CELL*2:
            self.player_floor_sample=None
            return player
        key=(int(xs[index])-x,int(ys[index])-y)
        previous=self.player_floor_sample
        if previous is None or previous[0]!=key:
            self.player_floor_sample=(key,now)
            return player
        if now-previous[1]<.08:return player
        return (centers[index]/[w,h]).tolist()

    def suspend(self, reason=None):
        with self.lock:
            self.pending.clear()
            self.stuck = False
            if reason:
                self.valid = False
                self.route = []
                self.reason = reason

    def resume(self):
        with self.lock:
            self.suspend()
            self.recovery_stage='none';self.recovery_count=0;self.goal=None

    def request_replan(self):
        with self.lock:
            if self.goal is not None:self.replan_goal=self.goal.copy()
            if self.goal is not None and self.position is not None:
                # Failed destinations must not be selected again immediately.
                self.completed_goals.append(self.goal.copy())
                delta=self.goal-self.position;length=float(np.linalg.norm(delta))
                if length>1e-6:self.blocked_goal_heading=delta/length
            self.goal=None;self.goal_heading=None;self.route=[]
            self.pending.clear();self.recovery_stage='none';self.recovery_count=0
            self.replan_requested=True

    @staticmethod
    def _crop(frame, settings, width=192):
        m = settings['minimap']
        if not m['enabled'] or not m.get('bbox'):return None
        a,b,c,d = minimap_region(frame,m)
        roi = frame[b:d,a:c]
        if roi.size == 0:return None
        return cv2.resize(roi, (width, max(24, round(width*roi.shape[0]/roi.shape[1]))), interpolation=cv2.INTER_AREA)

    @staticmethod
    def _pin_crop(frame, settings, width=192):
        m=settings['minimap']
        region=minimap_region(frame,m)
        if region is None:return None
        a,b,c,d=region
        margin=m.get('mapping',{}).get('pin_search_margin',0)
        pad=round((c-a)*margin)
        h,w=frame.shape[:2]
        left,top,right,bottom=max(0,a-pad),max(0,b-pad),min(w,c+pad),min(h,d+pad)
        if right<=left or bottom<=top or c<=a:return None
        scale=width/(c-a)
        crop=cv2.resize(frame[top:bottom,left:right],(max(1,round((right-left)*scale)),max(1,round((bottom-top)*scale))),interpolation=cv2.INTER_AREA)
        offset=np.array([a-left,b-top],float)*scale
        player=offset+np.array([m['player'][0]*width,m['player'][1]*(d-b)*scale])
        return crop,offset,player,(left,top,right,bottom)

    @staticmethod
    def _analysis_width(frame, settings):
        a,b,c,d=minimap_region(frame,settings['minimap'])
        requested=settings['minimap'].get('mapping',{}).get('analysis_width',192)
        return min(requested,max(192,c-a))

    @staticmethod
    def _frame_terrain(frame, settings, roi=None):
        """Classify at capture detail, then return canonical movement coordinates."""
        roi=MinimapMemory._crop(frame,settings) if roi is None else roi
        width=MinimapMemory._analysis_width(frame,settings)
        image=roi if width==192 else MinimapMemory._crop(frame,settings,width)
        guide_native=orange_route_mask(image)
        guide=guide_native if width==192 else (cv2.resize(guide_native,(roi.shape[1],roi.shape[0]),interpolation=cv2.INTER_AREA)>0).astype(np.uint8)*255
        m=settings['minimap'];mapping=m.get('mapping',{})
        local={**m,'mapping':{**mapping,'pixel_scale':width/192}}
        overlay=mapping.get('mode','bright_floor') in {'bright_floor','diablo4_auto'}
        clean=route_terrain_image(image,guide_native) if overlay else image
        if mapping.get('exclude_entity_icons',mapping.get('mode')=='diablo4_auto'):
            clean=remove_entity_icons(clean,entity_icon_mask(image))
        mask,valid=MinimapMemory._terrain(clean,local)
        if width!=192:mask=(cv2.resize(mask,(roi.shape[1],roi.shape[0]),interpolation=cv2.INTER_AREA)>=128).astype(np.uint8)*255
        support=np.zeros_like(mask)
        if overlay and valid:mask,support=MinimapMemory._guide_floor(mask,guide,m['player'])
        return mask,valid,guide,support,width

    @staticmethod
    def _dungeon_terrain(roi, m):
        """Recover dim filled rooms; brightness alone must not mark the entire map free."""
        scale=m.get('mapping',{}).get('pixel_scale',1)
        radius=max(1,round(12*scale));halo=max(1,round(9*scale))
        hsv=cv2.cvtColor(roi,cv2.COLOR_BGR2HSV)
        v=cv2.medianBlur(hsv[:,:,2],3)
        terrain=v[v<180]
        if terrain.size<.5*v.size:return np.zeros(v.shape,np.uint8),False
        contrast=float(np.percentile(terrain,95)-np.percentile(terrain,10))
        if contrast<6:return np.zeros(v.shape,np.uint8),False
        threshold,_=cv2.threshold(v,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)
        h,w=v.shape;px,py=m['player'];x=min(w-1,round(px*w));y=min(h-1,round(py*h))
        patch=v[max(0,y-radius):min(h,y+radius+1),max(0,x-radius):min(w,x+radius+1)]
        # Bright quest/player icons can dominate Otsu in a dim, low-contrast map.
        # The surrounding floor is the local reference, not the brightest icon.
        local=float(np.percentile(patch,40))
        threshold=max(25,min(100,threshold,local-max(4,contrast*.15)))
        walk=((v>threshold)&(hsv[:,:,1]<155)).astype(np.uint8)*255
        walk=cv2.morphologyEx(walk,cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))
        h,w=walk.shape;px,py=m['player'];x=min(w-1,round(px*w));y=min(h-1,round(py*h))
        # Fill the player marker only when an independently visible floor surrounds it.
        if (walk[max(0,y-halo):y+halo+1,max(0,x-halo):x+halo+1]>0).mean()<.3:return np.zeros(v.shape,np.uint8),False
        cv2.circle(walk,(x,y),max(1,round(5*scale)),255,-1)
        margin=round(m.get('mapping',{}).get('wall_margin_px',1)*scale)
        if margin:walk=cv2.erode(walk,np.ones((2*margin+1,2*margin+1),np.uint8))
        walk[:2]=0;walk[-2:]=0;walk[:,:2]=0;walk[:,-2:]=0
        _,labels,stats,_=cv2.connectedComponentsWithStats(walk)
        label=labels[y,x]
        if not label:return np.zeros(v.shape,np.uint8),False
        area=stats[label,cv2.CC_STAT_AREA];ratio=area/(h*w)
        if area<80*scale*scale or not .025<ratio<.94:return np.zeros(v.shape,np.uint8),False
        return (labels==label).astype(np.uint8)*255,True

    @staticmethod
    def _terrain(roi, m):
        if m.get('mapping',{}).get('calibrated') is False:
            return np.zeros(roi.shape[:2],np.uint8),False
        mode=m.get('mapping',{}).get('mode','bright_floor')
        if mode=='diablo4_auto':
            # Very dim filled maps need dungeon connectivity, not outdoor islands.
            if np.percentile(cv2.cvtColor(roi,cv2.COLOR_BGR2HSV)[:,:,2],90)<100:
                return MinimapMemory._dungeon_terrain(roi,m)
            outdoor={**m,'mapping':{**m.get('mapping',{}),'mode':'bright_floor','adaptive_threshold':True}}
            walk,valid=MinimapMemory._terrain(roi,outdoor)
            h,w=walk.shape;px,py=m['player'];x=min(w-1,round(px*w));y=min(h-1,round(py*h))
            if valid and walk[y,x]:return walk,True
            return MinimapMemory._dungeon_terrain(roi,m)
        if mode in {'hsv','wall_lines'}:
            hsv=cv2.cvtColor(roi,cv2.COLOR_BGR2HSV)
            mask=np.zeros(roi.shape[:2],np.uint8)
            ranges=m.get('wall_hsv',[]) if mode=='wall_lines' else m['walkable_hsv']
            for lo,hi in ranges:mask|=cv2.inRange(hsv,tuple(lo),tuple(hi))
            if mode=='wall_lines':
                # Transparent automaps require calibrated line colors. Never treat the game image as a floor map.
                if not m.get('mapping',{}).get('calibrated',False) or not ranges:return mask,False
                ratio=(mask>0).mean()
                if not .01<ratio<.40:return mask,False
                walls=cv2.dilate(mask,np.ones((3,3),np.uint8))
                free=255-walls
                px,py=m['player'];sx,sy=int(px*free.shape[1]),int(py*free.shape[0])
                n,labels,stats,_=cv2.connectedComponentsWithStats(free)
                label=labels[sy,sx]
                # Only a bounded component enclosed by calibrated map lines is known traversable.
                if not label:return free,False
                x,y,w,h,area=stats[label]
                if x==0 or y==0 or x+w>=free.shape[1] or y+h>=free.shape[0]:return free,False
                return (labels==label).astype(np.uint8)*255,area>=40
            return mask,.03<(mask>0).mean()<.97
        scale=m.get('mapping',{}).get('pixel_scale',1);halo=max(1,round(9*scale))
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        v = cv2.medianBlur(hsv[:,:,2], 5)
        # Beige paths and dark terrain in Diablo IV; reject flat/no-map captures.
        contrast = float(np.percentile(v,85)-np.percentile(v,15))
        classified=v
        if m.get('mapping',{}).get('adaptive_threshold',False):
            # Estimate the parchment illumination from the brighter floor
            # before separating terrain. Global brightness alone confuses a shaded
            # floor with a brighter rock elsewhere on the same minimap.
            # Lighting is smooth: estimate it at low resolution while retaining
            # the full-resolution terrain pixels for the final classification.
            coarse=cv2.resize(v,(96,max(12,round(96*v.shape[0]/v.shape[1]))),interpolation=cv2.INTER_AREA)
            # Fit the upper brightness surface instead of following every dark
            # region. A broad rock must not become its own lighting reference.
            yy,xx=np.mgrid[-1:1:complex(0,coarse.shape[0]),-1:1:complex(0,coarse.shape[1])]
            design=np.column_stack((np.ones(coarse.size),xx.ravel(),yy.ravel()))
            values=coarse.ravel().astype(float)
            selected=values<=np.percentile(values,98)
            surface=np.full(coarse.size,float(np.median(values)))
            for _ in range(6):
                if selected.sum()<coarse.size*.06:break
                coefficients=np.linalg.lstsq(design[selected],values[selected],rcond=None)[0]
                surface=design@coefficients
                residual=values-surface
                selected=(residual>=-3)&(residual<=max(3,float(np.percentile(residual,97))))
            background=np.clip(surface.reshape(coarse.shape),1,255).astype(np.float32)
            background=cv2.resize(background,(v.shape[1],v.shape[0]),interpolation=cv2.INTER_LINEAR)
            classified=np.clip(v.astype(np.float32)*170/np.maximum(background,1),0,255).astype(np.uint8)
        threshold,_ = cv2.threshold(classified,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)
        if m.get('mapping',{}).get('adaptive_threshold',False):
            threshold=max(25,min(190,threshold))
        else:threshold=max(m.get('dark_floor',85),min(190,threshold))
        walk = ((classified > threshold) & (hsv[:,:,1] < 155)).astype(np.uint8)*255
        walk = cv2.morphologyEx(walk,cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))
        # Fill tiny icon holes, while retaining large rocks and terrain islands.
        n,labels,stats,_ = cv2.connectedComponentsWithStats(255-walk)
        for i in range(1,n):
            x,y,w,h,area = stats[i]
            if area <= 36*scale*scale and x>0 and y>0 and x+w<walk.shape[1] and y+h<walk.shape[0]:walk[labels==i]=255
        px,py = m['player'];x,y=round(px*walk.shape[1]),round(py*walk.shape[0])
        # The central character symbol is an overlay, not a solid wall.
        if (walk[max(0,y-halo):y+halo+1,max(0,x-halo):x+halo+1]>0).mean()>.3:
            cv2.circle(walk,(x,y),max(1,round(5*scale)),255,-1)
        margin=round(m.get('mapping',{}).get('wall_margin_px',1)*scale)
        if margin:walk=cv2.erode(walk,np.ones((2*margin+1,2*margin+1),np.uint8))
        walk[:2]=0;walk[-2:]=0;walk[:,:2]=0;walk[:,-2:]=0
        ratio=(walk>0).mean()
        return walk, contrast>=16 and .06<ratio<.94

    @staticmethod
    def _guide_floor(mask, guide, player):
        """Repair short false wall gaps along a guide supported by nearby floor."""
        repaired=mask.copy();supported=np.zeros_like(mask)
        ys,xs=np.where(guide>0)
        if not len(xs):return repaired,supported
        h,w=mask.shape;position=np.array(player)*[w,h]
        i=int(np.argmin(np.linalg.norm(np.column_stack((xs,ys))-position,axis=1)))
        if np.linalg.norm(np.array([xs[i],ys[i]])-position)>32:return repaired,supported
        _,labels=cv2.connectedComponents((guide>0).astype(np.uint8))
        connected=(labels==labels[ys[i],xs[i]]).astype(np.uint8)*255
        # Only a narrow guide corridor with independently visible floor support.
        # A broad rock/wall has no such support and remains blocked.
        support=cv2.boxFilter((mask>0).astype(np.float32),-1,(15,15),normalize=True)
        corridor=cv2.dilate(connected,np.ones((9,9),np.uint8))
        supported[(corridor>0)&(support>=.35)]=255
        supported[:2]=0;supported[-2:]=0;supported[:,:2]=0;supported[:,-2:]=0
        repaired|=supported
        return repaired,supported

    @staticmethod
    def _translation(previous, gray, player):
        compare=np.ones_like(gray,np.uint8)*255
        h,w=gray.shape;cv2.circle(compare,(round(player[0]*w),round(player[1]*h)),12,0,-1)
        compare[:4]=0;compare[-4:]=0;compare[:,:4]=0;compare[:,-4:]=0
        def residual(first,second,support):
            difference=first.astype(np.float32)[support>0]-second.astype(np.float32)[support>0]
            if difference.size<64:return 255.
            # Exposure changes do not imply camera/map motion.
            return float(np.abs(difference-np.median(difference)).mean())
        error=residual(previous,gray,compare)
        if error<.05:return np.zeros(2), 'stationary'
        points=cv2.goodFeaturesToTrack(previous,100,.025,5,mask=compare)
        if points is not None and len(points)>=8:
            new,status,_=cv2.calcOpticalFlowPyrLK(previous,gray,points,None,winSize=(21,21),maxLevel=2)
            if new is not None:
                good=status.ravel()==1
                old=points[good].reshape(-1,2);new=new[good].reshape(-1,2)
                if len(old)>=8:
                    affine,inliers=cv2.estimateAffinePartial2D(old,new,method=cv2.RANSAC,ransacReprojThreshold=1.8)
                    if affine is not None and inliers.sum()>=8 and inliers.mean()>=.65:
                        scale=math.hypot(affine[0,0],affine[1,0])
                        angle=abs(math.atan2(affine[1,0],affine[0,0]))
                        shift=affine[:,2]
                        warped=cv2.warpAffine(previous,affine,(w,h))
                        support=cv2.warpAffine(compare,affine,(w,h)) & compare
                        alignment_error=residual(warped,gray,support)
                        if .985<scale<1.015 and angle<.02 and np.linalg.norm(shift)<min(w,h)*.25 and alignment_error<12:
                            return shift,'tracked'
        # Outline maps may have fewer than eight corners. A verified translation still suffices.
        window=cv2.createHanningWindow((w,h),cv2.CV_32F)
        shift,response=cv2.phaseCorrelate(previous.astype(np.float32),gray.astype(np.float32),window)
        shift=np.array(shift)
        if response>.5 and np.linalg.norm(shift)<min(w,h)*.25:
            affine=np.float32([[1,0,shift[0]],[0,1,shift[1]]])
            warped=cv2.warpAffine(previous,affine,(w,h));support=cv2.warpAffine(compare,affine,(w,h)) & compare
            if residual(warped,gray,support)<12:return shift,'tracked'
        # Sparse terrain may not supply eight stable corners. Search only a
        # bounded local translation and require a distinctive, verified match.
        radius=min(24,max(4,min(w,h)//6))
        template=previous[radius:h-radius,radius:w-radius]
        if template.size and float(template.std())>=5:
            scores=cv2.matchTemplate(gray,template,cv2.TM_CCOEFF_NORMED)
            _,score,_,location=cv2.minMaxLoc(scores)
            other=scores.copy();x,y=location
            other[max(0,y-3):y+4,max(0,x-3):x+4]=-1
            if score>=.88 and score-float(other.max())>=.015:
                shift=np.array([x-radius,y-radius],float)
                affine=np.float32([[1,0,shift[0]],[0,1,shift[1]]])
                warped=cv2.warpAffine(previous,affine,(w,h))
                support=cv2.warpAffine(compare,affine,(w,h)) & compare
                if residual(warped,gray,support)<8:return shift,'template_tracked'
        # Ambiguous map transitions must not glue unrelated areas together.
        if error<2:return np.zeros(2),'stationary'
        return None,'unregistered'

    def on_input(self, command, status, error=None):
        if status!='sent' or command.action_type not in {'MOVE','DODGE'} or command.direction is None:return
        with self.lock:
            if self.valid and self.position is not None:
                self.pending.append((time.monotonic(),self.position.copy(),tuple(command.direction)))

    def escape_vector(self, blocked_direction):
        """Choose a straight, connected exit ray, favouring room over the stalled heading."""
        with self.lock:
            if not self.valid or self.grid is None or self.mask is None:return None
            start=np.asarray(self.player)*np.array(self.mask.shape[::-1])/self.CELL
            angle=math.radians(self.rotation);c,s=math.cos(angle),math.sin(angle)
            dx,dy=blocked_direction
            blocked=np.array([dx*c-dy*s,dx*s+dy*c])
            blocked/=max(1e-6,float(np.linalg.norm(blocked)))
            best=None
            for radians in np.linspace(0,2*math.pi,32,endpoint=False):
                heading=np.array([math.cos(radians),math.sin(radians)])
                reach=0
                for distance in range(1,25):
                    if not corridor_clear(start,start+heading*distance,self.grid):break
                    reach=distance
                if reach<4:continue
                score=reach*(1-.5*max(0,float(heading@blocked)))
                if best is None or score>best[0]:best=(score,heading,reach)
            if best is None:return None
            _,heading,reach=best
            return heading*min(24,reach*self.CELL*.75)

    def _key(self, position):return tuple(np.floor(position/self.CELL).astype(int))

    def update(self, frame, settings, now=None):
        now=time.monotonic() if now is None else now
        roi=self._crop(frame,settings)
        with self.lock:
            self.valid=False
            if roi is None:
                self.suspend('disabled' if not settings['minimap']['enabled'] else 'no_roi')
                return None
            m=settings['minimap'];signature=(tuple(m['bbox']),roi.shape,tuple(m['player']),m['rotation_degrees'],m.get('dark_floor',85),str(m.get('mapping',{})),str(m.get('wall_hsv',[])))
            if self.signature is not None and signature!=self.signature:self.reset(preserve_atlas=True)
            self.signature=signature
            self.CELL=m.get('mapping',{}).get('grid_cell_px',4)
            pin_crop=self._pin_crop(frame,settings)
            self.pin_candidate=None
            if pin_crop is not None:
                image,offset,player,_=pin_crop
                pin=minimap_pin(image,player)
                if pin is not None:self.pin_candidate=pin-offset
            icons=entity_icon_mask(roi) if m.get('mapping',{}).get('exclude_entity_icons',m.get('mapping',{}).get('mode')=='diablo4_auto') else np.zeros(roi.shape[:2],np.uint8)
            gray=cv2.cvtColor(remove_entity_icons(roi,icons),cv2.COLOR_BGR2GRAY)
            mask,valid,self.orange_mask,guide_floor,self.analysis_width=self._frame_terrain(frame,settings,roi)
            self.walkable_ratio=float((mask>0).mean())
            self.player_walkable=False
            if not valid:
                self.pending.clear();self.registration='unavailable';self.route=[]
                self.reason='uncalibrated' if m.get('mapping',{}).get('calibrated') is False else 'terrain_invalid'
                # Keep the last registered crop for displaying the retained
                # destination. valid=False still blocks planning and input.
                return None
            if m.get('mapping',{}).get('mode')=='wall_lines':
                hsv=cv2.cvtColor(roi,cv2.COLOR_BGR2HSV);gray=np.zeros(roi.shape[:2],np.uint8)
                for lo,hi in m.get('wall_hsv',[]):gray|=cv2.inRange(hsv,tuple(lo),tuple(hi))
            if self.previous is not None:
                shift,registration=self._translation(self.previous,gray,m['player'])
                recovered_origin=None
                if shift is None:
                    for reference,origin in list(self.keyframes)[-6:][::-1]:
                        candidate,status=self._translation(reference,gray,m['player'])
                        if candidate is not None:
                            recovered_origin=origin-candidate;shift=self.origin-recovered_origin
                            registration='atlas_recovered';break
                elif now-self.last_correction>=2 and len(self.keyframes)>1:
                    # Correct small accumulated drift against an older overlapping frame.
                    reference,origin=self.keyframes[-2]
                    candidate,_=self._translation(reference,gray,m['player'])
                    if candidate is not None and np.linalg.norm((origin-candidate)-(self.origin-shift))<=3:
                        shift=self.origin-(origin-candidate);registration='atlas_corrected'
                    self.last_correction=now
                if shift is None:
                    if self.registration_failed_at is None:self.registration_failed_at=now
                    changed=float(cv2.absdiff(self.previous,gray).mean())>35
                    if now-self.registration_failed_at<self.registration_reset_seconds or not changed:
                        # Retry against the last aligned image, never glue an
                        # unverified frame to the world or erase the active goal.
                        self.registration='unregistered'
                        h,w=mask.shape;px,py=m['player']
                        x=min(w//self.CELL-1,int(px*w/self.CELL))*self.CELL
                        y=min(h//self.CELL-1,int(py*h/self.CELL))*self.CELL
                        self.reason='registration_wait' if mask[y:y+self.CELL,x:x+self.CELL].mean()>=160 else 'player_blocked'
                        self.route=[]
                        return None
                    self.reset(preserve_atlas=True);self.signature=signature
                else:
                    self.origin-=shift
                    # Retained route points belong to the previous crop. Move
                    # their endpoints with terrain, keeping the player anchored.
                    if self.route:
                        size=np.array(mask.shape[::-1],float)
                        self.route=[list(m['player'])]+[(np.asarray(point)+shift/size).tolist() for point in self.route[1:]]
                    self.registration_failed_at=None
                self.registration=registration
            self.previous=gray
            h,w=mask.shape
            measured_player=self._floor_player(mask,m['player'],now)
            px,py=measured_player;position=self.origin+np.array([px*w,py*h])
            self.movement=0 if self.position is None else float(np.linalg.norm(position-self.position))
            recorded=self.recorded_position
            recorded_distance=0 if recorded is None else float(np.linalg.norm(position-recorded))
            if recorded is None or recorded_distance>=.5:
                old=position if recorded is None else recorded
                previous_key=None
                for point in np.linspace(old,position,max(2,min(40,round(recorded_distance/self.CELL)+1))):
                    k=self._key(point);self.visits[k]=min(20,self.visits.get(k,0)+1)
                    if previous_key is not None and previous_key!=k:
                        self.travel_edges.add(tuple(sorted((previous_key,k))))
                    previous_key=k
                self.recorded_position=position.copy()
            self.position=position
            if not self.breadcrumbs or np.linalg.norm(position-self.breadcrumbs[-1])>=2:
                self.breadcrumbs.append(position.copy())
            gh,gw=h//self.CELL,w//self.CELL
            grid=(mask[:gh*self.CELL,:gw*self.CELL].reshape(gh,self.CELL,gw,self.CELL).mean(axis=(1,3))>=160).astype(np.uint8)
            guide_grid=guide_floor[:gh*self.CELL,:gw*self.CELL].reshape(gh,self.CELL,gw,self.CELL).mean(axis=(1,3))>=160
            icon_grid=icons[:gh*self.CELL,:gw*self.CELL].reshape(gh,self.CELL,gw,self.CELL).max(axis=(1,3))>0
            for y,x in np.ndindex(grid.shape):
                key=self._key(self.origin+np.array([(x+.5)*self.CELL,(y+.5)*self.CELL]))
                evidence=self.cells.get(key,0)
                # Occluded terrain is estimated for the local route, but an
                # entity overlay supplies no permanent wall/floor evidence.
                if not icon_grid[y,x]:self.cells[key]=max(-5,min(5,evidence+(1 if grid[y,x] else -1)))
                # Remember repeatedly observed walls even if a transient overlay brightens one frame.
                if evidence<=-3 and not guide_grid[y,x]:grid[y,x]=0
                elif guide_grid[y,x] and grid[y,x] and not icon_grid[y,x]:self.cells[key]=max(1,self.cells[key])
            # Distance from terrain walls is the principal route cost, not merely pass/fail.
            expanded=cv2.resize(grid,(gw*self.CELL,gh*self.CELL),interpolation=cv2.INTER_NEAREST)
            clearance=cv2.distanceTransform(expanded,cv2.DIST_L2,5)
            self.clearance=clearance.reshape(gh,self.CELL,gw,self.CELL).mean(axis=(1,3))/self.CELL
            if self.pin_candidate is not None:
                pin=self.origin+self.pin_candidate
                if self.pin_world is None or np.linalg.norm(pin-self.pin_world)<12:self.pin_world=pin
            self.follow_centerline=m.get('mapping',{}).get('follow_centerline',False)
            self.center_weight=m.get('mapping',{}).get('center_weight',3.)
            self.preferred_clearance=m.get('mapping',{}).get('preferred_clearance_px',10)/self.CELL
            self.stuck=False
            attempts=list(self.pending)
            remaining=[]
            for started,start,direction in attempts:
                if now-started>=4:continue
                distance=float(np.linalg.norm(position-start))
                if distance>=1.5:
                    if self.failures and np.linalg.norm(position-self.failures[-1][1])>=4:
                        self.recovery_stage='none';self.recovery_count=0
                    continue
                if now-started<4:remaining.append((started,start,direction))
                if now-started>=m.get('stuck_seconds',2):
                    self.stuck=True
                    if now-self.last_failure_at>=m.get('stuck_seconds',2):
                        self.failures.append((now,start.copy(),direction))
                        self.last_failure_at=now;self.recovery_count+=1
                        # The agent confirms a wall with its movement skill before
                        # releasing a locked destination. A stuck sample is only evidence.
                        if not getattr(self,'lock_current_heading',False):
                            self.goal=None
                            self.recovery_stage=('recenter','backtrack','detour','blocked')[min(3,self.recovery_count-1)]
            self.pending=deque(remaining,maxlen=20)
            self.failures=deque((f for f in self.failures if now-f[0]<25),maxlen=32)
            self.mask=mask;self.grid=grid;self.last_update=now
            self.player=list(measured_player);self.rotation=m['rotation_degrees']
            sx,sy=min(gw-1,int(px*w/self.CELL)),min(gh-1,int(py*h/self.CELL))
            self.walkable_ratio=float((mask>0).mean())
            self.player_walkable=bool(grid[sy,sx])
            self.valid=self.player_walkable
            self.reason='ready' if self.valid else 'player_blocked'
            if not self.valid:
                self.route=[];self.pending.clear()
                return None
            if len(self.route)>1:
                start=np.asarray(self.player)*np.array([w,h])/self.CELL
                end=np.asarray(self.route[1])*np.array([w,h])/self.CELL
                if not corridor_clear(start,end,grid,self.clearance,0):self.route=[]
            if not self.keyframes or np.linalg.norm(self.origin-self.keyframes[-1][1])>=8:
                self.keyframes.append((gray.copy(),self.origin.copy()))
            return mask

    def planned_target(self):
        """Destination survives temporary route loss; it is not an input approval."""
        with self.lock:
            goal=self.goal if self.goal is not None else self.replan_goal
            if goal is None or self.mask is None:return None
            return ((goal-self.origin)/np.array(self.mask.shape[::-1])).tolist()

    def atlas_snapshot(self):
        if not self.cells:return {'grid':[],'player':None,'bounds':None,'resolution':self.CELL,'archived_segments':len(self.archives)}
        keys=np.asarray(list(self.cells));lo=keys.min(axis=0);hi=keys.max(axis=0)+1
        size=hi-lo;stride=max(1,math.ceil(max(size)/192))
        width,height=np.ceil(size/stride).astype(int)
        canvas=np.zeros((height,width),np.uint8)
        lx,ly=map(int,lo)
        for (x,y),evidence in self.cells.items():
            gx,gy=(x-lx)//stride,(y-ly)//stride
            value=3 if evidence>0 and (x,y) in self.visits else 1 if evidence>0 else 2
            # Downsampling preserves walls rather than inventing a wide corridor.
            if canvas[gy,gx]!=2:canvas[gy,gx]=value
        player=((self.position/self.CELL-lo)/(np.array([width,height])*stride)).tolist() if self.position is not None else None
        return {'grid':[''.join(map(str,row)) for row in canvas],'player':player,
                'bounds':(np.r_[lo,hi]*self.CELL).tolist(),'resolution':self.CELL*stride,
                'archived_segments':len(self.archives),'archived_cells':sum(len(a['cells']) for a in self.archives)}

    def atlas_trail(self, atlas):
        """Export the complete measured trail only for hover previews, in atlas coordinates."""
        if not atlas or not atlas.get('grid'):return []
        rows=atlas['grid'];origin=np.array(atlas['bounds'][:2],float)
        size=np.array([len(rows[0]),len(rows)])*atlas['resolution']
        def point(key):return (((np.array(key)+.5)*self.CELL-origin)/size).tolist()
        return [[point(a),point(b)] for a,b in sorted(self.travel_edges)]

    def destination_blocked(self, target_world, now=None):
        """True for a visible wall/disconnected goal; None for an unmeasured goal."""
        now=time.monotonic() if now is None else now
        with self.lock:
            if not self.valid or now-self.last_update>=.4 or self.grid is None or self.position is None:
                return None
            h,w=self.grid.shape
            gx,gy=np.floor((np.asarray(target_world)-self.origin)/self.CELL).astype(int)
            sx,sy=np.floor((self.position-self.origin)/self.CELL).astype(int)
            if not (0<=gx<w and 0<=gy<h and 0<=sx<w and 0<=sy<h):return None
            if not self.grid[sy,sx]:return None
            if not self.grid[gy,gx]:return True
            # Four-connected regions also enforce the planner's no-corner-cut rule.
            _,labels=cv2.connectedComponents((self.grid>0).astype(np.uint8),connectivity=4)
            return bool(labels[sy,sx]!=labels[gy,gx])

    def suggest(self, direction, explore=True, now=None, lookahead_px=None, target_world=None):
        now=time.monotonic() if now is None else now
        with self.lock:
            if not self.valid or now-self.last_update>.8:return None
            if self.replan_requested:
                self.replan_requested=False
                flags=(getattr(self,'follow_orange_route',False),getattr(self,'follow_pin_route',False))
                self.follow_orange_route=False;self.follow_pin_route=False
                try:
                    alternate=(-direction[1],direction[0])
                    plan=self.suggest(alternate,explore=True,now=now,lookahead_px=lookahead_px)
                    if plan is None:
                        self.blocked_goal_heading=None
                        plan=self.suggest(direction,explore=True,now=now,lookahead_px=lookahead_px)
                    if plan is None:self.replan_requested=True
                    return plan
                finally:self.follow_orange_route,self.follow_pin_route=flags
            self.reason='route_blocked'
            dx,dy=direction;n=math.hypot(dx,dy)
            if n<1e-6:return None
            angle=math.radians(self.rotation);c,s=math.cos(angle),math.sin(angle)
            heading=np.array([(dx*c-dy*s)/n,(dx*s+dy*c)/n])
            continue_heading=(explore and target_world is None and self.recovery_stage=='none'
                              and self.last_direction is not None and self.blocked_goal_heading is None
                              and (self.goal is not None or not getattr(self,'prefer_unvisited',False)))
            if continue_heading:
                dx,dy=self.last_direction
                heading=np.array([dx*c-dy*s,dx*s+dy*c])
            locked_ray=None
            if getattr(self,'lock_current_heading',False) and self.goal is not None and self.blocked_goal_heading is None:
                start_point=np.asarray(self.player)*np.array(self.mask.shape[::-1])
                endpoint=self.goal-self.origin
                if corridor_clear(start_point/self.CELL,endpoint/self.CELL,self.grid,self.clearance,.5):
                    locked_ray=endpoint
                    target_world=self.goal.copy()
            if continue_heading and getattr(self,'lock_current_heading',False) and self.goal is None:
                start_point=np.asarray(self.player)*np.array(self.mask.shape[::-1])
                for length in range(1,math.ceil(math.hypot(*self.mask.shape))):
                    candidate=start_point+heading*length
                    if not corridor_clear(start_point/self.CELL,candidate/self.CELL,self.grid,self.clearance,.5):break
                    if length>=4:locked_ray=candidate
                if locked_ray is not None:
                    target_world=self.origin+locked_ray
            pin_target=self.pin_world if getattr(self,'follow_pin_route',False) else None
            if pin_target is not None and np.linalg.norm(pin_target-self.position)<=6:
                self.route=[];self.reason='pin_arrived';return None
            orange_goal=None
            new_guide_target=False
            orange_distance=None
            if getattr(self,'follow_orange_route',False) and pin_target is None:
                visible=self.orange_mask is not None and np.any(self.orange_mask)
                if visible:
                    orange_distance=cv2.distanceTransform((self.orange_mask==0).astype(np.uint8),cv2.DIST_L2,5)
                if target_world is None and self.goal is not None:
                    target_world=self.goal.copy()
                elif target_world is None:
                    if not visible:
                        if not getattr(self,'explore_without_guide',False):
                            self.route=[];self.reason='guide_missing';return None
                        # No guide/pin: choose a connected map frontier normally.
                    else:
                        point=orange_route_target(self.orange_mask,np.array(self.player)*self.mask.shape[::-1],self.origin,self.visits,self.CELL)
                        if point is None:self.route=[];self.reason='guide_disconnected';return None
                        target_world=self.origin+point
                        new_guide_target=True
            if pin_target is not None and self.orange_mask is not None and np.any(self.orange_mask):
                orange_distance=cv2.distanceTransform((self.orange_mask==0).astype(np.uint8),cv2.DIST_L2,5)
                orange_goal=orange_route_target(self.orange_mask,np.array(self.player)*self.mask.shape[::-1],self.origin,self.visits,self.CELL,destination=pin_target)
                if orange_goal is not None:
                    delta=self.origin+orange_goal-self.position
                    length=float(np.linalg.norm(delta))
                    if length>1e-6:heading=delta/length
            center_weight=max(10.,self.center_weight) if getattr(self,'follow_centerline',False) else self.center_weight
            grid=self.grid;h,w=grid.shape
            start=(min(w-1,int(self.player[0]*self.mask.shape[1]/self.CELL)),min(h-1,int(self.player[1]*self.mask.shape[0]/self.CELL)))
            if not grid[start[1],start[0]]:self.route=[];return None
            if self.recovery_stage=='blocked' and target_world is None and pin_target is None:self.route=[];return None
            failed_cells=set()
            for _,where,failed in self.failures:
                if np.linalg.norm(self.position-where)>14:continue
                fx,fy=failed;f=np.array([fx*c-fy*s,fx*s+fy*c]);fn=np.linalg.norm(f)
                if not fn:continue
                for ox in range(-4,5):
                    for oy in range(-4,5):
                        length=math.hypot(ox,oy)
                        if length and (ox*f[0]+oy*f[1])/(length*fn)>.8:
                            failed_cells.add((start[0]+ox,start[1]+oy))
            self.screen_obstacles=[(until,box) for until,box in self.screen_obstacles if until>now]
            screen_blocked=set()
            for _,(a,b,cx,d) in self.screen_obstacles:
                for y,x in np.ndindex(grid.shape):
                    point=self.origin+(np.array([x,y])+.5)*self.CELL
                    if max(abs(x-start[0]),abs(y-start[1]))>1 and a-self.CELL<=point[0]<=cx+self.CELL and b-self.CELL<=point[1]<=d+self.CELL:screen_blocked.add((x,y))
            # Node penalties are invariant during this search; compute once, not per edge.
            penalties=center_weight/(self.clearance+.5)
            if orange_distance is not None:
                xs=np.minimum(self.mask.shape[1]-1,((np.arange(w)+.5)*self.CELL).astype(int))
                ys=np.minimum(self.mask.shape[0]-1,((np.arange(h)+.5)*self.CELL).astype(int))
                guide_weight=.03 if (getattr(self,'follow_centerline',False) or pin_target is not None and getattr(self,'pin_direction_priority',False)) else 3
                penalties=penalties+orange_distance[np.ix_(ys,xs)]*guide_weight
            visits_cost=np.zeros(grid.shape,float)
            world_keys={}
            if explore:
                for ny,nx in np.argwhere(grid):
                    key=self._key(self.origin+np.array([(nx+.5)*self.CELL,(ny+.5)*self.CELL]))
                    world_keys[(int(nx),int(ny))]=key
                    count=self.visits.get(key,0)
                    # Returning through travelled terrain is a fallback when
                    # no connected unexplored route remains.
                    visits_cost[ny,nx]=0 if not count else 8+2*min(count,5)
            distance={start:0.};parent={};queue=[(0.,start)]
            reused={start:0};hops={start:0}
            recent_keys={self._key(point) for point in list(self.breadcrumbs)[-24:]
                         if np.linalg.norm(point-self.position)>=self.CELL*2}
            while queue:
                cost,node=heapq.heappop(queue)
                if cost>distance[node]:continue
                x,y=node
                for ox,oy in ((1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)):
                    nx,ny=x+ox,y+oy
                    if not (0<=nx<w and 0<=ny<h) or not grid[ny,nx]:continue
                    if ox and oy and (not grid[y,nx] or not grid[ny,x]):continue
                    next_node=(nx,ny)
                    new=cost+math.hypot(ox,oy)*(1+penalties[ny,nx])+visits_cost[ny,nx]+(12 if next_node in failed_cells else 0)+(30 if next_node in screen_blocked else 0)
                    if explore and tuple(sorted((world_keys[node],world_keys[next_node]))) in self.travel_edges:new+=8
                    if new<distance.get(next_node,float('inf')):
                        distance[next_node]=new;parent[next_node]=node;heapq.heappush(queue,(new,next_node))
                        hops[next_node]=hops[node]+1
                        reused[next_node]=reused[node]+int(explore and world_keys[next_node] in self.visits and math.hypot(nx-start[0],ny-start[1])>2)
            # Estimate how much unseen map a candidate player position reveals.
            # Integral counts keep this independent of the total accumulated map size.
            coverage=None
            if explore and target_world is None and self.recovery_stage=='none':
                base=np.floor(self.origin/self.CELL).astype(int)-[w,h]
                known=np.array([[(int(base[0]+x),int(base[1]+y)) in self.cells
                                 for x in range(w*3)] for y in range(h*3)],np.int32)
                coverage=np.pad(known.cumsum(0).cumsum(1),((1,0),(1,0)))
            def reveal(node):
                if coverage is None:return 0
                x=w+node[0]-start[0];y=h+node[1]-start[1]
                count=coverage[y+h,x+w]-coverage[y,x+w]-coverage[y+h,x]+coverage[y,x]
                return int(w*h-count)
            best=None;best_score=(-float('inf'),)
            for node,cost in (distance.items() if target_world is None else ()):
                delta=np.array(node)-start;length=float(np.linalg.norm(delta))
                if length<(1 if self.recovery_stage=='recenter' else 3):continue
                avoid=getattr(self,'blocked_goal_heading',None)
                if explore and target_world is None and self.goal is None and avoid is not None:
                    goal_delta=self.origin+(np.array(node)+.5)*self.CELL-self.position
                    goal_length=float(np.linalg.norm(goal_delta))
                    if goal_length and float(goal_delta@avoid)/goal_length>.35:continue
                forward=float(delta@heading)
                score=forward+length*.3-cost*.25+min(self.preferred_clearance,self.clearance[node[1],node[0]])*1.5
                if self.recovery_stage=='recenter':
                    score=self.clearance[node[1],node[0]]*6-cost*.4-length*.5
                elif self.recovery_stage=='detour':score=length*.7-cost*.25
                key=self._key(self.origin+(np.array(node)+.5)*self.CELL)
                if explore:
                    score-=self.visits.get(key,0)*.7
                    score+=sum((key[0]+ox,key[1]+oy) not in self.cells for ox,oy in ((1,0),(-1,0),(0,1),(0,-1)))*.5
                # Successful game input with no registered displacement is a temporary forbidden direction.
                for _,where,failed in self.failures:
                    if np.linalg.norm(self.position-where)>14:continue
                    fx,fy=failed;f=np.array([fx*c-fy*s,fx*s+fy*c]);fn=np.linalg.norm(f)
                    if fn and float(delta@f)/(length*fn)>.8:score-=30
                # Pick the farthest reachable unexplored destination in the
                # requested heading; route cost still determines how to get there.
                edge_band=min(node[0],node[1],w-1-node[0],h-1-node[1])//3
                edge_priority=-edge_band if explore and self.recovery_stage=='none' else 0
                alignment=float(delta@heading)/length
                straight=(continue_heading and alignment>=.985 and
                          corridor_clear(np.asarray(self.player)*np.array(self.mask.shape[::-1])/self.CELL,
                                         np.asarray(node)+.5,grid,self.clearance,.5))
                priority=(int(straight),edge_priority,int(not explore or self.recovery_stage!='none' or key not in self.visits),int(not explore or self.recovery_stage!='none' or key not in recent_keys),int(getattr(self,'direction_priority',False) and self.recovery_stage=='none' and alignment>.35),length if explore and self.recovery_stage=='none' else 0,int(coverage is None or self.clearance[node[1],node[0]]>=self.preferred_clearance),reveal(node),score)
                if explore and self.recovery_stage=='none' and getattr(self,'prefer_unvisited',False):
                    world=self.origin+(np.array(node)+.5)*self.CELL
                    repeats=sum(np.linalg.norm(world-goal)<=self.CELL*3 for goal in self.completed_goals)
                    priority=(-repeats,int(key not in self.visits),int(key not in recent_keys),-reused[node]/max(1,hops[node]))+priority
                if priority>best_score:best,best_score=node,priority
            if self.recovery_stage=='backtrack':
                for position in reversed(self.breadcrumbs):
                    if np.linalg.norm(position-self.position)<10:continue
                    x,y=np.floor((position-self.origin)/self.CELL).astype(int)
                    if (x,y) in distance and self.clearance[y,x]>=1.5:
                        best=(int(x),int(y));break
            if pin_target is not None and target_world is None:
                # Pins/icons may cover a floor cell or lie beyond this local map.
                # Choose a reachable approach point while retaining the final pin.
                eligible=[node for node in distance if node!=start]
                if not eligible:self.route=[];return None
                pin_first=getattr(self,'pin_direction_priority',False)
                approach=pin_target if pin_first else self.origin+orange_goal if orange_goal is not None else pin_target
                def floor_pixel(node):
                    x,y=((np.array(node)+.5)*self.CELL).astype(int)
                    return 0<=y<self.mask.shape[0] and 0<=x<self.mask.shape[1] and self.mask[y,x]>0
                eligible=[node for node in eligible if floor_pixel(node)]
                if not eligible:self.route=[];return None
                if pin_first:
                    delta=pin_target-self.position;length=float(np.linalg.norm(delta))
                    if length>1e-6:heading=delta/length
                    def approach_cost(node):
                        gap=float(np.linalg.norm(self.origin+(np.array(node)+.5)*self.CELL-pin_target))
                        return gap+center_weight*self.CELL/(float(self.clearance[node[1],node[0]])+.5)
                    best=min(eligible,key=lambda node:(approach_cost(node),distance[node]))
                    # Once the pin lies on connected floor, finish at its safe
                    # cell rather than repeatedly stopping short of it.
                    pin_local=pin_target-self.origin
                    px,py=np.floor(pin_local).astype(int)
                    pin_cell=tuple(np.floor(pin_local/self.CELL).astype(int))
                    if (pin_cell in eligible and 0<=py<self.mask.shape[0] and 0<=px<self.mask.shape[1]
                            and self.mask[py,px]>0):best=pin_cell
                else:
                    best=min(eligible,key=lambda node:(np.linalg.norm(self.origin+(np.array(node)+.5)*self.CELL-approach),distance[node]))
                retained_goal=False
                if self.goal is not None:
                    old=tuple(np.floor((self.goal-self.origin)/self.CELL).astype(int))
                    px,py=np.floor(self.goal-self.origin).astype(int)
                    if (old in eligible and 0<=py<self.mask.shape[0] and 0<=px<self.mask.shape[1]
                            and self.mask[py,px]>0):best=old;retained_goal=True
                guide_cell=tuple(np.floor((approach-self.origin)/self.CELL).astype(int))
                target_world=(self.goal.copy() if retained_goal else approach.copy()
                              if not pin_first and orange_goal is not None and best==guide_cell
                              else self.origin+(np.array(best)+.5)*self.CELL)
            keep_goal=False
            if target_world is not None:
                target_world=np.asarray(target_world,float)
                gx,gy=np.floor((target_world-self.origin)/self.CELL).astype(int)
                px,py=np.floor(target_world-self.origin).astype(int)
                if new_guide_target and ((int(gx),int(gy)) not in distance or not self.mask[py,px]):
                    # A thin guide may lie on a rejected edge cell. Its endpoint
                    # is a direction hint, not permission to target a wall.
                    candidates=[node for node in distance if node!=start and
                                self.mask[int((node[1]+.5)*self.CELL),int((node[0]+.5)*self.CELL)] ]
                    if not candidates:self.route=[];return None
                    gx,gy=min(candidates,key=lambda node: np.linalg.norm(
                        self.origin+(np.array(node)+.5)*self.CELL-target_world))
                    target_world=self.origin+(np.array([gx,gy])+.5)*self.CELL
                if (int(gx),int(gy)) not in distance:
                    self.route=[];return None  # Keep the destination; never substitute an exploration goal.
                best=(int(gx),int(gy));keep_goal=True
                self.goal=target_world.copy();self.goal_heading=heading.copy();self.goal_until=float('inf')
            else:
                keep_goal=False
                if explore and self.recovery_stage=='none' and self.goal is not None:
                    gx,gy=np.floor((self.goal-self.origin)/self.CELL).astype(int)
                    if (gx,gy) not in distance:self.route=[];return None
                    best=(int(gx),int(gy));keep_goal=True
                if best is None:self.route=[];return None
                if not keep_goal:
                    self.goal=self.origin+(np.array(best)+.5)*self.CELL;self.goal_heading=heading.copy();self.goal_until=now+4
            self.replan_goal=None
            path=[best]
            while path[-1]!=start:path.append(parent[path[-1]])
            path.reverse();self.route=[[(x+.5)/w,(y+.5)/h] for x,y in path]
            # Smooth grid bends through verified floor; display the same approved segment used for clicks.
            waypoint=None;waypoint_index=0
            start_point=np.array([self.player[0]*self.mask.shape[1],self.player[1]*self.mask.shape[0]])/self.CELL
            self.route[0]=list(self.player)
            previous=start_point;path_cost=0.
            minimum_path=float(self.clearance[start[1],start[0]])
            previous_weight=1+center_weight/(minimum_path+.5)
            for index,node in enumerate(path[1:],1):
                point=np.array(node)+.5
                length=float(np.linalg.norm(point-previous))
                weight=1+center_weight/(self.clearance[node[1],node[0]]+.5)
                path_cost+=length*(previous_weight+weight)/2
                previous_weight=weight
                previous=point
                minimum_path=min(minimum_path,float(self.clearance[node[1],node[0]]))
                minimum=min(self.preferred_clearance*.65,minimum_path)*.8
                if any(tuple(cell) in screen_blocked for cell in supercover_cells(start_point,point)):continue
                if not corridor_clear(start_point,point,grid,self.clearance,minimum):continue
                if shortcut_cost(start_point,point,self.clearance,center_weight)>path_cost*(1.02 if getattr(self,'follow_centerline',False) else 1.1):continue
                waypoint=node;waypoint_index=index
            if locked_ray is not None:
                waypoint=best;waypoint_index=len(path)-1
            if waypoint is None:
                if target_world is not None and best==start and corridor_clear(start_point,(target_world-self.origin)/self.CELL,grid,self.clearance,0):
                    waypoint=start
                else:
                    self.route=[]
                    return None
            point=(np.array(waypoint)+.5)*self.CELL
            if target_world is not None and waypoint==best:
                endpoint=(target_world-self.origin)/self.CELL
                if corridor_clear(start_point,endpoint,grid,self.clearance,0):point=target_world-self.origin
            # Publish the approved shortcut, rather than a raw staircase route
            # that would falsely make its own click appear to leave the path.
            remaining_route=self.route[waypoint_index+1:]
            player_point=start_point*self.CELL
            if getattr(self,'follow_orange_route',False) and self.orange_mask is not None:
                ys,xs=np.where(self.orange_mask>0)
                if len(xs):
                    points=np.column_stack((xs,ys));separation=np.linalg.norm(points-point,axis=1)
                    candidate=points[int(separation.argmin())].astype(float)
                    if (separation.min()<=self.CELL*1.5 and np.linalg.norm(candidate-player_point)>=6
                            and corridor_clear(start_point,candidate/self.CELL,grid,self.clearance,0)):
                        cy,cx=np.minimum(np.array(grid.shape)-1,(candidate[::-1]/self.CELL).astype(int))
                        py,px=np.minimum(np.array(grid.shape)-1,(point[::-1]/self.CELL).astype(int))
                        # A guide selects the travel direction; do not snap a
                        # centered click back towards a wall-side guide pixel.
                        if not getattr(self,'follow_centerline',False) or self.clearance[cy,cx]>=self.clearance[py,px]-.25:
                            point=candidate
            # Inspect and display the farthest verified straight segment. The
            # click radius limits execution, not how far terrain is inspected.
            self.route=[list(self.player),(point/np.array(self.mask.shape[::-1])).tolist()]+remaining_route
            vx,vy=point-player_point
            # Grid cells have equal pixel size; map direction converts back to screen direction.
            norm=math.hypot(vx,vy)
            if norm<1e-6:return None
            vx,vy=vx/norm,vy/norm
            result=(vx*c+vy*s,-vx*s+vy*c)
            length=min(norm,lookahead_px) if lookahead_px is not None and lookahead_px>0 else norm
            self.blocked_goal_heading=None
            self.reason='ready'
            self.last_direction=result;self.last_plan=now
            self.last_lookahead=length/self.mask.shape[1] if lookahead_px is not None else min(.14,length/self.mask.shape[1])
            return result,self.last_lookahead,min(1.,max(.4,length/26))

    def snapshot(self, now=None):
        now=time.monotonic() if now is None else now
        with self.lock:
            rows=[]
            if self.grid is not None:
                h,w=self.grid.shape
                for y in range(h):
                    row=[]
                    for x in range(w):
                        key=self._key(self.origin+np.array([(x+.5)*self.CELL,(y+.5)*self.CELL]))
                        row.append('3' if self.grid[y,x] and key in self.visits else '1' if self.grid[y,x] else '2')
                    rows.append(''.join(row))
            current=self.valid and now-self.last_update<.8
            if self.atlas_cache is None or now-self.atlas_cache_at>=1:
                self.atlas_cache=self.atlas_snapshot();self.atlas_cache_at=now
            return {'valid':current,'reason':'stale' if self.valid and not current else self.reason,
                    'age_seconds':round(max(0,now-self.last_update),1) if self.last_update else None,
                    'walkable_ratio':round(self.walkable_ratio,3),'player_walkable':self.player_walkable,
                    'registration':self.registration,'exploration_mode':'atlas_frontier','screen_obstacles':len(self.screen_obstacles),
                    'segment':self.segment,'known_cells':len(self.cells),'visited_cells':len(self.visits),
                    'failed_directions':len(self.failures),'movement_px':round(self.movement,2),
                    'pin_target':((self.pin_world-self.origin)/np.array(self.mask.shape[::-1])).tolist() if self.pin_world is not None and self.mask is not None else None,'atlas':self.atlas_cache,'grid':rows,'route':list(self.route),'player':getattr(self,'player',[.5,.5]),
                    'stuck':self.stuck,'recovery_stage':self.recovery_stage,'recovery_count':self.recovery_count,
                    'analysis_width':getattr(self,'analysis_width',192),'grid_cell_px':self.CELL,
                    'route_clearance_px':round(float(min((self.clearance[min(self.clearance.shape[0]-1,int(y*self.clearance.shape[0])),min(self.clearance.shape[1]-1,int(x*self.clearance.shape[1]))]*self.CELL for x,y in self.route),default=0)),1) if self.clearance is not None else 0}

    def prompt_summary(self):
        state=self.snapshot()
        return {k:state[k] for k in ('valid','registration','visited_cells','failed_directions','stuck','recovery_stage','recovery_count')} | {'local_direction':self.last_direction if state['valid'] else None}

    def allows(self,direction,now=None):
        from app.core.local_navigation import LocalNavigator
        now=time.monotonic() if now is None else now
        with self.lock:
            if not self.valid or now-self.last_update>.8:return False
            return LocalNavigator._map_clear(direction,self.mask,{'enabled':True,'player':self.player,
                'rotation_degrees':self.rotation,'mapping':{'enabled':True},'lookahead':self.last_lookahead})

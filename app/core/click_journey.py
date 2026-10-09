"""Keep a destination across repeated, freshly verified movement clicks."""
import math
from collections import OrderedDict, deque
import numpy as np


class ClickJourney:
    SOURCES = {'HUNT_EXPLORE', 'MINIMAP_QWEN', 'USER_COMMAND'}
    KEEP_ACTIONS = {'CAST_BUFF','USE_POTION'}
    MOVING_STATES = {'moving','approaching'}
    feedback_steps = False
    arrival_only = False
    STALL_SECONDS = 1.2
    REFRESH_SECONDS = .05
    PASSIVE_STOPS = {'PAUSED_OR_NO_FRESH_HUD','MINIMAP_REQUIRED','PATH_BLOCKED','ORANGE_ROUTE_NOT_FOUND','ORANGE_GUIDE_MISSING','ORANGE_GUIDE_DISCONNECTED','NO_CENTER_PATH','NO_KNOWN_TARGET'}

    def __init__(self):
        self.reset()

    def reset(self, reason='reset', *, preserve_record=False):
        if not hasattr(self,'history'):
            self.history=deque(maxlen=20);self.goal_sequence=0;self.last_release=None
        if getattr(self,'current',None) is not None:self._release(reason)
        self.arrival_at=None
        self.pending = OrderedDict()
        self.current = None
        self.progress = None
        if not preserve_record:self.record = None
        self.map_missing_since=None;self.obstacle_sample=None

    def _release(self,reason,now=None,position=None):
        self.current=None
        if reason!='step_refresh':self.progress=None
        self.arrival_at=None
        self.map_missing_since=None;self.obstacle_sample=None
        if position is not None and self.record is not None:
            delta=float(np.linalg.norm(np.asarray(position)-self.record['last']))
            if delta>=.5:
                self.record['distance']+=delta;self.record['last']=np.asarray(position).copy()
                self.record['path'].append(np.asarray(position).copy());self.record['path']=self.record['path'][-64:]
        if reason=='arrived' and self.record is not None and np.linalg.norm(self.record.get('destination',self.record['goal'])-self.record['last'])>2:
            reason='waypoint_arrived'
        self.last_release=reason
        if self.record is not None:
            self.record['status']=reason
            self.history.append({'goal_id':self.record['goal_id'],'reason':reason,
                                 'remaining_map_px':round(float(np.linalg.norm(self.record.get('destination',self.record['goal'])-self.record['last'])),2)})

    def _arrived(self,position,sampled_at):
        if self.current is None:return False
        distance=float(np.linalg.norm(self.current[2]-position))
        if distance>2:
            self.arrival_at=None;return False
        if self.arrival_at is None:
            self.arrival_at=sampled_at;return False
        return sampled_at-self.arrival_at>=.08

    def _near_destination(self,position,sampled_at):
        r=self.record
        if not self.feedback_steps or r is None:return False
        origin=r.get('destination_start',r['start'])
        requested=float(np.linalg.norm(r.get('destination',r['goal'])-origin))
        radius=max(4.,min(12.,requested*.15))
        if self.arrival_only:radius=2.
        destination=r.get('destination',r['goal'])
        current=np.asarray(position)
        vector=destination-origin
        projection=float((current-origin)@vector)/(requested*requested) if requested>0 else 0
        passed=projection>=1 and np.linalg.norm(current-(origin+projection*vector))<=radius
        return (sampled_at-r.get('destination_sent_at',r['sent_at'])>=.12
                and np.linalg.norm(np.asarray(position)-origin)>=min(2.,requested*.5)
                and (np.linalg.norm(destination-current)<=radius or passed))

    def _destination_release_reason(self,position):
        if not self.arrival_only:return 'near_goal'
        destination=self.record.get('destination',self.record['goal'])
        return 'arrived' if np.linalg.norm(destination-np.asarray(position))<=2 else 'destination_passed'

    def dispatched(self, command, *, position, segment, player_screen, shape, scale, rotation, destination=None):
        dx = (command.target[0]-player_screen[0])*shape[1]/scale
        dy = (command.target[1]-player_screen[1])*shape[0]/scale
        a = math.radians(rotation)
        goal = np.asarray(position) + [dx*math.cos(a)-dy*math.sin(a), dx*math.sin(a)+dy*math.cos(a)]
        self.pending[command.execute_at] = {'epoch':command.decision_epoch,'segment':segment,'goal':goal,
                'destination':np.asarray(destination).copy() if destination is not None else goal.copy(),
                'start':np.asarray(position).copy(),'screen_target':list(command.target),
                'click_distance_screen_px':round(math.hypot((command.target[0]-player_screen[0])*shape[1],(command.target[1]-player_screen[1])*shape[0]),1)}
        while len(self.pending)>32:
            self.pending.popitem(last=False)

    def on_input(self, command, status, now, position=None):
        if command.action_type in self.KEEP_ACTIONS:return
        if command.source=='MANUAL_SKILL':
            if status=='sent':
                self.pending.clear()
                self._release('step_refresh')
            return
        if command.action_type=='STOP' and (command.reason in self.PASSIVE_STOPS or command.source in {'SEMANTIC_WAIT','COMBAT_TRACK_WAIT'}):return
        if command.action_type != 'MOVE' or command.source not in self.SOURCES:
            if status=='sent':
                self.pending.clear()
                if self.current is not None:self._release('interrupted')
            return
        candidate = self.pending.pop(command.execute_at, None)
        if candidate is not None and status == 'sent':
            previous=self.record
            # An in-flight refresh may finish after the map already handed off.
            # Its late completion must not reopen the completed destination.
            if (previous is not None and self.last_release in {'near_goal','arrived','destination_passed','map_goal_arrived'}
                    and previous['epoch']==candidate['epoch'] and previous['segment']==candidate['segment']
                    and np.allclose(candidate['destination'],previous.get('destination',previous['goal']))):return
            continuing=(previous is not None and self.last_release in {'stalled','waypoint_arrived','step_progress','step_refresh'}
                        and previous['epoch']==candidate['epoch'] and previous['segment']==candidate['segment'])
            destination=previous.get('destination',previous['goal']).copy() if continuing else candidate.get('destination',candidate['goal']).copy()
            goal_id=previous['goal_id'] if continuing else self.goal_sequence+1
            self.current = (candidate['epoch'],candidate['segment'],candidate['goal'],now)
            self.arrival_at=None;self.last_release=None
            if not continuing:self.goal_sequence+=1
            self.map_missing_since=None;self.obstacle_sample=None
            if not continuing or self.progress is None:
                self.progress = (candidate['start'].copy(),now)
            start=candidate['start'] if position is None else np.asarray(position).copy()
            distance=previous['distance'] if continuing else 0.
            path=list(previous['path']) if continuing else []
            if continuing:distance+=float(np.linalg.norm(start-previous['last']))
            path=(path+[start.copy()])[-64:]
            destination_start=previous.get('destination_start',previous['start']).copy() if continuing else start.copy()
            self.record={**candidate,'destination':destination,'destination_start':destination_start,'goal_id':goal_id,'execute_at':command.execute_at,'start':start,'last':start.copy(),'sent_at':now,
                         'destination_sent_at':previous.get('destination_sent_at',previous['sent_at']) if continuing else now,
                         'observed_at':now,'distance':distance,'status':'moving','path':path}
        elif status != 'sent' and self.record is not None and self.record['execute_at']==command.execute_at:
            self._release('input_failed')

    def reanchor(self, *, position, segment, now):
        r=self.record
        if r is None or r['segment']==segment:return
        if self.current is None and self.last_release not in {'stalled','waypoint_arrived','step_progress','step_refresh'}:return
        # Registration has no world correspondence. Keep the remaining vector,
        # anchored to the new measured player, without inventing travelled distance.
        offset=np.asarray(position,float)-r['last']
        for key in ('start','last','goal','destination','destination_start'):
            if key in r:r[key]=r[key]+offset
        r['path']=[np.asarray(point)+offset for point in r['path']]
        r['segment']=segment;r['observed_at']=now
        if self.current is not None:
            self.current=(self.current[0],segment,r['goal'],self.current[3])
        self.pending.clear();self.progress=(np.asarray(position,float).copy(),now)
        self.arrival_at=None;self.map_missing_since=None

    def observe(self, *, position, segment, valid, now):
        r=self.record
        if r is None or r['status'] not in self.MOVING_STATES:return
        if segment!=r['segment']:
            if not valid:return
            # Instead of immediately releasing, try to maintain navigation with partial data
            if self.current is not None and self.record is not None and now-self.record.get('observed_at', 0) < 2:
                # Continue with existing position and goal if we have recent data
                r['observed_at']=now
                return
            self._release('map_lost');return
        if not valid:
            # When we lose valid navigation, try to continue with partial data
            if self.current is not None and self.record is not None and now-self.record.get('observed_at', 0) < 2:
                r['observed_at']=now
                return  # Keep navigation alive with partial data
            self.arrival_at=None
            return  # Keep the last measured goal through transient registration loss.
        current=np.asarray(position)
        delta=float(np.linalg.norm(current-r['last']))
        # Ignore registration jitter under half a map pixel.
        if delta>=.5:
            r['distance']+=delta;r['last']=current.copy()
            r['path'].append(current.copy());r['path']=r['path'][-64:]
        r['observed_at']=now
        requested=float(np.linalg.norm(r['goal']-r['start']))
        remaining=float(np.linalg.norm(current-r['goal']))
        r['status']='approaching' if remaining<=max(4,requested*.15) else 'moving'
        if self._near_destination(current,now):self._release(self._destination_release_reason(current),position=current)
        elif self._arrived(current,now):self._release('arrived')

    def snapshot(self, *, origin, shape, segment, valid, now):
        r=self.record
        if r is None:return None
        h,w=shape
        same=segment==r['segment']
        normalize=lambda point:((np.asarray(point)-origin)/[w,h]).tolist()
        remaining=float(np.linalg.norm(r.get('destination',r['goal'])-r['last']))
        return {'goal_id':r['goal_id'],'last_release':self.last_release,'release_history':list(self.history),'status':r['status'],'measurement_valid':bool(valid and same and now-r['observed_at']<.8),
                'age_seconds':round(max(0,now-r['sent_at']),2),
                'screen_target':r['screen_target'],'click_distance_screen_px':r['click_distance_screen_px'],
                'map_target':normalize(r['goal']) if same else None,
                'destination_map_target':normalize(r.get('destination',r['goal'])) if same else None,
                'map_start':normalize(r['start']) if same else None,
                'travelled_map_px':round(r['distance'],2),
                'displacement_map_px':round(float(np.linalg.norm(r['last']-r.get('destination_start',r['start']))),2),
                'remaining_map_px':round(remaining,2),
                'requested_map_px':round(float(np.linalg.norm(r['goal']-r['start'])),2),
                'travel_path':[normalize(point) for point in r['path']] if same else []}

    def continues(self, *, epoch, segment, position, origin, mask, valid, stuck, now, sampled_at=None):
        r=self.record
        eligible=(r is not None and r['epoch']==epoch and r['segment']==segment
                  and (self.current is not None or self.last_release in {'stalled','waypoint_arrived','step_progress','step_refresh'}))
        if (eligible and valid and mask is not None and sampled_at is not None
                and now-sampled_at<.4 and self._near_destination(position,sampled_at)):
            self._release(self._destination_release_reason(position),position=position);return False
        if self.current is None:return False
        saved_epoch,saved_segment,goal,started=self.current
        if saved_epoch==epoch and saved_segment!=segment and not valid:return True
        reason=('epoch_changed' if saved_epoch!=epoch else 'segment_changed' if saved_segment!=segment else None)
        if reason:
            self._release(reason);return False
        if not valid or mask is None:
            if self.map_missing_since is None:self.map_missing_since=now
            # Missing terrain is not arrival or a new destination. Retain the
            # world goal; input guards still forbid clicks without a valid map.
            return True
        if self.map_missing_since is not None:
            # Missing observations are not evidence of a stationary character.
            self.progress=(np.asarray(position).copy(),now)
            self.map_missing_since=None
        if self._arrived(np.asarray(position),now if sampled_at is None else sampled_at):
            self._release('arrived',position=position);return False
        if (self.feedback_steps and self.record is not None and now-started>=.25
                and sampled_at is not None and sampled_at>=started and now-sampled_at<.4
                and np.linalg.norm(np.asarray(position)-self.record['start'])>=1.5):
            # Replan a short local step from real displacement, keeping the frontier goal.
            self._release('step_progress',position=position);return False
        # Actual displacement is progress even when following a bend sideways.
        if self.progress is None or np.linalg.norm(np.asarray(position)-self.progress[0])>=.5:
            self.progress=(np.asarray(position).copy(),now)
        elif now-self.progress[1]>=self.STALL_SECONDS:
            self._release('stalled');return False
        # Repeat the verified destination without waiting for a post-click frame.
        # Arrival/progress above still require measured movement; stale maps forbid refresh.
        if (sampled_at is not None
                and 0<=now-sampled_at<.8 and now-started>=self.REFRESH_SECONDS):
            self._release('step_refresh',position=position);return False
        # A game may path around a wall after receiving a point click. A changing
        # corridor or an old stuck flag must not cancel a goal while position advances.
        return True

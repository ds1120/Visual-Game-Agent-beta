"""Sustained verified attacks with finite sessions and semantic rechecks."""
import time
class CombatGuard:
    def __init__(self):self.reset()
    def reset(self):self.sessions={};self.blocked={};self.focus=None
    @staticmethod
    def overlap(a,b):
        x=max(0,min(a[2],b[2])-max(a[0],b[0]));y=max(0,min(a[3],b[3])-max(a[1],b[1]))
        inter=x*y;union=(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-inter
        return inter/max(1,union)
    def is_blocked(self,obj):
        if obj.track_id in self.blocked:return True
        return any(tid in self.blocked and self.overlap(obj.bbox,s['bbox'])>.5 for tid,s in self.sessions.items())
    def observe(self,objects,now=None):
        now=time.monotonic() if now is None else now
        live={o.track_id:o for o in objects}
        for tid,s in list(self.sessions.items()):
            if now-s['start']>=s.get('max_seconds',60) and not (s.get('until_bar_lost') and getattr(live.get(tid),'enemy_bar_confirmed',False)):
                self.blocked[tid]='최대 전투 시간 초과'
            o=live.get(tid)
            if o is not None:
                s['misses']=0
                s['state']='engaged'
                if getattr(o, 'enemy_bar_confirmed', False):
                    s['bar_seen'] = True
                    s['bar_missing'] = False
                    if s.get('recheck_reason') == 'bar_lost':
                        s['pending'] = False
                        s.pop('recheck_reason', None)
                elif s.get('bar_seen') and not s.get('bar_missing'):
                    # A dying body's box can remain visible after its enemy
                    # bar disappears. Release the hold before asking Qwen.
                    s['bar_missing'] = True
                    s['pending'] = True
                    s['recheck_reason'] = 'bar_lost'
                if not getattr(o, 'enemy_health_valid', False):
                    s['zero_samples'] = 0
                    if s.get('feedback_required') and s.get('sent', 0) and not (s.get('until_bar_lost') and getattr(o,'enemy_bar_confirmed',False)):
                        s['missing_bar_since'] = s.get('missing_bar_since', now)
                        s['missing_bar_samples'] = s.get('missing_bar_samples', 0) + 1
                        if now - s['missing_bar_since'] >= 1 and s['missing_bar_samples'] >= 3:
                            self.blocked[tid] = 'NO_ENEMY_BAR_NONCOMBAT_CANDIDATE'
                if getattr(o, 'enemy_health_valid', False):
                    s.pop('missing_bar_since', None)
                    s['missing_bar_samples'] = 0
                    hp = o.enemy_health
                    previous = s.get('health')
                    if previous is None:
                        s['progress_at'] = now
                    if previous is not None and hp < previous - (0 if s.get('until_bar_lost') else 1):
                        s['progress_at'] = now
                        s['damage_observed'] = True
                    s['health'] = hp
                    s['health_at'] = now
                    depleted_threshold=0 if s.get('until_bar_lost') else 1
                    s['zero_samples'] = s.get('zero_samples', 0) + 1 if hp <= depleted_threshold else 0
                    if s['zero_samples'] >= 3 and s.get('damage_observed'):
                        s['state'] = 'dead'
                        self.blocked[tid] = 'HEALTH_DEPLETED_CONFIRMED'
                    elif s.get('sent', 0) and now - s.get('progress_at', s['start']) >= 5:
                        if s.get('feedback_required') and s.get('sent', 0) >= 3 and not (s.get('until_bar_lost') and getattr(o,'enemy_bar_confirmed',False)):
                            self.blocked[tid] = 'NO_DAMAGE_NONCOMBAT_CANDIDATE'
                        # Ask the existing semantic recheck loop to review a
                        # target when observed attacks cease making progress.
                        if now - s.get('checked', s['start']) >= 5 and not (s.get('until_bar_lost') and getattr(o,'enemy_bar_confirmed',False)):
                            s['pending'] = True
                            s['recheck_reason'] = 'health_stalled'
                if o.status!='confirmed' or o.semantic_confidence<.6:
                    s['uncertain']=True;continue
                if o.object_type!='monster' or o.relation!='hostile':
                    self.blocked[tid]='대상 의미 변경';continue
                s['uncertain']=False
                if s['memory_id'] is not None and o.memory_id!=s['memory_id']:
                    self.blocked[tid]='대상 외형 ID 변경';continue
                s['seen']=now;s['bbox']=tuple(o.bbox)
            elif tid in self.blocked and any(self.overlap(o.bbox,s['bbox'])>.5 for o in objects):
                s['seen']=now
            else:
                s['state']='lost'
                s['zero_samples']=0
                s['misses']=s.get('misses',0)+1
                if now-s['seen']>(1 if tid in self.blocked else s.get('lost_grace',2)) and (tid in self.blocked or s['misses']>=3):
                    self.sessions.pop(tid,None);self.blocked.pop(tid,None)
                    if self.focus==tid:self.focus=None
    def request(self,obj,settings,now=None):
        now=time.monotonic() if now is None else now;tid=obj.track_id
        if self.is_blocked(obj):return False
        if obj.object_type != 'monster' or obj.relation != 'hostile' or obj.status != 'confirmed':return False
        bar_confirmed = getattr(obj, 'enemy_bar_confirmed', False)
        s=self.sessions.setdefault(tid,{'start':now,'seen':now,'checked':now,'memory_id':obj.memory_id,'pending':settings.get('attack_verify_hostility',False) and not bar_confirmed,'sent':0,'bbox':tuple(obj.bbox),'uncertain':False,'misses':0,'lost_grace':settings.get('attack_lost_grace_ms',2000)/1000,'max_seconds':settings.get('attack_max_seconds',60),'feedback_required':settings.get('attack_require_health_feedback',False),'until_bar_lost':settings.get('attack_until_bar_lost',False)})
        s.setdefault('bar_seen', bar_confirmed)
        self.focus=tid
        if now-s['start']>=settings.get('attack_max_seconds',60) and not (s.get('until_bar_lost') and bar_confirmed):
            self.blocked[tid]='최대 전투 시간 초과';return False
        if bar_confirmed and s.get('recheck_reason') != 'health_stalled':
            s['pending'] = False
        if now-s['checked']>=settings.get('attack_recheck_seconds',8) and not bar_confirmed:s['pending']=True
        return not s['pending'] and not s.get('uncertain',False) and not s.get('bar_missing',False)
    def permits(self,tid):return tid not in self.blocked and not self.sessions.get(tid,{}).get('pending',False) and not self.sessions.get(tid,{}).get('uncertain',False) and not self.sessions.get(tid,{}).get('bar_missing',False)
    def waiting_for_focus(self, now=None):
        now=time.monotonic() if now is None else now
        s=self.sessions.get(self.focus)
        if not s or self.focus in self.blocked:return False
        # Require BOTH a time gap and three distinct detector observations to
        # abandon a disappearing target; never press at a cached target point.
        return s.get('uncertain',False) or s['pending'] or (s.get('misses',0)>0 and (now-s['seen']<s.get('lost_grace',2) or s.get('misses',0)<3))
    def pending(self):return next((tid for tid,s in self.sessions.items() if s['pending'] and tid not in self.blocked),None)
    def confirm(self,tid,valid,now=None):
        s=self.sessions.get(tid)
        if s is None:return
        s['pending']=False;s['checked']=time.monotonic() if now is None else now
        s.pop('recheck_reason', None)
        if not valid:self.blocked[tid]='Qwen 재판정 실패/비적대 객체'
    def on_input(self,c,status,error=None):
        if status=='sent' and (c.action_type=='ATTACK' or c.skill_id) and c.track_id in self.sessions:self.sessions[c.track_id]['sent']+=1
    def snapshot(self):return {'blocked':{str(k):v for k,v in self.blocked.items()},'sessions':[{'track_id':tid,'age_seconds':round(time.monotonic()-s['start'],1),'pending':s['pending'],'sent':s['sent'], 'state': 'dead' if self.blocked.get(tid)=='HEALTH_DEPLETED_CONFIRMED' else 'noncombat_candidate' if 'NONCOMBAT_CANDIDATE' in self.blocked.get(tid,'') else s.get('state','engaged'), 'health': s.get('health'), 'health_fresh':time.monotonic()-s.get('health_at',0)<.5} for tid,s in self.sessions.items()]}

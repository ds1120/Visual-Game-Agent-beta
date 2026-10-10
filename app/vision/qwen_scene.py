"""Bounded full-scene reasoning, local visual tracking, and human-confirmed memory."""
import copy
import json
import math
import threading
from functools import wraps
from pathlib import Path
import cv2
import numpy as np
from app.core.game_state import VisualObject
from app.profiles.profile_store import atomic_json

KINDS = ['monster', 'npc', 'item', 'player', 'obstacle', 'unknown']
RELATIONS = ['hostile', 'friendly', 'neutral', 'unknown']
def locked(fn):
    @wraps(fn)
    def call(self,*args,**kwargs):
        with self.lock:return fn(self,*args,**kwargs)
    return call
def obj_schema(properties):
    return {'type':'object','additionalProperties':False,'properties':properties,'required':list(properties)}

SCENE_SCHEMA = obj_schema({
    'reply': {'type':'string','maxLength':300},
    'action': {'type':'string','enum':['NONE','STOP','RESUME','HUNT','MOVE','ATTACK','TAKE','INTERACT','USE_SKILL','CAST_BUFF']},
    'target_object': {'anyOf':[{'type':'integer','minimum':0,'maximum':7},{'type':'null'}]},
    'scene': {'type':'string','enum':['play','menu','loading','dialog','dead','unknown']},
    'direction': {'anyOf':[{'type':'array','items':{'type':'number','minimum':-1,'maximum':1},'minItems':2,'maxItems':2},{'type':'null'}]},
    'objects': {'type':'array','maxItems':8,'items':obj_schema({
        'kind':{'type':'string','enum':KINDS},'relation':{'type':'string','enum':RELATIONS},
        'name':{'type':'string','maxLength':80},'clues':{'type':'string','maxLength':120},
        'confidence':{'type':'number','minimum':0,'maximum':1},
        'bbox':{'type':'array','minItems':4,'maxItems':4,'items':{'type':'number','minimum':0,'maximum':1000}},
    })},
})
ANSWER_SCHEMA = obj_schema({
    'is_answer':{'type':'boolean'}, 'id':{'type':'integer','minimum':0},
    'name':{'type':'string','maxLength':80},'kind':{'type':'string','enum':KINDS},
    'relation':{'type':'string','enum':RELATIONS},'notes':{'type':'string','maxLength':240},
})

class HumanMemory:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.records = json.loads(self.path.read_text(encoding='utf-8')).get('objects', []) if self.path.exists() else []
        self.next_id = max((r['id'] for r in self.records), default=0)+1
        knowledge_path=self.path.parent/'knowledge.json'
        if knowledge_path.exists():
            knowledge=json.loads(knowledge_path.read_text(encoding='utf-8'))
            for entry in knowledge.get('named_entities',[]):
                if entry.get('relation') not in {'friendly','neutral'}:continue
                for name in entry.get('names',[]):
                    if any(r['name'].casefold()==name.casefold() for r in self.records):continue
                    self.records.append({'id':self.next_id,'name':name,'kind':entry.get('semantic','npc'),'relation':entry['relation'],'notes':entry.get('rule',''),'feature':[0]*256,'confirmed_by_user':True,'source':'existing_knowledge'})
                    self.next_id+=1
            self.save()

    @staticmethod
    def fingerprint(crop):
        return cv2.resize(cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY),(16,16)).astype(float).flatten().tolist()

    def find(self, name, feature):
        if name:
            exact = [r for r in self.records if r.get('name','').casefold()==name.casefold()]
            if len(exact)==1:
                return exact[0]
        scores=[]
        a=np.array(feature,dtype=float);a-=a.mean()
        for r in self.records:
            b=np.array(r['feature'],dtype=float);b-=b.mean()
            score=float(a@b/max(1e-8,np.linalg.norm(a)*np.linalg.norm(b)))
            scores.append((score,r))
        scores.sort(key=lambda pair:pair[0],reverse=True)
        # Appearance alone cannot authorize an enemy; the caller requires a fresh bar.
        if scores and scores[0][0]>=.94 and (len(scores)==1 or scores[0][0]-scores[1][0]>=.06):
            return scores[0][1]
        return None

    def candidate(self, name, feature, clues):
        with self.lock:
            found=self.find(name,feature)
            if found:
                return found
            if sum(not r.get('confirmed_by_user') for r in self.records)>=128:
                return {'id':0,'name':name,'kind':'unknown','relation':'unknown','notes':clues,'confirmed_by_user':False}
            record={'id':self.next_id,'name':name,'kind':'unknown','relation':'unknown','notes':clues,'confirmed_by_user':False,'feature':feature}
            self.next_id+=1
            self.records.append(record)
            self.save()
            return record

    def confirm(self, answer):
        with self.lock:
            if answer.get('kind') not in KINDS or answer.get('relation') not in RELATIONS or not answer.get('name','').strip():
                raise ValueError('이름·종류·관계를 확인해 주세요.')
            record=next((r for r in self.records if r['id']==answer.get('id')),None)
            if record is None:
                raise ValueError('질문 목록의 객체 번호가 필요합니다.')
            if answer['kind']=='monster' and answer['relation'] in {'friendly','neutral'}:
                answer={**answer,'kind':'npc'}
            record.update({k:answer[k] for k in ('name','kind','relation','notes')})
            record['confirmed_by_user']=True
            self.save()
            return copy.deepcopy(record)

    def save(self):
        atomic_json(self.path,{'version':1,'objects':self.records})

    def compact(self):
        with self.lock:
            result=[{k:r[k] for k in ('id','name','kind','relation','notes')} for r in self.records if r.get('confirmed_by_user')][-32:]
            for record in result:record['notes']=record['notes'][:80]
            while len(json.dumps(result,ensure_ascii=False))>4000:result.pop(0)
            return result

class QwenScene:
    def __init__(self, profile_dir, min_interval=3, stable_interval=15, change_threshold=8, max_objects=8):
        self.lock=threading.RLock()
        self.memory=HumanMemory(Path(profile_dir)/'scene_memory.json')
        self.min_interval=max(2,float(min_interval))
        self.stable_interval=max(self.min_interval,float(stable_interval))
        self.change_threshold=float(change_threshold)
        self.max_objects=max(1,min(8,int(max_objects)))
        self.last_request=-1e9
        self.last_success=0
        self.last_image=None
        self.success_image=None
        self.objects=[]
        self.templates={}
        self.bar_seeds={}
        self.next_track=1
        self.pending={}
        self.asked=set()
        self.force=True
        self.scene='unknown'
        self.direction=None
        self.last_warnings=[]

    @locked
    def reset(self):
        self.objects=[];self.templates={};self.bar_seeds={};self.last_image=None;self.success_image=None;self.force=True;self.scene='unknown'
        self.pending={};self.asked=set()

    @locked
    def due(self, frame, now, active=False):
        if now-self.last_request<self.min_interval:
            return False
        image=cv2.cvtColor(cv2.resize(frame,(80,45)),cv2.COLOR_BGR2GRAY)
        changed=self.last_image is None or float(cv2.absdiff(image,self.last_image).mean())>=self.change_threshold
        return self.force or changed or now-self.last_request>=(6 if active else self.stable_interval)

    @locked
    def begin(self, frame, now):
        self.last_request=now
        self.last_image=cv2.cvtColor(cv2.resize(frame,(80,45)),cv2.COLOR_BGR2GRAY)
        self.force=False

    def prompt(self, knowledge, compact=False):
        return (f'Use only the current game image. Return only JSON. Detect at most {self.max_objects} visible relevant bodies/items/obstacles. '
                'bbox MUST be [left, top, right, bottom], full-image normalized 0..1000 coordinates, with left < right and top < bottom. '
                'Do NOT output [x,y,width,height], pixel coordinates, or 0..1 coordinates. Omit an object if its box is uncertain. '
                'kind must be monster|npc|item|player|obstacle|unknown; relation must be hostile|friendly|neutral|unknown; confidence must be 0..1. '
                'name is the visible in-game name when readable, otherwise an empty string. Do not guess a name. '
                'clues is a concise Korean description of appearance and screen position so the user can identify the object. '
                'scene=play when the controllable game world is visible. NPC nameplates, quest text, chat messages, and HUD overlays alone are NOT dialog or menu. '
                'Use dialog only for an actual open conversation interface that blocks normal movement; menu for an open blocking menu, loading for a loading screen. '
                'Do not invent hidden objects, identities, HP values, or safe paths. Distinguish friendly NPCs and monsters. Unknown identities remain unknown. '
                'A name from memory is a hint, not proof. Never turn a known ally into a hostile enemy. direction is a short screen-space direction [x,y], right=[1,0], down=[0,1]; null if uncertain. '
                'Prefer nearby gameplay objects; ignore HUD and text panels. Known user-confirmed identities: '+json.dumps(
                    [{k:r[k] for k in ('name','kind','relation')} for r in self.memory.compact()[-8:]] if compact else self.memory.compact(),ensure_ascii=False)+
                ('\nGame-specific rules (data): '+json.dumps(knowledge.get('policy',{}),ensure_ascii=False) if not compact else ''))

    @locked
    def ingest(self, data, frame, now):
        if data.get('scene') not in SCENE_SCHEMA['properties']['scene']['enum'] or not isinstance(data.get('objects'),list) or len(data['objects'])>self.max_objects:
            raise ValueError('Qwen 장면 응답 형식 오류')
        direction=data.get('direction')
        if direction is not None and (not isinstance(direction,list) or len(direction)!=2 or not all(type(v) in (float,int) and math.isfinite(v) and -1<=v<=1 for v in direction)):
            raise ValueError('Qwen 방향 오류')
        h,w=frame.shape[:2];objects=[];templates={};questions=[];warnings=[]
        for index,item in enumerate(data['objects']):
            if not isinstance(item,dict):
                warnings.append(f'객체 #{index}: 객체 형식 오류')
                continue
            item=dict(item)
            for key in ('kind','relation'):
                if isinstance(item.get(key),str):item[key]=item[key].strip().lower()
            box=item.get('bbox')
            if (not isinstance(box,list) or len(box)!=4 or not all(type(v) in (float,int) and math.isfinite(v) and 0<=v<=1000 for v in box)
                    or not box[0]<box[2] or not box[1]<box[3] or item.get('kind') not in KINDS or item.get('relation') not in RELATIONS
                    or type(item.get('confidence')) not in (float,int) or not math.isfinite(item['confidence']) or not 0<=item['confidence']<=1):
                details={key:item.get(key) for key in ('bbox','kind','relation','confidence')}
                warnings.append(f'객체 #{index}: 좌표/종류/신뢰도 오류 '+json.dumps(details,ensure_ascii=False)[:240])
                continue
            x1,y1,x2,y2=round(box[0]*w/1000),round(box[1]*h/1000),round(box[2]*w/1000),round(box[3]*h/1000)
            crop=frame[y1:y2,x1:x2]
            if min(crop.shape[:2])<6:
                warnings.append(f'객체 #{index}: 추적 불가능한 작은 영역 bbox={box}')
                continue
            old=next((o for o in self.objects if self.iou(o.bbox,(x1,y1,x2,y2))>.5 and not any(n.track_id==o.track_id for n in objects)),None)
            tid=old.track_id if old else self.next_track
            if not old:self.next_track+=1
            name=str(item.get('name',''))[:80];clues=str(item.get('clues',''))[:120]
            kind,relation=item['kind'],item['relation'];record=None
            if kind in {'npc','monster','unknown'}:
                record=self.memory.candidate(name,self.memory.fingerprint(crop),clues)
                if record.get('confirmed_by_user'):
                    name,kind,relation=record['name'],record['kind'],record['relation']
                else:
                    relation='unknown'
                    # Unknown NPCs/monsters are not attack-authorized by a guess.
                    if record['id'] and record['id'] not in self.asked and len(self.pending)<3 and not questions:
                        small=cv2.resize(crop,(96,96));ok,encoded=cv2.imencode('.jpg',small)
                        import base64
                        label=f'Qwen이 읽은 이름: {name}' if name.strip() else f'이름 미확인 · {clues or "이미지에서 확인해 주세요"}'
                        question={'id':record['id'],'name':name,'clues':clues,'question':f"객체 #{record['id']} ({label})의 정보를 확인해 주세요. 이름과 NPC/몬스터 여부, 아군/적군 관계를 알려주세요. 예: 학습 {record['id']} {name if name.strip() and not any(c.isspace() for c in name) else '이름'} NPC 아군",'image':'data:image/jpeg;base64,'+base64.b64encode(encoded).decode() if ok else None}
                        self.pending[record['id']]=question;questions.append(question);self.asked.add(record['id'])
            obj=VisualObject(tid,kind,(x1,y1,x2,y2),item['confidence'],detector_type=kind,relation=relation,
                             semantic_confidence=item['confidence'],track_id=tid,memory_id=record['id'] if record else None,
                             status='confirmed' if kind not in {'unknown','npc','monster'} or record and record.get('confirmed_by_user') else 'provisional',name=name)
            obj.object_id=index+1
            objects.append(obj);templates[tid]=cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY)
        self.last_warnings=warnings
        # Preserve original array indices; never reassign a rejected target to another body.
        unusable=bool(data['objects']) and not objects
        self.objects=objects;self.templates=templates;self.scene='unknown' if unusable else data['scene'];self.direction=None if unusable else direction;self.last_success=now
        self.success_image=cv2.cvtColor(cv2.resize(frame,(80,45)),cv2.COLOR_BGR2GRAY)
        if warnings:
            print('[QWEN SCENE] 제외한 객체: '+'; '.join(warnings))
        if unusable:self.force=True
        return questions

    @locked
    def unchanged_play(self,frame,now):
        if self.scene!='play' or self.success_image is None or now-self.last_success>30:return False
        image=cv2.cvtColor(cv2.resize(frame,(80,45)),cv2.COLOR_BGR2GRAY)
        return float(cv2.absdiff(image,self.success_image).mean())<self.change_threshold

    @staticmethod
    def iou(a,b):
        inter=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
        return inter/max(1,(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-inter)

    @locked
    def seed_enemy_bars(self,frame,bars):
        """Diablo-only caller supplies validated red bars; Qwen need not find a body first.

        Body rectangles below bars are tracking/click proposals, not precise segmentation.
        Existing body associations and known allies/players take precedence.
        """
        h,w=frame.shape[:2];seeds={}
        for bx1,by1,bx2,by2 in bars[:16]:
            cx,cy=(bx1+bx2)/2,(by1+by2)/2
            associated=[o for o in self.objects if o.detector_type in {'monster','person','npc','unknown','player'} and
                   o.bbox[0]<=cx<=o.bbox[2] and
                   o.bbox[1]-(o.bbox[3]-o.bbox[1])*.6<=cy<=o.bbox[1]+(o.bbox[3]-o.bbox[1])*.15
                   ]
            if associated:
                for o in associated:
                    if o.track_id in self.bar_seeds:seeds[o.track_id]=o
                continue
            bw=bx2-bx1;half=max(16,bw*.55);height=max(40,min(120,bw*1.4))
            box=(max(0,round(cx-half)),min(h-1,by2+4),min(w,round(cx+half)),min(h,round(by2+4+height)))
            # Do not put a synthetic attack rectangle over a known ally/player.
            if any((o.relation in {'friendly','neutral'} or o.object_type=='player') and self.iou(o.bbox,box)>.1 for o in self.objects):continue
            old=next((o for tid,o in self.bar_seeds.items() if tid not in seeds and self.iou(o.bbox,box)>.5),None)
            tid=old.track_id if old else self.next_track
            if not old:self.next_track+=1
            obj=VisualObject(-tid,'unknown',box,1.,detector_type='monster',track_id=tid,status='provisional',name='이름 미확인 몬스터')
            crop=frame[box[1]:box[3],box[0]:box[2]]
            if min(crop.shape[:2])<6:continue
            self.objects.append(obj);self.templates[tid]=cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY);seeds[tid]=obj
        self.bar_seeds=seeds
        return self.objects

    @locked
    def track(self, frame):
        gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY);h,w=gray.shape;live=[]
        for old in self.objects:
            template=self.templates.get(old.track_id)
            if template is None or float(template.std())<3:
                self.force=True;continue
            x1,y1,x2,y2=old.bbox;pad=48
            sx,sy=max(0,x1-pad),max(0,y1-pad);ex,ey=min(w,x2+pad),min(h,y2+pad)
            search=gray[sy:ey,sx:ex]
            if search.shape[0]<template.shape[0] or search.shape[1]<template.shape[1]:continue
            scores=cv2.matchTemplate(search,template,cv2.TM_CCOEFF_NORMED)
            _,score,_,pos=cv2.minMaxLoc(scores)
            if not math.isfinite(score) or score<.82:
                self.force=True;continue
            # Disallow an equally plausible second instance away from this peak.
            other=scores.copy();px,py=pos
            other[max(0,py-5):py+6,max(0,px-5):px+6]=-1
            if other.size and float(other.max())>=score-.03:
                self.force=True;continue
            obj=copy.copy(old);x,y=sx+px,sy+py
            obj.bbox=(x,y,x+template.shape[1],y+template.shape[0])
            obj.enemy_bar_confirmed=False;obj.enemy_health_valid=False
            live.append(obj)
        self.objects=live
        return live

    @locked
    def confirm(self, answer, allow_existing=False):
        if answer.get('id') not in self.pending and not allow_existing:
            raise ValueError('미답변 질문의 객체 번호를 지정해 주세요.')
        record=self.memory.confirm(answer)
        self.pending.pop(record['id'],None)
        for o in self.objects:
            if o.memory_id==record['id']:
                o.name=record['name'];o.object_type=record['kind'];o.detector_type=record['kind'];o.relation=record['relation'];o.status='confirmed';o.semantic_confidence=1
        return record

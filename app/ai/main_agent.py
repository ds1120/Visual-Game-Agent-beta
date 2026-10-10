"""OpenCV + Qwen agent; inherits capture/HUD/inputs, never runs YOLO or crop classification."""
import asyncio
import configparser
import copy
import json
import logging
import re
import time
import math
import random
import numpy as np
import cv2
from app.vision.orange_route import orange_route_target
from app.ai.visual_agent import VisualAgent
from app.ai.game_conversation import GameConversation
from app.ai.game_conversation import validate_directive
from app.vision.qwen_scene import QwenScene, SCENE_SCHEMA, ANSWER_SCHEMA
from app.profiles.profile_store import PROJECT_ROOT
from app.core.fast_policy import FastPolicy, command
from app.core.control_hotkeys import mouse_left_down
from app.core.physical_mouse import physical_mouse
from app.core.game_state import HUDState
from app.profiles.profile_store import validate_document
from app.profiles.gameplay_spec import load_gameplay_spec
from app.vision.click_safety import click_exclusions, point_outside
from app.core.minimap_memory import MinimapMemory
from app.core.route_geometry import corridor_clear
from app.core.click_journey import ClickJourney
from app.vision.game_viewport import game_viewport
from app.vision.screen_route_guide import screen_route_guide
from app.vision.space_prompt import space_prompt
from app.vision.arrival_notice import arrival_notice
from app.core.local_navigation import minimap_mask
from dataclasses import replace

class EncounterCombatPolicy(FastPolicy):
    def decide(self,objects,hud,docs,shape,*args,**kwargs):
        c=super().decide(objects,hud,docs,shape,*args,**kwargs,combat_on_move=True)
        combat=docs['input.json'].get('combat',{})
        origin=getattr(self,'player_origin',docs.get('navigation.json',{}).get('steering',{}).get('player_screen',[.5,.5]))
        if c.action_type=='DODGE':
            danger=[o for o in objects if o.status=='confirmed' and o.object_type=='monster' and o.relation=='hostile']
            if danger:
                h,w=shape[:2];enemy=min(danger,key=lambda o:math.hypot((o.bbox[0]+o.bbox[2])/2-origin[0]*w,o.bbox[3]-origin[1]*h))
                c=replace(c,direction=((origin[0]*w-(enemy.bbox[0]+enemy.bbox[2])/2)/h,(origin[1]*h-enemy.bbox[3])/h))
            return replace(c,action_type='MOVE') if combat.get('dodge_mode')=='move' else c
        target=next((o for o in objects if o.track_id==c.track_id),None)
        if c.action_type!='ATTACK' or target is None:return c
        h,w=shape[:2];dx=((target.bbox[0]+target.bbox[2])/2/w-origin[0])*w/h
        dy=target.bbox[3]/h-origin[1];distance=math.hypot(dx,dy)
        stance=combat.get('stance','stationary')
        # A small release band prevents alternating attack/move at a distance boundary.
        previous=getattr(self,'spacing',None)
        near=combat.get('minimum_distance',.18)+(.03 if previous==('away',target.track_id) else 0)
        far=combat.get('ranged_attack_distance',.45) if stance=='ranged' else combat.get('attack_distance',.16)
        far-=.03 if previous==('approach',target.track_id) else 0
        if stance=='ranged' and distance<near and distance>1e-6:
            self.spacing=('away',target.track_id)
            return command('MOVE',direction=(-dx,-dy),source='COMBAT_SPACING',track_id=target.track_id,epoch=c.decision_epoch)
        if stance in {'melee','ranged'} and distance>far:
            self.spacing=('approach',target.track_id)
            return command('MOVE',direction=(dx,dy),source='COMBAT_APPROACH',track_id=target.track_id,epoch=c.decision_epoch)
        self.spacing=None
        return c

class SceneConversation:
    def __init__(self, agent):
        self.agent=agent
        self.base=GameConversation(agent.chat_vl,agent.store)

    @property
    def history(self):return self.base.history
    @history.setter
    def history(self,value):self.base.history=value

    def propose(self,message,image,objects,hud):
        agent=self.agent;scene=agent.scene
        epoch=agent._epoch
        # Explicit teaching can be parsed locally, without another model call.
        match=re.fullmatch(r'학습\s+(\d+)\s+(\S+)\s+(NPC|npc|몬스터|몹|아이템|플레이어|장애물)\s+(아군|적군|중립)',message.strip())
        answer=None
        if match:
            kinds={'npc':'npc','몬스터':'monster','몹':'monster','아이템':'item','플레이어':'player','장애물':'obstacle'}
            answer={'is_answer':True,'id':int(match[1]),'name':match[2],'kind':kinds[match[3].lower()],
                    'relation':{'아군':'friendly','적군':'hostile','중립':'neutral'}[match[4]],'notes':'사용자 확인'}
        elif not agent._foreground():
            raise ValueError('Qwen 처리 대기: 게임 창을 활성화하세요.')
        elif scene.pending and not re.search(r'이동|공격|사냥|중단|물약|스킬|버프|설정',message):
            with scene.lock:
                questions=[{k:q[k] for k in ('id','name','clues','question')} for q in scene.pending.values()]
            result=agent.chat_vl.chat_profile(message,
                '사용자가 객체 확인 질문에 답했는지 판별하세요. 다른 명령·질문이면 is_answer=false입니다. '
                '대상이 불명확하거나 이름·관계를 알 수 없으면 false. 질문 번호가 없고 미답변 질문이 정확히 하나면 그 번호를 사용하세요. '
                '몬스터=monster, NPC=npc, 아군=friendly, 적군=hostile, 중립=neutral. 사용자 답변에 없는 사실을 만들지 마세요. 질문: '+json.dumps(questions,ensure_ascii=False),
                [],ANSWER_SCHEMA,None)
            if result.data.get('is_answer'):
                answer=result.data
        if answer:
            if epoch!=agent._epoch or scene is not agent.scene:
                raise ValueError('중단·게임 변경 후 도착한 학습 답변을 폐기했습니다.')
            if agent._processing_halted:
                raise ValueError('처리 중단 상태입니다. 재개 후 답해주세요.')
            record=scene.confirm(answer,allow_existing=bool(match))
            agent.emit_web_event('memory_confirmed',memory_id=record['id'],name=record['name'])
            _,revisions=agent.store.snapshot()
            return {'reply':f"저장했습니다: {record['name']} · {record['kind']} / {record['relation']}",
                    'operations':[],'revisions':revisions,'directive':{'action':'NONE','track_id':None,'direction':None,'ttl_seconds':1}}
        if re.search(r'설정|기준|바꿔|변경|프로필|키.*저장',message):
            return self.base.propose(message,image,objects,hud)
        if re.search(r'이동|걸어|가줘',message) and not re.search(r'공격|몬스터|적군|화면|누구',message):
            # Movement instructions never send the world image to Qwen.
            direction=None
            for names,vector in ((r'북|위쪽|위로',(0,-1)),(r'남|아래',(0,1)),(r'동쪽|오른쪽',(1,0)),(r'서쪽|왼쪽',(-1,0))):
                if re.search(names,message):direction=vector;break
            if direction is None:
                roi=MinimapMemory._crop(image,agent._docs['navigation.json']) if image is not None else None
                if roi is None:raise ValueError('이동에는 보정한 미니맵 캡처가 필요합니다.')
                schema={'type':'object','properties':{'direction':{'anyOf':[{'type':'array','items':{'type':'number','minimum':-1,'maximum':1},'minItems':2,'maxItems':2},{'type':'null'}]},'reply':{'type':'string','maxLength':160}},'required':['direction','reply'],'additionalProperties':False}
                result=agent.vl._request(roi,'이 미니맵만 보고 사용자의 이동 방향을 화면 방향 벡터로 해석하세요. 통로 상세 경로는 로컬에서 검증합니다. 불명확하면 direction=null. 사용자: '+message,schema,'minimap_direction',agent.profile.name,max_tokens=192,timeout=5)
                direction=result.data.get('direction')
            if epoch!=agent._epoch or agent._processing_halted or not agent._foreground():raise ValueError('중단 또는 창 비활성 후 이동 판단을 폐기했습니다.')
            directive={'action':'MOVE' if direction else 'NONE','track_id':None,'direction':list(direction) if direction else None,'ttl_seconds':3}
            validate_directive(directive,[])
            _,revisions=agent.store.snapshot()
            return {'reply':'미니맵 통로를 검증하며 이동합니다.' if direction else '이동 방향을 확인하지 못했습니다.','operations':[],'revisions':revisions,'directive':directive}
        if image is not None and not agent._processing_halted:
            # This runs inside the base chat worker's shared Qwen lock.
            epoch=agent._epoch
            data=agent.scan_scene_sync(image,epoch,user_message=message)
            if data is None:raise ValueError('늦게 도착한 화면 판단을 폐기했습니다.')
            action=data.get('action','NONE')
            target=None
            if action in {'ATTACK','TAKE','INTERACT'}:
                index=data.get('target_object')
                target=next((o for o in agent.scene.objects if type(index) is int and o.object_id==index+1),None)
            directive={'action':action,'track_id':target.track_id if target else None,
                       'direction':data.get('direction') if action=='MOVE' else None,'ttl_seconds':3 if action=='MOVE' else 1}
            validate_directive(directive,agent.scene.objects)
            _,revisions=agent.store.snapshot()
            return {'reply':str(data.get('reply','화면을 확인했습니다.'))[:300],'operations':[],'revisions':revisions,'directive':directive}
        raise ValueError('최신 게임 캡처가 필요합니다. 게임 창과 캡처 설정을 확인하세요.')

class MainAgent(VisualAgent):
    qwen_requires_foreground=True
    focus_activation_starts_hunt=False
    default_auto_hunt=False

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        controller=self.scheduler.executor._input
        physical_mouse.start()
        watch=getattr(controller,'start_manual_mouse_watch',None)
        if watch is not None:
            watch(lambda:getattr(self,'_release_paused',False) or self._manual_mouse_pause_enabled() and mouse_left_down(),self._physical_mouse_pause)
        self._manual_control=False
        for client in (self.vl,self.chat_vl):
            if callable(getattr(type(client),'enable_server_busy_guard',None)):client.enable_server_busy_guard()
        self._move_only=False
        self._hunt_active=False
        self._paused=True
        self._paused=False
        self._qwen_stopped=False
        for client in {id(self.vl):self.vl,id(self.chat_vl):self.chat_vl}.values():
            if callable(getattr(type(client),'enable_cancellation',None)):client.enable_cancellation()
        self.policy=EncounterCombatPolicy()
        self.attack_until_bar_lost=True
        self._combat_spec_cache=None
        self._last_move_block_reason=None
        self._scene_scan_status='idle'
        self.startup_hud_only=True
        self._startup_hud_attempted=False
        self._startup_hud_inflight=False
        self._hud_waiting=True
        self._hp_display_value=None
        self._hp_display_missing_at=time.monotonic()
        self.yolo.enabled=True
        cfg=configparser.ConfigParser();cfg.read(PROJECT_ROOT/'config.ini',encoding='utf-8')
        self._buff_threshold=cfg.getfloat('DIABLO4','buff_match_threshold',fallback=.78)
        self._configure_buff_monitor()
        self.scene_settings={
            'min_interval':cfg.getfloat('SCENE','min_interval',fallback=3),
            'stable_interval':cfg.getfloat('SCENE','stable_interval',fallback=15),
            'change_threshold':cfg.getfloat('SCENE','change_threshold',fallback=8),
            'max_objects':cfg.getint('SCENE','max_objects',fallback=8),
        }
        self._tracking_interval=max(.05,min(.5,cfg.getfloat('SCENE','tracking_interval',fallback=.1)))
        self._background_scene_timeout=max(5.,min(30.,cfg.getfloat('SCENE','request_timeout',fallback=15)))
        self._background_scene_tokens=max(256,min(1024,cfg.getint('SCENE','max_tokens',fallback=384)))
        self.scene=QwenScene(self.profile.profile_dir,**self.scene_settings)
        self.conversation=SceneConversation(self)
        self._scene_identity=None
        self._scene_error_until=0
        self._scene_failures=0
        self._scene_scan_task=None
        self.minimap_memory=MinimapMemory()
        self.click_journey=ClickJourney()
        self._next_navigation_plan=None
        self._combat_spec()

    def _prepare_live_vision(self,document):
        return None

    def _apply_live_vision(self,document,prepared=None):
        super()._apply_live_vision(document,prepared)
        self.yolo.enabled=True;self.yolo.interval=.1
        if hasattr(self,'scene'):self.scene.reset()

    async def switch_game(self,name):
        await super().switch_game(name)
        self._follow_orange_route=False
        self._move_only=False
        self._combat_spec_cache=None
        self._combat_spec()
        self.policy=EncounterCombatPolicy()
        self.minimap_memory.reset()
        self.click_journey.reset()
        self._configure_buff_monitor()
        self.scene=QwenScene(self.profile.profile_dir,**self.scene_settings)
        self.conversation=SceneConversation(self)
        self._scene_identity=None
        self._scene_error_until=0
        self._scene_failures=0
        self._hud_waiting=True
        self._hp_display_value=None
        self._hp_display_missing_at=time.monotonic()

    async def _idle_sensor_loop(self):
        while self._running:await asyncio.sleep(.1)

    async def _movement_test_loop(self):
        while self._running:
            if getattr(self,'_release_paused',False):
                await asyncio.sleep(.02)
                continue
            if self._paused or self._processing_halted or not self._foreground() or not self._fresh():
                release=getattr(getattr(self.scheduler,'executor',None),'release_held_move',None)
                if release is not None:await release()
                await asyncio.sleep(.05)
                continue
            if (not getattr(self,'_stationary_hunt_mode',False) and self._manual_mouse_pause_enabled()
                    and await self._stationary_manual_move_tick()):
                await asyncio.sleep(.02)
                continue
            if self._move_only and await self._arrival_notice_tick():
                await asyncio.sleep(.02)
                continue
            if (not getattr(self,'_stationary_hunt_mode',False) and not getattr(self,'_stationary_skill_mode',False)
                    and self.profile.name=='diablo4' and await self._space_prompt_tick()):
                release=getattr(getattr(self.scheduler,'executor',None),'release_held_move',None)
                if release is not None:await release()
                await asyncio.sleep(.02)
                continue
            if self._move_only and not getattr(self,'_stationary_hunt_mode',False) and not getattr(self,'_stationary_skill_mode',False):
                guide=await self._update_screen_move_guide()
                if getattr(self,'_manual_skill_mode',False) and await self._manual_skill_tick():
                    pass  # Skills and cursor tracking share the movement cycle.
                if guide and guide.get('arrow_tip'):
                    c=self._arrow_move_command(guide)
                    guide['click_target']=c.target
                    if await self._arrow_escape_tick(c):
                        await asyncio.sleep(.02)
                        continue
                    self._last_requested_command=c
                    await self.scheduler.submit(c)
                else:
                    release=getattr(getattr(self.scheduler,'executor',None),'release_held_move',None)
                    if release is not None:await release()
                await asyncio.sleep(.02)
                continue
            if getattr(self,'_stationary_hunt_mode',False):
                await self._stationary_hunt_tick()
                await asyncio.sleep(.03)
                continue
            if getattr(self,'_screen_guide_enabled',False):
                guide=await self._update_screen_move_guide()
                if guide is None:
                    self._screen_move_guide=None
                    orange=getattr(self.minimap_memory,'orange_mask',None)
                    self._orange_guide_active=orange is not None and bool(np.any(orange))
                    self._hunt_preferred_direction=None if self._orange_guide_active else self._random_move_heading()
                else:self._hunt_preferred_direction=guide['direction']
            heading=getattr(self,'_hunt_preferred_direction',None) or self.minimap_memory.last_direction or (1.,0.)
            c=command('MOVE',source='HUNT_EXPLORE',direction=heading,epoch=self._epoch)
            guide=getattr(self,'_screen_move_guide',None) if getattr(self,'_screen_guide_enabled',False) else None
            if not (guide and guide.get('arrow_tip')):
                self._review_missing_navigation_route()
                self._recover_rejected_movement()
            if getattr(self,'_stationary_skill_mode',False):
                skill=self._hunt_attack_rotation(command('ATTACK',source='MANUAL_SKILL',epoch=self._epoch))
                if skill is not None:await self.scheduler.submit(skill)
                await asyncio.sleep(.05)
                continue
            if getattr(self,'_manual_skill_mode',False) or getattr(self,'_repeat_skills_hunting',False):
                if await self._manual_skill_tick():
                    await asyncio.sleep(.05)
                    continue
            if not getattr(self,'_manual_skill_mode',False) and self._movement_hunt_enabled():
                attack=self._movement_hunt_command()
                if attack is not None:
                    self._last_requested_command=attack
                    await self.scheduler.submit(attack)
                    await asyncio.sleep(.1)
                    continue
            guide=getattr(self,'_screen_move_guide',None) if getattr(self,'_screen_guide_enabled',False) else None
            if guide and guide.get('arrow_tip'):
                c=self._arrow_move_command(guide)
                guide['click_target']=c.target
                guide['direction']=c.direction
                self._hunt_preferred_direction=c.direction
                self._last_requested_command=c
                self._next_navigation_plan=None
                await self.scheduler.submit(c)
                await asyncio.sleep(.05)
                continue
            if await self._try_navigation_escape():
                await asyncio.sleep(.05)
                continue
            if not self._continue_navigation(c):
                if await self._try_navigation_escape():
                    await asyncio.sleep(.05)
                    continue
                settings=copy.deepcopy(self._docs['navigation.json'])
                c=self._prepare_navigation(c,settings)
                if c.action_type=='MOVE':
                    step=self.navigator.choose(c.direction,self._objects,self._latest_frame.shape,settings,self._minimap_mask)
                    if step is None:
                        self._navigation_click_failed(c)
                        c=command('STOP',reason='PATH_BLOCKED',epoch=self._epoch)
                    else:c=replace(c,direction=step[0],target=step[1])
                c=self._finalize_navigation_command(c)
                self._last_requested_command=c
                if self._dispatch_action(c):await self.scheduler.submit(c)
            await asyncio.sleep(.05)

    async def _arrival_notice_tick(self):
        now=time.monotonic()
        task=getattr(self,'_arrival_notice_task',None)
        if task is not None and task.done():
            self._arrival_notice_task=None
            try:arrived=task.result()
            except Exception as exc:
                if not getattr(self,'_arrival_notice_error_logged',False):
                    print(f'[ARRIVAL OCR] failed: {exc}')
                    self._arrival_notice_error_logged=True
                arrived=False
            if (arrived and getattr(self,'_arrival_notice_epoch',None)==self._epoch
                    and self._move_only and not self._paused and not self._processing_halted
                    and self._foreground() and self._fresh()):
                repeat=getattr(self,'_manual_skill_mode',False) or getattr(self,'_repeat_skills_hunting',False)
                if repeat:
                    self._epoch+=1
                    self._move_only=False;self._screen_guide_enabled=False
                    self._screen_move_guide=None;self._movement_hunt_requested=False
                    self._stationary_skill_mode=True;self._manual_skill_mode=True
                    self._repeat_skills_hunting=False
                    await self.scheduler.submit_emergency(command('STOP',source='DESTINATION_REACHED',epoch=self._epoch))
                    self.emit_web_event('control_mode',mode='repeat_skills',message='목적지 도착 · 이동 중단 · 반복스킬 유지')
                else:await self.handle_control('/stop')
                print('[ARRIVAL] 목적지에 도착했습니다 · 이동 중단')
                return True
            task=None
        if task is None and now-getattr(self,'_arrival_notice_scanned_at',-1e9)>=.5:
            self._arrival_notice_scanned_at=now;self._arrival_notice_epoch=self._epoch
            self._arrival_notice_task=asyncio.create_task(asyncio.to_thread(arrival_notice,self._latest_frame))
        return False

    def _space_heading_allows(self):
        move=getattr(self,'_last_arrow_move_sent',None)
        if (move is None or move.decision_epoch!=self._epoch or move.direction is None
                or time.monotonic()-getattr(self,'_last_arrow_move_sent_at',-1e9)>.5):return False
        guide=screen_route_guide(self._latest_frame,self._world_player_origin(),arrow_only=True,fast_only=True)
        if not guide:return False
        _,heading=self._arrow_position_heading(guide)
        if heading is None:return False
        direction=np.asarray(move.direction,dtype=float)
        norm=float(np.linalg.norm(heading)*np.linalg.norm(direction))
        return bool(norm>1e-6 and np.all(np.isfinite(heading)) and np.all(np.isfinite(direction))
                    and float(heading@direction)/norm>=math.cos(math.radians(30))-1e-9)

    async def _space_prompt_tick(self):
        now=time.monotonic()
        if now<getattr(self,'_space_prompt_wait_until',0):return True
        if now<getattr(self,'_space_prompt_pending_until',0):return True
        if now-getattr(self,'_space_prompt_scan_at',-1e9)<.15:return False
        self._space_prompt_scan_at=now
        # World click exclusions protect mouse targets, not keyboard prompts.
        # The detector itself excludes the permanent bottom HUD keycap.
        box=space_prompt(self._latest_frame)
        if box is None:
            if getattr(self,'_space_prompt_missing_since',None) is None:self._space_prompt_missing_since=now
            if now-self._space_prompt_missing_since>=.6:self._space_prompt_latched=False
            return False
        self._space_prompt_missing_since=None
        if getattr(self,'_space_prompt_latched',False):return False
        if not self._space_heading_allows():return False
        self._space_prompt_seen_at=now
        print('[SPACE] 화면 안내 감지 · 이동 중단 후 Space 전송')
        self._space_prompt_pending_until=now+.7
        c=command('INTERACT',source='SCREEN_SPACE_PROMPT',reason='화면 Space 안내 · 입력 후 즉시 재탐색',
                  duration_ms=30,epoch=self._epoch)
        self._last_requested_command=c
        await self.scheduler.submit_emergency(c)
        return True

    async def _arrow_escape_tick(self,move):
        now=time.monotonic()
        if now<getattr(self,'_arrow_escape_pending_until',0):return True
        if now<getattr(self,'_arrow_escape_retry_at',0):return False
        settings=getattr(self,'_docs',{}).get('input.json',{})
        if not settings.get('movement_skill',{}).get('enabled') or 'DODGE' in settings.get('disabled_actions',[]):return False
        memory=getattr(self,'minimap_memory',None)
        if memory is None:return False
        with memory.lock:
            if not memory.valid or not memory.stuck or now-memory.last_update>.75:return False
        sent=getattr(self,'_last_arrow_move_sent',None)
        if sent is None or sent.decision_epoch!=self._epoch or now-getattr(self,'_last_arrow_move_sent_at',-1e9)>.8:return False
        with memory.lock:
            vector=memory.escape_vector(move.direction)
            if vector is None:
                print('[MOVE ESCAPE] 탈출 가능한 미니맵 통로 없음 · 지도 재확인')
                self._arrow_escape_retry_at=now+1
                self._arrow_escape_pending_until=now+1
                return True
            angle=math.radians(memory.rotation);cosine,sine=math.cos(angle),math.sin(angle)
            screen_vector=np.array([vector[0]*cosine+vector[1]*sine,-vector[0]*sine+vector[1]*cosine])
        h,w=self._latest_frame.shape[:2]
        _,top,_,bottom=game_viewport(self._latest_frame)
        scale=self._docs.get('navigation.json',{}).get('minimap',{}).get('mapping',{}).get('screen_pixels_per_map_pixel',12)*(bottom-top)/1080
        target=np.asarray(self._world_player_origin())+screen_vector*scale/np.array([w,h])
        direction=screen_vector/np.linalg.norm(screen_vector)
        self._arrow_escape_pending_until=now+.7
        self._arrow_escape_retry_at=now+3
        c=command('DODGE',source='SCREEN_ARROW_ESCAPE',target=tuple(target),direction=tuple(direction),
                  reason='미니맵 통로 계산 · 검증된 탈출 방향으로 이동 스킬 사용',epoch=self._epoch)
        self._last_requested_command=c
        print(f'[MOVE ESCAPE] 이동 정체 확인 · 미니맵 탈출 방향 {tuple(np.round(direction,3))} · 이동 스킬 사용')
        await self.scheduler.submit_emergency(c)
        return True

    async def _manual_skill_tick(self):
        if self._manual_mouse_pause_enabled() and (mouse_left_down() or getattr(self,'_stationary_left_down',False)):
            return await self._stationary_manual_move_tick()
        pending=getattr(self,'_manual_skill_pending_at',None)
        if pending is not None:
            if time.monotonic()-pending<1.5:return not getattr(self,'_move_only',False)
            self._manual_skill_pending_at=None
            self._manual_skill_move_required=True
            self._manual_skill_resume_until=time.monotonic()+.15
        if getattr(self,'_manual_skill_move_required',False):
            if time.monotonic()<getattr(self,'_manual_skill_resume_until',0):return False
            # A blocked movement must never suspend explicit skill repetition.
            self._manual_skill_move_required=False
        skill=self._hunt_attack_rotation(command('ATTACK',source='MANUAL_SKILL',epoch=self._epoch))
        if skill is None:return False
        self._manual_skill_pending_at=time.monotonic()
        await self.scheduler.submit(skill)
        return True

    def _movement_hunt_enabled(self):
        return (getattr(self,'_movement_hunt_requested',False)
                and self._hunt_active and not self._move_only and not self._paused)

    def _manual_mouse_pause_enabled(self):
        return (getattr(self,'_stationary_hunt_mode',False)
                or getattr(self,'_hunt_active',False))

    def _physical_mouse_pause(self):
        self._movement_hunt_target=None
        self._movement_target_missing=None
        self._stationary_attack_record=None
        self._manual_skill_pending_at=None
        self._manual_skill_move_required=False
        self._manual_skill_resume_until=0
        self.scheduler.executor._held_attack_command=None
        self.scheduler.executor._held_move_command=None

    async def _stationary_hunt_tick(self):
        if await self._stationary_manual_move_tick():return
        if getattr(self,'_manual_skill_mode',False) and await self._manual_skill_tick():return
        executor=self.scheduler.executor
        if getattr(executor,'_held_attack_command',None) is None:
            await executor.execute(self._stationary_hold_command())
        attack=self._movement_hunt_command()
        if attack is not None and attack.action_type=='ATTACK':
            await self.scheduler.submit(self._stationary_hold_command(attack.target,attack.track_id))

    async def _stationary_manual_move_tick(self):
        now=time.monotonic()
        down=mouse_left_down()
        was_down=getattr(self,'_stationary_left_down',False)
        self._stationary_left_down=down
        if down:
            self._stationary_manual_move_until=now+.2
            self._movement_hunt_target=None
            self._movement_target_missing=None
            self._stationary_attack_record=None
            guard=getattr(self,'combat_guard',None)
            if guard is not None:
                focus=getattr(guard,'focus',None)
                if focus is not None and focus not in guard.blocked:
                    guard.sessions.pop(focus,None)
                guard.focus=None
            if not was_down:
                logging.getLogger(__name__).debug('[MOUSE] 왼버튼 유지 · 자동 이동/타겟/공격/모든 스킬 일시 정지')
                await self.scheduler.clear()
                await self.scheduler._cancel_execution()
                await self.scheduler.executor.release_held_attack()
                release_move=getattr(self.scheduler.executor,'release_held_move',None)
                if release_move is not None:await release_move()
                self._manual_skill_pending_at=None
                self._manual_skill_move_required=False
                self._manual_skill_resume_until=0
            return True
        self._stationary_manual_move_until=0
        if was_down:logging.getLogger(__name__).debug('[MOUSE] 왼버튼 해제 · 자동 이동/사냥/스킬 재개')
        return False

    def _stationary_hold_command(self,target=None,track_id=None):
        attack=command('ATTACK',source='STATIONARY_HUNT',reason='STATIONARY_MODE_HOLD',
                       target=target,track_id=track_id,duration_ms=0,epoch=self._epoch,cooldown_seconds=.05)
        return replace(attack,maintain_attack=True,expires_at=None)

    def _stationary_post_death_command(self,now):
        record=getattr(self,'_stationary_attack_record',None)
        if (not getattr(self,'_stationary_hunt_mode',False) or not record
                or record['epoch']!=self._epoch
                or self.combat_guard.blocked.get(record['track_id'])!='HEALTH_DEPLETED_CONFIRMED'):
            return None
        if record['until'] is None:record['until']=now+2.0
        if now>=record['until']:
            self._stationary_attack_record=None
            return None
        attack=command('ATTACK',source='MOVEMENT_HUNT',reason='STATIONARY_POST_DEATH',
                       target=record['target'],track_id=record['track_id'],duration_ms=0,epoch=self._epoch,cooldown_seconds=.05)
        return replace(attack,maintain_attack=True,expires_at=min(attack.expires_at,record['until']))

    def _stationary_target_grace_command(self,now):
        record=getattr(self,'_stationary_attack_record',None)
        if (not getattr(self,'_stationary_hunt_mode',False) or not record
                or record['epoch']!=self._epoch or record.get('until') is not None
                or now-record.get('seen_at',-1e9)>=.5
                or record['track_id'] in self.combat_guard.blocked):return None
        attack=command('ATTACK',source='MOVEMENT_HUNT',reason='STATIONARY_TARGET_GRACE',
                       target=record['target'],track_id=record['track_id'],duration_ms=0,
                       epoch=self._epoch,cooldown_seconds=.05)
        return replace(attack,maintain_attack=True,expires_at=min(attack.expires_at,record['seen_at']+.5))

    def _movement_hunt_command(self):
        if self._manual_mouse_pause_enabled() and (mouse_left_down() or getattr(self,'_stationary_left_down',False)):
            return None
        if not self._movement_hunt_enabled() or self.profile.name!='diablo4':return None
        frame=self._latest_frame
        live=self._confirm_diablo_enemies(frame)
        self.combat_feedback.annotate(frame,live,self._docs.get('vision.json',{}).get('enemy_health_bar',{}))
        self._objects=live;self._latest_yolo_objects=live;self._yolo_at=time.monotonic()
        self.combat_guard.observe(live)
        now=time.monotonic()
        finishing=self._stationary_post_death_command(now)
        if finishing is not None:return finishing
        focused=getattr(self,'_movement_hunt_target',None)
        current=next((o for o in live if o.track_id==focused and o.enemy_bar_confirmed
                      and o.relation=='hostile' and not self.combat_guard.is_blocked(o)),None)
        if focused is not None and current is None:
            grace=self._stationary_target_grace_command(now)
            if grace is not None:return grace
            if self.combat_guard.blocked.get(focused)=='HEALTH_DEPLETED_CONFIRMED':
                self._movement_hunt_target=None;self._movement_target_missing=None
            else:
                missing=getattr(self,'_movement_target_missing',None)
                if missing is None:missing=(now,0)
                missing=(missing[0],missing[1]+1);self._movement_target_missing=missing
                stationary=getattr(self,'_stationary_hunt_mode',False)
                if now-missing[0] < (.15 if stationary else 1) or missing[1] < (2 if stationary else 3):
                    return command('STOP',source='MOVEMENT_HUNT',reason='MONSTER_RESULT_WAIT',epoch=self._epoch)
                # Sustained loss ends engagement without claiming an unseen death.
                self._movement_hunt_target=None;self._movement_target_missing=None
        enemies=[o for o in live if o.enemy_bar_confirmed and o.relation=='hostile'
                 and not self.combat_guard.is_blocked(o) and self.combat_guard.permits(o.track_id)]
        if not enemies:return None
        h,w=frame.shape[:2];origin=self._world_player_origin()
        def center(o):
            x1,y1,x2,y2=o.bbox
            return ((x1+x2)/2/w,(y1+y2)/2/h)
        target=current or min(enemies,key=lambda o:np.linalg.norm(np.asarray(center(o))-origin))
        self._movement_hunt_target=target.track_id;self._movement_target_missing=None
        combat_settings=dict(self._docs['input.json'],attack_until_bar_lost=True)
        if not self.combat_guard.request(target,combat_settings):
            return command('STOP',source='MOVEMENT_HUNT',reason='MONSTER_RESULT_WAIT',epoch=self._epoch)
        attack=command('ATTACK',source='MOVEMENT_HUNT',target=center(target),track_id=target.track_id,epoch=self._epoch)
        if getattr(self,'_stationary_hunt_mode',False):
            self._stationary_attack_record={'track_id':target.track_id,'target':center(target),
                                            'epoch':self._epoch,'until':None,'seen_at':now}
            return replace(attack,duration_ms=0,maintain_attack=True,cooldown=.05,reason='STATIONARY_HOLD')
        return self._hunt_attack_rotation(attack) or command('STOP',source='MOVEMENT_HUNT',reason='COMBAT_COOLDOWN',epoch=self._epoch)

    def _hunt_attack_rotation(self,attack):
        settings=self._docs['input.json']
        disabled=settings.get('disabled_actions',[])
        choices=([attack] if attack.source=='MOVEMENT_HUNT' and getattr(self,'_repeat_skills_hunting',False)
                 and 'ATTACK' not in disabled else [])
        if 'USE_SKILL' not in disabled:
            choices.extend(replace(attack,action_type='USE_SKILL',skill_id=s['id'],
                                   maintain_attack=False,cooldown=s['cooldown_ms']/1000)
                           for s in settings.get('attack_skills',[]) if s.get('enabled',False))
        if not choices:return None
        start=getattr(self,'_hunt_rotation_index',0)%len(choices)
        for offset in range(len(choices)):
            index=(start+offset)%len(choices)
            candidate=choices[index]
            if self.scheduler._is_cooldown_ready(candidate):
                self._hunt_rotation_index=(index+1)%len(choices)
                return candidate
        return None

    def _escape_cursor_target(self):
        memory=self.minimap_memory
        with memory.lock:
            if not memory.snapshot()['valid'] or memory.mask is None or memory.grid is None:return None
            direction=memory.last_direction
            if direction is None and memory.goal is not None and memory.position is not None:
                vector=memory.goal-memory.position
                norm=float(np.linalg.norm(vector))
                if norm:
                    a=math.radians(memory.rotation);x,y=vector/norm
                    direction=(x*math.cos(a)+y*math.sin(a),-x*math.sin(a)+y*math.cos(a))
            if direction is None:return None
            # This is a bounded wall probe, not a normal terrain-approved click.
            # Aim far along the current heading before pressing the registered skill.
            h,w=self._latest_frame.shape[:2]
            origin=self._world_player_origin()
            limits=[]
            for component,coordinate,size in ((direction[0],origin[0],w),(direction[1],origin[1],h)):
                if abs(component)>1e-6:limits.append(max(0,((.98 if component>0 else .02)-coordinate)*size/component))
            distance=min(limits) if limits else 0
            zones=self._world_click_exclusions()
            for fraction in np.linspace(1,.05,40):
                target=(origin[0]+direction[0]*distance*fraction/w,origin[1]+direction[1]*distance*fraction/h)
                if self.navigator.click_is_clear(target,self._objects,self._latest_frame.shape,zones,origin,18/w):return target
            return None

    async def _try_navigation_escape(self):
        memory=self.minimap_memory
        attempt=getattr(self,'_navigation_escape',None)
        now=time.monotonic()
        if attempt is not None:
            if attempt['epoch']!=self._epoch or attempt['segment']!=memory.segment:
                self._navigation_escape=None
                return False
            if not memory.snapshot()['valid']:
                attempt['valid_since']=None
                if attempt.get('map_wait_since') is None:attempt['map_wait_since']=now
                if now-attempt['map_wait_since']>=1:
                    memory.request_replan()
                    self.click_journey.reset('player_map_wait_timeout',preserve_record=True)
                    self._navigation_escape=None
                    self._wall_probe_pending=False
                    self.emit_web_event('navigation_goal_released',reason='player_map_wait_timeout')
                    return False
                return True
            attempt['map_wait_since']=None
            active=self.scheduler.executor.active_command
            if active is not None and active.source=='NAVIGATION_ESCAPE':return True
            if memory.position is not None and np.linalg.norm(memory.position-attempt['position'])>=1.5:
                self._navigation_escape=None
                memory.pending.clear();memory.recovery_count=0;memory.recovery_stage='none'
                return False
            valid_since=attempt.get('valid_since')
            if valid_since is None:
                attempt['valid_since']=now
                return True
            if now-max(attempt['at'],valid_since)<1.2:return True
            memory.request_replan()
            self.click_journey._release('escape_failed')
            self.click_journey.reset('escape_failed',preserve_record=True)
            self._navigation_escape=None
            self.emit_web_event('navigation_goal_released',reason='escape_failed')
            return False
        if (self.click_journey.last_release!='stalled' and not getattr(self,'_wall_probe_pending',False)) or memory.position is None:return False
        self._wall_probe_pending=False
        if ('DODGE' in self._docs['input.json'].get('disabled_actions',[])
                or not self._docs['input.json'].get('movement_skill',{}).get('enabled',False)):
            memory.request_replan()
            self.click_journey._release('escape_disabled')
            self.click_journey.reset('escape_disabled',preserve_record=True)
            return False
        target=self._escape_cursor_target()
        if target is None:
            memory.request_replan()
            self.click_journey._release('escape_no_target')
            self.click_journey.reset('escape_no_target',preserve_record=True)
            return False
        self._navigation_escape={'epoch':self._epoch,'segment':memory.segment,
                                 'position':memory.position.copy(),'at':now}
        memory.pending.clear()
        self.click_journey._release('escape_attempt')
        self.click_journey.reset('escape_attempt',preserve_record=True)
        await self.scheduler.submit(command('DODGE',source='NAVIGATION_ESCAPE',
            target=target,reason='이동 정체 · 먼 진행 방향에 커서 이동 후 이동 스킬',epoch=self._epoch))
        return True

    def _fresh(self):
        # A 2-3 FPS capture can exceed the legacy 0.5s HUD interval.
        # Only a still-valid recent measurement is usable; missing HP stays blocked.
        now=time.monotonic()
        if getattr(self,'movement_test_mode',False):
            return self._latest_frame is not None and now-self._capture_at<1
        return (self._latest_frame is not None and now-self._capture_at<1
                and now-self._hud_at<.8)

    def _configure_buff_monitor(self):
        self.buff_monitor.absence_seconds=4
        self.buff_monitor.once_per_absence=False
        self.buff_monitor.require_all_absent=True
        self.buff_monitor.repeat_seconds=4
        self.buff_monitor.threshold=self._buff_threshold
        self.buff_monitor.stable_icon=True

    async def _dodge_after_movement_skill(self,c):
        await asyncio.sleep(1)
        if (not self._running or self._paused or self._processing_halted
                or c.decision_epoch!=self._epoch or not self._foreground() or not self._fresh()):return
        await self.scheduler.submit(command('DODGE',source='NAVIGATION_DODGE',target=c.target,
                                            reason='이동 스킬 전송 1초 후 회피',epoch=self._epoch))

    def _record_input(self,c,status,error=None):
        if c.source=='SCREEN_ARROW_MOVE' and status=='sent':
            self._last_arrow_move_sent=c
            self._last_arrow_move_sent_at=time.monotonic()
        if c.source=='SCREEN_SPACE_PROMPT' and c.decision_epoch==self._epoch:
            self._space_prompt_pending_until=0
            if status=='sent':
                self._space_prompt_wait_until=0
                self._space_prompt_latched=True
                self._space_prompt_missing_since=None
                # Space can cross a ledge/door and change the route direction.
                # Reacquire immediately instead of treating the new arrow
                # as a passed marker behind the previous movement heading.
                self._planned_move_heading=None
                self._planned_recovery_since=None
                self._planned_turn_candidate=None
                self._arrow_hold_distance=None
                self._screen_move_guide=None
                self._screen_guide_seen_at=-1e9
                self._screen_guide_scanned_at=-1e9
                self._last_arrow_move_sent=None
                self._arrow_escape_pending_until=0
                self._arrow_escape_retry_at=0
                self.scheduler.emergency_until=time.monotonic()
        if c.source=='SCREEN_ARROW_ESCAPE' and c.decision_epoch==self._epoch:
            self._arrow_escape_pending_until=0
            self._arrow_escape_retry_at=time.monotonic()+(3 if status=='sent' else .5)
            if status=='sent' and hasattr(self,'minimap_memory'):
                with self.minimap_memory.lock:
                    self.minimap_memory.pending.clear()
                    self.minimap_memory.stuck=False
        if c.action_type=='MOVE' and c.source in self.click_journey.SOURCES:
            if status=='sent':self._rejected_move_sample=None
            elif status in {'blocked','failed'}:self._remember_rejected_movement(c)
        if c.source=='MANUAL_SKILL' and status in {'sent','blocked','failed'}:
            self._manual_skill_pending_at=None
            self._manual_skill_move_required=True
            self._manual_skill_resume_until=time.monotonic()+.15
        if c.action_type=='MOVE' and status=='sent':self._manual_skill_move_required=False
        if c.source=='NAVIGATION_ESCAPE' and status=='sent':
            attempt=getattr(self,'_navigation_escape',None)
            if attempt is not None and attempt['epoch']==c.decision_epoch:
                attempt['at']=time.monotonic()
            task=getattr(self,'_post_movement_dodge_task',None)
            if task is not None and not task.done():task.cancel()
            self._post_movement_dodge_task=asyncio.create_task(self._dodge_after_movement_skill(c))
        super()._record_input(c,status,error)
        if hasattr(self,'minimap_memory'):self.minimap_memory.on_input(c,status,error)
        if hasattr(self,'click_journey'):
            with self.minimap_memory.lock:
                self.click_journey.on_input(c,status,time.monotonic(),position=self.minimap_memory.position)

    def _remember_rejected_movement(self,c):
        memory=self.minimap_memory
        key=(c.decision_epoch,memory.segment)
        previous=getattr(self,'_rejected_move_sample',None)
        if previous is None or previous[0]!=key:
            self._rejected_move_sample=(key,time.monotonic(),memory.last_update)

    def _recover_rejected_movement(self,now=None):
        sample=getattr(self,'_rejected_move_sample',None)
        if sample is None:return False
        now=time.monotonic() if now is None else now
        memory=self.minimap_memory
        with memory.lock:
            if sample[0]!=(self._epoch,memory.segment):
                self._rejected_move_sample=None
                return False
            if now-sample[1]<1 or memory.last_update<=sample[2] or not memory.snapshot()['valid']:return False
            active=self.scheduler.executor.active_command
            if active is not None:return False
            memory.request_replan()
            self.click_journey.reset('click_rejected_timeout',preserve_record=True)
            self._next_navigation_plan=None
            self._rejected_move_sample=None
            self.emit_web_event('navigation_goal_released',reason='click_rejected_timeout')
            return True

    def _mapped_click_navigation(self):
        return (self._docs['input.json']['movement']['mode']=='click'
                and self._docs['navigation.json']['minimap'].get('mapping',{}).get('enabled',False))

    def _review_missing_navigation_route(self,now=None):
        if self._movement_hunt_enabled() and getattr(self,'_movement_hunt_target',None) is not None:
            self._missing_route_sample=None
            return False
        now=time.monotonic() if now is None else now
        memory=self.minimap_memory
        with memory.lock:
            state=memory.snapshot(now=now)
            key=(self._epoch,memory.segment)
            if state['valid'] and len(memory.route)>1:
                self._missing_route_sample=None
                return False
            if memory.goal is None and memory.replan_goal is None:
                self._missing_route_sample=None
                return False
            sample=getattr(self,'_missing_route_sample',None)
            if sample is None or sample[0]!=key:
                self._missing_route_sample=(key,now,False)
                return False
            if sample[2] or now-sample[1]<1:return False
            active=self.scheduler.executor.active_command
            if active is not None and active.action_type in {'MOVE','DODGE'}:return False
            memory.request_replan()
            self.click_journey._release('route_missing_timeout')
            self.click_journey.reset('route_missing_timeout',preserve_record=True)
            self._navigation_escape=None;self._wall_probe_pending=False
            self._next_navigation_plan=None
            self._missing_route_sample=(key,sample[1],True)
            self.emit_web_event('navigation_goal_released',reason='route_missing_timeout')
            return True

    def _check_map_goal_arrival(self,c):
        memory=self.minimap_memory
        with memory.lock:
            if (memory.goal is None or memory.position is None or not memory.snapshot()['valid']):
                self._map_arrival_sample=None
                return False
            distance=float(np.linalg.norm(memory.position-memory.goal))
            # Grid quantization can leave the player across a cell boundary
            # despite visibly reaching the endpoint. Require two fresh samples.
            arrival_radius=max(4.,min(6.,memory.CELL*1.5))
            if distance>arrival_radius:
                self._map_arrival_sample=None
                return False
            from app.core.route_geometry import corridor_clear
            if memory.grid is not None and not corridor_clear(
                    (memory.position-memory.origin)/memory.CELL,
                    (memory.goal-memory.origin)/memory.CELL,memory.grid,memory.clearance,0):
                self._map_arrival_sample=None
                return False
            key=(c.decision_epoch,memory.segment,tuple(memory.goal))
            previous=getattr(self,'_map_arrival_sample',None)
            if previous is None or previous[0]!=key:
                self._map_arrival_sample=(key,memory.last_update)
                return False
            if memory.last_update-previous[1]<.08:return False
            self.click_journey._release('map_goal_arrived',position=memory.position)
            self.click_journey.reset('map_goal_arrived',preserve_record=True)
            memory.completed_goals.append(memory.goal.copy())
            memory.replan_goal=None
            memory.goal=None;memory.goal_heading=None;memory.goal_until=0;memory.route=[]
            self._map_arrival_sample=None
            self._next_navigation_plan=None
            self._navigation_escape=None
            self._wall_probe_pending=False
            self.emit_web_event('navigation_goal_released',reason='map_goal_arrived')
            return True

    def _continue_navigation(self,c):
        if (c.action_type!='MOVE' or c.source not in self.click_journey.SOURCES
                or self._docs['input.json']['movement']['mode']!='click'
                or not self._docs['navigation.json']['minimap'].get('mapping',{}).get('enabled',False)):
            return False
        memory=self.minimap_memory
        guide=getattr(self,'_screen_move_guide',None) if getattr(self,'_screen_guide_enabled',False) else None
        self.click_journey.feedback_steps=self._docs['navigation.json']['minimap'].get('mapping',{}).get('control_mode')=='feedback_steps'
        self.click_journey.arrival_only=True
        with memory.lock:
            state=memory.snapshot();now=time.monotonic()
            if memory.position is None:return False
            active=self.scheduler.executor.active_command
            if active is not None and active.action_type=='MOVE' and active.decision_epoch==c.decision_epoch:
                return True
            if memory.goal is None:
                self.click_journey.reset('missing_destination',preserve_record=True)
                return False
            # Hold the plan during the short queue-to-pointer handoff too.
            if self.scheduler.running and any(p['epoch']==c.decision_epoch and p['segment']==memory.segment
                    and now-key<.5 for key,p in self.click_journey.pending.items()):return True
            if self._check_map_goal_arrival(c):return False
            record=self.click_journey.record
            if (c.source in self.click_journey.SOURCES and record
                    and record['epoch']==c.decision_epoch and record['segment']==memory.segment
                    and (self.click_journey.current is not None or self.click_journey.last_release in {'stalled','waypoint_arrived','step_progress','step_refresh'})):
                blocked=memory.destination_blocked(record.get('destination',record['goal']),now)
                sample=getattr(self,'_blocked_goal_sample',None)
                if blocked:
                    if sample is None or sample[0]!=record['goal_id']:
                        self._blocked_goal_sample=(record['goal_id'],memory.last_update)
                    elif memory.last_update-sample[1]>=.08:
                        if self._mapped_click_navigation() and not guide:
                            self._wall_probe_pending=True
                            self.click_journey._release('wall_probe')
                            self._blocked_goal_sample=None
                            return False
                        delta=record.get('destination',record['goal'])-memory.position
                        length=float(np.linalg.norm(delta))
                        memory.blocked_goal_heading=delta/length if length>1e-6 else None
                        self.click_journey._release('goal_blocked',position=memory.position)
                        memory.goal=None;memory.goal_heading=None;memory.goal_until=0;memory.route=[]
                        self._next_navigation_plan=None;self._blocked_goal_sample=None
                        self.emit_web_event('navigation_goal_released',reason='goal_blocked')
                        print('[MOVE GOAL] 미니맵 벽/통로 단절 확인 · 다른 방향 목표 선택')
                        return False
                else:self._blocked_goal_sample=None
            was_active=self.click_journey.current is not None
            keep=self.click_journey.continues(epoch=c.decision_epoch,segment=memory.segment,
                    position=memory.position,origin=memory.origin,mask=memory.mask,
                    valid=state['valid'],stuck=memory.stuck,now=now,sampled_at=memory.last_update)
            if keep:self._next_navigation_plan=None
            elif was_active:
                reason=self.click_journey.last_release
                if reason=='stalled':self._request_stall_obstacles()
                self.emit_web_event('navigation_goal_released',reason=reason)
                print(f'[MOVE GOAL] 목표 유지 해제: {reason}')
                if reason!='arrived':self._next_navigation_plan=None
            return keep

    def _request_stall_obstacles(self):
        if getattr(self,'movement_test_mode',False):return
        if getattr(self,'_follow_orange_route',False) or self._hunt_active and self.minimap_memory.pin_world is not None:return
        now=time.monotonic();task=getattr(self,'_obstacle_scan_task',None)
        if (self._latest_frame is None or not self._foreground() or self._paused
                or now<getattr(self,'_obstacle_retry_at',0) or task and not task.done()):return
        self._obstacle_retry_at=now+15
        self._obstacle_scan_task=asyncio.create_task(self._scan_stall_obstacles(self._latest_frame.copy(),self._epoch))

    async def _scan_stall_obstacles(self,frame,epoch):
        if getattr(self,'_follow_orange_route',False) or self._hunt_active and self.minimap_memory.pin_world is not None:return
        memory=self.minimap_memory
        with memory.lock:
            if not memory.valid or memory.position is None:return
            position=memory.position.copy();segment=memory.segment;rotation=memory.rotation
        origin=self._world_player_origin();h,w=frame.shape[:2]
        _,top,_,bottom=game_viewport(frame)
        scale=self._docs['navigation.json']['minimap']['mapping'].get('screen_pixels_per_map_pixel',12)*(bottom-top)/1080
        schema={'type':'object','additionalProperties':False,'required':['obstacles'],'properties':{'obstacles':{'type':'array','maxItems':4,'items':{'type':'object','additionalProperties':False,'required':['bbox','confidence'],'properties':{'bbox':{'type':'array','minItems':4,'maxItems':4,'items':{'type':'integer','minimum':0,'maximum':1000}},'confidence':{'type':'number','minimum':0,'maximum':1}}}}}}
        prompt='Movement is stuck. Locate visible blocking walls, rocks, furniture near the player. Return only obstacles with confidence >= 0.65 and bbox normalized 0..1000. Use ground footprint, exclude HUD, allies, enemies, player and effects. Do not invent unseen obstacles. Return empty if uncertain.'
        try:
            async with self._vl_lock:
                if epoch!=self._epoch or not self._foreground() or self._paused or not self._chat_queue.empty():return
                small=cv2.resize(frame,(512,max(1,round(h*512/w)))) if w>512 else frame
                result=await asyncio.to_thread(self.vl._request,small,prompt,schema,'stall_obstacles',self.profile.name,max_tokens=256,timeout=6)
            if epoch!=self._epoch or self._paused or not self._foreground():return
            boxes=[];angle=math.radians(rotation)
            for item in result.data.get('obstacles',[]):
                box=item.get('bbox',[])
                if item.get('confidence',0)<.65 or len(box)!=4 or not all(type(v) in (int,float) and 0<=v<=1000 for v in box) or box[0]>=box[2] or box[1]>=box[3]:continue
                if box[0]/1000<=origin[0]<=box[2]/1000 and box[1]/1000<=origin[1]<=box[3]/1000:continue
                points=[]
                for x,y in ((box[0],box[1]),(box[2],box[3]),(box[0],box[3]),(box[2],box[1])):
                    dx,dy=(x/1000-origin[0])*w/scale,(y/1000-origin[1])*h/scale
                    points.append(position+[dx*math.cos(angle)-dy*math.sin(angle),dx*math.sin(angle)+dy*math.cos(angle)])
                points=np.asarray(points);boxes.append((*points.min(0),*points.max(0)))
            with memory.lock:
                if segment!=memory.segment:return
                memory.screen_obstacles=[(time.monotonic()+8,box) for box in boxes]
            self.emit_web_event('navigation_obstacles',count=len(boxes),message='막힘 화면 장애물 확인 · 같은 목표 우회')
        except Exception as exc:
            self.emit_web_event('navigation_obstacle_error',message=str(exc))

    def _defer_optional_actions(self,c):
        if (c.action_type not in {'TAKE','INTERACT'} or self._directive is not None
                or not self._hunt_active or (self.click_journey.current is None and self.click_journey.last_release not in {'stalled','waypoint_arrived','step_progress','step_refresh'})):return False
        if self.buff_monitor.pending_recast and time.monotonic()-self._buff_at<1:return False
        with self.minimap_memory.lock:
            memory=self.minimap_memory
            if memory.position is None:return False
            record=self.click_journey.record
            target=self.click_journey.current[2] if self.click_journey.current is not None else record.get('destination',record['goal'])
            delta=target-memory.position
            length=float(np.linalg.norm(delta))
            if length<1e-6:return False
            dx,dy=delta/length;angle=math.radians(memory.rotation)
            direction=(dx*math.cos(angle)+dy*math.sin(angle),-dx*math.sin(angle)+dy*math.cos(angle))
            return self._continue_navigation(replace(c,action_type='MOVE',source='HUNT_EXPLORE',direction=direction))

    def _stage_next_navigation(self,c,now):
        # No speculative replacement in transit; planning starts at arrival or the near-goal handoff.
        self._next_navigation_plan=None

    def _finalize_navigation_command(self,c):
        if c.action_type in {'ATTACK','USE_SKILL'} and self._docs['input.json'].get('basic_attack_mode')=='tap':
            c=replace(c,maintain_attack=False)
        # ClickJourney controls repeat timing while preserving the destination.
        # Each repeat goes through planning and the normal input guards again.
        if (c.action_type=='MOVE' and c.source in self.click_journey.SOURCES
                and self._docs['input.json']['movement']['mode']=='click'
                and self._docs['navigation.json']['minimap'].get('mapping',{}).get('enabled',False)):
            return replace(c,duration_ms=0,cooldown=0,move_clicks=3)
        return c

    def _dispatch_action(self,c):
        journey=self.click_journey
        if c.action_type in journey.KEEP_ACTIONS:return True
        if c.action_type=='STOP' and (c.reason in journey.PASSIVE_STOPS or c.source in {'SEMANTIC_WAIT','COMBAT_TRACK_WAIT'}):return True
        nav=self._docs['navigation.json'];mapping=nav['minimap'].get('mapping',{})
        journey.feedback_steps=mapping.get('control_mode')=='feedback_steps'
        journey.arrival_only=True
        if (c.action_type!='MOVE' or c.source not in journey.SOURCES
                or self._docs['input.json']['movement']['mode']!='click'
                or not mapping.get('enabled',False) or c.target is None):
            self._next_navigation_plan=None
            journey.reset(c.reason or c.source or c.action_type,preserve_record=True)
            return True
        # Never queue another click while pointer positioning or this move is active.
        active=self.scheduler.executor.active_command
        if active is not None and active.action_type=='MOVE':return False
        memory=self.minimap_memory
        with memory.lock:
            state=memory.snapshot()
            if memory.position is None:return True
            if journey.continues(epoch=c.decision_epoch,segment=memory.segment,
                    position=memory.position,origin=memory.origin,mask=memory.mask,
                    valid=state['valid'],stuck=memory.stuck,now=time.monotonic(),sampled_at=memory.last_update):return False
            _,top,_,bottom=game_viewport(self._latest_frame)
            scale=mapping.get('screen_pixels_per_map_pixel',12)*(bottom-top)/1080
            journey.dispatched(c,position=memory.position.copy(),segment=memory.segment,
                    player_screen=self._world_player_origin(),shape=self._latest_frame.shape,
                    scale=scale,rotation=memory.rotation,destination=memory.goal)
        return True

    async def _navigation_loop(self):
        processed_frame=None
        while self._running:
            if not self._focus_work_allowed():
                if hasattr(self,'minimap_memory'):self.minimap_memory.suspend('foreground_wait')
                await asyncio.sleep(.1);continue
            # Map inspection needs a fresh game capture, not a valid HP measurement.
            # Execution keeps its independent pause/HUD/foreground checks.
            reason=('foreground_wait' if not self._foreground()
                    else 'capture_wait' if self._latest_frame is None or time.monotonic()-self._capture_at>=1 else None)
            if reason:
                self.navigation_monitor.reset();self.minimap_memory.suspend(reason)
                self._minimap_at=0;self._minimap_mask=None
                await asyncio.sleep(.1);continue
            frame_key=(self._epoch,self._frame_seq,self._frame_identity)
            if frame_key==processed_frame:
                await asyncio.sleep(.03);continue
            processed_frame=frame_key
            if self._paused or self._processing_halted:
                self.navigation_monitor.reset()
            settings=copy.deepcopy(self._docs['navigation.json']);epoch=self._epoch
            frame=self._latest_frame.copy()
            mapped=settings['minimap'].get('mapping',{}).get('enabled',False)
            if mapped:
                try:
                    mask=await asyncio.to_thread(self.minimap_memory.update,frame,settings)
                except Exception as exc:
                    self.minimap_memory.suspend('processing_error')
                    self._minimap_at=0;self._minimap_mask=None
                    self.emit_web_event('minimap_error',message=f'미니맵 처리 오류: {exc}')
                    await asyncio.sleep(1);continue
                state=self.minimap_memory.snapshot()
                if not self._paused and not self._processing_halted and not self._move_only:
                    self._review_missing_navigation_route()
                    self._recover_rejected_movement()
                player=settings['minimap']['player']
            else:
                player=self.navigation_monitor.update(frame,settings)
                mask=await asyncio.to_thread(minimap_mask,frame,settings)
                state=None
            if epoch==self._epoch:
                self._nav_player=player
                self._minimap_unusable=bool(settings['minimap']['enabled'] and (mask is None or (mask>0).mean()<.01 or (state is not None and not state['valid'])))
                self._minimap_mask=None if self._minimap_unusable else mask
                self._minimap_at=0 if self._minimap_unusable else time.monotonic()
                if state:
                    with self.minimap_memory.lock:
                        if self.minimap_memory.position is not None:
                            if state['valid']:
                                self.click_journey.reanchor(position=self.minimap_memory.position,segment=self.minimap_memory.segment,now=self.minimap_memory.last_update)
                            self.click_journey.observe(position=self.minimap_memory.position,segment=self.minimap_memory.segment,valid=state['valid'],now=self.minimap_memory.last_update)
                    self.navigation_monitor.stuck=state['stuck']
                    if state['stuck']:self._hunt_turn_at=0
            await asyncio.sleep(.03)

    def _arrow_position_heading(self,guide):
        position=guide.get('marker') or guide.get('arrow_tip')
        if position is None:return None,None
        h,w=self._latest_frame.shape[:2]
        point=np.asarray(position,dtype=float)*np.array([w,h])
        delta=point-np.asarray(self._world_player_origin())*np.array([w,h])
        length=float(np.linalg.norm(delta))
        if length<1 or not np.all(np.isfinite(delta)):return None,None
        return point,delta/length

    @staticmethod
    def _arrow_cursor_adjustment(heading):
        # Screen Y grows down; rotate right/left routes toward the bottom.
        heading=np.asarray(heading,dtype=float)
        angle=math.radians(5)*(1 if heading[0]>=0 else -1)
        cosine,sine=math.cos(angle),math.sin(angle)
        corrected=np.array([cosine*heading[0]-sine*heading[1],
                            sine*heading[0]+cosine*heading[1]])
        corrected/=np.linalg.norm(corrected)
        return corrected

    def _arrow_move_command(self,guide):
        h,w=self._latest_frame.shape[:2]
        tip,heading=self._arrow_position_heading(guide)
        if guide.get('planned_recovery'):
            heading=np.asarray(guide['planned_heading'])
        # Fixed 100 native pixels from the arrow marker.
        heading=self._arrow_cursor_adjustment(heading)
        target=(tip+100*heading)/np.array([w,h])
        direction=target-np.array(self._world_player_origin())
        direction*=np.array([w,h]);length=np.linalg.norm(direction)
        c=command('MOVE',source='SCREEN_ARROW_MOVE',target=tuple(float(v) for v in target),
                       direction=tuple(float(v) for v in direction/max(length,1e-6)),
                       duration_ms=0,cooldown_seconds=.05,epoch=self._epoch,
                       reason='캐릭터 → 화살표 방향 아래쪽 5도 · 화살표에서 80px 고정 · 왼버튼 유지')
        return replace(c,maintain_move=True,expires_at=time.monotonic()+.6,
                       navigation_anchor=tuple(float(v) for v in tip/np.array([w,h])))

    def _stabilize_arrow(self,guide):
        if guide is None:
            self._arrow_hold_distance=None
            return None
        point,heading=self._arrow_position_heading(guide)
        if heading is None:return None
        now=time.monotonic()
        previous=getattr(self,'_planned_move_heading',None)
        if previous is not None:
            old=np.asarray(previous)
            alignment=float(old@heading)
            if alignment<=0:
                # A passed marker behind the player is not a request to turn back.
                since=getattr(self,'_planned_recovery_since',None)
                if since is None:self._planned_recovery_since=since=now
                self._planned_turn_candidate=None
                if now-since>.6:return None
                guide['planned_recovery']=True
                guide['planned_heading']=tuple(old)
                return guide
            self._planned_recovery_since=None
            if alignment<.5:
                candidate=getattr(self,'_planned_turn_candidate',None)
                count=candidate[1]+1 if candidate and float(np.asarray(candidate[0])@heading)>.95 else 1
                self._planned_turn_candidate=(tuple(heading),count)
                if count<3:return None
            self._planned_turn_candidate=None
            heading=old*.25+heading*.75
            heading/=np.linalg.norm(heading)
        self._planned_move_heading=tuple(heading)
        self._planned_recovery_since=None
        h,w=self._latest_frame.shape[:2]
        guide['arrow_tip']=tuple(point/np.array([w,h]))
        guide['arrow_direction']=tuple(heading)
        guide['direction']=tuple(heading)
        return guide

    def _random_move_heading(self):
        now=time.monotonic()
        if now>=getattr(self,'_random_move_until',0):
            angle=random.uniform(0,math.tau)
            self._random_move_direction=(math.cos(angle),math.sin(angle))
            self._random_move_until=now+2
        return self._random_move_direction

    async def _update_screen_move_guide(self):
        now=time.monotonic()
        if now-getattr(self,'_screen_guide_scanned_at',-1e9)>=(.04 if getattr(self,'_move_only',False) else .15):
            self._screen_guide_scanned_at=now
            if getattr(self,'_move_only',False):
                previous=getattr(self,'_screen_move_guide',None)
                diagnostic={}
                epoch=self._epoch
                guide=await asyncio.to_thread(screen_route_guide,self._latest_frame,self._world_player_origin(),arrow_only=True,
                                         previous_marker=previous.get('marker') if previous else None,
                                         preferred_heading=getattr(self,'_planned_move_heading',None),diagnostics=diagnostic,fast_only=True)
                if epoch!=self._epoch or self._paused or self._processing_halted:
                    self._screen_move_guide=None
                    return None
                diagnostic['latency_ms']=round((time.monotonic()-now)*1000,1)
                now=time.monotonic()
                detected=guide is not None
                self._arrow_match_diagnostic=diagnostic
                if guide is None and previous and now-getattr(self,'_screen_guide_seen_at',-1e9)<=.2:
                    guide=previous
                    diagnostic['method']='short_occlusion'
                else:
                    guide=self._stabilize_arrow(guide)
                if now-getattr(self,'_arrow_diagnostic_at',-1e9)>=2:
                    self._arrow_diagnostic_at=now
                    logging.getLogger(__name__).debug(f'[ARROW MATCH] detector=shape-v2 detected={detected} move_ready={guide is not None} '
                          f'score={diagnostic.get("best_score",0):.3f} candidates={diagnostic.get("candidates",0)} '
                          f'method={diagnostic.get("method","template")} proposed={diagnostic.get("proposed",0)} '
                          f'latency={diagnostic.get("latency_ms",0)}ms')
                self._screen_move_guide=guide
                if detected and guide:self._screen_guide_seen_at=now
                return guide
            guide,marker,dots=screen_route_guide(self._latest_frame,self._world_player_origin(),self._world_click_exclusions(),with_presence=True)
            self._screen_guide_has_marker=marker;self._screen_guide_has_dots=dots
            if guide:
                previous=getattr(self,'_screen_move_guide',None)
                if (previous is None or previous.get('source')!=guide.get('source')) and hasattr(self,'click_journey'):
                    # A newly available higher-priority cue replaces the previous destination.
                    self.click_journey.reset('route_guide_priority_changed')
                    with self.minimap_memory.lock:
                        self.minimap_memory.goal=None;self.minimap_memory.goal_heading=None
                        self.minimap_memory.goal_until=0;self.minimap_memory.route=[]
                self._screen_move_guide=guide
                self._screen_guide_seen_at=now
                self._orange_guide_active=False
        return (getattr(self,'_screen_move_guide',None)
                if now-getattr(self,'_screen_guide_seen_at',-1e9)<.8 else None)

    def _prepare_navigation(self,c,settings):
        steering=settings.get('steering',{})
        preferred=getattr(self,'_hunt_preferred_direction',None)
        if c.source=='HUNT_EXPLORE' and preferred:c=replace(c,direction=preferred)
        settings['projection']='isotropic'
        settings['map_only']=True
        settings['player_screen']=self._world_player_origin()
        settings['excluded_regions']=self._world_click_exclusions()
        interval=steering.get('move_interval_ms',500)/1000
        if c.source in self.click_journey.SOURCES:interval=min(interval,ClickJourney.REFRESH_SECONDS)
        c=replace(c,cooldown=max(c.cooldown,interval))
        mapping=settings['minimap'].get('mapping',{})
        if getattr(self,'_follow_orange_route',False) and c.source in {'HUNT_EXPLORE','MINIMAP_QWEN'} and not mapping.get('enabled',False):
            return command('STOP',source='NAVIGATION',reason='ORANGE_ROUTE_REQUIRES_MAPPING',epoch=c.decision_epoch)
        emergency=c.source=='HP_RETREAT'
        exact_distance=c.source in self.click_journey.SOURCES
        if exact_distance:
            # The percentage is a screen click radius, not a tiny map waypoint.
            a,top,b,bottom=game_viewport(self._latest_frame)
            settings['step_fraction']*=min(b-a,bottom-top)/self._latest_frame.shape[1]
            settings['exact_click_distance']=True
        if exact_distance:
            settings['minimum_move_pixels']=18
        if emergency:settings['step_fraction']=min(.12,settings['step_fraction'])
        if c.source=='COMBAT_SPACING':settings['step_fraction']=min(.12,settings['step_fraction'])
        elif c.source=='COMBAT_APPROACH':
            target=next((o for o in self._objects if o.track_id==c.track_id),None)
            if target is not None:
                h,w=self._latest_frame.shape[:2];ox,oy=settings['player_screen']
                distance=math.hypot((target.bbox[0]+target.bbox[2])/2-ox*w,target.bbox[3]-oy*h)
                combat=self._docs['input.json'].get('combat',{})
                reach=combat.get('ranged_attack_distance',.45) if combat.get('stance')=='ranged' else combat.get('attack_distance',.16)
                settings['step_fraction']=min(settings['step_fraction'],max(.08,(distance-reach*h*.8)/w))
        if not emergency and (not settings['minimap']['enabled'] or not self._local_map_current(settings)):
            return command('STOP',source='NAVIGATION',reason='MINIMAP_REQUIRED',epoch=c.decision_epoch)
        if not settings['minimap']['enabled']:return c
        if not mapping.get('enabled',False):
            # HSV maps use the same conservative endpoint projection, without session mapping.
            h,w=self._latest_frame.shape[:2];mask=self._minimap_mask
            if mask is None:return c if emergency else command('STOP',source='NAVIGATION',reason='MINIMAP_REQUIRED',epoch=c.decision_epoch)
            _,top,_,bottom=game_viewport(self._latest_frame)
            settings['map_screen_scale']=mapping.get('screen_pixels_per_map_pixel',12)*(bottom-top)/1080*192/mask.shape[1]
            if exact_distance:
                settings['minimap']['lookahead']=settings['step_fraction']*w/settings['map_screen_scale']/mask.shape[1]
            else:settings['step_fraction']=min(settings['step_fraction'],settings['minimap'].get('lookahead',.18)*mask.shape[1]*settings['map_screen_scale']/w)
            settings['minimum_click_distance']=min(.035,18/self._latest_frame.shape[1])
            settings['minimap']['pixel_directions']=True
            return c
        _,top,_,bottom=game_viewport(self._latest_frame)
        scale=mapping.get('screen_pixels_per_map_pixel',12)*(bottom-top)/1080
        if exact_distance and mapping.get('control_mode')=='feedback_steps':
            settings['step_fraction']=max(settings['step_fraction'],.04)
        desired=settings['step_fraction']*self._latest_frame.shape[1]/scale if exact_distance else None
        memory=self.minimap_memory
        guide=(getattr(self,'_screen_move_guide',None) if getattr(self,'_screen_guide_enabled',False)
               and c.source in {'HUNT_EXPLORE','MINIMAP_QWEN'} else None)
        self._next_navigation_plan=None
        with memory.lock:
            memory.lock_current_heading=self._mapped_click_navigation()
            memory.prefer_unvisited=self._mapped_click_navigation()
            if guide:
                memory.lock_current_heading=False;memory.prefer_unvisited=False
            elif getattr(self,'_screen_guide_enabled',False):
                memory.lock_current_heading=False
            if (memory.pin_world is not None and not getattr(self,'movement_test_mode',False) and c.source in {'HUNT_EXPLORE','MINIMAP_QWEN'}
                    and getattr(self,'_planned_pin_segment',None)!=memory.segment):
                memory.goal=None;memory.route=[]
                self.click_journey.reset('pin_target_selected')
                self._planned_pin_segment=memory.segment
            memory.follow_pin_route=c.source in {'HUNT_EXPLORE','MINIMAP_QWEN'} and not getattr(self,'movement_test_mode',False)
            memory.pin_direction_priority=mapping.get('pin_direction_priority',True)
            memory.follow_orange_route=bool(getattr(self,'_follow_orange_route',False)) and c.source in {'HUNT_EXPLORE','MINIMAP_QWEN'} and (not getattr(self,'movement_test_mode',False) or getattr(self,'_screen_guide_enabled',False))
            memory.explore_without_guide=mapping.get('explore_without_guide',True) and c.source in {'HUNT_EXPLORE','MINIMAP_QWEN'}
            memory.direction_priority=bool(preferred) or c.source in {'HUNT_EXPLORE','MINIMAP_QWEN'}
            record=self.click_journey.record
            if (self.click_journey.last_release in {'arrived','near_goal','destination_passed'} and record
                    and getattr(self,'_planned_after_arrival',None)!=record['goal_id']):
                memory.completed_goals.append(record.get('destination',record['goal']).copy())
                memory.goal=None;memory.goal_heading=None;memory.goal_until=0;memory.route=[]
                if c.source in {'HUNT_EXPLORE','MINIMAP_QWEN'} and not preferred:
                    delta=record.get('destination',record['goal'])-record.get('destination_start',record['start']);length=float(np.linalg.norm(delta))
                    if length>1e-6:
                        dx,dy=delta/length;angle=math.radians(memory.rotation)
                        c=replace(c,direction=(dx*math.cos(angle)+dy*math.sin(angle),-dx*math.sin(angle)+dy*math.cos(angle)))
                self._planned_after_arrival=record['goal_id']
            retained=(record is not None and self.click_journey.last_release in {'stalled','waypoint_arrived','step_progress','step_refresh'}
                      and record['epoch']==c.decision_epoch and record['segment']==memory.segment)
            if retained:
                plan=memory.suggest(c.direction,explore=False,lookahead_px=desired,target_world=record.get('destination',record['goal']))
            elif guide:
                h,w=self._latest_frame.shape[:2]
                ox,oy=self._world_player_origin();tx,ty=guide['target']
                dx,dy=(tx-ox)*w/scale,(ty-oy)*h/scale
                angle=math.radians(memory.rotation)
                goal=(memory.goal if memory.goal is not None else memory.position+
                      np.array([dx*math.cos(angle)-dy*math.sin(angle),dx*math.sin(angle)+dy*math.cos(angle)]))
                plan=memory.suggest(c.direction,explore=False,lookahead_px=desired,target_world=goal)
            else:
                plan=memory.suggest(c.direction,explore=c.source in {'HUNT_EXPLORE','MINIMAP_QWEN'},lookahead_px=desired)
            # A goal can become disconnected before any click was sent. Confirm
            # against a second fresh map, then release it here as well as in the
            # active-click continuation path.
            blocked_goal=(record.get('destination',record['goal']) if retained else memory.goal)
            if plan is None and c.source in self.click_journey.SOURCES and blocked_goal is not None:
                key=(c.decision_epoch,memory.segment,tuple(blocked_goal))
                sample=getattr(self,'_unreachable_plan_sample',None)
                if memory.destination_blocked(blocked_goal) is True:
                    if sample is None or sample[0]!=key:
                        self._unreachable_plan_sample=(key,memory.last_update)
                    elif memory.last_update-sample[1]>=.08:
                        if self._mapped_click_navigation():
                            self._wall_probe_pending=True
                            return command('STOP',reason='WALL_PROBE_PENDING',epoch=c.decision_epoch)
                        if self.click_journey.record is not None:
                            self.click_journey._release('goal_blocked',position=memory.position)
                        self.click_journey.reset('goal_blocked',preserve_record=True)
                        memory.goal=None;memory.goal_heading=None;memory.goal_until=0;memory.route=[]
                        plan=None if guide else memory.suggest(c.direction,explore=True,lookahead_px=desired)
                        if plan is None and not guide:
                            memory.request_replan()
                            plan=memory.suggest(c.direction,explore=True,lookahead_px=desired)
                        self._unreachable_plan_sample=None
                else:
                    self._unreachable_plan_sample=None
            else:
                self._unreachable_plan_sample=None
            if plan and memory.grid is not None:
                # Bind execution to the very map used for this plan. The capture
                # worker may still be publishing its separate mask reference.
                approved=cv2.resize(memory.grid*255,(memory.grid.shape[1]*memory.CELL,memory.grid.shape[0]*memory.CELL),interpolation=cv2.INTER_NEAREST)
                settings['route_mask']=np.zeros_like(memory.mask)
                settings['route_mask'][:approved.shape[0],:approved.shape[1]]=approved
                settings['minimap']['player']=list(memory.player)
                settings['minimap']['verified_route']=True
        settings['smooth_navigation']=True
        if plan:
            direction,lookahead,fraction=plan
            settings['strict_route']=True
            settings['minimap']['lookahead']=lookahead
            # Project the verified map waypoint, rather than multiplying an unrelated 30% screen click.
            h,w=self._latest_frame.shape[:2]
            _,top,_,bottom=game_viewport(self._latest_frame)
            scale=mapping.get('screen_pixels_per_map_pixel',12)*(bottom-top)/1080
            map_width=self.minimap_memory.mask.shape[1]
            settings['map_screen_scale']=scale
            settings['step_fraction']=min(settings['step_fraction'],lookahead*map_width*scale/w)
            settings['minimum_click_distance']=min(.035,18/self._latest_frame.shape[1])
            return replace(c,direction=direction,reason=('설정 거리/통로 회전 반영 · ' if exact_distance else '')+('방향 화살표 + 흰 점 경로 · ' if guide else '미니맵 핀 경로 · ' if memory.follow_pin_route and memory.pin_world is not None else '미니맵 주황색 선 · ' if memory.follow_orange_route and memory.orange_mask is not None and np.any(memory.orange_mask) else '미니맵 통로 탐색 · ')+self.minimap_memory.recovery_stage)
        if memory.reason=='pin_arrived':return command('STOP',source='NAVIGATION',reason='PIN_TARGET_REACHED',epoch=c.decision_epoch)
        if emergency:
            settings['minimap']['lookahead']=.035
            return c
        if guide:return command('STOP',source='NAVIGATION',reason='SCREEN_GUIDE_BLOCKED',epoch=c.decision_epoch)
        reason=('RECOVERY_LIMIT' if self.minimap_memory.recovery_stage=='blocked' else
                {'guide_missing':'ORANGE_GUIDE_MISSING','guide_disconnected':'ORANGE_GUIDE_DISCONNECTED'}.get(memory.reason,'ORANGE_ROUTE_NOT_FOUND')
                if getattr(self,'_follow_orange_route',False) else 'NO_CENTER_PATH')
        return command('STOP',source='NAVIGATION',reason=reason,epoch=c.decision_epoch)

    def _navigation_click_failed(self,c):
        if c.source not in self.click_journey.SOURCES:return
        self._remember_rejected_movement(c)
        if getattr(self.navigator,'last_block_reason',None)=='클릭 위치가 객체와 겹치거나 캐릭터에 너무 가까움':
            memory=self.minimap_memory
            with memory.lock:
                if not memory.snapshot()['valid'] or memory.goal is None:return
                sample=getattr(self,'_object_click_failure',None)
                key=(c.decision_epoch,memory.segment,tuple(memory.goal))
                if sample is None or sample[0]!=key:
                    self._object_click_failure=(key,memory.last_update)
                elif memory.last_update-sample[1]>=.08:
                    memory.request_replan()
                    self.click_journey.reset('object_click_blocked',preserve_record=True)
                    self._object_click_failure=None
            return
        if (self._mapped_click_navigation()
                and getattr(self.navigator,'last_block_reason',None)!='지도에서 클릭까지의 통로가 차단됨'):
            return
        memory=self.minimap_memory
        with memory.lock:
            if not memory.snapshot()['valid'] or memory.goal is None:return
            key=(c.decision_epoch,memory.segment,tuple(memory.goal))
            previous=getattr(self,'_click_failure_sample',None)
            if previous is None or previous[0]!=key:
                self._click_failure_sample=(key,memory.last_update)
                return
            if memory.last_update-previous[1]<.08:return
            if self._mapped_click_navigation():
                self._wall_probe_pending=True
                self._click_failure_sample=None
                return
            # A fresh map confirming the same unusable screen click must not
            # lock us forever to that destination. Keep map/visited evidence.
            memory.request_replan()
            self.click_journey.reset('click_target_blocked',preserve_record=True)
            self._next_navigation_plan=None
            self._click_failure_sample=None
            self.emit_web_event('navigation_goal_released',reason='click_target_blocked')

    def _prepare_hud_probe(self,settings):
        # Never insert a blind click around the player between route clicks.
        active=self.scheduler.executor.active_command
        if (self.click_journey.current is not None or active is not None and active.action_type=='MOVE'
                or not self._local_map_current(settings)):return None
        c=self._prepare_navigation(command('MOVE',source='USER_COMMAND',direction=(1,0),epoch=self._epoch),settings)
        if c.action_type!='MOVE':return None
        return self.navigator.choose(c.direction,self._objects,self._latest_frame.shape,settings,mask=self._minimap_mask)

    def _local_map_current(self,settings):
        if settings['minimap'].get('mapping',{}).get('calibrated') is False:return False
        if settings['minimap'].get('mapping',{}).get('enabled',False):return self.minimap_memory.snapshot()['valid']
        return time.monotonic()-self._minimap_at<.8 and self._minimap_mask is not None

    def _world_player_origin(self):
        settings=self._docs['navigation.json'].get('steering',{})
        origin=tuple(settings.get('player_screen',[.5,.5]))
        if getattr(self,'movement_test_mode',False):return origin
        regions=self._latest_hud_state.regions;health=regions.get('health',{})
        if (not settings.get('infer_player_from_hud',False) or not self._latest_hud_state.health_valid
                or time.monotonic()-self._hud_at>=.8 or not health.get('tracked_stack') or not health.get('bbox') or self._latest_frame is None):return origin
        h,w=self._latest_frame.shape[:2];box=health['bbox'];cx=(box[0]+box[2])/2000
        bottom=max((r['bbox'][3] for k,r in regions.items() if k in {'health','sp','mp'} and r.get('visible') and r.get('bbox')),default=box[3])/1000
        foot=bottom+max(100,(box[2]-box[0])*w/1000*4)/h
        return (cx,foot) if .2<cx<.8 and .25<foot<.75 else origin

    async def handle_control(self,message):
        if ''.join(message.lower().split()) in {'/release-pause','일시중지'}:
            self._release_paused=not getattr(self,'_release_paused',False)
            if self._release_paused:
                await self.scheduler.clear()
                await self.scheduler._cancel_execution()
                await self.scheduler.executor.release_held_attack()
                await self.scheduler.executor.release_held_move()
                controller=self.scheduler.executor._input
                release=getattr(controller,'release_inputs',controller.stop)
                await release()
                self._physical_mouse_pause()
            print('[CONTROL] RELEASE pause '+('enabled' if self._release_paused else 'disabled'))
            self.emit_web_event('control',action='release_pause',release_paused=self._release_paused)
            return True
        paused=self._paused
        text=message.lower()
        requested=''.join(text.split()).rstrip('.!?')
        active=not self._paused and not getattr(self,'_processing_halted',False)
        if requested in {'제자리사냥','/stationary-hunt'}:
            if active and getattr(self,'_stationary_hunt_mode',False):return True
            repeat=getattr(self,'_manual_skill_mode',False)
            result=await self.handle_control('/hunt')
            if result:
                self._stationary_hunt_mode=True
                self._stationary_left_down=False;self._stationary_manual_move_until=0
                self._move_only=False
                self._movement_hunt_requested=True
                self._screen_guide_enabled=False
                self._repeat_skills_hunting=False
                self._manual_skill_mode=repeat
                self._stationary_skill_mode=False
                self._navigation_escape=None;self._wall_probe_pending=False
                task=getattr(self,'_post_movement_dodge_task',None)
                if task is not None and not task.done():task.cancel()
                await self.scheduler.submit_emergency(command('STOP',source='STATIONARY_HUNT',epoch=self._epoch))
                self.emit_web_event('control_mode',mode='stationary_hunt',message='제자리사냥 · 빠른 몹 타겟팅 + 기본 공격(오른쪽 버튼) 유지')
            return result
        if requested in {'공격스킬','반복스킬','/skills','/repeat-skills'}:
            if active and getattr(self,'_manual_skill_mode',False):return True
            moving=(active and (self._move_only or not getattr(self,'_stationary_skill_mode',False)))
            result=True if active else await self.handle_control('/hunt')
            if result:
                self._manual_skill_mode=True
                self._stationary_skill_mode=(not getattr(self,'_stationary_hunt_mode',False)
                                             and not moving and requested in {'반복스킬','/repeat-skills'})
                if self._stationary_skill_mode:
                    self._repeat_skills_hunting=False
                    self._navigation_escape=None;self._wall_probe_pending=False
                    task=getattr(self,'_post_movement_dodge_task',None)
                    if task is not None and not task.done():task.cancel()
                    await self.scheduler.submit_emergency(command('STOP',source='REPEAT_SKILLS',epoch=self._epoch))
                self.emit_web_event('control_mode',mode='skills',message='등록된 활성 공격스킬 · 설정 간격으로 반복')
            return result
        if requested in {'/replan','예정진행방향변경','예정진행방향바꿔줘'}:
            self._planned_move_heading=None;self._planned_recovery_since=None;self._planned_turn_candidate=None
            self._epoch+=1
            self._next_navigation_plan=None;self._blocked_goal_sample=None
            self._directive=None;self._nav_direction=None
            self.click_journey.reset('user_replan')
            self.minimap_memory.request_replan()
            await self.scheduler.submit_emergency(command('STOP',source='USER_REPLAN',epoch=self._epoch))
            self.emit_web_event('control',action='replan')
            return True
        corridor_move=requested in {'/move','이동','이동해','이동해줘','통로중앙이동','통로중앙으로이동해'}
        hunt_start=requested in {'/hunt','hunt','사냥시작','사냥시작해','사냥시작해줘',
                                '자동사냥','자동사냥시작','자동사냥시작해','자동사냥시작해줘',
                                '자동사냥해','자동사냥해줘','사냥해','사냥해줘','사냥을해줘'}
        route_start=corridor_move or hunt_start
        if route_start and active:
            same=(corridor_move and self._move_only and not getattr(self,'_stationary_skill_mode',False)
                  or hunt_start and not self._move_only and not getattr(self,'_stationary_skill_mode',False)
                  and not getattr(self,'_stationary_hunt_mode',False))
            if same:return True
            # Adding movement preserves the current route, cooldowns and pending input.
            self._stationary_hunt_mode=False
            self._stationary_skill_mode=False
            self._stationary_attack_record=None
            self._move_only=corridor_move
            self._movement_hunt_requested=hunt_start
            self._repeat_skills_hunting=hunt_start
            self._screen_guide_enabled=corridor_move and self.profile.name=='diablo4'
            self._follow_orange_route=True
            self.emit_web_event('control_mode',mode='move_only' if corridor_move else 'move_and_attack',
                                message='이동' if corridor_move else '사냥시작')
            return True
        if route_start:
            self._planned_move_heading=None;self._planned_recovery_since=None;self._planned_turn_candidate=None
            self._arrow_last_good_guide=None;self._arrow_last_good_at=-1e9
            self._arrow_hold_distance=None;self._arrow_turn_candidate=None
            self._space_prompt_latched=False;self._space_prompt_missing_since=None
            self._space_prompt_wait_until=0;self._space_prompt_pending_until=0
            self._space_prompt_seen_at=-1e9;self._space_prompt_scan_at=-1e9
            self._screen_guide_enabled=corridor_move and self.profile.name=='diablo4'
            self._screen_move_guide=None;self._screen_guide_seen_at=-1e9;self._screen_guide_scanned_at=-1e9
            self._stationary_attack_record=None
            self._stationary_hunt_mode=False
            self._stationary_skill_mode=False
            self._repeat_skills_hunting=hunt_start
            self._manual_skill_pending_at=None;self._manual_skill_move_required=False
            self._manual_skill_mode=False
            self._movement_hunt_target=None;self._movement_target_missing=None
            self._movement_hunt_requested=not corridor_move
            self._move_only=corridor_move
            if corridor_move:
                self.combat_guard.reset()
                self._attack_track_id=None
                await self.scheduler.executor.release_held_attack()
            self._follow_orange_route=True
            pending_gpu=False
            for name in ('_scene_scan_task','_obstacle_scan_task'):
                task=getattr(self,name,None)
                if task and not task.done():
                    task.cancel();pending_gpu=True
            if pending_gpu and callable(getattr(type(self.vl),'cancel_pending',None)):
                self.vl.cancel_pending()
            self._hunt_preferred_direction=None
            self.click_journey.reset()
            self.minimap_memory.resume()
            message='/hunt'
        hint=None
        if re.search(r'사냥.*(?:시작|해)|자동사냥|/hunt',text) and not re.search(r'중단|중지|그만|멈',text):
            x=1 if re.search(r'동쪽|오른쪽|동북|북동|동남|남동',text) else -1 if re.search(r'서쪽|왼쪽|서북|북서|서남|남서',text) else 0
            y=-1 if re.search(r'북|위쪽|위로',text) else 1 if re.search(r'남|아래',text) else 0
            if x or y:hint=(x,y);message='/hunt'
        compact=''.join(message.lower().split()).rstrip('.!?')
        stopping=compact in {'/stop','/pause','/quit','멈춰','정지','중지','stop','사냥중단','사냥중단해','사냥중지','사냥중지해','사냥멈춰','사냥그만','사냥그만해'}
        if stopping:
            if not getattr(self,'_tab_toggle_inflight',False):self._tab_resume_state=None
            self._arrow_hold_distance=None;self._arrow_turn_candidate=None
            self._space_prompt_latched=False;self._space_prompt_missing_since=None
            self._space_prompt_wait_until=0;self._space_prompt_pending_until=0
            self._space_prompt_seen_at=-1e9
            self._screen_guide_enabled=False
            self._stationary_attack_record=None
            if not self._focus_stopping:
                self._processing_halted=True
                self._halt_reason='사냥 중단 · 모든 작업 일시정지'
            self._stationary_hunt_mode=False
            self._stationary_skill_mode=False
            self._repeat_skills_hunting=False
            self._manual_skill_mode=False
            self._movement_hunt_target=None;self._movement_target_missing=None
            self._movement_hunt_requested=False
            if not self._focus_stopping:
                self._manual_control=True
                self._follow_orange_route=False
            self._qwen_stopped=True
            task=getattr(self,'_scene_scan_task',None)
            if task and not task.done():task.cancel()
            self._scene_scan_status='stopped'
            if self._startup_hud_inflight:self._startup_hud_attempted=False
        if not stopping and compact in {'/resume','resume','계속','다시시작'}:message='/hunt'
        try:
            result=await super().handle_control(message)
        finally:
            if stopping:
                for client in {id(self.vl):self.vl,id(self.chat_vl):self.chat_vl}.values():
                    if callable(getattr(type(client),'cancel_pending',None)):client.cancel_pending()
        if result and stopping and not self._focus_stopping:
            self.emit_web_event('processing_halted',message=self._halt_reason)
        if result and not self._paused:
            if not getattr(self,'_tab_toggle_inflight',False):self._tab_resume_state=None
            self._manual_control=False
            self._qwen_stopped=False
            for client in {id(self.vl):self.vl,id(self.chat_vl):self.chat_vl}.values():
                if callable(getattr(type(client),'resume_requests',None)):client.resume_requests()
            self._scene_scan_status='idle'
            if hunt_start:
                self._movement_hunt_requested=True
                self._move_only=False
                self.emit_web_event('control_mode',mode='move_and_attack',message='사냥 시작 · 이동 + 공격')
        if result and not stopping and not corridor_move and compact not in {'/status'}:self._move_only=False
        if hint is not None:self._hunt_preferred_direction=hint
        elif str(message).strip()=='/hunt' and not hasattr(self,'_hunt_preferred_direction'):self._hunt_preferred_direction=None
        if self._paused:
            self.click_journey.reset()
            task=getattr(self,'_obstacle_scan_task',None)
            if task and not task.done():task.cancel()
        if paused and not self._paused:self.minimap_memory.resume()
        return result

    def _world_click_exclusions(self):
        if self._latest_frame is None:return []
        hud=copy.deepcopy(self._docs['hud.json'])
        measured=self._latest_hud_state.regions
        if (not getattr(self,'movement_test_mode',False) and measured
                and self._latest_hud_state.health_valid and time.monotonic()-self._hud_at<.8):
            hud['regions'].update(measured)
        else:
            for k in ('health','sp','mp'):
                if k in hud['regions']:hud['regions'][k]['tracked_stack']=False
        zones=click_exclusions(self.profile.name,hud,self._docs['navigation.json'],self._latest_frame.shape,self._latest_frame)
        if not hud['regions'].get('health',{}).get('tracked_stack'):zones.append((.47,.43,.53,.60))
        h,w=self._latest_frame.shape[:2]
        for obj in self._objects:
            if obj.object_type=='player':
                x1,y1,x2,y2=obj.bbox;zones.append((max(0,x1/w-.02),max(0,y1/h-.02),min(1,x2/w+.02),min(1,y2/h+.02)))
        return zones

    def _policy_objects(self,objects):
        self.policy.player_origin=self._world_player_origin()
        if self._move_only:return [o for o in objects if o.object_type=='obstacle']
        h,w=self._latest_frame.shape[:2];zones=self._world_click_exclusions()
        return [o for o in objects if o.object_type!='monster' or point_outside(((o.bbox[0]+o.bbox[2])/2/w,(o.bbox[1]+o.bbox[3])/2/h),zones)]

    def _analyze_hud(self,frame):
        state=super()._analyze_hud(frame)
        with self._profile_lock:
            if state.health_valid and state.health is not None:
                self._hp_display_value=state.health
                self._hp_display_missing_at=None
            elif self._hp_display_missing_at is None:self._hp_display_missing_at=time.monotonic()
        return state

    async def _recheck_hud(self,reason='HP 미측정'):
        # After the one startup request, all recovery is local OpenCV.
        if self._processing_halted or self._hud_rechecking:return
        self._hud_waiting=True
        if self._hud_missing_at and time.monotonic()-self._hud_missing_at>=2:
            await self._try_hud_probe_move()

    async def _calibration_loop(self):
        while self._running:
            if not self._focus_work_allowed():
                if hasattr(self,'minimap_memory'):self.minimap_memory.suspend('foreground_wait')
                await asyncio.sleep(.1);continue
            if self._latest_frame is not None and not self._processing_halted and not self._startup_hud_attempted:
                await self._calibrate_startup_hud()
            if self._startup_hud_attempted and not self._hud_ready and not self._processing_halted:
                self._hud_waiting=True
            await asyncio.sleep(.1)

    async def _calibrate_startup_hud(self):
        if self._qwen_stopped or self._startup_hud_inflight or self._startup_hud_attempted or self._latest_frame is None or self._hud_rechecking or not self._foreground():return
        self._startup_hud_inflight=True;self._hud_recheck_status='startup'
        identity=self._frame_identity;profile=self.profile;epoch=self._epoch
        frame=self._latest_frame.copy();previous=copy.deepcopy(self._docs['hud.json']);applied=False;restore_regions=None
        print('[HUD] 시작 보정 1회 · 창 크기와 색상 정보 적용')
        try:
            native=await asyncio.to_thread(profile.locate_bars,frame)
            color_regions=native or previous['regions']
            colors={}
            for name,item in color_regions.items():
                if name not in {'health','sp','mp'}:continue
                colors[name]={'hsv_ranges':item.get('hsv_ranges',[])}
                box=item.get('bbox')
                if not box:continue
                h,w=frame.shape[:2];x1,y1,x2,y2=[round(v*s/1000) for v,s in zip(box,(w,h,w,h))]
                crop=frame[max(0,y1):min(h,y2),max(0,x1):min(w,x2)]
                if crop.size:
                    hsv=cv2.cvtColor(crop,cv2.COLOR_BGR2HSV);mask=cv2.inRange(hsv,(0,40,30),(179,255,255))
                    pixels=hsv[mask>0]
                    if len(pixels):
                        import numpy as np
                        colors[name]['sample_hsv']=np.median(pixels,axis=0).astype(int).tolist()
            geometry={'capture_resolution':[frame.shape[1],frame.shape[0]],'coordinate_space':'full captured image, bbox normalized 0..1000'}
            get_region=getattr(self.capture,'input_region',None)
            rect=get_region() if callable(get_region) else None
            if isinstance(rect,(list,tuple)) and all(type(v) in (int,float) for v in rect):geometry['window_rect']=list(rect)
            native_boxes={k:v.get('bbox') for k,v in (native or {}).items() if k in {'health','sp','mp'}}
            context=profile.hud_prompt_context+'\nStartup calibration only. Window geometry: '+json.dumps(geometry)+'\nExpected HSV ranges and sampled colors: '+json.dumps(colors)+'\nOpenCV player meter candidates: '+json.dumps(native_boxes)+ '\nUse color and PLAYER layout together, never enemy bars. Do not estimate HP numbers.'
            async with self._vl_lock:
                if epoch!=self._epoch or not self._focus_work_allowed() or self._processing_halted or not self._running:return
                self._startup_hud_attempted=True  # Only an actual request consumes the single attempt.
                h,w=frame.shape[:2]
                request_frame=cv2.resize(frame,(512,max(1,round(h*512/w))),interpolation=cv2.INTER_AREA) if w>512 else frame
                result=await asyncio.wait_for(asyncio.to_thread(self.vl.discover_hud,request_frame,getattr(self._latest_captured,'source',profile.name),context,max_tokens=384,timeout=6),timeout=6.5)
            if epoch!=self._epoch or not self._focus_work_allowed() or not self._running or self._processing_halted or identity!=self._frame_identity or profile is not self.profile:return
            self._last_vl_ms=result.elapsed_ms
            regions=copy.deepcopy(previous['regions'])
            for name,proposal in result.data.items():
                if name not in {'health','sp','mp'} or not proposal.get('visible') or proposal.get('confidence',0)<.6:continue
                regions[name]={**proposal}
                old=previous['regions'].get(name,{})
                for key in ('hsv_ranges','inset_fraction','allow_empty'):
                    if key in old:regions[name][key]=copy.deepcopy(old[key])
            native=await asyncio.to_thread(profile.locate_bars,self._latest_frame)
            if epoch!=self._epoch or not self._focus_work_allowed() or identity!=self._frame_identity or profile is not self.profile:return
            if native:regions.update(native)  # Prefer current geometry while capture and movement continued.
            regions=profile.prepare_regions(regions)
            candidate={**previous,'regions':regions,'calibration':{'resolution':geometry['capture_resolution'],'validated':False,'source':'startup-qwen+opencv','window_geometry':geometry,'colors':colors}}
            validate_document('hud.json',candidate)
            restore_regions=profile.export_hud_regions() or previous['regions']
            self._set_hud_regions(regions);applied=True
            state=await asyncio.to_thread(self._analyze_hud,self._latest_frame)
            if epoch!=self._epoch or not self._focus_work_allowed() or not self._running or self._processing_halted or identity!=self._frame_identity or profile is not self.profile:return
            if not state.health_valid:raise ValueError('시작 보정의 HP를 OpenCV로 확인하지 못했습니다')
            candidate['regions']=profile.export_hud_regions() or regions
            candidate['calibration']['validated']=True
            _,revisions=self.store.snapshot()
            ops=[{'file':'hud.json','op':'replace','path':'/'+k,'value_json':json.dumps(candidate[k],ensure_ascii=False)} for k in ('regions','calibration')]
            self._profile_write_in_progress=True
            try:
                await asyncio.to_thread(self.store.apply,ops,revisions)
                docs,self._revisions=self.store.snapshot();self._docs['hud.json']=docs['hud.json']
            finally:self._profile_write_in_progress=False
            self._latest_hud_state=state;self._hud_at=time.monotonic();self._hud_ready=True;self._hud_scan_complete=True
            self._hud_recheck_status='startup_complete'
            print('[HUD] 시작 보정 완료 · 이후 OpenCV만 사용')
        except Exception as exc:
            if applied and profile is self.profile and identity==self._frame_identity:self._set_hud_regions(restore_regions)
            self._hud_recheck_status='startup_failed'
            print(f'[HUD] 시작 보정 실패 · 재보정 없이 기존 설정으로 OpenCV 측정: {exc}')
        finally:
            self._startup_hud_inflight=False;self._hud_waiting=not self._hud_ready

    def _combat_spec(self,compact=False):
        spec=load_gameplay_spec(self.profile.profile_dir)
        path=PROJECT_ROOT/'BASIC_COMBAT.md'
        try:
            stat=path.stat();common_signature=(stat.st_mtime_ns,stat.st_size)
        except OSError:
            common_signature=None
        signature=(str(spec.path.resolve()),spec.revision,common_signature)
        if self._combat_spec_cache is None or self._combat_spec_cache[0]!=signature:
            common=path.read_text(encoding='utf-8') if common_signature else ''
            # Keep later profile updates/custom rules within the same prompt budget.
            profile_text=spec.instructions if len(spec.instructions)<=2500 else spec.instructions[:1600]+'\n…\n'+spec.instructions[-897:]
            full='저장된 JSON > 선택한 게임 프로필 > 공통 규칙 순으로 적용. 충돌하면 현재 프로필/JSON을 따른다.\n선택한 게임 프로필 규칙:\n'+profile_text+'\n공통 규칙:\n'+common[:3500]
            game_brief='\n'.join(line for line in spec.instructions.splitlines() if line.startswith('- ') and '미니맵' not in line)[:380]
            common_brief='\n'.join(line for line in common.splitlines() if line.startswith('- '))[:260]
            brief=f'선택한 프로필 {self.profile.name}:\n{game_brief}\n공통 규칙:\n{common_brief}'
            self._combat_spec_cache=(signature,full,brief[:700])
            self._profile_gameplay_info={'profile':self.profile.name,'file':f'app/profiles/{self.profile.name}/GAMEPLAY.md',
                'revision':spec.revision,'applied':True,'settings_precedence':'profile_json_then_md_defaults'}
        return self._combat_spec_cache[2 if compact else 1]

    def _automatic_skill_allowed(self,skill):
        return self.profile.name!='diablo4' or skill['key']!=self._docs['input.json']['bindings']['CAST_BUFF']

    def _qwen_execution_context(self):
        settings=self._docs['input.json'];now=time.monotonic();disabled=settings.get('disabled_actions',[])
        skills=[]
        for skill in settings.get('attack_skills',[]):
            if not skill['enabled'] or 'USE_SKILL' in disabled or not self._automatic_skill_allowed(skill):continue
            last=self.scheduler._last_execution.get(('USE_SKILL',skill['id']),-1e9)
            ready=now-last>=skill['cooldown_ms']/1000
            if skill.get('visual_ready'):
                observed,at,epoch=self._skill_feedback.get(skill['id'],(False,0,-1))
                ready=ready and observed and now-at<=.5 and epoch==self._epoch
            skills.append({'id':skill['id'],'key':skill['key'],'ready':bool(ready)})
        record=self.click_journey.record
        travel={'status':record['status'],'travelled_map_px':round(record['distance'],1),
                'remaining_map_px':round(float(np.linalg.norm(record.get('destination',record['goal'])-record['last'])),1),
                'sent_screen_click':record['screen_target']} if record else {'status':'no_sent_move'}
        return {'attack_mode':settings.get('basic_attack_mode','tap'),'attack_key':settings['bindings']['ATTACK'],
                'move_key':settings['bindings']['MOVE'],'skills':skills,'travel':travel,
                'confirmed_enemy_bars':sum(1 for o in self._objects if o.enemy_bar_confirmed and o.relation=='hostile'),
                'buff':{'key':settings['bindings']['CAST_BUFF'],'registered':len(self.buff_monitor.references),
                        'active':list(getattr(self,'_observed_buffs',[])),'fresh':now-self._buff_at<1,
                        'allowed':bool(self.buff_monitor.references and self.buff_monitor.pending_recast and now-self._buff_at<1 and 'CAST_BUFF' not in disabled),
                        'mode':'retry_only_when_all_registered_icons_absent'}}

    def scan_scene_sync(self,frame,epoch,user_message=None):
        if self._qwen_stopped or not self._foreground():return
        if not user_message and (getattr(self,'_follow_orange_route',False) or self._hunt_active and self.minimap_memory.pin_world is not None):return
        scene=self.scene
        now=time.monotonic()
        scene.begin(frame,now)
        background=not user_message
        prompt=scene.prompt(self._docs['knowledge.json'],compact=background)
        prompt+='\nDEFAULT COMBAT INSTRUCTIONS:\n'+self._combat_spec(compact=background)
        prompt+='\nLOCAL MINIMAP MEMORY (OpenCV, not guessed): '+json.dumps(self.minimap_memory.prompt_summary())
        prompt+='\nCURRENT EXECUTION FEEDBACK: '+json.dumps(self._qwen_execution_context(),ensure_ascii=False,separators=(',',':'))
        prompt+=' Buff keys are allowed only when fresh feedback confirms ALL registered icons absent. ANY matching registered icon stops retries; a timer alone never permits casting. Choose intent; the local engine handles approach/retreat, path clicks and skill timing.'
        prompt+='\nGAME COMBAT SETTINGS: '+json.dumps({'combat':self._docs['input.json'].get('combat',{}),'disabled_actions':self._docs['input.json'].get('disabled_actions',[])})
        prompt+=' Never invoke a disabled action; ask the user to configure its game key. Diablo IV red-bar evidence does not apply to other games.'
        prompt+=' Local navigation uses the selected game map mode, reachable central routes and verified movement history. Use the world image for combat identification. Travel directions come from the minimap planner, never from world terrain.'
        prompt+='\nreply is concise Korean. Without a user instruction, action=NONE and target_object=null. '
        prompt+='For attack/take/interact, target_object is the zero-based index of your objects array. Unknown targets must remain NONE. HUNT starts persistent hunting. '
        if not background:prompt+='Game policy: '+json.dumps(self._docs['hunting.json']['policy'],ensure_ascii=False)
        if user_message:prompt+='\nUSER INSTRUCTION: '+user_message
        schema=copy.deepcopy(SCENE_SCHEMA)
        schema['properties']['objects']['maxItems']=min(2,scene.max_objects) if background else scene.max_objects
        request_frame=frame
        if background:
            schema['properties']['reply']['maxLength']=40
            fields=schema['properties']['objects']['items']['properties']
            fields['clues']['maxLength']=24;fields['name']['maxLength']=24
            fields['bbox']['items']['type']='integer'
            prompt+=f'\nAutomatic scan: detect at most {min(2,scene.max_objects)} objects. reply="", action=NONE, target_object=null. Brief clues only; prioritize unknown NPCs and visible threats.'
            h,w=frame.shape[:2]
            if w>512:request_frame=cv2.resize(frame,(512,max(1,round(h*512/w))),interpolation=cv2.INTER_AREA)
        options={'max_tokens':self._background_scene_tokens,'timeout':self._background_scene_timeout} if background else {}
        result=self.vl._request(request_frame,prompt,schema,'scene_analysis',self.profile.name,**options)
        if epoch!=self._epoch or scene is not self.scene or not self._running or self._processing_halted or self._hud_rechecking or not self._foreground():
            return
        self._last_vl_ms=result.elapsed_ms
        # A renewal launched before an encounter must not replace an actively
        # verified target with stale model boxes when its response arrives.
        if background and time.monotonic()-self._yolo_at<.75 and any(
                o.track_id==self.combat_guard.focus and o.enemy_bar_confirmed and self.combat_guard.permits(o.track_id)
                for o in self._objects):
            return result.data
        questions=self.scene.ingest(result.data,frame,time.monotonic())
        # Re-locate all bodies in the latest capture before staging any command.
        current=self._latest_frame if self._latest_frame is not None else frame
        objects=self.scene.track(current)
        if self.profile.name=='diablo4':
            objects=self._confirm_diablo_enemies(current)
        for o in objects:self.resolver.protect_named_ally(o)
        self._objects=objects
        self._yolo_at=time.monotonic()
        self._last_vl_ms=result.elapsed_ms
        for q in questions:
            print('Qwen-VL> '+q['question'])
            self.emit_web_event('learning_question',question=q)
        return result.data

    def _confirm_diablo_enemies(self,frame):
        config=self._docs['vision.json'].get('red_enemy_bar',{})
        bars=self.combat_feedback.red_bar_boxes(frame,config)
        bars+=self.combat_feedback.tracked_black_bars(frame,config)
        bars=list(dict.fromkeys(bars))
        with self.scene.lock:
            for o in self.scene.objects:
                # Movement mode does not run the tracking worker that normally
                # clears these flags. Never reuse a previous frame's enemy bar.
                o.enemy_bar_confirmed=False;o.enemy_health_valid=False
                self.resolver.protect_named_ally(o)
            objects=self.scene.seed_enemy_bars(frame,bars)
            self.combat_feedback.confirm_red_bars(frame,objects,config,bars=bars)
            return objects

    async def _scan_background(self,frame,epoch):
        attempted=False
        try:
            self._scene_scan_status='waiting'
            async with self._vl_lock:
                if time.monotonic()<getattr(self,'_background_rest_until',0):return
                if self._qwen_stopped or epoch!=self._epoch or not self._chat_queue.empty() or not self._foreground() or not self._combat_scene_needed(self._objects):return
                self._scene_scan_status='analyzing'
                print('[QWEN SCENE] 장면 재분석 시작')
                attempted=True
                await asyncio.to_thread(self.scan_scene_sync,frame,epoch)
            if attempted:self._scene_failures=0
            self.yolo.last_error='; '.join(self.scene.last_warnings) or None
            if epoch==self._epoch:
                print(f'[QWEN SCENE] 분석 완료 · 화면={self.scene.scene} · {self._last_vl_ms:.0f} ms')
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.yolo.last_error=str(exc)
            self._scene_failures=min(4,self._scene_failures+1)
            retry=min(60,12*2**(self._scene_failures-1))
            self._scene_error_until=time.monotonic()+retry
            self.scene.force=True
            print(f'[QWEN SCENE] {exc}; local navigation continues; retry after {retry}s')
        finally:
            if attempted:self._background_rest_until=time.monotonic()+12
            self._scene_scan_status='stopped' if self._qwen_stopped else 'idle'

    def stop(self):
        task=getattr(self,'_post_movement_dodge_task',None)
        if task is not None and not task.done():task.cancel()
        task=getattr(self,'_obstacle_scan_task',None)
        if task and not task.done():task.cancel()
        self._epoch+=1
        if self._scene_scan_task:self._scene_scan_task.cancel()
        super().stop()

    async def _yolo_loop(self):
        # Kept as a worker name for the existing lifecycle; only OpenCV/Qwen run.
        while self._running:
            if not self._focus_work_allowed():
                if hasattr(self,'minimap_memory'):self.minimap_memory.suspend('foreground_wait')
                await asyncio.sleep(.1);continue
            if self._processing_halted or self._hud_rechecking or not self._hud_ready or self._latest_frame is None:
                await asyncio.sleep(.1);continue
            identity=(str(self.profile.profile_dir),self._frame_identity)
            if identity!=self._scene_identity:
                self.scene.reset();self._scene_identity=identity
            frame=self._latest_frame.copy();epoch=self._epoch
            started=time.monotonic()
            try:
                live=await asyncio.to_thread(self.scene.track,frame)
                if self.profile.name=='diablo4':
                    live=self._confirm_diablo_enemies(frame)
                self.combat_feedback.annotate(frame,live,self._docs['vision.json'].get('enemy_health_bar',{}))
                if epoch!=self._epoch or not self._focus_work_allowed():continue
                self.resolver.friendly_names={name.casefold().strip() for entry in self._docs['knowledge.json'].get('named_entities',[]) if entry.get('relation')=='friendly' for name in entry.get('names',[])}
                for o in live:
                    self.resolver.protect_named_ally(o)
                self._objects=live
                self._latest_yolo_objects=live
                self._yolo_at=time.monotonic()
                self.combat_guard.observe(live)
                self.yolo.raw_count=self.yolo.filtered_count=len(live)
                self.yolo.last_inference_ms=(time.monotonic()-started)*1000
                self.yolo.classes={i:o.object_type for i,o in enumerate(live)}
                self.statistics.observe(live,shape=frame.shape,roi=(0,0,1,1))
                for skill in self._docs['input.json'].get('attack_skills',[]):
                    if skill.get('visual_ready'):
                        self._skill_feedback[skill['id']]=(self.combat_feedback.matches(frame,skill['visual_ready']),self._capture_at,epoch)
                # No scene calls during a visually verified held attack.
                engaged=any(o.track_id==self.combat_guard.focus and o.enemy_bar_confirmed and self.combat_guard.permits(o.track_id) for o in live)
                due=self._combat_scene_needed(live) and self.scene.due(frame,started,active=self._hunt_active or bool(self._directive))
                idle=self._paused and self.scene.last_success>0 and self._chat_queue.empty()
                if (not self._qwen_stopped and self._combat_scene_needed(live) and due and self._foreground() and not engaged and not idle and self._chat_queue.empty() and started>=max(self._scene_error_until,getattr(self,'_background_rest_until',0))
                        and (self._scene_scan_task is None or self._scene_scan_task.done())):
                    self._scene_scan_task=asyncio.create_task(self._scan_background(frame,epoch),name='qwen-scene-scan')
                # World-scene directions must never steer travel. The map planner owns it.
                self._nav_direction=None;self._nav_direction_at=0
                if self._hunt_active or self._directive and self._directive.get('action')=='MOVE':
                    reason=self._movement_block_reason()
                    self._log_movement_status(reason)
            except Exception as exc:
                self.yolo.last_error=str(exc)
                self._scene_error_until=time.monotonic()+10
                self.scene.force=True
                print(f'[QWEN SCENE] {exc}; retry after 10s')
            await asyncio.sleep(self._tracking_interval)

    async def _learning_loop(self):
        while self._running:await asyncio.sleep(.5)

    async def _combat_recheck_loop(self):
        # Fresh OpenCV bars and user confirmation replace repeated crop inference.
        while self._running:
            if not self._focus_work_allowed():
                if hasattr(self,'minimap_memory'):self.minimap_memory.suspend('foreground_wait')
                await asyncio.sleep(.1);continue
            for o in self._objects:
                if o.enemy_bar_confirmed and o.track_id in self.combat_guard.sessions:
                    self.combat_guard.confirm(o.track_id,o.relation=='hostile')
                elif (self.profile.name!='diablo4' and o.track_id in self.combat_guard.sessions and time.monotonic()-self.scene.last_success<6
                      and o.status=='confirmed' and o.semantic_confidence>=.8):
                    self.combat_guard.confirm(o.track_id,o.object_type=='monster' and o.relation=='hostile')
            await asyncio.sleep(.1)

    def _combat_scene_needed(self,objects):
        if getattr(self,'_follow_orange_route',False) or self._hunt_active and self.minimap_memory.pin_world is not None:return False
        if self.profile.name=='diablo4':
            # Red bars already provide a local hostile verdict; no GPU renewal needed.
            return any(o.object_type=='monster' and o.relation=='hostile' and not o.enemy_bar_confirmed for o in objects)
        return bool((self._directive and self._directive.get('action') in {'ATTACK','USE_SKILL'})
                    or any(o.object_type=='monster' and o.relation=='hostile' for o in objects))

    async def _navigation_replan_loop(self):
        # The .2-second map worker replans locally; travel does not force a world scan.
        while self._running:await asyncio.sleep(.5)

    def _scene_lease_seconds(self):
        # Renewals start every six seconds, then spend time waiting for Qwen.
        # Do not let a fixed eight-second lease expire during a normal renewal.
        latency=max(0,float(self._last_vl_ms or 0))/1000
        return min(30,max(8,max(6,self.scene.min_interval)+latency*1.5+2))

    def _movement_scene_current(self):
        if time.monotonic()-self.scene.last_success<=self._scene_lease_seconds():return True
        if (self._hunt_active and self.scene.scene=='play' and time.monotonic()-self.scene.last_success<=30
                and self._hud_ready and self._latest_hud_state.health_valid and self._fresh()
                and self._latest_hud_state.regions.get('health',{}).get('tracked_stack')):
            return True
        return self._latest_frame is not None and self.scene.unchanged_play(self._latest_frame,time.monotonic())

    def can_execute(self,c):
        self._input_block_reason=None
        def reject(reason):
            self._input_block_reason=reason
            return False
        if c.action_type!='STOP' and getattr(self,'_release_paused',False):
            return reject('RELEASE 일시중지')
        if (c.action_type!='STOP' and self._manual_mouse_pause_enabled()
                and (mouse_left_down() or getattr(self,'_stationary_left_down',False))):
            return reject('사용자 왼버튼 유지 · 자동 입력 일시 정지')
        movement_test=getattr(self,'movement_test_mode',False)
        if c.action_type!='STOP' and time.monotonic()<getattr(self,'_space_prompt_wait_until',0):
            return reject('Space 입력 후 2초 대기')
        if getattr(c,'source',None)=='SCREEN_SPACE_PROMPT':
            valid=(c.action_type=='INTERACT' and self.profile.name=='diablo4'
                   and not getattr(self,'_stationary_hunt_mode',False) and not getattr(self,'_stationary_skill_mode',False)
                   and not self._paused and not self._processing_halted
                   and c.decision_epoch==self._epoch and self._fresh() and self._foreground()
                   and (time.monotonic()-getattr(self,'_space_prompt_seen_at',-1e9)<.5
                        or space_prompt(self._latest_frame) is not None))
            if not valid:return reject('Space 안내 미확인/중단/게임 비활성')
            return True if self._space_heading_allows() else reject('Space 입력 대기 · 이동 방향과 화살표 방향의 차이가 30도 초과 또는 방향 미확인')
        stationary_repeat=(c.action_type=='USE_SKILL' and getattr(c,'source',None)=='MANUAL_SKILL'
                           and getattr(self,'_manual_skill_mode',False))
        if (getattr(self,'_stationary_hunt_mode',False) and (c.action_type=='ATTACK' or stationary_repeat)
                and (mouse_left_down() or getattr(self,'_stationary_left_down',False)
                     or time.monotonic()<getattr(self,'_stationary_manual_move_until',0))):
            return reject('제자리 사냥 · 수동 이동 중 몬스터 타겟/공격 중지')
        if getattr(self,'_stationary_hunt_mode',False) and c.action_type in {'MOVE','DODGE'}:
            return reject('제자리 사냥 · 이동 중지')
        if getattr(self,'_stationary_hunt_mode',False) and c.action_type not in {'STOP','ATTACK'} and not stationary_repeat:
            return reject('제자리 사냥 · 오른쪽 클릭 공격만 실행')
        if c.reason=='STATIONARY_MODE_HOLD':
            valid=(getattr(self,'_stationary_hunt_mode',False) and c.action_type=='ATTACK'
                   and c.source=='STATIONARY_HUNT' and c.maintain_attack
                   and c.decision_epoch==self._epoch and self._movement_hunt_enabled()
                   and not self._processing_halted and self._fresh() and self._foreground()
                   and not mouse_left_down()
                   and time.monotonic()>=getattr(self,'_stationary_manual_move_until',0)
                   and 'ATTACK' not in self._docs['input.json'].get('disabled_actions',[])
                   and (c.target is None or point_outside(c.target,self._world_click_exclusions())))
            return True if valid else reject('제자리 사냥 · 버튼 유지 중단/포커스 확인 필요')
        if c.reason=='STATIONARY_TARGET_GRACE':
            current=self._stationary_target_grace_command(time.monotonic())
            valid=(current is not None and c.action_type=='ATTACK' and c.maintain_attack
                   and c.source=='MOVEMENT_HUNT' and c.decision_epoch==self._epoch
                   and c.track_id==current.track_id and c.target==current.target
                   and self._movement_hunt_enabled() and not self._processing_halted
                   and self._fresh() and self._foreground()
                   and 'ATTACK' not in self._docs['input.json'].get('disabled_actions',[])
                   and point_outside(c.target,self._world_click_exclusions()))
            return True if valid else reject('제자리 사냥 · 대상 재확인 대기 종료/중단')
        if c.reason=='STATIONARY_POST_DEATH':
            record=getattr(self,'_stationary_attack_record',None)
            valid=(getattr(self,'_stationary_hunt_mode',False) and record
                   and c.action_type=='ATTACK' and c.source=='MOVEMENT_HUNT'
                   and c.track_id==record['track_id'] and c.target==record['target']
                   and record['until'] is not None and time.monotonic()<record['until']
                   and c.decision_epoch==record['epoch']==self._epoch
                   and self.combat_guard.blocked.get(c.track_id)=='HEALTH_DEPLETED_CONFIRMED'
                   and self._movement_hunt_enabled() and not self._processing_halted
                   and self._fresh() and self._foreground()
                   and 'ATTACK' not in self._docs['input.json'].get('disabled_actions',[])
                   and c.target is not None and point_outside(c.target,self._world_click_exclusions()))
            return True if valid else reject('제자리 사냥 · 사망 후 2초 추가 공격 종료/중단')
        if getattr(self,'_stationary_skill_mode',False) and c.action_type in {'MOVE','DODGE','ATTACK'}:
            return reject('반복 스킬 · 스킬만 실행')
        if c.action_type=='ATTACK' and not self._movement_hunt_enabled():return reject('사냥 시작 시 기본 공격 활성화')
        manual_skill=(c.action_type=='USE_SKILL' and c.source=='MANUAL_SKILL'
                      and (getattr(self,'_manual_skill_mode',False) or getattr(self,'_repeat_skills_hunting',False)))
        if c.source in {'NAVIGATION_ESCAPE','SCREEN_ARROW_ESCAPE'} and not self._docs['input.json'].get('movement_skill',{}).get('enabled',False):
            return reject('이동 스킬 미등록 또는 비활성')
        if movement_test:
            if c.action_type=='STOP':return True
            escape=c.action_type=='DODGE' and c.source in {'NAVIGATION_ESCAPE','NAVIGATION_DODGE','SCREEN_ARROW_ESCAPE'}
            hunting=(c.action_type in {'ATTACK','USE_SKILL'} and c.source=='MOVEMENT_HUNT'
                     and self._movement_hunt_enabled())
            if c.action_type!='MOVE' and not escape and not hunting and not manual_skill:return reject('이동 테스트 모드 · 이동 외 입력 중지')
            if hunting and (time.monotonic()-self._yolo_at>.75 or not any(
                    o.track_id==c.track_id and o.enemy_bar_confirmed and o.relation=='hostile'
                    and self.combat_guard.permits(o.track_id) and not self.combat_guard.is_blocked(o)
                    for o in self._objects)):
                return reject('공격 대상 최신 확인 대기')
            if self._paused or self._processing_halted or c.decision_epoch!=self._epoch or not self._fresh() or not self._foreground():
                return reject('이동 테스트 · 중단/포커스/캡처/명령 확인 필요')
        if self._move_only and not manual_skill and c.action_type in {'ATTACK','USE_SKILL','TAKE','INTERACT'}:return False
        if self.profile.name=='diablo4' and c.action_type=='USE_SKILL':
            key=next((s['key'] for s in self._docs['input.json'].get('attack_skills',[]) if s['id']==c.skill_id),None) if c.skill_id else self._docs['input.json']['bindings']['USE_SKILL']
            if not c.skill_id and key==self._docs['input.json']['bindings']['CAST_BUFF']:return False
        if c.action_type=='CAST_BUFF' and (not self.buff_monitor.references or not self.buff_monitor.pending_recast or time.monotonic()-self._buff_at>=1):return False
        if c.action_type in self._docs['input.json'].get('disabled_actions',[]):return False
        if c.skill_id:
            skill=next((s for s in self._docs['input.json'].get('attack_skills',[]) if s['id']==c.skill_id and s.get('enabled')),None)
            if skill is None:return False
            checked=replace(c,cooldown=skill['cooldown_ms']/1000)
            if not self.scheduler._is_cooldown_ready(checked):return reject('공격스킬 사용 간격 대기')
        if manual_skill:
            return (not self._paused and not self._processing_halted and c.decision_epoch==self._epoch
                    and self._latest_frame is not None and time.monotonic()-self._capture_at<1 and self._foreground())
        arrow_escape=c.source=='SCREEN_ARROW_ESCAPE'
        arrow_click=c.source=='SCREEN_ARROW_MOVE' or arrow_escape
        if arrow_click:
            guide=getattr(self,'_screen_move_guide',None)
            valid=(c.action_type==('DODGE' if arrow_escape else 'MOVE') and getattr(self,'_screen_guide_enabled',False)
                   and self._move_only and self._hunt_active and not self._paused and not self._processing_halted
                   and c.decision_epoch==self._epoch and self._fresh() and self._foreground()
                   and time.monotonic()-getattr(self,'_screen_guide_seen_at',-1e9)<.5
                   and guide and (guide.get('marker') or guide.get('arrow_tip')) and c.target is not None)
            if not valid:return reject('화살표 클릭 · 최신 안내/포커스/실행 상태 확인 필요')
            if arrow_escape:
                if 'DODGE' in self._docs['input.json'].get('disabled_actions',[]):return reject('이동 스킬 비활성')
                memory=self.minimap_memory
                with memory.lock:
                    if not memory.valid or not memory.stuck or time.monotonic()-memory.last_update>.75:
                        return reject('이동 스킬 · 최신 정체 확인 필요')
                    h,w=self._latest_frame.shape[:2]
                    _,top,_,bottom=game_viewport(self._latest_frame)
                    scale=self._docs.get('navigation.json',{}).get('minimap',{}).get('mapping',{}).get('screen_pixels_per_map_pixel',12)*(bottom-top)/1080
                    delta=(np.asarray(c.target)-self._world_player_origin())*np.array([w,h])/scale
                    angle=math.radians(memory.rotation);cosine,sine=math.cos(angle),math.sin(angle)
                    vector=np.array([delta[0]*cosine-delta[1]*sine,delta[0]*sine+delta[1]*cosine])
                    start=np.asarray(memory.player)*np.array(memory.mask.shape[::-1])/memory.CELL
                    if (not np.all(np.isfinite(vector)) or not all(0<=v<=1 for v in c.target)
                            or not 3*memory.CELL<=np.linalg.norm(vector)<=24.1
                            or not corridor_clear(start,start+vector/memory.CELL,memory.grid)):
                        return reject('이동 스킬 · 탈출 통로 차단 또는 범위 초과')
                return True
            h,w=self._latest_frame.shape[:2]
            point,heading=self._arrow_position_heading(guide)
            if heading is None:return reject('화살표 위치와 캐릭터 위치 확인 필요')
            if guide.get('planned_recovery'):
                since=getattr(self,'_planned_recovery_since',None)
                if since is None or time.monotonic()-since>.6:return reject('예정 방향 보정 종료 · 전방 화살표 확인 대기')
                heading=np.asarray(guide['planned_heading'])
            heading=self._arrow_cursor_adjustment(heading)
            anchor=getattr(c,'navigation_anchor',None)
            if anchor is not None:
                original=np.asarray(anchor)*np.array([w,h])
                intended=np.array(c.target)*np.array([w,h])-original
                intended_length=float(np.linalg.norm(intended))
                if (not np.all(np.isfinite(original)) or np.linalg.norm(point-original)>80
                        or intended_length<1e-6
                        or float(intended@heading)/intended_length<math.cos(math.pi/6)-.001):
                    return reject('화살표 이동 유지 · 기준 위치 또는 진행 방향 변경')
                point=original
            offset=np.array(c.target)*np.array([w,h])-point
            length=float(np.linalg.norm(offset))
            if (not np.all(np.isfinite(offset)) or not all(0<=v<=1 for v in c.target)
                    or not 99.9<=length<=100.1
                    or float(offset@heading)/max(length,1e-6)<math.cos(math.radians(5))):
                return reject('화살표 이동 · 화살표 기준 80px/아래쪽 5도 위치 재확인')
            # A current world-space arrow is authoritative; map projection,
            # terrain, object and HUD click exclusions cannot veto this click.
            return True
        zones=self._world_click_exclusions()
        nav=self._docs['navigation.json']
        if (c.action_type in {'MOVE','DODGE'} and c is not self._hud_probe_command
                and not arrow_click and c.source!='HP_RETREAT' and (not nav['minimap']['enabled'] or not self._local_map_current(nav))):return reject('미니맵 미확인: '+self.minimap_memory.snapshot()['reason'])
        if (c.action_type in {'MOVE','DODGE'} and c.direction is not None and c.target is None and c is not self._hud_probe_command
                and c.source!='HP_RETREAT' and self._docs['navigation.json']['minimap'].get('mapping',{}).get('enabled',False)
                and not self.minimap_memory.allows(c.direction)):return False
        if c.action_type in {'MOVE','DODGE'} and c.target is not None and not arrow_click and c is not self._hud_probe_command and c.source not in {'HP_RETREAT','NAVIGATION_ESCAPE','NAVIGATION_DODGE'}:
            h,w=self._latest_frame.shape[:2];ox,oy=self._world_player_origin()
            dx,dy=(c.target[0]-ox)*w,(c.target[1]-oy)*h
            length=math.hypot(dx,dy)
            if length<1e-6:return False
            mapped=nav['minimap'].get('mapping',{}).get('enabled',False)
            m=copy.deepcopy(nav['minimap']);mask=self._minimap_mask
            if mapped:
                memory=self.minimap_memory
                with memory.lock:
                    if not memory.snapshot()['valid'] or memory.grid is None or memory.mask is None:return reject('전송 직전 지도 갱신/위치 연결 실패')
                    approved=cv2.resize(memory.grid*255,(memory.grid.shape[1]*memory.CELL,memory.grid.shape[0]*memory.CELL),interpolation=cv2.INTER_NEAREST)
                    mask=np.zeros_like(memory.mask)
                    mask[:approved.shape[0],:approved.shape[1]]=approved
                    m['player']=list(memory.player)
                    m['verified_route']=True
            if mask is None:return False
            _,top,_,bottom=game_viewport(self._latest_frame)
            scale=m.get('mapping',{}).get('screen_pixels_per_map_pixel',12)*(bottom-top)/1080*(1 if mapped else 192/mask.shape[1])
            m['lookahead']=length/scale/mask.shape[1];m['pixel_directions']=True
            if not self.navigator._map_clear((dx/length,dy/length),mask,m):return reject('클릭까지의 미니맵 통로 차단')
        if c.action_type in {'MOVE','DODGE','ATTACK','USE_SKILL','TAKE','INTERACT'} and c.target is not None and not point_outside(c.target,zones):return reject('클릭 위치가 HUD/캐릭터 제외 영역 안에 있음')
        if c.action_type in {'MOVE','DODGE','ATTACK','USE_SKILL','TAKE','INTERACT'} and c is not self._hud_probe_command:
            movement=c.action_type in {'MOVE','DODGE'}
            fresh_bar=self.profile.name=='diablo4' and any(o.track_id==c.track_id and o.enemy_bar_confirmed for o in self._objects)
            if not movement and not fresh_bar and self.scene.scene!='play':return False
            if movement and self.scene.scene in {'menu','dialog','loading'} and time.monotonic()-self.scene.last_success<3:return False
            if c.action_type in {'TAKE','INTERACT'} and not self._movement_scene_current():
                self.scene.force=True
                return False
            if c.action_type=='MOVE' and c.target is not None and not self.navigator.click_is_clear(c.target,self._objects,self._latest_frame.shape,zones,self._world_player_origin(),min(.035,18/self._latest_frame.shape[1]) if nav['minimap']['enabled'] else .08):
                self.navigator.last_block_reason='클릭 위치가 객체와 겹치거나 캐릭터에 너무 가까움'
                self._navigation_click_failed(c)
                return reject(self.navigator.last_block_reason)
            if c.action_type=='ATTACK' or c.skill_id:
                target=next((o for o in self._objects if o.track_id==c.track_id),None)
                if self.profile.name=='diablo4' and (target is None or not target.enemy_bar_confirmed):return False
        if movement_test:return True
        allowed=super().can_execute(c)
        if not allowed:
            reason=('일시정지' if self._paused else '처리 중단' if self._processing_halted else 'HUD 재측정' if self._hud_rechecking else '이전 명령' if c.decision_epoch!=self._epoch else '화면/HP 측정 지연' if not self._fresh() else '게임 창 비활성' if not self._foreground() else 'HP/객체/대상 기본 검증 실패')
            return reject(reason)
        return True

    def _log_movement_status(self,reason,now=None):
        now=time.monotonic() if now is None else now
        registration=bool(reason and '지도 스크롤 위치 연결 재확인 중' in reason)
        if registration:
            since=getattr(self,'_registration_log_since',None)
            if since is None:self._registration_log_since=since=now
            if now-since<1 or now-getattr(self,'_registration_log_last',-float('inf'))<10:return
            if reason==self._last_move_block_reason:return
            self._registration_log_last=now
        else:self._registration_log_since=None
        if reason!=self._last_move_block_reason:
            print('[MOVE] '+(reason or '이동 실행 조건 충족 · 적이 있으면 전투 우선'))
            self._last_move_block_reason=reason

    def _movement_block_reason(self):
        if self._move_only and not self._paused:
            guide=getattr(self,'_screen_move_guide',None)
            return None if guide and guide.get('arrow_tip') else '화살표 감지 대기 · 단순 클릭 이동'
        if getattr(self,'_screen_guide_enabled',False) and not self._paused:
            if (time.monotonic()-getattr(self,'_screen_guide_seen_at',-1e9)>=.8
                    and (getattr(self,'_screen_guide_has_marker',False) or getattr(self,'_screen_guide_has_dots',False))):
                return '방향 화살표와 흰 점 안내선 인식 대기'
            if self._last_requested_command and self._last_requested_command.reason=='SCREEN_GUIDE_BLOCKED':
                return '안내선 방향 미니맵 통로 차단 · 경로 재확인 대기'
        if getattr(self,'_stationary_hunt_mode',False) and not self._paused:
            return '제자리 사냥 · 이동하지 않음'
        if getattr(self,'movement_test_mode',False):
            if self._paused:return '이동 테스트 일시정지'
            if self._processing_halted:return '이동 테스트 처리 중단 · 시작/재개 필요'
            if not self._foreground():return '게임 창 활성화 대기'
            if not self._fresh():return '최신 게임 캡처 대기'
            if not self._local_map_current(self._docs['navigation.json']):return '미니맵 통로 확인 대기 · '+self.minimap_memory.reason
            requested=self._last_requested_command
            if requested and requested.reason=='PATH_BLOCKED':return '이동 테스트 · '+(getattr(self.navigator,'last_block_reason',None) or '클릭 경로 재검사')
            return None
        if self._manual_control:return '수동 조작 · 자동 입력 해제 · 사냥 시작/재개로 자동사냥 복귀'
        if self._focus_paused:return '게임 창 비활성 · 즉시 중단 · 활성화 시 자동사냥 시작/재개'
        if self._processing_halted:return self._halt_reason or '처리 중단'
        if self._paused:return '일시정지 · 자동사냥 또는 이동 명령 필요'
        if not self._foreground():return '게임 창 비활성 · 게임 창을 클릭해 활성화하세요'
        if self._hud_rechecking:return 'HP 위치 재확인 중'
        if not self._hud_ready or not self._latest_hud_state.health_valid:return '플레이어 HP 미확인'
        if not self._fresh():return '최신 화면 또는 HP 측정 대기'
        if self.scene.scene in {'menu','dialog','loading'} and time.monotonic()-self.scene.last_success<3:return '최근 전투 분석에서 메뉴·대화 화면 확인 · 이동 보류'
        nav=self._docs['navigation.json']
        if not nav['minimap']['enabled']:return '미니맵 통로 미확인 · 미니맵이 꺼져 있습니다'
        if not self._local_map_current(nav):
            detail={'terrain_invalid':'지형 명암 분리 재확인 중',
                    'player_blocked':'플레이어 주변 통로 재확인 중',
                    'registration_wait':'지도 스크롤 위치 연결 재확인 중',
                    'uncalibrated':'미니맵 보정이 필요합니다',
                    'no_roi':'미니맵 영역 확인 필요'}.get(self.minimap_memory.reason,'최신 지도 재측정 중')
            return '미니맵 통로 미확인 · '+detail
        if time.monotonic()-self._yolo_at>max(.75,self.yolo.interval*2):return '최신 OpenCV 추적 대기'
        if not self._hunt_active and not (self._directive and self._directive.get('action')=='MOVE'):return '이동 명령 없음 · 자동사냥 또는 이동을 지시하세요'
        requested=self._last_requested_command
        if requested and requested.reason=='MINIMAP_REQUIRED':return '미니맵 통로 미확인 · 미니맵 영역·색상·플레이어 위치를 확인하세요'
        if requested and requested.reason=='RECOVERY_LIMIT':return '막힘 복구 4회 실패 · 공격은 계속 검증하며 이동 보류 · 중단 후 시작/재개로 복구 초기화'
        if requested and requested.reason=='PIN_TARGET_REACHED':return '최종 핀 목표 도착 · 적이 있으면 전투'
        if requested and requested.reason=='ORANGE_ROUTE_REQUIRES_MAPPING':return '주황색 선 이동 대기 · 미니맵 맵핑을 켜세요'
        if requested and requested.reason=='ORANGE_GUIDE_MISSING':return '새 목표 대기 · 안내선 재검출 중'
        if requested and requested.reason=='ORANGE_GUIDE_DISCONNECTED':return '새 목표 대기 · 플레이어 주변 안내선 연결 확인 중'
        if requested and requested.reason=='ORANGE_ROUTE_NOT_FOUND':return '현재 목표 통로 차단 · 미니맵 경로 재확인 중'
        if requested and requested.reason=='NO_CENTER_PATH':return '연결된 중앙 통로 없음 · 벽 방향 반복 클릭 보류'
        if requested and requested.reason=='PATH_BLOCKED':return '계획 경로 클릭 검증 실패 · '+(getattr(self.navigator,'last_block_reason',None) or '새 미니맵 측정 대기')
        return None

    def _apply_orange_route_display(self,state,memory):
        if self._move_only:
            state.update(route=[],planned_target=None,destination_target=None,waypoint_target=None,
                         planned_source='arrow_click_only',goal_locked=False,destination_locked=False,next_route_ready=False)
            return
        if not getattr(self,'_screen_guide_enabled',False):return
        orange=getattr(memory,'orange_mask',None)
        guide=getattr(self,'_screen_move_guide',None)
        if (guide and guide.get('arrow_direction') and memory.mask is not None and state['valid']
                and time.monotonic()-getattr(self,'_screen_guide_seen_at',-1e9)<.5):
            shape=np.array(memory.mask.shape[::-1]);player=np.array(memory.player)*shape
            heading=np.array(guide['arrow_direction'],float)
            angle=math.radians(memory.rotation);dx,dy=heading
            mapped_heading=np.array([dx*math.cos(angle)-dy*math.sin(angle),dx*math.sin(angle)+dy*math.cos(angle)])
            h,w=self._latest_frame.shape[:2];ox,oy=self._world_player_origin()
            _,top,_,bottom=game_viewport(self._latest_frame)
            scale=self._docs['navigation.json']['minimap'].get('mapping',{}).get('screen_pixels_per_map_pixel',12)*(bottom-top)/1080
            # Display only the arrow heading; map coordinates are a projection, not a route decision.
            target=player+mapped_heading*135/scale
            path=np.array([player,target])
            route=(path/shape).tolist()
            state.update(route=route,planned_target=route[-1],destination_target=route[-1],
                         waypoint_target=route[1],planned_source='black_arrow',goal_locked=False,
                         destination_locked=False,next_route_ready=False)
            return
        if orange is None or memory.mask is None or not state['valid']:return
        shape=np.array(memory.mask.shape[::-1])
        path=orange_route_target(orange,np.array(memory.player)*shape,
                                memory.origin,memory.visits,memory.CELL,return_path=True)
        if path is None:
            state.update(route=[],planned_target=None,destination_target=None,waypoint_target=None,
                         planned_source='orange_route_missing',goal_locked=False,destination_locked=False)
            return
        route=(path/shape).tolist()
        state.update(route=route,planned_target=route[-1],destination_target=route[-1],
                     waypoint_target=route[1] if len(route)>1 else route[0],
                     planned_source='orange_route',goal_locked=False,destination_locked=False,next_route_ready=False)

    def _current_function_status(self):
        if self._processing_halted or self._paused:
            return '사냥중단', '모든 작업 일시정지'
        if getattr(self,'_stationary_hunt_mode',False):
            name,detail='제자리사냥','오른쪽 버튼 계속 유지 · 몹 감지 시 타겟 위치 갱신'
            if getattr(self,'_manual_skill_mode',False):
                name='제자리사냥 + 반복스킬'
                detail+=' · 등록된 활성 스킬을 설정 간격으로 반복'
            record=getattr(self,'_stationary_attack_record',None)
            if record and record['until'] is not None and time.monotonic()<record['until']:
                detail=f"오른쪽 버튼 유지 · 사망 지점 추가 타겟 유지 {max(0,record['until']-time.monotonic()):.1f}초"
        elif getattr(self,'_stationary_skill_mode',False) or getattr(self,'_manual_skill_mode',False):
            name,detail='반복스킬','등록된 활성 스킬을 설정 간격으로 반복'
        elif self._move_only and self._hunt_active:
            name,detail='이동','이미지 매칭 화살표 → 흰 점 → 주황색 경로 순으로 조합' if getattr(self,'_screen_guide_enabled',False) else '공격 없이 미니맵 경로 이동'
            if (getattr(self,'_screen_guide_enabled',False)
                    and time.monotonic()-getattr(self,'_screen_guide_seen_at',-1e9)>=.8):
                detail=('주황색 이동 경로 따라 이동' if getattr(self,'_orange_guide_active',False)
                        else '이동 안내 없음 · 미니맵 통로 내 랜덤 이동')
            if time.monotonic()<getattr(self,'_space_prompt_wait_until',0):
                detail=f"Space 입력 후 대기 · 남은 {self._space_prompt_wait_until-time.monotonic():.1f}초"
            detail='예정 진행 방향 유지 · 아래쪽 5도 보정 · 화살표에서 80px 고정'
            if (getattr(self,'_screen_move_guide',None) or {}).get('planned_recovery'):
                detail='뒤쪽 화살표 무시 · 예정 진행 방향으로 짧게 보정'
        elif self._hunt_active:
            name,detail='사냥시작','이동하며 몹 감지·공격 및 스킬 실행'
        else:
            return '대기','실행 중인 기능 없음'
        if (getattr(self,'_manual_skill_mode',False) and not getattr(self,'_stationary_skill_mode',False)
                and not getattr(self,'_stationary_hunt_mode',False) and self._hunt_active):
            name=('이동' if self._move_only else '사냥시작')+' + 반복스킬'
            detail='이동 경로 유지 · 등록된 활성 스킬을 설정 간격으로 반복'
        if not self._foreground():detail='게임 창 활성화 대기'
        return name,detail

    def web_snapshot(self):
        result=super().web_snapshot()
        result['release_paused']=getattr(self,'_release_paused',False)
        result['hud_mode']=getattr(self,'hud_mode',False)
        if getattr(self,'movement_test_mode',False):
            result['movement_test_mode']=True
        function,detail=self._current_function_status()
        result['attack'].update(function=function,reason=function+' · '+detail)
        result['recognition_mode']='opencv_qwen'
        result['control_mode']='manual' if self._manual_control else 'move_only' if self._move_only and self._hunt_active else 'auto_hunt' if self._hunt_active else 'paused'
        if getattr(self,'_stationary_hunt_mode',False) and not self._paused:
            result['control_mode']='stationary_hunt'
        result['gameplay_spec']=dict(self._profile_gameplay_info)
        now=time.monotonic()
        valid=(self._hud_ready and self._latest_hud_state.health_valid and now-self._hud_at<.8 and not self._processing_halted and not self._hud_rechecking)
        with self._profile_lock:
            if not valid and self._hp_display_missing_at is None:self._hp_display_missing_at=now
            missing=0 if valid else now-self._hp_display_missing_at
            if valid:
                result['hud'].update(health=self._latest_hud_state.health,sp=self._latest_hud_state.sp if self._latest_hud_state.sp_valid else None,mp=self._latest_hud_state.mp if self._latest_hud_state.mp_valid else None)
            result['hud']['missing_visible']=missing>=2
            result['hud']['missing_seconds']=round(missing,1)
            if not valid:result['hud']['health']=self._hp_display_value if missing<2 else None
        result['hud']['calibration_mode']='startup_once'
        result['buffs']={'configured':bool(self.buff_monitor.references),'active':list(getattr(self,'_observed_buffs',[])),
                         'known_templates':list(self.buff_monitor.references),'fresh':now-self._buff_at<1,
                         'pending_recast':self.buff_monitor.pending_recast,'absence_seconds':4,'retry_seconds':4,'once_per_absence':False,'stop_on_any_registered_icon':True,
                         'bbox':self._docs['hud.json']['regions'].get('buffs',{}).get('bbox')}
        result['navigation']['excluded_click_regions']=self._world_click_exclusions()
        result['navigation']['screen_guide']=(getattr(self,'_screen_move_guide',None)
            if getattr(self,'_screen_guide_enabled',False) and time.monotonic()-getattr(self,'_screen_guide_seen_at',-1e9)<.8 else None)
        result['navigation']['movement_block_reason']=self._movement_block_reason()
        with self.minimap_memory.lock:
            memory=self.minimap_memory
            state=memory.snapshot()
            state['planned_target']=memory.planned_target()
            state['journey']=self.click_journey.snapshot(origin=memory.origin,shape=memory.mask.shape if memory.mask is not None else (144,192),segment=memory.segment,valid=state['valid'],now=time.monotonic())
            state['last_goal_release']=self.click_journey.last_release
            state['goal_release_history']=list(self.click_journey.history)
            state['last_goal_transition']=dict(self.click_journey.history[-1]) if self.click_journey.history else None
            cache=self._next_navigation_plan
            state['next_route_ready']=bool(cache and cache['epoch']==self._epoch and cache['segment']==memory.segment and time.monotonic()-cache['at']<.8)
            journey=state['journey']
            state['goal_locked']=bool(self.click_journey.current is not None
                                      and journey and journey['status'] in self.click_journey.MOVING_STATES and journey['map_target'] is not None)
            retained=bool(journey and self.click_journey.last_release in {'stalled','waypoint_arrived','step_progress','step_refresh'} and journey['destination_map_target'] is not None)
            if (state['goal_locked'] or retained) and not getattr(self,'movement_test_mode',False):
                state['planned_target']=journey['destination_map_target']
            state['destination_target']=state['planned_target']
            state['waypoint_target']=list(state['route'][1]) if state['valid'] and len(state['route'])>1 else None
            state['planned_target']=state['destination_target']
            state['destination_locked']=bool(memory.goal is not None or state['goal_locked'] or retained)
            state['goal_locked']=state['destination_locked']
            requested=self._last_requested_command
            guide=getattr(self,'_screen_move_guide',None)
            if (requested and requested.source=='SCREEN_ARROW_MOVE' and requested.decision_epoch==self._epoch
                    and not self._move_only and not self._paused and getattr(self,'_screen_guide_enabled',False) and guide
                    and time.monotonic()-getattr(self,'_screen_guide_seen_at',-1e9)<.8
                    and memory.position is not None and memory.mask is not None):
                h,w=self._latest_frame.shape[:2];ox,oy=self._world_player_origin()
                _,top,_,bottom=game_viewport(self._latest_frame)
                mapping=self._docs['navigation.json']['minimap'].get('mapping',{})
                scale=mapping.get('screen_pixels_per_map_pixel',12)*(bottom-top)/1080
                tx,ty=requested.target;dx,dy=(tx-ox)*w/scale,(ty-oy)*h/scale
                angle=math.radians(memory.rotation)
                world=memory.position+np.array([dx*math.cos(angle)-dy*math.sin(angle),dx*math.sin(angle)+dy*math.cos(angle)])
                target=((world-memory.origin)/np.array(memory.mask.shape[::-1])).tolist()
                state.update(actual_click_target=target,actual_click_start=list(memory.player))
            self._apply_orange_route_display(state,memory)
            planned=getattr(self,'_planned_move_heading',None)
            state['planned_direction']=None
            state['planned_correction']=bool(guide and guide.get('planned_recovery'))
            if planned is not None and self._move_only and not self._paused:
                dx,dy=planned;angle=math.radians(memory.rotation)
                direction=np.array([dx*math.cos(angle)-dy*math.sin(angle),
                                    dx*math.sin(angle)+dy*math.cos(angle)])
                if memory.mask is not None:direction/=np.array(memory.mask.shape[::-1])
                length=float(np.linalg.norm(direction))
                if length>1e-6:state['planned_direction']=(direction/length).tolist()
            result['navigation']['mapping']=state
        result['navigation']['perception_mode']='minimap_travel_world_combat'
        result['settings']['vlInterval']=self.scene.min_interval
        with self.scene.lock:
            result['learning']={'pending':copy.deepcopy(list(self.scene.pending.values())),'confirmed':self.scene.memory.compact()}
        result['restart_required']=False
        result['scene']={'type':self.scene.scene,'age_seconds':round(time.monotonic()-self.scene.last_success,1) if self.scene.last_success else None,
                         'min_interval':self.scene.min_interval,'stable_interval':self.scene.stable_interval,
                         'lease_seconds':round(self._scene_lease_seconds(),1),'analysis_status':self._scene_scan_status if self._foreground() else 'foreground_wait'}
        if result['attack']['reason'].startswith('YOLO'):
            result['attack']['reason']='확인된 적 없음 · 미니맵 이동은 계속 가능'
        return result

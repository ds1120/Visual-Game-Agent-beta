from __future__ import annotations

from app.core.action_command import ActionCommand
from app.controller.input_controller import InputController
from collections import deque
import asyncio
import time
from dataclasses import replace


class ActionExecutor:
    """Reuses the AiBotForDia execution boundary; AI never presses keys directly."""

    def __init__(self, input_controller: InputController) -> None:
        self._input = input_controller
        self._running = True
        self.validator = None
        self.last_command = None
        self.active_command = None
        self.requested_command = None
        self.last_error = None
        self.last_completed_at = 0
        self.input_events = deque(maxlen=40)
        self._event_seq = 0
        self.result_observer = None
        self._held_attack_command = None
        self._held_move_command = None
        self._input.hold_validator = lambda c: self._running and not c.is_expired() and (self.validator is None or self.validator(c))

    async def release_held_attack(self):
        self._held_attack_command = None
        release = getattr(self._input, 'release_attack', None)
        if release is not None:
            await release()

    async def check_held_attack(self):
        move=self._held_move_command
        if move is not None and (not self._running or move.is_expired() or (self.validator and not self.validator(move))):
            await self.release_held_move()
        c = self._held_attack_command
        if c is not None and (not self._running or c.is_expired() or (self.validator and not self.validator(c))):
            await self.release_held_attack()

    async def release_held_move(self):
        self._held_move_command=None
        release=getattr(self._input,'release_move',None)
        if release is not None:await release()

    def renew_held_attack(self,c):
        move=self._held_move_command
        if (move is not None and c.maintain_move and c.action_type=='MOVE'
                and move.decision_epoch==c.decision_epoch and not c.is_expired()
                and (self.validator is None or self.validator(c))):self._held_move_command=c
        old=self._held_attack_command
        if (old is not None and c.maintain_attack and c.action_type in {'ATTACK','USE_SKILL'}
                and old.track_id==c.track_id and old.decision_epoch==c.decision_epoch
                and not c.is_expired() and (self.validator is None or self.validator(c))):
            self._held_attack_command=c

    async def execute(self, command: ActionCommand) -> bool:
        self.requested_command = command
        if not self._running or command.is_expired() or (self.validator and not self.validator(command)):
            await self.release_held_move()
            await self.release_held_attack()
            owner=getattr(self.validator,'__self__',None)
            self.last_error = getattr(owner,'_input_block_reason',None) or "HP·전경·센서·대상 검증으로 입력 차단"
            self._observe(command, "blocked")
            return False
        action = command.action_type
        move_skill=(action=='USE_SKILL' and command.source=='MANUAL_SKILL' and self._held_move_command is not None)
        resume_move=self._held_move_command if move_skill else None
        if not move_skill and (action!='MOVE' or not command.maintain_move):await self.release_held_move()
        holding = command.maintain_attack and (action == 'ATTACK' or action == 'USE_SKILL' and command.skill_id)
        if not holding and action not in {'USE_POTION', 'CAST_BUFF'}:
            await self.release_held_attack()
        handlers = {
            "MOVE": "move", "ATTACK": "attack", "USE_SKILL": "use_skill",
            "USE_POTION": "use_potion", "CAST_BUFF": "cast_buff", "DODGE": "dodge",
            "INTERACT": "interact", "PICKUP": "pickup", "TAKE": "pickup", "STOP": "stop",
        }
        handler = getattr(self._input, handlers.get(action, ""), None)
        if handler is None:
            print(f"[Executor] Unknown action: {action}")
            return False
        self.active_command = command
        self.last_error = None
        status = "sent"
        reported = False
        try:
            if holding:
                self._held_attack_command = command
                if action != 'ATTACK':
                    basic = replace(command, action_type='ATTACK', skill_id=None)
                    if await self._input.attack(basic) is False:
                        await self.release_held_attack()
                        status = 'blocked'
                        return False
                    self._observe(basic, 'sent')
            count=max(1,min(3,command.move_clicks)) if action=='MOVE' and command.target is not None else 1
            current=command
            for index in range(count):
                if index:
                    await asyncio.sleep(.05)
                    # Request TTL covers queueing/planning, not a fresh guarded
                    # continuation after a successful (possibly slow HID) click.
                    current=replace(command,expires_at=time.monotonic()+.15)
                    if (not self._running
                            or self.validator and not self.validator(current)):
                        return True  # Earlier clicks were already sent.
                result = await handler(current)
                reported = False
                if result is False:break
                if index<count-1:
                    self._observe(command,'sent')
                    reported = True
            if result is False:
                await self.release_held_attack()
                status = "blocked"
                controller_error = getattr(self._input, 'last_error', None)
                self.last_error = controller_error if isinstance(controller_error, str) and controller_error else "입력 컨트롤러가 전송을 거부했습니다"
                return False
            self.last_command = command
            if resume_move is not None and not getattr(self._input,'move_held',False):
                renewed=replace(resume_move,expires_at=time.monotonic()+.35)
                if self._running and (self.validator is None or self.validator(renewed)):
                    if await self._input.move(renewed) is not False:
                        self._held_move_command=renewed
            if command.maintain_move and action=='MOVE':
                self._held_move_command=replace(command,expires_at=time.monotonic()+.35)
            self.last_completed_at = time.monotonic()
            return True
        except BaseException as exc:
            await self.release_held_attack()
            status = "cancelled" if type(exc).__name__ == "CancelledError" else "failed"
            self.last_error = "입력 취소" if status == "cancelled" else str(exc)
            raise
        finally:
            if status!='sent':await self.release_held_move()
            self.active_command = None
            if status!='sent' or not reported:self._observe(command, status)

    def _observe(self, command, status):
        if command.source=='SCREEN_SPACE_PROMPT':
            print(f'[SPACE INPUT] {status}: {self.last_error or "Space 전송 완료"}')
        if command.action_type=='MOVE' and status in {'blocked','failed'}:
            message=f'[MOVE INPUT] {status}: {self.last_error}'
            now=time.monotonic()
            if message!=getattr(self,'_move_error_message',None) or now-getattr(self,'_move_error_at',0)>=2:
                print(message)
                self._move_error_message=message;self._move_error_at=now
        # Record blocked inputs and the basic hold sent before a rotation skill too.
        if command.action_type != "STOP" or not self.input_events or self.input_events[-1]["command"].action_type != "STOP" or self.input_events[-1]["status"] != status:
            self._event_seq += 1
            self.input_events.append({"seq": self._event_seq, "at": time.monotonic(), "command": command, "status": status, "error": self.last_error})
        if self.result_observer:
            try:
                self.result_observer(command, status, self.last_error)
            except Exception:
                # Recording failures must not turn a successful HID input into a replay.
                pass

    async def close(self) -> None:
        self._running = False
        await self.release_held_move()
        await self.release_held_attack()
        await self._input.close()

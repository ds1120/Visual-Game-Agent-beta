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
        self._input.hold_validator = lambda c: self._running and not c.is_expired() and (self.validator is None or self.validator(c))

    async def release_held_attack(self):
        self._held_attack_command = None
        release = getattr(self._input, 'release_attack', None)
        if release is not None:
            await release()

    async def check_held_attack(self):
        c = self._held_attack_command
        if c is not None and (not self._running or c.is_expired() or (self.validator and not self.validator(c))):
            await self.release_held_attack()

    def renew_held_attack(self,c):
        old=self._held_attack_command
        if (old is not None and c.maintain_attack and c.action_type in {'ATTACK','USE_SKILL'}
                and old.track_id==c.track_id and old.decision_epoch==c.decision_epoch
                and not c.is_expired() and (self.validator is None or self.validator(c))):
            self._held_attack_command=c

    async def execute(self, command: ActionCommand) -> bool:
        self.requested_command = command
        if not self._running or (self.validator and not self.validator(command)):
            await self.release_held_attack()
            self.last_error = "HP·전경·센서·대상 검증으로 입력 차단"
            self._observe(command, "blocked")
            return False
        action = command.action_type
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
            for index in range(count):
                if index:
                    await asyncio.sleep(.05)
                    if (not self._running or command.is_expired()
                            or self.validator and not self.validator(command)):
                        return True  # Earlier clicks were already sent.
                result = await handler(command)
                reported = False
                if result is False:break
                if index<count-1:
                    self._observe(command,'sent')
                    reported = True
            if result is False:
                await self.release_held_attack()
                status = "blocked"
                self.last_error = "입력 컨트롤러가 전송을 거부했습니다"
                return False
            self.last_command = command
            self.last_completed_at = time.monotonic()
            return True
        except BaseException as exc:
            await self.release_held_attack()
            status = "cancelled" if type(exc).__name__ == "CancelledError" else "failed"
            self.last_error = "입력 취소" if status == "cancelled" else str(exc)
            raise
        finally:
            self.active_command = None
            if status!='sent' or not reported:self._observe(command, status)

    def _observe(self, command, status):
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
        await self.release_held_attack()
        await self._input.close()

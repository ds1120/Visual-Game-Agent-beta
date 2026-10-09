from __future__ import annotations

import time

from app.core.action_command import ActionCommand


class MockInputController:
    attack_held = False

    async def release_attack(self):
        self.attack_held = False
    _last_log_action: str | None = None
    _last_log_time: float = 0.0
    _log_interval: float = 2.0

    @classmethod
    def _log_action(cls, command: ActionCommand) -> None:
        now = time.monotonic()
        action = str(command.action_type)
        if action == cls._last_log_action and now - cls._last_log_time < cls._log_interval:
            return
        source = getattr(command, "source", "UNKNOWN")
        reason = getattr(command, "reason", "")
        reason_text = f" | Reason={reason}" if reason else ""
        reason_text += f" | target={command.target} direction={command.direction} duration={command.duration_ms}ms"
        print(
            f"[ACTION] {command.action_type} | "
            f"Priority {command.priority} | "
            f"Source={source}{reason_text}"
        )
        cls._last_log_action = action
        cls._last_log_time = now

    async def move(
        self,
        command: ActionCommand,
    ) -> None:
        self._log_action(command)

    async def attack(
        self,
        command: ActionCommand,
    ) -> None:
        self.attack_held = command.maintain_attack
        self._log_action(command)

    async def use_skill(
        self,
        command: ActionCommand,
    ) -> None:
        self._log_action(command)

    async def use_potion(
        self,
        command: ActionCommand,
    ) -> None:
        self._log_action(command)

    async def dodge(
        self,
        command: ActionCommand,
    ) -> None:
        self._log_action(command)

    async def cast_buff(
        self,
        command: ActionCommand,
    ) -> None:
        self._log_action(command)

    async def interact(
        self,
        command: ActionCommand,
    ) -> None:
        self._log_action(command)

    async def pickup(
        self,
        command: ActionCommand,
    ) -> None:
        self._log_action(command)

    async def stop(
        self,
        command: ActionCommand,
    ) -> None:
        self._log_action(command)

    async def close(self) -> None:
        return None

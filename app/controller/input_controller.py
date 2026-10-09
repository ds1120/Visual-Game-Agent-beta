from __future__ import annotations

from typing import Protocol

from app.core.action_command import ActionCommand


class InputController(Protocol):
    async def release_attack(self) -> None:
        ...

    async def move(self, command: ActionCommand) -> None:
        ...

    async def attack(self, command: ActionCommand) -> None:
        ...

    async def use_skill(self, command: ActionCommand) -> None:
        ...

    async def use_potion(self, command: ActionCommand) -> None:
        ...

    async def dodge(self, command: ActionCommand) -> None:
        ...

    async def cast_buff(self, command: ActionCommand) -> None:
        ...

    async def interact(self, command: ActionCommand) -> None:
        ...

    async def pickup(self, command: ActionCommand) -> None:
        ...

    async def stop(self, command: ActionCommand) -> None:
        ...

    async def close(self) -> None:
        ...

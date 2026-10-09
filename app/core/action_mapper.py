from __future__ import annotations

import time

from app.core.action_command import ActionCommand
from app.core.intent import Intent


class ActionMapper:
    """Generic Qwen-VL intent -> stable executor command.

    Game-specific key mapping remains below InputController/GameProfile.
    """

    MAP = {
        "ENGAGE": "ATTACK",
        "ATTACK": "ATTACK",
        "TAKE": "TAKE",
        "MOVE": "MOVE",
        "INTERACT": "INTERACT",
        "RETREAT": "DODGE",
        "USE_RESOURCE": "USE_POTION",
        "USE_POTION": "USE_POTION",
        "EXPLORE": "MOVE",
        "WAIT": "STOP",
    }

    def __init__(self, min_confidence: float = 0.60) -> None:
        self.min_confidence = max(0.0, min(1.0, min_confidence))

    def map(
        self,
        intent: Intent,
        source: str = "PYTHON_DECISION",
        reason: str = "",
    ) -> ActionCommand:
        action = "STOP"
        if intent.confidence >= self.min_confidence:
            action = self.MAP.get(intent.action, "STOP")
        now = time.monotonic()
        return ActionCommand(
            action_type=action,
            priority=100 if action == "USE_POTION" else (50 if action != "STOP" else 10),
            execute_at=now,
            expires_at=now + 1.0,
            cooldown=1.0 if action == "USE_POTION" else (0.15 if action == "ATTACK" else 0.0),
            concurrent=False,
            source=source,
            reason=reason,
        )

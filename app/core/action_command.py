from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Any, Optional


@dataclass(frozen=True)
class ActionCommand:
    """
    실제 실행 계층으로 전달되는 행동 명령.

    Qwen 또는 규칙 기반 DecisionEngine이 생성한다.
    """

    action_type: str

    priority: int

    execute_at: float

    expires_at: Optional[float] = None

    cooldown: float = 0.0

    concurrent: bool = False

    # Diagnostic provenance. These fields travel with the command all the way
    # to InputController so the final [ACTION] log shows who requested it.
    source: str = "UNKNOWN"
    reason: str = ""
    target: tuple[float, float] | None = None  # normalized capture coordinates
    direction: tuple[float, float] | None = None
    track_id: int | None = None
    duration_ms: int = 180
    decision_epoch: int = 0
    skill_id: str | None = None
    maintain_attack: bool = False
    move_clicks: int = 1
    maintain_move: bool = False

    def is_expired(self) -> bool:
        """
        명령이 만료되었는지 확인한다.
        """

        if self.expires_at is None:
            return False

        return time.monotonic() > self.expires_at

    def with_execute_at(
        self,
        execute_at: float,
    ) -> ActionCommand:
        """
        실행 시간을 변경한 새로운 ActionCommand를 반환한다.
        """

        return replace(self, execute_at=execute_at)

    def with_priority(self, priority: int) -> ActionCommand:
        return replace(self, priority=priority)

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
    ) -> ActionCommand:
        """
        Qwen JSON 결과를 ActionCommand로 변환한다.

        Args:
            data:
                Qwen이 반환한 JSON dictionary

        Returns:
            ActionCommand
        """

        action_type = str(
            data.get(
                "action_type",
                "STOP",
            )
        ).upper()

        priority = int(
            data.get(
                "priority",
                10,
            )
        )

        cooldown = float(
            data.get(
                "cooldown",
                0.0,
            )
        )

        concurrent = bool(
            data.get(
                "concurrent",
                False,
            )
        )

        # --------------------------------------------------
        # Priority 안전 범위
        # --------------------------------------------------

        priority = max(
            1,
            min(
                100,
                priority,
            ),
        )

        # --------------------------------------------------
        # cooldown 안전 범위
        # --------------------------------------------------

        cooldown = max(
            0.0,
            cooldown,
        )

        # --------------------------------------------------
        # 실행 시간
        # --------------------------------------------------

        execute_at = time.monotonic()

        # --------------------------------------------------
        # 기본 만료 시간
        #
        # Qwen이 별도의 expires_at을 반환하지 않으므로
        # 생성 후 1초 동안만 유효하도록 한다.
        # --------------------------------------------------

        expires_at = execute_at + 1.0

        return cls(
            action_type=action_type,
            priority=priority,
            execute_at=execute_at,
            expires_at=expires_at,
            cooldown=cooldown,
            concurrent=concurrent,
            source=str(data.get("source", "QWEN_JSON")),
            reason=str(data.get("reason", "")),
        )

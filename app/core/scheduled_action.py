from __future__ import annotations

from dataclasses import dataclass, field

from app.core.action_command import ActionCommand


@dataclass(order=True)
class ScheduledAction:
    """
    ActionScheduler 내부에서 사용하는 예약 액션.

    PriorityQueue 정렬 기준:
    1. 높은 priority가 먼저 실행
    2. execute_at이 빠른 액션이 먼저 실행
    3. sequence가 작은 액션이 먼저 실행
    """

    sort_priority: int = field(init=False)
    execute_at: float = field(init=False)
    sequence: int
    command: ActionCommand = field(compare=False)

    def __post_init__(self) -> None:
        # PriorityQueue는 숫자가 작을수록 먼저 처리하므로
        # 높은 priority가 먼저 오도록 음수로 변환한다.
        self.sort_priority = -self.command.priority

        # 실행 예정 시간
        self.execute_at = self.command.execute_at
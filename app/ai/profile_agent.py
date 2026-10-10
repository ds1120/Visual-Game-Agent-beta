"""Shared application shell for new, independently implemented game profiles."""
import asyncio
import logging

from app.ai.visual_agent import VisualAgent
from app.core.fast_policy import command
from app.profiles.profile_store import atomic_json, resolve_profile_dir

log = logging.getLogger(__name__)


class ProfileAgent(VisualAgent):
    default_auto_hunt = False
    focus_activation_starts_hunt = False
    automation_ready = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.shared_movement_mode = True
        self._paused = True
        self._move_only = True
        self._release_paused = False
        self._runtime_restart_required = False

    async def _request_runtime_switch(self, name):
        directory = resolve_profile_dir(name, self.profile_root)
        if directory.name == self.profile.name:
            return False
        await self.handle_control('/stop')
        await asyncio.to_thread(self.statistics.flush)
        await asyncio.to_thread(atomic_json, self.profile_root / 'active_game.json', {'id': directory.name})
        self._runtime_restart_required = True
        self._pending_profile = directory.name
        self._processing_halted = True
        self._halt_reason = '게임별 Agent 변경 · 프로그램 재시작 필요'
        self.emit_web_event('game_changed', profile=directory.name, restart_required=True)
        return True

    async def switch_game(self, name):
        if await self._request_runtime_switch(name):
            return self._pending_profile
        return await super().switch_game(name)

    async def _idle_sensor_loop(self):
        while self._running:
            await asyncio.sleep(.1)

    async def _movement_test_loop(self):
        # Game-specific navigation and combat are implemented in the profile.
        await self._idle_sensor_loop()

    async def _navigation_loop(self):
        await self._idle_sensor_loop()

    async def handle_control(self, message):
        requested = ''.join(message.lower().split()).rstrip('.!?')
        if requested in {'/release-pause', '일시중지'}:
            self._release_paused = not self._release_paused
            if self._release_paused:
                await self.scheduler.submit_emergency(command('STOP', epoch=self._epoch))
                controller = self.scheduler.executor._input
                release = getattr(controller, 'release_inputs', None)
                if release is not None:
                    await release()
            log.debug('[CONTROL] RELEASE pause %s', self._release_paused)
            self.emit_web_event('control', action='release_pause', release_paused=self._release_paused)
            return True
        if requested in {'/stop', '/pause', '/quit', '/status', 'stop', '정지', '중지', '멈춰', '사냥중단', '사냥중단해'}:
            return await super().handle_control(message)
        if requested in {'/hunt', 'hunt', '사냥시작', '사냥시작해', '/resume', 'resume', '계속', '다시시작',
                         '/move', '이동', '이동해', '이동해줘', '반복스킬', '/repeat-skills', '/skills',
                         '제자리사냥', '/stationary-hunt', '/replan', '예정진행방향변경'}:
            self.emit_web_event('control_unavailable', message=f'{self.profile.name} 전용 동작 구현 대기')
            return True
        return False

    def can_execute(self, command):
        return command.action_type == 'STOP'

    def web_snapshot(self):
        result = super().web_snapshot()
        result.update(release_paused=self._release_paused,
                      restart_required=self._runtime_restart_required,
                      pending_profile=getattr(self, '_pending_profile', None),
                      automation_ready=self.automation_ready)
        result['attack'].update(function='대기', reason=f'{self.profile.name} 전용 동작 구현 대기')
        return result

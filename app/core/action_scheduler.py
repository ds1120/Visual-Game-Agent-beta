"""Latest decision wins; emergency actions preempt pending and active input."""

from __future__ import annotations
import asyncio
import time
from dataclasses import replace
from app.core.action_command import ActionCommand


class ActionScheduler:
    def __init__(self, executor):
        self.executor = executor
        self._queue = asyncio.Queue()
        self._running = False
        self._task = None
        self._execution_task = None
        self._current_action = None
        self._last_execution = {}
        self._decision_generation = 0
        self.emergency_until = 0.0
        self._hold_watch_task = None
        self._pending_manual_skill = False

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name="action-scheduler")
        self._hold_watch_task = asyncio.create_task(self._watch_held_attack(), name='attack-hold-watch')

    async def _watch_held_attack(self):
        while self._running:
            try:
                await self.executor.check_held_attack()
            except Exception as exc:
                print(f'[Attack hold] release failed: {exc}')
            await asyncio.sleep(.05)

    async def stop(self):
        self._running = False
        await self.executor.release_held_move()
        if self._hold_watch_task:
            self._hold_watch_task.cancel()
            await asyncio.gather(self._hold_watch_task, return_exceptions=True)
            self._hold_watch_task = None
        await self._cancel_execution()
        await self.executor.release_held_attack()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self.clear()

    async def _cancel_execution(self):
        task = self._execution_task
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def submit(self, command):
        if not self._running or command.is_expired() or time.monotonic() < self.emergency_until:
            return
        renew=getattr(self.executor,'renew_held_attack',None)
        if callable(renew):renew(command)
        if command.action_type=='MOVE' and self._pending_manual_skill:
            return  # Renew the hold, preserving the queued skill until execution.
        active=self.executor.active_command
        if (active is not None and active.action_type=='MOVE' and active.move_clicks>1
                and command.action_type not in {'MOVE','CAST_BUFF','USE_POTION'}):
            await self._cancel_execution()
        self._decision_generation += 1
        await self.clear()
        await self._queue.put((self._decision_generation, command))
        self._pending_manual_skill = command.source=='MANUAL_SKILL'

    async def submit_emergency(self, command):
        stopping = command.action_type == 'STOP'
        if stopping:
            command=replace(command,execute_at=time.monotonic(),expires_at=None)
        if not stopping and (not self._running or command.is_expired() or not self._is_cooldown_ready(command)):
            return
        self._decision_generation += 1
        # A normal sensor decision must not replace an unconsumed emergency.
        self.emergency_until = time.monotonic() + max(0.15, command.duration_ms / 1000)
        await self.clear()
        if stopping:
            # Cancel the old task without awaiting its serial-release cleanup.
            # The controller's STOP invalidates in-flight epochs and releases
            # all inputs independently of a stuck old action's finally block.
            task=self._execution_task
            if task and not task.done():task.cancel()
            await self.executor.execute(command)
            return
        await self._cancel_execution()
        if command.source=='SCREEN_SPACE_PROMPT':
            # Cancelling an in-flight serial transaction can consume its TTL.
            # The executor still verifies current focus and visible prompt.
            now=time.monotonic()
            command=replace(command,execute_at=now,expires_at=now+.5)
            self.emergency_until=now+.5
        await self._queue.put((self._decision_generation, command))

    async def _run_loop(self):
        while self._running:
            generation, command = await self._queue.get()
            self._pending_manual_skill = False
            try:
                while self._running and command.execute_at > time.monotonic():
                    if generation != self._decision_generation:
                        break
                    await asyncio.sleep(min(0.02, command.execute_at - time.monotonic()))
                if (
                    generation != self._decision_generation
                    or command.is_expired()
                    or command.action_type != "STOP" and not self._is_cooldown_ready(command)
                ):
                    continue
                self._current_action = command
                self._execution_task = asyncio.create_task(self.executor.execute(command))
                try:
                    executed = await self._execution_task
                except asyncio.CancelledError:
                    if not self._running:
                        raise
                    continue  # emergency cancelled the active short movement
                if executed is not False:
                    self._last_execution[self.cooldown_key(command)] = time.monotonic()
                    if command.action_type == "ATTACK" or command.skill_id:
                        self._last_execution["combat_input"] = time.monotonic()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[Scheduler] ERROR: {exc}")
            finally:
                self._current_action = None
                self._execution_task = None
                self._queue.task_done()

    @staticmethod
    def cooldown_key(command):
        return (command.action_type, command.skill_id) if command.skill_id else command.action_type

    def _is_cooldown_ready(self, command):
        now = time.monotonic()
        timed_skill=bool(command.skill_id and command.source in {'MANUAL_SKILL','MOVEMENT_HUNT','ATTACK_ROTATION'})
        stationary_hold=command.maintain_attack and command.reason in {'STATIONARY_HOLD','STATIONARY_POST_DEATH','STATIONARY_TARGET_GRACE','STATIONARY_MODE_HOLD'}
        if not timed_skill and not stationary_hold and (command.action_type == "ATTACK" or command.skill_id) and now - self._last_execution.get("combat_input", -1e9) < 0.15:
            return False
        return now - self._last_execution.get(self.cooldown_key(command), -1e9) >= command.cooldown

    async def clear(self):
        self._pending_manual_skill = False
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break

    @property
    def current_action(self):
        return self._current_action

    @property
    def queue_size(self):
        return self._queue.qsize()

    @property
    def running(self):
        return self._running

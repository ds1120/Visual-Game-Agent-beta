"""Latest decision wins; emergency actions preempt pending and active input."""

from __future__ import annotations
import asyncio
import time
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
        active=self.executor.active_command
        if (active is not None and active.action_type=='MOVE' and active.move_clicks>1
                and command.action_type not in {'MOVE','CAST_BUFF','USE_POTION'}):
            await self._cancel_execution()
        self._decision_generation += 1
        await self.clear()
        await self._queue.put((self._decision_generation, command))

    async def submit_emergency(self, command):
        if not self._running or command.is_expired() or command.action_type != "STOP" and not self._is_cooldown_ready(command):
            return
        self._decision_generation += 1
        # A normal sensor decision must not replace an unconsumed emergency.
        self.emergency_until = time.monotonic() + max(0.15, command.duration_ms / 1000)
        await self.clear()
        await self._cancel_execution()
        await self._queue.put((self._decision_generation, command))

    async def _run_loop(self):
        while self._running:
            generation, command = await self._queue.get()
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
        if (command.action_type == "ATTACK" or command.skill_id) and now - self._last_execution.get("combat_input", -1e9) < 0.15:
            return False
        return now - self._last_execution.get(self.cooldown_key(command), -1e9) >= command.cooldown

    async def clear(self):
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

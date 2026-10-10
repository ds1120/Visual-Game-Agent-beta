"""USB serial commands to ESP32 BLE HID; no software input injection."""

from __future__ import annotations
import asyncio, ctypes, json, math, os, secrets, threading, time
from ctypes import wintypes
from dataclasses import replace

from app.controller.input_bindings import binding_for_command
from app.controller.input_rejected import InputRejected
from app.controller.pointer_motion import smooth_point
from app.core.control_hotkeys import begin_bot_keys, end_bot_keys

PROTOCOL = "VGA_BLE_1"
SPECIAL = {
    "SPACE": 44,
    "RIGHT": 79,
    "LEFT": 80,
    "DOWN": 81,
    "UP": 82,
    "CTRL": 224,
    "SHIFT": 225,
    "ALT": 226,
}


def hid_key(value):
    value = value.upper()
    if value in SPECIAL:
        return SPECIAL[value]
    if len(value) == 1 and "A" <= value <= "Z":
        return ord(value) - ord("A") + 4
    if len(value) == 1 and value in "1234567890":
        return 30 + "1234567890".index(value)
    raise ValueError(f"지원하지 않는 HID 키: {value}")


class BLESerialTransport:
    def __init__(self, port, baudrate=115200, timeout=0.7, connection=None):
        if not port:
            raise ValueError(
                "COM 포트 필요: py -m tools.esp32_ble_setup --port COM5 --configure"
            )
        self.timeout = max(0.1, min(2, float(timeout)))
        self.lock = threading.Lock()
        self.sequence = secrets.randbelow(1000000) + 1
        self.closed = False
        if connection is None:
            import serial

            connection = serial.Serial(
                port=None, baudrate=baudrate, timeout=0.05, write_timeout=self.timeout
            )
            connection.dtr = False
            connection.rts = False
            connection.port = port
            connection.open()
        self.connection = connection

    def request(self, op, *args, guard=None):
        with self.lock:
            if self.closed:
                raise ConnectionError("ESP32 연결 종료")
            if guard is not None and not guard():
                raise InputRejected("중단되거나 비활성 게임의 입력을 폐기했습니다.")
            self.sequence = self.sequence % 2000000000 + 1
            seq = self.sequence
            raw = (
                f"{PROTOCOL} {seq} {op}" + "".join(f" {int(v)}" for v in args) + "\n"
            ).encode("ascii")
            if self.connection.write(raw) != len(raw):
                raise ConnectionError("ESP32 전송 실패")
            self.connection.flush()
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                try:
                    reply = json.loads(
                        self.connection.readline(512).decode("utf-8", errors="replace")
                    )
                except (ValueError, TypeError):
                    continue
                if (
                    not isinstance(reply, dict)
                    or reply.get("protocol") != PROTOCOL
                    or reply.get("id") != seq
                ):
                    continue
                if reply.get("ok") is not True:
                    raise ConnectionError(
                        "ESP32: " + str(reply.get("error", "명령 거절"))
                    )
                return reply
            raise TimeoutError(
                "ESP32 응답 없음: COM·BLE 펌웨어·시리얼 모니터 종료를 확인하세요."
            )

    def probe(self, timeout=6, require_ready=False):
        deadline = time.monotonic() + timeout
        while True:
            try:
                result = self.request("STATUS")
                if (
                    not require_ready
                    or result.get("ready") is True
                    or time.monotonic() >= deadline
                ):
                    return result
                time.sleep(0.15)
            except TimeoutError:
                if time.monotonic() >= deadline:
                    raise

    def close(self):
        if self.closed:
            return
        try:
            self.request("STOP")
        finally:
            with self.lock:
                self.closed = True
                self.connection.close()


class WindowsCursor:
    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("ESP32 게임 커서 확인은 Windows에서 지원합니다.")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        self.user32.GetCursorPos.restype = wintypes.BOOL

    def position(self):
        p = wintypes.POINT()
        if not self.user32.GetCursorPos(ctypes.byref(p)):
            raise OSError(ctypes.get_last_error(), "GetCursorPos 실패")
        return p.x, p.y


class ESP32InputController:
    def __init__(
        self,
        capture,
        settings_provider,
        *,
        port,
        baudrate=115200,
        timeout=0.7,
        transport=None,
        cursor=None,
    ):
        self.capture = capture
        self.settings_provider = settings_provider
        self.cursor = cursor or WindowsCursor()
        self.transport = transport or BLESerialTransport(port, baudrate, timeout)
        self._epoch = 0
        self._move_clicked = False
        self.attack_held = False
        self._hold_task = None
        self._hold_capable = False
        self._move_hold_capable = False
        self.last_error = None
        try:
            capabilities = self.transport.probe(timeout=10, require_ready=True)
            self._hold_capable = capabilities.get('hold_attack') is True
            self._move_hold_capable = capabilities.get('hold_move') is True
            if capabilities.get('ready') is not True:
                raise ConnectionError(
                    "Windows에서 VisualAgent-ESP32를 BLE 페어링하세요."
                )
            self.transport.request("STOP")
        except Exception:
            self.transport.close()
            raise

    async def _send(self, op, *args):
        epoch = self._epoch
        guard = (
            None
            if op in {"STOP", "STATUS", "RELEASE", "END_TAP"}
            else lambda: epoch == self._epoch and self.capture.can_input()
                and not getattr(self,'manual_pause_check',lambda:False)()
        )
        def request():
            bot_keys=begin_bot_keys(args[1:]) if op=='KEY' else []
            try:return self.transport.request(op,*args,guard=guard)
            finally:
                if bot_keys:end_bot_keys(bot_keys,args[0])
        try:
            reply = await asyncio.to_thread(request)
        except asyncio.CancelledError:
            self._epoch += 1
            raise
        if op not in {"STOP", "STATUS", "RELEASE", "END_TAP"} and reply.get("ready") is not True:
            raise ConnectionError("ESP32 BLE HID 연결이 끊겼습니다.")
        return reply

    def start_manual_mouse_watch(self,check,on_pause):
        self.manual_pause_check=check
        self._manual_watch_stop=threading.Event()
        def watch():
            paused=False
            while not self._manual_watch_stop.wait(.01):
                down=check()
                if down and not paused:
                    # Invalidate queued serial writes before waiting for the
                    # transport lock. RELEASE must not depend on the AI loop.
                    self._epoch+=1
                    self.attack_held=False
                    self.move_held=False
                    on_pause()
                    try:
                        self.transport.request('RELEASE')
                        print('[MOUSE ESP32] 사용자 버튼 유지 · RELEASE 전송 완료')
                    except Exception as exc:
                        print(f'[MOUSE ESP32] RELEASE 전송 실패: {exc}')
                        continue  # Retry while held.
                paused=down
        self._manual_watch_thread=threading.Thread(target=watch,name='esp32-manual-mouse',daemon=True)
        self._manual_watch_thread.start()

    async def release_inputs(self):
        self._epoch+=1
        self.attack_held=False
        self.move_held=False
        await self._send('RELEASE')

    async def release_attack(self):
        was_held = self.attack_held
        self.attack_held = False
        task, self._hold_task = self._hold_task, None
        if task and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if was_held:
            await self._send('RELEASE')

    async def release_move(self):
        if getattr(self,'move_held',False):
            self.move_held=False
            await self._send('RELEASE')

    async def _refresh_attack_hold(self):
        epoch = self._epoch
        try:
            while self.attack_held and epoch == self._epoch:
                await asyncio.sleep(.2)
                if not self.capture.can_input() or getattr(self,'manual_pause_check',lambda:False)():
                    break
                await self._send('HOLD', 2, 1000)
        except asyncio.CancelledError:
            return
        except Exception:
            pass  # Firmware lease also expires if serial/BLE communication fails.
        if self.attack_held:
            self.attack_held = False
            try:
                await self._send('STOP')
            except Exception:
                pass

    async def _point(self, target, epoch, *, fast=False):
        if target is None or not all(math.isfinite(x) and 0 <= x <= 1 for x in target):
            self.last_error = '커서 이동: 화면 밖이거나 잘못된 목표 좌표'
            return False
        region = self.capture.input_region()
        if region is None:
            self.last_error = '커서 이동: 게임 입력 영역 없음'
            return False
        l, t, r, b = region
        sx = round(l + target[0] * (r - l - 1))
        sy = round(t + target[1] * (b - t - 1))
        start_position = self.cursor.position()
        start_at = time.monotonic()
        settings = self.settings_provider()
        duration = settings.get("pointer_duration_ms", 120)/1000 if settings.get("pointer_smoothing", True) else 0
        if fast:duration=0
        deadline = start_at + (.25 if fast else 2)
        for _ in range(160):
            if (
                epoch != self._epoch
                or not self.capture.can_input()
                or time.monotonic() > deadline
                or self.capture.input_region() != region
            ):
                self.last_error = '커서 이동 중 중단/포커스 변경/입력 영역 변경/보정 시간 제한'
                return False
            x, y = self.cursor.position()
            elapsed = time.monotonic() - start_at
            final_dx, final_dy = sx-x, sy-y
            if abs(final_dx) <= 3 and abs(final_dy) <= 3:
                return True
            smooth = duration > 0 and elapsed < duration
            tx, ty = smooth_point(start_position, (sx,sy), elapsed/duration) if smooth else (sx,sy)
            dx, dy = tx-x, ty-y

            def step(v):
                limit=127 if fast else 80
                divisor=2  # Feedback damping avoids Windows acceleration overshoot.
                return max(-limit, min(limit, int(v / divisor) or (1 if v > 0 else -1))) if v else 0

            # Track intermediate targets; feedback settles the end point despite Windows acceleration.
            if dx or dy:
                if smooth:
                    await self._send("MOVE", max(-80,min(80,dx)), max(-80,min(80,dy)))
                else:
                    await self._send("MOVE", step(dx), step(dy))
            await asyncio.sleep(.003 if fast else .012)
        self.last_error = '커서 이동: 목표 위치에 도달하지 못함'
        return False

    async def _wait(self, ms, epoch):
        deadline = time.monotonic() + ms / 1000
        while time.monotonic() < deadline:
            if epoch != self._epoch or not self.capture.can_input():
                return False
            await asyncio.sleep(min(0.01, max(0, deadline - time.monotonic())))
        return epoch == self._epoch and self.capture.can_input()

    async def _tap(self, key, target=None, *, fast=False, command=None):
        epoch = self._epoch
        if key.startswith('mouse_'):
            await self.release_move()
        if not self.capture.can_input():
            return False
        if target is not None and not await self._point(target, epoch, fast=fast):
            return False
        if epoch != self._epoch or not self.capture.can_input():
            return False
        if command is not None and not getattr(self,'hold_validator',lambda _:True)(command):
            return False
        ms = int(self.settings_provider()["tap_ms"])
        try:
            if key.startswith("mouse_"):
                await self._send("CLICK", ms, {"mouse_left": 1, "mouse_right": 2}[key])
            else:
                await self._send("KEY", ms, hid_key(key), 0)
            return await self._wait(ms, epoch)
        finally:
            if (self.attack_held or getattr(self,'move_held',False)) and epoch == self._epoch and self.capture.can_input():
                await self._send('END_TAP')
            else:
                await self.release_attack()
                await self._send("STOP")

    async def _action(self, name, c):
        self._move_clicked = False
        target = c.target
        if name == "DODGE" and c.direction and target is None:
            dx, dy = c.direction
            n = math.hypot(dx, dy)
            if n:
                target = (0.5 + dx / n * 0.12, 0.5 + dy / n * 0.12)
        return await self._tap(binding_for_command(self.settings_provider(), c), target,command=c)

    async def move(self, c):
        await self.release_attack()
        if c.maintain_move:
            if not self._move_hold_capable:
                self.last_error = 'ESP32 펌웨어 1.2 업로드 필요: 기존 펌웨어는 왼버튼 유지를 지원하지 않습니다.'
                await self.release_move()
                return False
            self.last_error = None
            epoch=self._epoch
            if not self.capture.can_input():
                self.last_error = '왼버튼 유지: 게임 포커스 없음'
                await self.release_move()
                return False
            if not await self._point(c.target,epoch,fast=True):
                self.last_error = self.last_error or '왼버튼 유지: 커서 이동 실패'
                await self.release_move()
                return False
            # Queue TTL must not expire solely because BLE cursor positioning
            # took longer. Recheck the current arrow, focus and epoch before HOLD.
            current=replace(c,expires_at=time.monotonic()+.6)
            if (epoch!=self._epoch or not self.capture.can_input()
                    or not getattr(self,'hold_validator',lambda _:True)(current)):
                owner=getattr(getattr(self,'hold_validator',None),'__self__',None)
                self.last_error = getattr(owner,'_input_block_reason',None) or '왼버튼 유지: 커서 이동 후 최신 화살표/포커스/중단 상태 검증 실패'
                await self.release_move()
                return False
            await self._send('HOLD',1,600)
            self.move_held=True
            self._move_clicked=True
            return True
        if not self.capture.can_input() or c.direction is None:
            return False
        s = self.settings_provider()
        epoch = self._epoch
        if s["movement"]["mode"] == "click":
            ok = await self._tap(s["bindings"]["MOVE"], c.target, fast=True)
            self._move_clicked = ok
            return ok and await self._wait(c.duration_ms, epoch)
        dx, dy = c.direction
        keys = []
        if abs(dx) > 0.25:
            keys.append(hid_key(s["movement"]["right" if dx > 0 else "left"]))
        if abs(dy) > 0.25:
            keys.append(hid_key(s["movement"]["down" if dy > 0 else "up"]))
        if not keys:
            return False
        try:
            await self._send(
                "KEY", c.duration_ms, keys[0], keys[1] if len(keys) > 1 else 0
            )
            return await self._wait(c.duration_ms, epoch)
        finally:
            await self._send("STOP")

    async def attack(self, c):
        if c.maintain_attack:
            if not self._hold_capable:
                raise ConnectionError('우클릭 유지에는 ESP32 BLE 펌웨어 1.1 업로드가 필요합니다: firmware/esp32_ble_hid/esp32_ble_hid.ino')
            self._move_clicked = False
            epoch = self._epoch
            fast=c.reason in {'STATIONARY_HOLD','STATIONARY_POST_DEATH','STATIONARY_TARGET_GRACE','STATIONARY_MODE_HOLD'}
            if not self.capture.can_input() or (c.target is not None and not await self._point(c.target, epoch,fast=fast)):
                await self.release_attack()
                return False
            current=replace(c,expires_at=time.monotonic()+.5) if c.reason=='STATIONARY_HOLD' else c
            if epoch != self._epoch or not self.capture.can_input() or current.is_expired() or not getattr(self, 'hold_validator', lambda _: True)(current):
                await self.release_attack()
                return False
            await self._send('HOLD', 2, 1000)
            self.attack_held = True
            if self._hold_task is None or self._hold_task.done():
                self._hold_task = asyncio.create_task(self._refresh_attack_hold())
            return True
        return await self._action("ATTACK", c)

    async def pickup(self, c):
        return await self._action("TAKE", c)

    async def interact(self, c):
        return await self._action("INTERACT", c)

    async def use_potion(self, c):
        return await self._action("USE_POTION", c)

    async def use_skill(self, c):
        return await self._action("USE_SKILL", c)

    async def cast_buff(self, c):
        return await self._action("CAST_BUFF", c)

    async def dodge(self, c):
        await self.release_attack()
        return await self._action("DODGE", c)

    async def stop(self, c):
        self._epoch += 1
        self.move_held=False
        self.attack_held=False
        task,self._hold_task=self._hold_task,None
        if task and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
        # Firmware STOP releases every mouse button and keyboard key in one
        # transaction, even if a previous RELEASE transaction failed.
        await self._send("STOP")
        self._move_clicked = False
        return True

    async def close(self):
        stop=getattr(self,'_manual_watch_stop',None)
        if stop is not None:
            stop.set()
            await asyncio.to_thread(self._manual_watch_thread.join,1)
        self._epoch += 1
        await self.release_move()
        await self.release_attack()
        await asyncio.to_thread(self.transport.close)

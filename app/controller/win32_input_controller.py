"""Windows SendInput backend. Bounded inputs, foreground guard and key release."""

from __future__ import annotations
import asyncio
import ctypes
import os
import math
import time
from ctypes import wintypes
from app.controller.input_bindings import binding_for_command
from app.controller.pointer_motion import smooth_point


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", INPUTUNION)]


class Win32InputController:
    def __init__(self, capture, settings_provider):
        if os.name != "nt":
            raise RuntimeError("win32 입력은 Windows에서만 사용할 수 있습니다.")
        self.capture = capture
        self.settings_provider = settings_provider
        self._held = set()
        self._epoch = 0
        self._move_clicked = False
        self.attack_held = False
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
        self.user32.SendInput.restype = wintypes.UINT
        self.user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
        self.user32.MapVirtualKeyW.restype = wintypes.UINT

    def _send(self, event):
        if self.user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT)) != 1:
            raise OSError(
                ctypes.get_last_error(),
                "SendInput failed (check game/process privilege and input support)",
            )

    def _key(self, key, up=False):
        event = INPUT()
        if key.startswith("mouse_"):
            event.type = 0
            event.mi = MOUSEINPUT(
                0,
                0,
                0,
                (
                    {"mouse_left": 0x4, "mouse_right": 0x10}
                    if up
                    else {"mouse_left": 0x2, "mouse_right": 0x8}
                )[key],
                0,
                0,
            )
        else:
            vk = {
                "SPACE": 32,
                "SHIFT": 16,
                "CTRL": 17,
                "ALT": 18,
                "UP": 38,
                "DOWN": 40,
                "LEFT": 37,
                "RIGHT": 39,
            }.get(key, ord(key.upper()[0]))
            event.type = 1
            scan = self.user32.MapVirtualKeyW(vk, 0)
            flags = 8 | (2 if up else 0) | (1 if key in {"UP", "DOWN", "LEFT", "RIGHT"} else 0)
            event.ki = KEYBDINPUT(0, scan, flags, 0, 0)
        self._send(event)
        if up:
            self._held.discard(key)
        else:
            self._held.add(key)

    async def _point(self, target, *, fast=False):
        region = self.capture.input_region()
        if region is None or target is None:
            return False
        x, y = target
        if not 0 <= x <= 1 or not 0 <= y <= 1:
            return False
        left, top, right, bottom = region
        sx, sy = left + x * (right - left - 1), top + y * (bottom - top - 1)
        vx, vy = self.user32.GetSystemMetrics(76), self.user32.GetSystemMetrics(77)
        vw, vh = self.user32.GetSystemMetrics(78), self.user32.GetSystemMetrics(79)
        if vw <= 1 or vh <= 1:
            return False
        epoch = getattr(self, "_epoch", 0)
        settings = self.settings_provider()
        duration = settings.get("pointer_duration_ms", 120)/1000 if settings.get("pointer_smoothing", True) else 0
        if fast:duration=min(duration,.04)
        position = wintypes.POINT()
        if not self.user32.GetCursorPos(ctypes.byref(position)):
            return False
        start = (position.x,position.y)
        target_px = (round(sx),round(sy))
        start_at = time.monotonic()
        while True:
            if epoch != getattr(self, "_epoch", 0) or not self.capture.can_input() or self.capture.input_region() != region:
                return False
            progress = min(1, (time.monotonic()-start_at)/duration) if duration else 1
            px,py = smooth_point(start,target_px,progress)
            event = INPUT()
            event.type = 0
            event.mi = MOUSEINPUT(round((px-vx)*65535/(vw-1)),round((py-vy)*65535/(vh-1)),0,0x8000|0x4000|1,0,0)
            self._send(event)
            if progress >= 1:
                return True
            await asyncio.sleep(.01)

    def _release(self):
        for key in list(self._held):
            self._key(key, True)
        self.attack_held = False

    async def release_attack(self):
        if getattr(self, 'attack_held', False) or 'mouse_right' in self._held:
            self._key('mouse_right', True)
            self.attack_held = False

    async def _tap(self, key, target=None, *, fast=False):
        epoch = getattr(self, "_epoch", 0)
        if not self.capture.can_input():
            return False
        if target is not None and not await self._point(target, fast=fast):
            return False
        if epoch != getattr(self, "_epoch", 0) or not self.capture.can_input():
            return False
        try:
            self._key(key)
            await asyncio.sleep(self.settings_provider()["tap_ms"] / 1000)
        finally:
            self._key(key, True)
        return self.capture.can_input()

    async def _action(self, name, command):
        self._move_clicked = False
        settings = self.settings_provider()
        target = command.target
        if name == "DODGE" and command.direction is not None and target is None:
            dx, dy = command.direction
            n = math.hypot(dx, dy)
            if n > 0:
                target = (0.5 + dx / n * 0.12, 0.5 + dy / n * 0.12)
        return await self._tap(binding_for_command(settings, command), target)

    async def move(self, command):
        await self.release_attack()
        if not self.capture.can_input() or command.direction is None:
            return False
        settings = self.settings_provider()
        movement = settings["movement"]
        dx, dy = command.direction
        if movement["mode"] == "click":
            ok = await self._tap(settings["bindings"]["MOVE"], command.target, fast=True)
            self._move_clicked = ok
            await asyncio.sleep(command.duration_ms / 1000)
            return ok
        keys = []
        if abs(dx) > 0.25:
            keys.append(movement["right" if dx > 0 else "left"])
        if abs(dy) > 0.25:
            keys.append(movement["down" if dy > 0 else "up"])
        try:
            for key in keys:
                self._key(key)
            for _ in range(max(1, command.duration_ms // 10)):
                await asyncio.sleep(0.01)
                if not self.capture.can_input():
                    return False
        finally:
            self._release()
        return True

    async def attack(self, c):
        if c.maintain_attack:
            self._move_clicked = False
            if not self.capture.can_input():
                await self.release_attack()
                return False
            epoch = self._epoch
            if c.target is not None and not await self._point(c.target):
                await self.release_attack()
                return False
            if epoch != self._epoch or not self.capture.can_input() or c.is_expired() or not getattr(self, 'hold_validator', lambda _: True)(c):
                await self.release_attack()
                return False
            if not self.attack_held:
                self._key('mouse_right')
                self.attack_held = True
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
        self._epoch = getattr(self, "_epoch", 0) + 1
        self._release()
        self._move_clicked = False
        return True

    async def close(self):
        self._epoch = getattr(self, "_epoch", 0) + 1
        self._release()

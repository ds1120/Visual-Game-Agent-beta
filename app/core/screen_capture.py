from __future__ import annotations

import ctypes
import threading
from dataclasses import dataclass

import dxcam
import numpy as np
import win32gui


@dataclass(frozen=True)
class CaptureFrame:
    image: np.ndarray
    source: str


class ScreenCapture:
    """Capture a configured game/streaming window, or optionally the full display."""

    def __init__(
        self,
        output_idx: int = 0,
        mode: str = "window",
        window_titles: list[str] | None = None,
        fallback_to_screen: bool = False,
    ) -> None:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

        self.output_idx = max(0, int(output_idx))
        self.mode = mode.strip().lower()
        self.window_titles = [x.strip() for x in (window_titles or []) if x.strip()]
        self.fallback_to_screen = bool(fallback_to_screen)
        self.camera = dxcam.create(output_idx=self.output_idx, output_color="BGR")
        self._camera_lock = threading.RLock()
        self._last_source: str | None = None
        self._cached_hwnd: int | None = None
        self._cached_title: str | None = None

    def _cached_window(self) -> tuple[int, str] | None:
        hwnd = self._cached_hwnd
        if hwnd is None:
            return None
        try:
            if not win32gui.IsWindow(hwnd):
                self._cached_hwnd = None
                self._cached_title = None
                return None
            title = win32gui.GetWindowText(hwnd).strip()
            if not title:
                return None
            # Do not reject a temporarily non-foreground/visibility-transition
            # frame. The stable HWND is still the configured game window.
            return hwnd, title
        except Exception:
            self._cached_hwnd = None
            self._cached_title = None
            return None

    def _find_window(self) -> tuple[int, str] | None:
        cached = self._cached_window()
        if cached is not None:
            return cached

        candidates: list[tuple[int, str]] = []

        def callback(hwnd: int, _: object) -> None:
            if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
                return
            title = win32gui.GetWindowText(hwnd).strip()
            if not title:
                return
            title_lower = title.lower()
            for wanted in self.window_titles:
                if wanted.lower() in title_lower:
                    candidates.append((hwnd, title))
                    return

        win32gui.EnumWindows(callback, None)
        if not candidates:
            return None

        foreground = win32gui.GetForegroundWindow()
        selected = next(
            (item for item in candidates if item[0] == foreground),
            candidates[0],
        )
        self._cached_hwnd, self._cached_title = selected
        return selected

    def _invalidate_window(self, hwnd: int | None = None) -> None:
        if hwnd is None or hwnd == self._cached_hwnd:
            self._cached_hwnd = None
            self._cached_title = None

    @staticmethod
    def _client_region(hwnd: int) -> tuple[int, int, int, int] | None:
        left, top = win32gui.ClientToScreen(hwnd, (0, 0))
        client = win32gui.GetClientRect(hwnd)
        width = client[2] - client[0]
        height = client[3] - client[1]
        if width <= 0 or height <= 0:
            return None
        return left, top, left + width, top + height

    def _log_source(self, source: str) -> None:
        if source != self._last_source:
            print(f"[CAPTURE] source={source}")
            self._last_source = source

    def can_input(self) -> bool:
        hwnd = self._cached_hwnd
        return bool(
            hwnd
            and not self.emergency_stop_held()
            and win32gui.IsWindow(hwnd)
            and not win32gui.IsIconic(hwnd)
            and win32gui.GetForegroundWindow() == hwnd
        )

    def is_foreground(self) -> bool:
        # Window discovery must work while pixel capture is paused.
        found = self._find_window()
        return bool(found and not win32gui.IsIconic(found[0])
                    and win32gui.GetForegroundWindow() == found[0])

    def input_region(self):
        return self._client_region(self._cached_hwnd) if self.can_input() else None

    @staticmethod
    def control_shortcuts_down():
        down = lambda key: bool(ctypes.windll.user32.GetAsyncKeyState(key) & 0x8000)
        if not (down(0x11) and down(0x10)):
            return set()
        return {number for number in range(1, 6) if down(0x30 + number)}

    @staticmethod
    def emergency_stop_held():
        return bool(ctypes.windll.user32.GetAsyncKeyState(0x1B) & 0x8000)

    def emergency_stop_pressed(self):
        held = self.emergency_stop_held()
        pressed = held and not getattr(self, "_escape_was_down", False)
        self._escape_was_down = held
        return pressed

    def close(self):
        # A cancelled asyncio capture can still be running in its executor.
        # Wait for that grab before disposing its DXGI resources.
        with self._camera_lock:
            camera = self.camera
            if camera is None:
                return
            camera.stop()
            duplicator = getattr(camera, "_duplicator", None)
            surface = getattr(camera, "_stagesurf", None)
            # DXCAM's DXGI release calls Release() explicitly then drops owning
            # comtypes pointers, whose destructors call Release() again. Drop
            # those owning references once while COM is still initialized.
            pointer = getattr(duplicator, "duplicator", None)
            if isinstance(pointer, ctypes.c_void_p) and callable(getattr(pointer, "Release", None)):
                duplicator.release_frame()
                del pointer
                duplicator.duplicator = None
            pointer = getattr(surface, "texture", None)
            if isinstance(pointer, ctypes.c_void_p) and callable(getattr(pointer, "Release", None)):
                del pointer
                surface.interface = None
                surface.texture = None
            camera.release()
            self.camera = None

    def _grab_camera(self, **kwargs):
        with self._camera_lock:
            return self.camera.grab(**kwargs) if self.camera is not None else None

    def grab(self) -> CaptureFrame | None:
        if self.mode == "window":
            found = self._find_window()
            if found is not None:
                hwnd, title = found
                try:
                    region = self._client_region(hwnd)
                    if region is not None:
                        frame = self._grab_camera(region=region)
                        if frame is not None:
                            self._log_source(title)
                            return CaptureFrame(frame, title)
                except Exception:
                    # Invalidate only on an actual Win32/DXGI failure. A single
                    # empty DXCAM frame is normal and must not trigger EnumWindows.
                    self._invalidate_window(hwnd)

                # Keep the cached HWND across transient empty frames.
                return None

            if not self.fallback_to_screen:
                self._log_source("waiting for configured window")
                return None

        frame = self._grab_camera()
        if frame is None:
            return None
        source = f"display:{self.output_idx}"
        self._log_source(source)
        return CaptureFrame(frame, source)

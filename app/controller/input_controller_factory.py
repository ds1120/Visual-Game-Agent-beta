from __future__ import annotations

from app.controller.input_controller import InputController
from app.controller.mock_input_controller import MockInputController


def create_input_controller(
    backend: str,
    capture=None,
    settings_provider=None,
    esp32_options=None,
) -> InputController:

    backend = backend.lower().strip()

    if backend == "mock":
        return MockInputController()

    if backend == "win32":
        if capture is None or settings_provider is None:
            raise ValueError("win32 needs capture and profile input settings")
        from app.controller.win32_input_controller import Win32InputController

        return Win32InputController(capture, settings_provider)

    if backend in {"esp32", "esp32_ble"}:
        if capture is None or settings_provider is None:
            raise ValueError("esp32_ble needs capture and profile input settings")
        from app.controller.esp32_input_controller import (
            ESP32InputController,
        )

        return ESP32InputController(
            capture, settings_provider, **(esp32_options or {"port": ""})
        )

    raise ValueError(f"Unknown input backend: {backend}")

# ESP32 BLE HID firmware 1.2

Firmware 1.2 adds `HOLD 1 <lease_ms>` for left-button movement, alongside
the existing `HOLD 2 <lease_ms>` for right-button attack. STATUS advertises
`hold_move: true`. The lease is 200–1500 ms; expiration, BLE disconnection,
RELEASE and STOP release the held button. MOVE preserves the held button.

The app checks `hold_move` before sending left-button HOLD commands.
Firmware 1.1 cannot provide this movement mode and must be upgraded.

Stop GameBot and close any serial monitor before uploading. From the project
directory in PowerShell, using the installed Arduino CLI:

```powershell
& 'C:\Users\ds1120\AppData\Local\Programs\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe' compile --fqbn esp32:esp32:esp32 --build-path "$PWD\firmware\esp32_ble_hid\build" "$PWD\firmware\esp32_ble_hid"
& 'C:\Users\ds1120\AppData\Local\Programs\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe' upload --fqbn esp32:esp32:esp32 --port COM3 --input-dir "$PWD\firmware\esp32_ble_hid\build" "$PWD\firmware\esp32_ble_hid"
```

Restart GameBot after uploading so it probes the updated capabilities.
The BLE device name and HID report map remain compatible with existing pairing.

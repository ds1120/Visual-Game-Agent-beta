@echo off

cd /d C:\ai\llama.cpp-master\build-cuda\bin\Release

set "MODEL=C:\ai\llama.cpp-master\models\Qwen3-VL-2B\Qwen3-VL-2B-Instruct-Q4_K_M.gguf"
set "MMPROJ=C:\ai\llama.cpp-master\models\Qwen3-VL-2B\mmproj-Qwen3-VL-2B-Instruct-F16.gguf"
set "MODEL_NAME=Qwen3-VL-2B-Instruct"
set "CONTEXT_TOKENS=16384"
set "RESPONSE_TOKENS=4096"

llama-server.exe ^
  -m "%MODEL%" ^
  --mmproj "%MMPROJ%" ^
  --alias "%MODEL_NAME%" ^
  -ngl 99 ^
  -c %CONTEXT_TOKENS% ^
  -n %RESPONSE_TOKENS% ^
  --parallel 1 ^
  -fa on ^
  --host 127.0.0.1 ^
  --port 8082

pause

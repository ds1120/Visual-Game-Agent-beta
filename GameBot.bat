@echo off
setlocal
cd /d "%~dp0"

py -m app.main --web --web-port 8766 --no-chat --chat-max-tokens 4096 %*

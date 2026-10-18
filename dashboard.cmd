@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\cardnews-dashboard.exe" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "& '.\.venv\Scripts\python.exe' -m pip install -e ."
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$running = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue; if ($running) { Start-Process 'http://127.0.0.1:8765' } else { & '.\.venv\Scripts\cardnews-dashboard.exe' }"

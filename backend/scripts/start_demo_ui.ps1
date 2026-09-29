# Start the ReviewMind demo UI + API (needs the local Hindsight server on :8888 for live parts).
# Usage (from reviewmind/backend):  powershell -ExecutionPolicy Bypass -File scripts\start_demo_ui.ps1
# Then open http://localhost:8010
$ErrorActionPreference = "Stop"
Set-Location "$PSScriptRoot\.."
& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8010

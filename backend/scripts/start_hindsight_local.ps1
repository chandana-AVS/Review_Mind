# Start the local Hindsight server used by ReviewMind (no Hindsight Cloud credits).
#   LLM: claude-code provider (uses the machine's existing `claude auth login`; no API key)
#   DB:  embedded pg0 (persists across restarts)
# Usage (from reviewmind/backend):  powershell -File scripts\start_hindsight_local.ps1
$ErrorActionPreference = "Stop"
$root = Resolve-Path "$PSScriptRoot\..\.."

$env:PYTHONUTF8 = "1"                              # Windows console can't print the banner otherwise
$env:PYTHONIOENCODING = "utf-8"
$env:HINDSIGHT_API_LLM_PROVIDER = "claude-code"
$env:HINDSIGHT_API_PORT = "8888"
$env:HINDSIGHT_API_MODEL_INIT_TIMEOUT = "900"      # first start downloads embedding/reranker models
$env:HINDSIGHT_API_REFLECT_WALL_TIMEOUT = "1200"   # mental-model reflects exceed the 300s default
$env:HINDSIGHT_API_RERANKER_PROVIDER = "rrf"       # skip the CPU cross-encoder (15-217s per recall on this laptop); RRF fusion still ranks

& "$root\.hindsight-venv\Scripts\hindsight-api.exe"

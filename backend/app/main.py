"""ReviewMind demo server: API + the static demo UI.

    cd backend
    .venv/Scripts/python -m uvicorn app.main:app --port 8010
    open http://localhost:8010
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import demo

UI_DIR = Path(__file__).resolve().parents[2] / "frontend" / "demo"

app = FastAPI(title="ReviewMind")
app.include_router(demo.router)
app.mount("/", StaticFiles(directory=UI_DIR, html=True), name="ui")

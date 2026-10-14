"""Mount static frontend files on the Gateway API for a single-server deployment."""

from pathlib import Path
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from .main import app

FRONTEND_DIR = Path(__file__).resolve().parents[4] / "frontend"

if FRONTEND_DIR.exists():
    @app.get("/")
    def serve_index() -> FileResponse:
        return FileResponse(FRONTEND_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

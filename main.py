"""AetherGuard — autonomous orbital safety & conjunction assessment API."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.routes import router

FRONTEND_DIST = Path(__file__).resolve().parent / "frontend" / "dist"

app = FastAPI(
    title="AetherGuard",
    description=(
        "Autonomous orbital safety and conjunction assessment API. "
        "Propagates TLEs with SGP4/Skyfield and evaluates Probability of "
        "Collision via Chan series / Foster–Chan encounter-plane methods."
    ),
    version="1.1.0",
)

# Comma-separated origins, e.g. "https://aetherguard.onrender.com".
# Unset means allow any origin, which the browser only honours when
# credentials are disabled.
_allowed_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins or ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/api")
def api_root() -> dict[str, str]:
    """JSON service banner (API)."""
    return {
        "name": "AetherGuard",
        "version": "1.1.0",
        "docs": "/docs",
        "ui": "/",
    }


if FRONTEND_DIST.is_dir():
    assets = FRONTEND_DIST / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/")
    def spa_index() -> FileResponse:
        return FileResponse(FRONTEND_DIST / "index.html")

else:

    @app.get("/")
    def root_dev_hint() -> dict[str, str]:
        return {
            "name": "AetherGuard",
            "hint": "Frontend not built. Run: cd frontend && npm install && npm run build",
            "dev": "cd frontend && npm run dev",
            "docs": "/docs",
        }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

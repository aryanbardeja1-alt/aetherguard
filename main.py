"""AetherGuard — autonomous orbital safety & conjunction assessment API."""

from __future__ import annotations

from fastapi import FastAPI

from api.routes import router

app = FastAPI(
    title="AetherGuard",
    description=(
        "Autonomous orbital safety and conjunction assessment API. "
        "Propagates TLEs with SGP4/Skyfield and evaluates Probability of "
        "Collision via Foster–Chan / Patera 2-D encounter-plane integration."
    ),
    version="1.0.0",
)

app.include_router(router)


@app.get("/")
def root() -> dict[str, str]:
    """Service banner."""
    return {
        "name": "AetherGuard",
        "version": "1.0.0",
        "docs": "/docs",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

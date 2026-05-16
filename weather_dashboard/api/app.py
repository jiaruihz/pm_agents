"""FastAPI application factory for the weather dashboard."""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from weather_dashboard.api.routers import compare, configs, runs


def create_app() -> FastAPI:
    app = FastAPI(
        title="Weather Dashboard API",
        version="1.0.0",
        description="Strategy run tracking, metrics, and comparison for weather Polymarket positions",
    )

    # CORS — allow the React dev server and any configured origin
    origins = os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000").split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in origins],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(runs.router, prefix="/api")
    app.include_router(compare.router, prefix="/api")
    app.include_router(configs.router, prefix="/api")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "weather_dashboard.api.app:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
        reload=True,
    )

"""FastAPI application factory for the weather dashboard."""

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from weather_dashboard.api.routers import compare, configs, copy_trade, glossary, live, probes, research, runs, strategy_runtime

ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = ROOT / "frontend" / "strategy_dashboard" / "dist"


def create_app() -> FastAPI:
    app = FastAPI(
        title="Weather Dashboard API",
        version="1.0.0",
        description="Strategy run tracking, metrics, and comparison for weather Polymarket positions",
    )

    # CORS — allow any localhost origin (dev) plus any explicitly configured origins.
    # Vite may auto-increment its port (5173→5174 etc) so we use allow_origin_regex
    # to cover all localhost ports rather than a fixed whitelist.
    extra = os.environ.get("CORS_ORIGINS", "")
    extra_origins = [o.strip() for o in extra.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=extra_origins,
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(runs.router, prefix="/api")
    app.include_router(compare.router, prefix="/api")
    app.include_router(configs.router, prefix="/api")
    app.include_router(live.router, prefix="/api")
    app.include_router(research.router, prefix="/api")
    app.include_router(copy_trade.router, prefix="/api")
    app.include_router(strategy_runtime.router, prefix="/api")
    app.include_router(glossary.router, prefix="/api")
    app.include_router(probes.router, prefix="/api")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    if FRONTEND_DIST.exists():
        assets_dir = FRONTEND_DIST / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str):
            candidate = FRONTEND_DIST / full_path
            if candidate.is_file() and FRONTEND_DIST in candidate.resolve().parents:
                return FileResponse(candidate)
            return FileResponse(FRONTEND_DIST / "index.html")

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

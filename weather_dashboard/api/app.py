"""FastAPI application factory for the weather dashboard."""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from weather_dashboard.api.routers import compare, configs, copy_trade, live, research, runs


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

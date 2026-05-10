from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any, Dict, Optional

import aiohttp

logger = logging.getLogger("pmm.weather.provider")


class AsyncOpenMeteoClient:
    """Async client for Open-Meteo API to fetch target-day weather forecasts."""

    def __init__(self, update_interval_sec: int = 300):
        self._update_interval_sec = update_interval_sec
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._targets: Dict[str, Dict[str, Any]] = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def register_target(
        self,
        token_id: str,
        lat: float,
        lon: float,
        target_date: Optional[str] = None,
    ) -> None:
        """Register a token's physical coordinate and target date for weather listening."""
        self._targets[token_id] = {
            "lat": lat,
            "lon": lon,
            "target_date": (target_date or "").strip(),
        }

    def get_precipitation_probability(self, token_id: str) -> Optional[float]:
        """Get the latest cached target-day precipitation probability (0.0 to 1.0)."""
        data = self._cache.get(token_id)
        if not data:
            return None
        return data.get("precip_prob")

    def get_daily_max_temperature(self, token_id: str) -> Optional[float]:
        """Get the cached target-day max temperature in Celsius for the token."""
        data = self._cache.get(token_id)
        if not data:
            return None
        return data.get("daily_max_temp_c")

    def get_target_date(self, token_id: str) -> str:
        target = self._targets.get(token_id) or {}
        return str(target.get("target_date") or "")

    async def start_polling(self) -> None:
        """Start the background weather polling loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("Weather provider started. Polling interval: %ss", self._update_interval_sec)

    async def stop_polling(self) -> None:
        """Stop polling."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Weather provider stopped.")

    async def _poll_loop(self) -> None:
        async with aiohttp.ClientSession() as session:
            while self._running:
                for token_id, coords in list(self._targets.items()):
                    if not self._running:
                        break
                    try:
                        await self._fetch_weather_for_target(session, token_id, coords["lat"], coords["lon"])
                    except Exception as exc:
                        logger.error("Failed to fetch weather for token %s: %s", token_id, exc)
                    await asyncio.sleep(1.0)

                for _ in range(self._update_interval_sec):
                    if not self._running:
                        break
                    await asyncio.sleep(1.0)

    async def _fetch_weather_for_target(
        self,
        session: aiohttp.ClientSession,
        token_id: str,
        lat: float,
        lon: float,
    ) -> None:
        target = self._targets.get(token_id) or {}
        target_date = str(target.get("target_date") or "").strip()
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": lat,
            "longitude": lon,
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "forecast_days": 7,
            "timezone": "UTC",
        }

        async with session.get(url, params=params, timeout=10) as resp:
            resp.raise_for_status()
            data = await resp.json()

        daily = data.get("daily", {})
        dates = daily.get("time", []) or []
        max_temps = daily.get("temperature_2m_max", []) or []
        min_temps = daily.get("temperature_2m_min", []) or []
        precip_probs = daily.get("precipitation_probability_max", []) or []
        idx = self._pick_target_index(dates, target_date)
        if idx is None:
            return

        daily_max_temp_c = float(max_temps[idx]) if idx < len(max_temps) else None
        daily_min_temp_c = float(min_temps[idx]) if idx < len(min_temps) else None
        precip_prob = (float(precip_probs[idx]) / 100.0) if idx < len(precip_probs) else None

        self._cache[token_id] = {
            "forecast_date": dates[idx],
            "daily_max_temp_c": daily_max_temp_c,
            "daily_min_temp_c": daily_min_temp_c,
            "precip_prob": precip_prob,
        }
        logger.debug(
            "Weather update for %s: forecast_date=%s daily_max_temp_c=%s precip_prob=%s",
            token_id,
            dates[idx],
            daily_max_temp_c,
            precip_prob,
        )

    def _pick_target_index(self, dates: list[Any], target_date: str) -> Optional[int]:
        if not dates:
            return None
        if target_date:
            for idx, raw in enumerate(dates):
                if str(raw) == target_date:
                    return idx
        today = dt.datetime.utcnow().date().isoformat()
        for idx, raw in enumerate(dates):
            if str(raw) >= today:
                return idx
        return 0

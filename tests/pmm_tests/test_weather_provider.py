from __future__ import annotations

from typing import Any

import pytest

from src.strategies.weather_edge_v1.tools.provider import AsyncOpenMeteoClient


class StubResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    async def __aenter__(self) -> "StubResponse":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def json(self) -> dict[str, Any]:
        return self.payload


class StubSession:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.request: tuple[str, dict[str, Any], int] | None = None

    def get(self, url: str, *, params: dict[str, Any], timeout: int) -> StubResponse:
        self.request = (url, params, timeout)
        return StubResponse(self.payload)


@pytest.mark.asyncio
async def test_fetch_weather_success() -> None:
    client = AsyncOpenMeteoClient(update_interval_sec=300)
    client.register_target("NO_TOKEN", 25.77, -80.19, target_date="2026-03-10")
    session = StubSession(
        {
            "daily": {
                "time": ["2026-03-10", "2026-03-11"],
                "temperature_2m_max": [28.4, 27.1],
                "temperature_2m_min": [20.2, 19.4],
                "precipitation_probability_max": [6, 35],
            }
        }
    )

    await client._fetch_weather_for_target(session, "NO_TOKEN", 25.77, -80.19)  # type: ignore[arg-type]

    assert session.request == (
        "https://api.open-meteo.com/v1/forecast",
        {
            "latitude": 25.77,
            "longitude": -80.19,
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "forecast_days": 7,
            "timezone": "UTC",
        },
        10,
    )
    assert client.get_precipitation_probability("NO_TOKEN") == 0.06
    assert client.get_daily_max_temperature("NO_TOKEN") == 28.4

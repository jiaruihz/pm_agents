import pytest
from aioresponses import aioresponses
from src.strategies.weather_edge_v1.tools.provider import AsyncOpenMeteoClient

@pytest.mark.asyncio
async def test_fetch_weather_success():
    client = AsyncOpenMeteoClient(update_interval_sec=300)
    client.register_target("NO_TOKEN", 25.77, -80.19, target_date="2026-03-10")
    with aioresponses() as m:
        m.get(
            'https://api.open-meteo.com/v1/forecast?daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max&forecast_days=7&latitude=25.77&longitude=-80.19&timezone=UTC',
            payload={
                'daily': {
                    'time': ['2026-03-10', '2026-03-11'],
                    'temperature_2m_max': [28.4, 27.1],
                    'temperature_2m_min': [20.2, 19.4],
                    'precipitation_probability_max': [6, 35],
                }
            }
        )
        import aiohttp
        async with aiohttp.ClientSession() as session:
            await client._fetch_weather_for_target(session, 'NO_TOKEN', 25.77, -80.19)

    assert client.get_precipitation_probability('NO_TOKEN') == 0.06
    assert client.get_daily_max_temperature('NO_TOKEN') == 28.4

from __future__ import annotations

import ast
import gzip
import json
from pathlib import Path

import httpx
import pytest

from us_fast_weather_lab.awc import AwcCollector, AwcConfig, _read_response
from us_fast_weather_lab.storage import EvidenceStore
from us_fast_weather_lab.wis2 import _inline_content, _integrity_status, discover_brokers


def test_awc_rate_limit_guard(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "runtime")
    store.start_run(config_hash="abc", vantage_id="TEST")
    config = AwcConfig("https://a", "https://b", 0.5, 60, "ua", 30)
    with pytest.raises(ValueError):
        AwcCollector(store, config=config, stations={"KJFK"}, vantage_id="TEST")
    store.close()


def test_http_reader_preserves_wire_gzip_for_replay() -> None:
    payload = gzip.compress(b"<response><raw_text>METAR KJFK 281051Z 00000KT 10SM 22/21 A2998</raw_text></response>")
    response = httpx.Response(
        200,
        headers={"Content-Encoding": "gzip"},
        stream=httpx.ByteStream(payload),
    )
    first_wall_ns, first_monotonic_ns, captured = _read_response(response)
    assert first_wall_ns is not None
    assert first_monotonic_ns is not None
    assert captured == payload
    assert gzip.decompress(captured).startswith(b"<response>")


def test_awc_repeated_transport_errors_replace_pool_then_recover(tmp_path, monkeypatch) -> None:
    store = EvidenceStore(tmp_path / "runtime")
    store.start_run(config_hash="awc-reset", vantage_id="TEST")
    collector = AwcCollector(
        store,
        config=AwcConfig(
            api_url="https://aviationweather.gov/api/data/metar",
            cache_url="https://aviationweather.gov/data/cache/metars.cache.xml.gz",
            poll_interval_seconds=2.0,
            cache_interval_seconds=60.0,
            user_agent="test-agent",
            max_requests_per_minute=100,
        ),
        stations={"KATL"},
        vantage_id="TEST",
    )

    class PoisonedClient:
        closed = False

        def stream(self, *_args, **_kwargs):
            raise httpx.PoolTimeout("synthetic exhausted pool")

        def close(self):
            self.closed = True

    class FreshClient:
        closed = False

        def stream(self, _method, url, *, params=None):
            request = httpx.Request("GET", url, params=params)
            response = httpx.Response(
                200,
                headers={"Content-Type": "application/json"},
                stream=httpx.ByteStream(
                    b'[{"icaoId":"KATL","obsTime":1787956800,"temp":25,'
                    b'"metarType":"METAR","rawOb":"METAR KATL 282240Z '
                    b'00000KT 10SM CLR 25/20 A3000"}]'
                ),
                request=request,
            )

            class ResponseContext:
                def __enter__(self):
                    return response

                def __exit__(self, *_args):
                    response.close()

            return ResponseContext()

        def close(self):
            self.closed = True

    poisoned = PoisonedClient()
    poisoned_again = PoisonedClient()
    fresh = FreshClient()
    assert collector._request(  # noqa: SLF001
        poisoned,
        source_id="AWC_API",
        url="https://aviationweather.gov/api/data/metar",
        params={"ids": "KATL", "format": "json"},
    ) is None
    assert collector._client_reset_requested is True  # noqa: SLF001
    replacements = iter((poisoned_again, fresh))
    monkeypatch.setattr(collector, "_new_client", lambda: next(replacements))
    replacement = collector._reset_client_if_requested(poisoned)  # noqa: SLF001
    assert replacement is poisoned_again
    assert poisoned.closed is True
    assert collector.client_resets == 1
    assert collector._client_reset_requested is False  # noqa: SLF001
    assert collector._request(  # noqa: SLF001
        replacement,
        source_id="AWC_API",
        url="https://aviationweather.gov/api/data/metar",
        params={"ids": "KATL", "format": "json"},
    ) is None
    replacement = collector._reset_client_if_requested(replacement)  # noqa: SLF001
    assert replacement is fresh
    assert poisoned_again.closed is True
    assert collector.client_resets == 2
    recovered = collector._request(  # noqa: SLF001
        replacement,
        source_id="AWC_API",
        url="https://aviationweather.gov/api/data/metar",
        params={"ids": "KATL", "format": "json"},
    )
    assert recovered is not None
    assert collector._client_reset_requested is False  # noqa: SLF001
    assert store.query("SELECT COUNT(*) AS n FROM transport_message")[0]["n"] == 1
    access = store.query("SELECT phase, status FROM access_attempt ORDER BY attempted_at_ns")
    assert [(row["phase"], row["status"]) for row in access] == [
        ("http_request", "error"),
        ("http_client_reset", "ok"),
        ("http_request", "error"),
        ("http_client_reset", "ok"),
    ]
    store.close()


def test_inline_content_and_integrity() -> None:
    import base64
    import hashlib

    payload = b"METAR KJFK 281051Z 05003KT 10SM 22/21 A2998"
    notification = {
        "properties": {
            "content": {"encoding": "base64", "value": base64.b64encode(payload).decode()},
            "integrity": {"method": "sha256", "value": base64.b64encode(hashlib.sha256(payload).digest()).decode()},
        }
    }
    assert _inline_content(notification) == payload
    assert _integrity_status(notification, payload) == "match"


def test_authoritative_wis2_endpoint_is_added_without_erasing_stale_discovery(monkeypatch) -> None:
    class Response:
        status_code = 200
        content = b"discovery"

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "links": [
                    {
                        "href": "mqtts://everyone:everyone@old.example:8883",
                        "title": "Global Broker (old)",
                    }
                ]
            }

    monkeypatch.setattr("us_fast_weather_lab.wis2.httpx.get", lambda *args, **kwargs: Response())
    brokers, evidence = discover_brokers(
        "https://gdc.example/item",
        fallback_brokers={},
        authoritative_brokers={"official-current": "new.example"},
    )
    assert {item.hostname for item in brokers} == {"old.example", "new.example"}
    assert evidence["authoritative_brokers"] == {"official-current": "new.example"}
    assert brokers[0].username == "everyone"
    assert brokers[0].password == "everyone"
    encoded_evidence = json.dumps(evidence, sort_keys=True)
    assert '"username"' not in encoded_evidence
    assert '"password"' not in encoded_evidence


def test_mqtt_callback_stamps_before_capture_or_worker() -> None:
    tree = ast.parse((Path(__file__).resolve().parents[1] / "wis2.py").read_text(encoding="utf-8"))
    callbacks = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "on_message"]
    assert len(callbacks) == 1
    assignments = callbacks[0].body[:4]
    targets = [node.targets[0].id for node in assignments if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)]
    assert targets == [
        "transport_received_wall_ns",
        "transport_received_monotonic_ns",
        "broker_id",
        "mqtt_topic",
    ]

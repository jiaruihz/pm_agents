import gzip
import json
from pathlib import Path

from scripts.ops.all_yes_underround_micro_snapshot_v0 import (
    build_output_path,
    extract_bracket_label,
    extract_market_tokens,
    load_weather_predict_city_configs,
    summarize_book_payload,
    write_gzip_jsonl_atomic,
)


def test_extract_bracket_label_from_weather_question():
    assert extract_bracket_label("Will the highest temperature in Paris be 14°C on June 14?") == "14"
    assert extract_bracket_label("Will the highest temperature in NYC reach 92F?") == "92"


def test_extract_market_tokens_maps_yes_no_outcomes():
    market = {"outcomes": '["Yes","No"]', "clobTokenIds": '["yes-token","no-token"]'}

    assert extract_market_tokens(market) == {"yes": "yes-token", "no": "no-token"}


def test_summarize_book_payload_normalizes_raw_clob_book():
    summary = summarize_book_payload(
        {
            "bids": [{"price": "0.41", "size": "7"}, {"price": "0.39", "size": "2"}],
            "asks": [{"price": "0.44", "size": "5"}, {"price": "0.46", "size": "9"}],
        },
        top_n=20,
    )

    assert summary["best_bid"] == 0.41
    assert summary["best_ask"] == 0.44
    assert summary["spread"] == 0.03
    assert summary["ask_size"] == 5.0


def test_load_weather_predict_city_configs_falls_back_without_weather_predict(tmp_path: Path):
    configs, t1, t2 = load_weather_predict_city_configs(tmp_path / "missing")

    assert "Paris" in configs
    assert "slug" in configs["Paris"]
    assert not t1
    assert not t2


def test_build_output_path_uses_snapshot_date():
    path = build_output_path(Path("/tmp/snapshots"), "2026-06-13T19:56:46Z")

    assert path == Path("/tmp/snapshots/2026-06-13/all_yes_micro_orderbook_snapshot_20260613_195646.jsonl.gz")


def test_write_gzip_jsonl_atomic(tmp_path: Path):
    out = tmp_path / "rows.jsonl.gz"
    write_gzip_jsonl_atomic(out, [{"a": 1}, {"b": 2}])

    with gzip.open(out, "rt", encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]

    assert rows == [{"a": 1}, {"b": 2}]

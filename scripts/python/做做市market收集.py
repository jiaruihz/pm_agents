import ast
import json
import os
import time
from typing import Any, Dict, List

from agents.polymarket.polymarket import Polymarket


def _timestamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _write_json(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _parse_token_ids(raw: str) -> List[str]:
    if not raw:
        return []
    try:
        parsed = ast.literal_eval(raw)
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
    except Exception:
        pass
    return []


def collect_events(polymarket: Polymarket) -> List[Any]:
    return polymarket.get_all_events()


def coarse_filter_events(polymarket: Polymarket, events: List[Any]) -> List[Any]:
    return polymarket.filter_events_for_trading(events)


def collect_markets(polymarket: Polymarket) -> List[Any]:
    return polymarket.get_all_markets()


def coarse_filter_markets(markets: List[Any]) -> List[Any]:
    # TODO: add coarse market filters (e.g., liquidity, spread, end time)
    return markets


def collect_clob_data(polymarket: Polymarket, markets: List[Any]) -> List[Dict[str, Any]]:
    clob_data: List[Dict[str, Any]] = []
    for market in markets:
        try:
            token_ids = _parse_token_ids(getattr(market, "clob_token_ids", ""))
            for token_id in token_ids:
                try:
                    orderbook = polymarket.get_orderbook(token_id)
                    clob_data.append(
                        {
                            "market_id": getattr(market, "id", None),
                            "token_id": token_id,
                            "orderbook": orderbook,
                        }
                    )
                except Exception as exc:
                    clob_data.append(
                        {
                            "market_id": getattr(market, "id", None),
                            "token_id": token_id,
                            "error": str(exc),
                        }
                    )
        except Exception as exc:
            clob_data.append({"market_id": getattr(market, "id", None), "error": str(exc)})
    return clob_data


def collect_and_persist(output_dir: str = "local_market_collection") -> Dict[str, str]:
    _ensure_dir(output_dir)
    polymarket = Polymarket()

    events = collect_events(polymarket)
    filtered_events = coarse_filter_events(polymarket, events)

    markets = collect_markets(polymarket)
    filtered_markets = coarse_filter_markets(markets)

    clob_data = collect_clob_data(polymarket, filtered_markets)

    ts = _timestamp()
    events_path = os.path.join(output_dir, f"events_{ts}.json")
    filtered_events_path = os.path.join(output_dir, f"events_filtered_{ts}.json")
    markets_path = os.path.join(output_dir, f"markets_{ts}.json")
    filtered_markets_path = os.path.join(output_dir, f"markets_filtered_{ts}.json")
    clob_path = os.path.join(output_dir, f"clob_{ts}.json")

    _write_json(events_path, events)
    _write_json(filtered_events_path, filtered_events)
    _write_json(markets_path, markets)
    _write_json(filtered_markets_path, filtered_markets)
    _write_json(clob_path, clob_data)

    return {
        "events": events_path,
        "events_filtered": filtered_events_path,
        "markets": markets_path,
        "markets_filtered": filtered_markets_path,
        "clob": clob_path,
    }


if __name__ == "__main__":
    paths = collect_and_persist()
    print("Saved:")
    for k, v in paths.items():
        print(f"- {k}: {v}")

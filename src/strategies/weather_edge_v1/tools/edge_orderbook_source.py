from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


def _open_text(path: Path):
    suffixes = "".join(path.suffixes).lower()
    if suffixes.endswith(".jsonl.gz") or path.suffix.lower() == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open("r", encoding="utf-8")


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _best_bid_ask(book: Dict[str, Any]) -> Dict[str, float]:
    bids = book.get("bids") if isinstance(book, dict) else []
    asks = book.get("asks") if isinstance(book, dict) else []
    bid_prices = [_to_float(level.get("price")) for level in bids or [] if isinstance(level, dict)]
    ask_prices = [_to_float(level.get("price")) for level in asks or [] if isinstance(level, dict)]
    best_bid = max([x for x in bid_prices if x > 0], default=0.0)
    best_ask = min([x for x in ask_prices if x > 0], default=0.0)
    return {"best_bid": best_bid, "best_ask": best_ask}


def load_latest_orderbook_prices(paths: Iterable[Path | str]) -> Dict[str, Dict[str, Any]]:
    """Read recorder JSONL/JSONL.GZ files and return latest top-of-book per token."""
    latest: Dict[str, Dict[str, Any]] = {}
    for raw_path in paths:
        path = Path(raw_path)
        if not path.exists():
            continue
        with _open_text(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                orderbooks = row.get("orderbooks")
                if not isinstance(orderbooks, dict):
                    continue
                ts = _to_float(row.get("ts_event") or row.get("ts_ingest"), 0.0)
                for token_id, book in orderbooks.items():
                    if not isinstance(book, dict):
                        continue
                    top = _best_bid_ask(book)
                    if top["best_bid"] <= 0 and top["best_ask"] <= 0:
                        continue
                    prev = latest.get(str(token_id))
                    if prev is None or ts >= _to_float(prev.get("ts"), 0.0):
                        latest[str(token_id)] = {
                            "token_id": str(token_id),
                            "ts": ts,
                            "best_bid": top["best_bid"],
                            "best_ask": top["best_ask"],
                            "source_file": str(path),
                        }
    return latest


def apply_orderbook_prices(event: Dict[str, Any], prices: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Return event copy with YES/NO market prices replaced by own top-of-book ask where available."""
    out = {**event}
    brackets = []
    for bracket in event.get("brackets", []) or []:
        item = dict(bracket)
        yes_token = str(item.get("yes_token_id") or "")
        no_token = str(item.get("no_token_id") or "")
        yes_px = prices.get(yes_token)
        no_px = prices.get(no_token)
        if yes_px and _to_float(yes_px.get("best_ask"), 0.0) > 0:
            item["yes_price"] = _to_float(yes_px["best_ask"])
            item["yes_price_source"] = "own_orderbook_best_ask"
            item["yes_price_ts"] = yes_px.get("ts")
        if no_px and _to_float(no_px.get("best_ask"), 0.0) > 0:
            item["no_price"] = _to_float(no_px["best_ask"])
            item["no_price_source"] = "own_orderbook_best_ask"
            item["no_price_ts"] = no_px.get("ts")
        brackets.append(item)
    out["brackets"] = brackets
    return out

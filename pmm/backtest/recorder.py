from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pmm.backtest.scenario_validator import validate_scenario_payload
from pmm.config import PMMConfig
from pmm.data.market_ws import MarketWsFeed


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _normalize_orderbook(
    ob: Dict[str, Any], max_levels: int
) -> Dict[str, List[Dict[str, float]]]:
    bids = ob.get("bids") if isinstance(ob, dict) else []
    asks = ob.get("asks") if isinstance(ob, dict) else []
    normalized_bids: List[Dict[str, float]] = []
    normalized_asks: List[Dict[str, float]] = []

    for level in list(bids or [])[:max_levels]:
        if isinstance(level, dict):
            price = _to_float(level.get("price"))
            size = _to_float(level.get("size"))
            if price > 0 and size >= 0:
                normalized_bids.append({"price": price, "size": size})

    for level in list(asks or [])[:max_levels]:
        if isinstance(level, dict):
            price = _to_float(level.get("price"))
            size = _to_float(level.get("size"))
            if price > 0 and size >= 0:
                normalized_asks.append({"price": price, "size": size})

    return {"bids": normalized_bids, "asks": normalized_asks}


def _side_map(levels: List[Dict[str, float]]) -> Dict[float, float]:
    out: Dict[float, float] = {}
    for lv in levels:
        px = _to_float(lv.get("price"))
        sz = max(0.0, _to_float(lv.get("size")))
        if px > 0:
            out[px] = sz
    return out


def _estimate_trade_flow(prev_ob: Dict[str, Any], cur_ob: Dict[str, Any]) -> Dict[str, float]:
    """
    Estimate taker flow from orderbook depth reductions.

    Approximation:
    - Ask depth reduction => buy taker pressure.
    - Bid depth reduction => sell taker pressure.
    """
    prev_asks = _side_map(prev_ob.get("asks", []))
    cur_asks = _side_map(cur_ob.get("asks", []))
    prev_bids = _side_map(prev_ob.get("bids", []))
    cur_bids = _side_map(cur_ob.get("bids", []))

    buy_taker_qty = 0.0
    sell_taker_qty = 0.0

    for px, prev_sz in prev_asks.items():
        cur_sz = cur_asks.get(px, 0.0)
        if prev_sz > cur_sz:
            buy_taker_qty += prev_sz - cur_sz

    for px, prev_sz in prev_bids.items():
        cur_sz = cur_bids.get(px, 0.0)
        if prev_sz > cur_sz:
            sell_taker_qty += prev_sz - cur_sz

    return {
        "buy_taker_qty": round(max(0.0, buy_taker_qty), 4),
        "sell_taker_qty": round(max(0.0, sell_taker_qty), 4),
    }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LiveRecorder:
    def __init__(
        self,
        token_ids: List[str],
        interval: float = 1.0,
        max_levels: int = 20,
        config: Optional[PMMConfig] = None,
    ) -> None:
        self.token_ids = [str(x).strip() for x in token_ids if str(x).strip()]
        if not self.token_ids:
            raise ValueError("token_ids is empty")
        self.interval = max(0.1, float(interval))
        self.max_levels = max(1, int(max_levels))
        self.config = config or PMMConfig.from_env()

        self.ws_feed = MarketWsFeed(
            ws_url=self.config.ws_market_url,
            token_ids=self.token_ids,
            detail_level=self.config.ws_detail_level,
            app_ping_interval_sec=self.config.ws_app_ping_interval_sec,
            reconnect_delay_sec=self.config.ws_reconnect_delay_sec,
            stale_after_sec=self.config.ws_stale_after_sec,
            level_limit=max(self.max_levels, self.config.ws_level_limit),
        )

    async def run(
        self,
        duration_sec: int = 60,
        output_file: str = "pmm/backtest/scenarios/recorded_live.json",
        output_jsonl: Optional[str] = None,
        warmup_sec: float = 4.0,
        initial_usdc: float = 100.0,
        initial_positions: Optional[Dict[str, float]] = None,
        scenario_id: Optional[str] = None,
        description: Optional[str] = None,
        event_label: str = "real_data",
    ) -> Dict[str, Any]:
        start_ts = time.time()
        if output_jsonl is None:
            output_jsonl = str(Path(output_file).with_suffix(".jsonl"))

        await self.ws_feed.start()
        await asyncio.sleep(max(0.0, warmup_sec))

        ticks: List[Dict[str, Any]] = []
        prev_books: Dict[str, Dict[str, Any]] = {}
        tick_idx = 0
        try:
            while True:
                elapsed = time.time() - start_ts
                if duration_sec > 0 and elapsed >= duration_sec:
                    break

                orderbooks: Dict[str, Dict[str, Any]] = {}
                trade_flow: Dict[str, Dict[str, float]] = {}

                for token_id in self.token_ids:
                    ob = self.ws_feed.get_orderbook(token_id)
                    cur = _normalize_orderbook(ob, self.max_levels)
                    orderbooks[token_id] = cur
                    if token_id in prev_books:
                        trade_flow[token_id] = _estimate_trade_flow(prev_books[token_id], cur)
                    else:
                        trade_flow[token_id] = {"buy_taker_qty": 0.0, "sell_taker_qty": 0.0}
                    prev_books[token_id] = cur

                tick = {
                    "t": tick_idx,
                    "ts": time.time(),
                    "event": event_label,
                    "orderbooks": orderbooks,
                    "trade_flow": trade_flow,
                }
                ticks.append(tick)
                tick_idx += 1

                next_ts = start_ts + tick_idx * self.interval
                await asyncio.sleep(max(0.0, next_ts - time.time()))
        finally:
            await self.ws_feed.stop()

        if initial_positions is None:
            initial_positions = {tid: 0.0 for tid in self.token_ids}

        sid = scenario_id or f"recorded_live_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        scenario_payload: Dict[str, Any] = {
            "scenario_id": sid,
            "description": description
            or f"Recorded live market data via WS at {_utc_now_iso()}",
            "token_ids": self.token_ids,
            "initial_state": {
                "usdc": float(initial_usdc),
                "positions": {k: float(v) for k, v in initial_positions.items()},
            },
            "strategy_overrides": {
                "paper_fill_model": "conservative",
                "market_data_source": "rest",
            },
            "meta": {
                "source": "live_ws",
                "recorded_at": _utc_now_iso(),
                "duration_sec": duration_sec,
                "interval_sec": self.interval,
                "warmup_sec": warmup_sec,
                "max_levels": self.max_levels,
                "trade_flow_mode": "estimated_from_book_delta",
                "ws_url": self.config.ws_market_url,
                "ws_detail_level": self.config.ws_detail_level,
            },
            "ticks": ticks,
        }

        validation_report = validate_scenario_payload(scenario_payload, strict=False)
        scenario_payload["meta"]["validation"] = {
            "ok": validation_report.get("ok", False),
            "errors_count": validation_report.get("errors_count", 0),
            "warnings_count": validation_report.get("warnings_count", 0),
            "stats": validation_report.get("stats", {}),
        }

        jsonl_path = Path(output_jsonl)
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with jsonl_path.open("w", encoding="utf-8") as f:
            for tick in ticks:
                f.write(json.dumps(tick, ensure_ascii=False) + "\n")

        out_path = Path(output_file)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(scenario_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return {
            "scenario_file": str(out_path),
            "jsonl_file": str(jsonl_path),
            "ticks": len(ticks),
            "token_ids": self.token_ids,
            "validation": validation_report,
        }


def convert_jsonl_to_scenario(
    jsonl_file: str,
    output_file: str,
    token_ids: Optional[List[str]] = None,
    scenario_id: Optional[str] = None,
    description: Optional[str] = None,
    initial_usdc: float = 100.0,
    initial_positions: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    path = Path(jsonl_file)
    if not path.exists():
        raise FileNotFoundError(jsonl_file)

    ticks: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for idx, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if "t" not in row:
                row["t"] = idx
            if "event" not in row:
                row["event"] = "real_data"
            if "orderbooks" not in row:
                row["orderbooks"] = {}
            if "trade_flow" not in row:
                row["trade_flow"] = {}
            ticks.append(row)

    if not ticks:
        raise ValueError(f"no tick data in {jsonl_file}")

    if token_ids is None:
        first_ob = ticks[0].get("orderbooks", {})
        token_ids = [str(x) for x in first_ob.keys()]
    token_ids = [str(x).strip() for x in (token_ids or []) if str(x).strip()]
    if not token_ids:
        raise ValueError("token_ids is empty; pass --tokens explicitly")

    if initial_positions is None:
        initial_positions = {tid: 0.0 for tid in token_ids}

    sid = scenario_id or f"{path.stem}_scenario"
    payload = {
        "scenario_id": sid,
        "description": description
        or f"Converted from live jsonl {path.name} at {_utc_now_iso()}",
        "token_ids": token_ids,
        "initial_state": {
            "usdc": float(initial_usdc),
            "positions": {k: float(v) for k, v in initial_positions.items()},
        },
        "strategy_overrides": {
            "paper_fill_model": "conservative",
            "market_data_source": "rest",
        },
        "meta": {
            "source": "live_jsonl_convert",
            "converted_at": _utc_now_iso(),
            "input_jsonl": str(path),
            "trade_flow_mode": "estimated_from_book_delta_or_input",
        },
        "ticks": ticks,
    }

    validation_report = validate_scenario_payload(payload, strict=False)
    payload["meta"]["validation"] = {
        "ok": validation_report.get("ok", False),
        "errors_count": validation_report.get("errors_count", 0),
        "warnings_count": validation_report.get("warnings_count", 0),
        "stats": validation_report.get("stats", {}),
    }

    out = Path(output_file)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "scenario_file": str(out),
        "ticks": len(ticks),
        "token_ids": token_ids,
        "validation": validation_report,
    }

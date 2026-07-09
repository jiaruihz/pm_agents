from __future__ import annotations

import json
import hashlib
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from weather_dashboard.contract import CanonicalValidationError
from weather_dashboard.ingest.canonical import (
    ingest_canonical_fills,
    ingest_canonical_orders,
    ingest_canonical_plans,
    ingest_canonical_signals,
    insert_code_version,
    insert_run,
    insert_strategy_config,
    insert_universe,
)
from src.strategies.weather_edge_v1.ids import make_execution_id, make_fill_id
from weather_dashboard.legacy_migration.live_cycle import (
    CITY_ICAO,
    _canonical_order,
    _canonical_plan,
    _canonical_signal,
    _insert_artifact,
    _json_text,
    _producer_system,
    _read_jsonl,
    _snapshot_ts,
    _strategy_config_id,
    _strategy_config_name,
    _strategy_params,
)


DEFAULT_ROOTS = (
    "runtime/weather_edge_v1/remote_pm_agent",
    # 2026-07-04: N100 7/1 事故后 Mac 是事实执行机；本地 runner 目录与 live/<strategy>_orders.jsonl
    # 必须进 canonical 血缘，否则 tiny-live fill 不出现在 fact_trades（数据审计 v1 §1.2）。
    "runtime/weather_edge_v1",
)
SNAPSHOT_DIR = Path("runtime/weather_edge_v1/market_data/paper_snapshots")
GAMMA_HOST = os.getenv("POLYMARKET_GAMMA_HOST", "https://gamma-api.polymarket.com").rstrip("/")
_GAMMA_MARKET_CACHE: dict[str, dict[str, Any] | None] = {}


@dataclass
class StrategyRuntimeOrderMigrationReport:
    source_path: str
    run_id: str
    producer_system: str
    input_orders: int = 0
    signals: int = 0
    plans: int = 0
    orders: int = 0
    fills: int = 0
    skipped_rows: int = 0
    skipped_reasons: dict[str, int] = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.skipped_rows += 1
        self.skipped_reasons[reason] = self.skipped_reasons.get(reason, 0) + 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "run_id": self.run_id,
            "producer_system": self.producer_system,
            "input": {"orders": self.input_orders},
            "inserted": {
                "signals": self.signals,
                "plans": self.plans,
                "orders": self.orders,
                "fills": self.fills,
            },
            "skipped_rows": self.skipped_rows,
            "skipped_reasons": self.skipped_reasons,
        }


def _mac_live_strategy_from_filename(path: Path) -> str | None:
    name = path.name
    if not name.endswith("_orders.jsonl"):
        return None
    if name in {"live_orders.jsonl", "paper_orders.jsonl"}:
        return None
    # live_<cycle_id>_orders.jsonl / live_mid_price_core_* 是 live-cycle journal，归 live_cycle
    # migration 管；这里只认 Mac runner 的 <strategy_instance>_orders.jsonl。
    if name.startswith("live_"):
        return None
    return name[: -len("_orders.jsonl")]


def _order_path_shape(order_path: Path) -> tuple[str, str]:
    """Return (strategy_instance, order_kind) for supported runtime layouts."""
    if order_path.name == "live_orders.jsonl":
        return order_path.parent.name, "live"
    if order_path.name == "paper_orders.jsonl":
        return order_path.parent.name, "paper"
    if order_path.name == "orders.jsonl":
        return order_path.parent.name, "live"
    if order_path.parent.name == "live":
        strategy_instance = _mac_live_strategy_from_filename(order_path)
        if strategy_instance:
            return strategy_instance, "live"
    return order_path.parent.name, "paper"


def iter_strategy_order_paths(roots: Iterable[str | Path]) -> list[Path]:
    paths: list[Path] = []
    for root in roots:
        root_path = Path(root)
        if not root_path.exists():
            continue
        for path in sorted(root_path.glob("*/live_orders.jsonl")):
            paths.append(path)
        for path in sorted(root_path.glob("*/paper_orders.jsonl")):
            paths.append(path)
        for path in sorted((root_path / "live").glob("*_orders.jsonl")):
            if _mac_live_strategy_from_filename(path):
                paths.append(path)
    return paths


def _run_id(order_path: Path, producer_system: str) -> str:
    strategy_instance, order_kind = _order_path_shape(order_path)
    return f"{producer_system}_strategy_runtime_{strategy_instance}_{order_kind}"


def _parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _snapshot_files_for_date(target_date: str) -> list[Path]:
    ymd = target_date.replace("-", "")
    if not ymd or not SNAPSHOT_DIR.exists():
        return []
    return sorted(SNAPSHOT_DIR.glob(f"snapshot_{ymd}_*.json"))


def _row_token_ids(row: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for key in ("token_id", "yes_token_id", "no_token_id"):
        value = str(row.get(key) or "").strip()
        if value:
            out.add(value)
    return out


def _needs_snapshot_lookup(row: dict[str, Any]) -> bool:
    if not str(row.get("token_id") or "").strip():
        return False
    has_condition = bool(str(row.get("condition_id") or "").strip())
    has_question = bool(str(row.get("question") or "").strip())
    has_bracket = bool(str(row.get("bracket") or row.get("t_minus_1_no_bracket_c") or "").strip())
    return not (has_condition and has_question and has_bracket)


def _fetch_gamma_market(market_id: str) -> dict[str, Any] | None:
    market_id = str(market_id or "").strip()
    if not market_id:
        return None
    if market_id in _GAMMA_MARKET_CACHE:
        return _GAMMA_MARKET_CACHE[market_id]
    url = f"{GAMMA_HOST}/markets/{market_id}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "pm_agents/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        payload = None
    if not isinstance(payload, dict):
        payload = None
    _GAMMA_MARKET_CACHE[market_id] = payload
    return payload


def _enrich_from_gamma_market(row: dict[str, Any]) -> None:
    """Fill market lineage for compact runtime rows when snapshots are absent.

    Strategy-local live order rows are sometimes compact and omit
    ``condition_id``. Canonical DB requires the real market condition id, so use
    Gamma market metadata as a last-resort lineage source keyed by market_id.
    """
    if row.get("condition_id") or not row.get("market_id"):
        return
    market = _fetch_gamma_market(str(row.get("market_id") or ""))
    if not market:
        return
    condition_id = str(market.get("conditionId") or market.get("condition_id") or "").strip()
    if condition_id:
        row["condition_id"] = condition_id
    for key in ("question", "slug"):
        if not row.get(key) and market.get(key) is not None:
            row[key] = market.get(key)


def _build_snapshot_lookup(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """Return best snapshot record by (target_date, token_id).

    The strategy-local runner intentionally keeps compact order rows. Canonical
    ingest needs static market lineage such as condition_id and forecast_source;
    those are recovered from the paper snapshot mirror using the order token.
    Prefer the latest snapshot at or before order placement to preserve PIT
    semantics. If no prior snapshot exists, use the first matching snapshot.
    """
    wanted: dict[str, list[tuple[str, datetime | None]]] = {}
    for row in rows:
        if not _needs_snapshot_lookup(row):
            continue
        target_date = str(row.get("target_date") or "").strip()
        token_id = str(row.get("token_id") or "").strip()
        if target_date and token_id:
            wanted.setdefault(target_date, []).append((token_id, _parse_dt(row.get("created_at_utc"))))

    lookup: dict[tuple[str, str], dict[str, Any]] = {}
    best_ts: dict[tuple[str, str], datetime | None] = {}
    for target_date, token_orders in wanted.items():
        token_set = {token for token, _ in token_orders}
        order_cutoffs = {token: cutoff for token, cutoff in token_orders}
        for path in _snapshot_files_for_date(target_date):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            records = payload.get("records") if isinstance(payload, dict) else None
            if not isinstance(records, list):
                continue
            for rec in records:
                if not isinstance(rec, dict):
                    continue
                tokens = _row_token_ids(rec) & token_set
                if not tokens:
                    continue
                rec_ts = _parse_dt(rec.get("snapshot_ts_utc") or rec.get("ts_utc"))
                for token in tokens:
                    key = (target_date, token)
                    cutoff = order_cutoffs.get(token)
                    existing = best_ts.get(key)
                    if cutoff is not None and rec_ts is not None and rec_ts > cutoff:
                        if key in lookup:
                            continue
                    if existing is None or (rec_ts is not None and rec_ts > existing):
                        lookup[key] = rec
                        best_ts[key] = rec_ts
    return lookup


def _side_from_runtime(raw: dict[str, Any]) -> str:
    side = str(raw.get("signal_side") or raw.get("side") or "").upper()
    if side in {"BUY_NO", "SELL_NO", "NO"}:
        return "NO"
    if side in {"BUY_YES", "SELL_YES", "YES"}:
        return "YES"
    return "NO" if str(raw.get("order_side") or "").upper() == "BUY" else side


def _order_side_from_runtime(raw: dict[str, Any]) -> str:
    raw_order_side = str(raw.get("order_side") or "").upper()
    raw_signal_side = str(raw.get("signal_side") or raw.get("side") or "").upper()
    if raw_signal_side in {"BUY_YES", "BUY_NO", "SELL_YES", "SELL_NO"}:
        return raw_signal_side
    if raw_order_side in {"BUY_YES", "BUY_NO", "SELL_YES", "SELL_NO"}:
        return raw_order_side
    if raw_order_side == "SELL":
        return f"SELL_{_side_from_runtime(raw)}"
    return "BUY_NO" if _side_from_runtime(raw) == "NO" else "BUY_YES"


def _stable_attempt_key(raw: dict[str, Any]) -> str:
    for key in ("order_id", "live_attempt_ts_utc", "event_key", "source_obs_ts_utc", "ts_utc"):
        value = str(raw.get(key) or "").strip()
        if value:
            return value
    return hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _runtime_order_status(raw: dict[str, Any]) -> str:
    submit_status = str(raw.get("live_submit_status") or "").strip()
    if submit_status == "submit_failed":
        return "failed"
    if submit_status == "submitted":
        return "submitted"
    return str(raw.get("order_status") or raw.get("status") or "").strip() or "submitted"


def _runtime_fill_from_order(raw: dict[str, Any], order: dict[str, Any]) -> dict[str, Any] | None:
    response = raw.get("exchange_response")
    if not isinstance(response, dict):
        return None
    place = response.get("place")
    if not isinstance(place, dict):
        return None
    if place.get("status") != "matched" and place.get("success") is not True:
        return None
    try:
        cost = float(place.get("makingAmount") or 0.0)
        shares = float(place.get("takingAmount") or 0.0)
    except (TypeError, ValueError):
        return None
    if cost <= 0 or shares <= 0:
        return None
    return {
        "fill_id": make_fill_id(execution_id=order["execution_id"]),
        "execution_id": order["execution_id"],
        "order_id": order["order_id"],
        "filled_shares": shares,
        "filled_price": cost / shares,
        "fees_usd": 0.0,
        "status": "filled",
        "filled_at_utc": raw.get("live_attempt_ts_utc") or raw.get("created_at_utc") or raw.get("ts_utc"),
    }


def _enrich_runtime_order(raw: dict[str, Any], snapshot: dict[str, Any] | None) -> dict[str, Any]:
    row = dict(raw)
    snap = snapshot or {}

    for key in (
        "condition_id",
        "forecast_source",
        "model_version",
        "forecast_max_f",
        "forecast_max_native",
        "forecast_peak_hour_local",
        "forecast_peak_time_local",
        "forecast_peak_hour_utc",
        "forecast_peak_time_utc",
        "forecast_hourly_count",
        "forecast_values_hash",
        "forecast_peak_source",
        "forecast_timezone",
        "forecast_utc_offset_seconds",
        "forecast_peak_delta_hours_local",
        "forecast_max_in_bracket",
        "forecast_max_above_bracket_f",
        "forecast_max_below_bracket_f",
        "forecast_max_above_metar_max_f",
        "question",
    ):
        if not row.get(key) and snap.get(key) is not None:
            row[key] = snap.get(key)

    row["created_at_utc"] = row.get("created_at_utc") or row.get("live_attempt_ts_utc") or row.get("ts_utc")
    row["snapshot_ts_utc"] = row.get("snapshot_ts_utc") or snap.get("snapshot_ts_utc") or snap.get("ts_utc") or row.get("created_at_utc")
    row["venue"] = row.get("venue") or "polymarket_clob"
    row["status"] = _runtime_order_status(row)
    row["bracket"] = row.get("bracket") or row.get("t_minus_1_no_bracket_c")
    row["city_pool"] = snap.get("city_pool") if snap.get("city_pool") in {"t1_trading", "t2_research"} else "t1_trading"
    row["icao"] = row.get("icao") or snap.get("icao") or CITY_ICAO.get(str(row.get("city") or ""), "")
    row["unit"] = row.get("unit") or snap.get("unit") or "C"
    row["signal_side"] = _side_from_runtime(row)
    row["order_side"] = _order_side_from_runtime(row)
    row["model_p_yes"] = row.get("model_p_yes") or row.get("model_p_yes_used") or row.get("model_p_yes_raw") or snap.get("model_prob") or 0.0
    row["market_price"] = row.get("market_price") or row.get("best_ask") or row.get("posted_price") or row.get("limit_price") or snap.get("entry_price")
    row["posted_price"] = row.get("posted_price") or row.get("limit_price") or row.get("best_ask")
    row["shares"] = row.get("shares") or row.get("size") or row.get("planned_shares")
    row["posted_notional"] = row.get("posted_notional") or row.get("submitted_notional_usd") or row.get("planned_notional_usd")
    row["notional"] = row.get("notional") or row.get("posted_notional")
    row["edge"] = row.get("edge") or snap.get("edge") or 0.0
    row["forecast_source"] = row.get("forecast_source") or "open_meteo_live_gfs"
    if not row.get("model_version"):
        source = str(row.get("forecast_source") or "").lower()
        if "ecmwf" in source:
            row["model_version"] = "ecmwf"
        elif "gfs" in source:
            row["model_version"] = "gfs"
        else:
            row["model_version"] = str(row.get("forecast_model_tail") or snap.get("forecast_model_tail") or "gfs")
    row["source_run_id"] = row.get("source_run_id") or row.get("strategy_instance") or row.get("strategy_id") or row.get("record_type")
    row["execution_policy"] = row.get("execution_policy") or "fast_source_prev_no_fok"
    if not row.get("condition_id"):
        row["condition_id"] = snap.get("condition_id") or ""
    _enrich_from_gamma_market(row)
    return row


def migrate_strategy_runtime_orders(conn, *, order_path: str | Path) -> StrategyRuntimeOrderMigrationReport:
    order_path = Path(order_path)
    producer_system = _producer_system(order_path)
    strategy_instance, order_kind = _order_path_shape(order_path)
    run_id = _run_id(order_path, producer_system)
    report = StrategyRuntimeOrderMigrationReport(str(order_path), run_id, producer_system)

    raw_orders = _read_jsonl(order_path)
    report.input_orders = len(raw_orders)
    if not raw_orders:
        return report

    snapshot_lookup = _build_snapshot_lookup(raw_orders)
    enriched = [
        _enrich_runtime_order(
            raw,
            snapshot_lookup.get((str(raw.get("target_date") or ""), str(raw.get("token_id") or ""))),
        )
        for raw in raw_orders
    ]

    strategy_params = _strategy_params({}, enriched)
    config_id = _strategy_config_id(strategy_params)
    signals = []
    plans = []
    orders = []
    fills = []

    for raw in enriched:
        try:
            signal = _canonical_signal(raw, producer_system=producer_system, cycle_id=run_id)
            plan = _canonical_plan(raw, run_id=run_id, config_id=config_id, signal_id=signal["signal_id"])
            if not str(raw.get("execution_id") or "").strip():
                raw["execution_id"] = make_execution_id(
                    run_id=run_id,
                    plan_id=plan["plan_id"],
                    venue=raw.get("venue") or "polymarket_clob",
                    attempt_index=_stable_attempt_key(raw),
                )
            order = _canonical_order(raw, run_id=run_id, plan_id=plan["plan_id"])
            fill = _runtime_fill_from_order(raw, order)
        except (ValueError, CanonicalValidationError) as exc:
            report.skip(f"runtime_order:{exc}")
            continue
        signals.append(signal)
        plans.append(plan)
        orders.append(order)
        if fill is not None:
            fills.append(fill)

    cities = sorted({row["city"] for row in signals})
    models = sorted({row["model_version"] for row in signals})
    started = min((_snapshot_ts(row) for row in enriched if _snapshot_ts(row)), default=None)
    ended = max((str(row.get("created_at_utc") or "") for row in enriched if row.get("created_at_utc")), default=started)

    insert_strategy_config(conn, config_id, _strategy_config_name(strategy_params), strategy_params)
    insert_universe(conn, f"{run_id}_universe", f"strategy_runtime_{producer_system}_{order_path.parent.name}_{order_path.stem}", cities=cities, models=models)
    insert_code_version(conn, "strategy-runtime-order-migration")
    insert_run(
        conn,
        {
            "run_id": run_id,
            "producer_system": producer_system,
            "producer_run_id": order_path.parent.name,
            "config_id": config_id,
            "universe_id": f"{run_id}_universe",
            "code_version": "strategy-runtime-order-migration",
            "execution_mode": order_kind,
            "date_range_start": min((row["target_date"] for row in signals), default=None),
            "date_range_end": max((row["target_date"] for row in signals), default=None),
            "started_at_utc": started,
            "ended_at_utc": ended,
            "state": order_kind,
            "source_root": str(order_path.parent),
            "repro_key": f"strategy_runtime:{producer_system}:{order_path.parent.name}:{order_path.name}",
            "tags": ["strategy_runtime", producer_system, strategy_instance, order_kind],
            "metrics": None,
            "notes": f"Migrated strategy-local runtime orders from {order_path}",
        },
    )

    report.signals = ingest_canonical_signals(conn, signals, str(order_path))
    report.plans = ingest_canonical_plans(conn, plans, str(order_path))
    report.orders = ingest_canonical_orders(conn, orders, str(order_path))
    report.fills = ingest_canonical_fills(conn, fills, str(order_path))
    _insert_artifact(conn, run_id=run_id, kind=order_path.name, path=order_path, row_count=len(raw_orders))
    return report


def write_report(reports: list[StrategyRuntimeOrderMigrationReport], report_dir: str | Path) -> Path:
    out_dir = Path(report_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"strategy_runtime_order_migration_{ts}.json"
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "reports": [report.as_dict() for report in reports],
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return out_path

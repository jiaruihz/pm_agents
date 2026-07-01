from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from weather_dashboard.contract import CanonicalValidationError
from weather_dashboard.ingest.canonical import (
    ingest_canonical_orders,
    ingest_canonical_plans,
    ingest_canonical_signals,
    insert_code_version,
    insert_run,
    insert_strategy_config,
    insert_universe,
)
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
            },
            "skipped_rows": self.skipped_rows,
            "skipped_reasons": self.skipped_reasons,
        }


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
    return paths


def _run_id(order_path: Path, producer_system: str) -> str:
    strategy_dir = order_path.parent.name
    kind = order_path.stem
    return f"{producer_system}_strategy_runtime_{strategy_dir}_{kind}"


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
    if side in {"BUY_NO", "NO"}:
        return "NO"
    if side in {"BUY_YES", "YES"}:
        return "YES"
    return "NO" if str(raw.get("order_side") or "").upper() == "BUY" else side


def _enrich_runtime_order(raw: dict[str, Any], snapshot: dict[str, Any] | None) -> dict[str, Any]:
    row = dict(raw)
    snap = snapshot or {}

    for key in (
        "condition_id",
        "forecast_source",
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

    row["snapshot_ts_utc"] = row.get("snapshot_ts_utc") or snap.get("snapshot_ts_utc") or snap.get("ts_utc") or row.get("created_at_utc")
    row["city_pool"] = snap.get("city_pool") if snap.get("city_pool") in {"t1_trading", "t2_research"} else "t1_trading"
    row["icao"] = row.get("icao") or snap.get("icao") or CITY_ICAO.get(str(row.get("city") or ""), "")
    row["unit"] = row.get("unit") or snap.get("unit") or "F"
    row["signal_side"] = _side_from_runtime(row)
    row["order_side"] = "BUY_NO" if row["signal_side"] == "NO" else "BUY_YES"
    row["model_p_yes"] = row.get("model_p_yes") or row.get("model_p_yes_used") or row.get("model_p_yes_raw") or snap.get("model_prob") or 0.0
    row["market_price"] = row.get("market_price") or row.get("posted_price") or row.get("limit_price") or snap.get("entry_price")
    row["edge"] = row.get("edge") or snap.get("edge") or 0.0
    row["forecast_source"] = row.get("forecast_source") or "open_meteo_live_gfs"
    row["source_run_id"] = row.get("source_run_id") or row.get("strategy_instance") or row.get("strategy_id") or row.get("record_type")
    if not row.get("condition_id"):
        row["condition_id"] = snap.get("condition_id") or ""
    _enrich_from_gamma_market(row)
    return row


def migrate_strategy_runtime_orders(conn, *, order_path: str | Path) -> StrategyRuntimeOrderMigrationReport:
    order_path = Path(order_path)
    producer_system = _producer_system(order_path)
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

    for raw in enriched:
        try:
            signal = _canonical_signal(raw, producer_system=producer_system, cycle_id=run_id)
            plan = _canonical_plan(raw, run_id=run_id, config_id=config_id, signal_id=signal["signal_id"])
            order = _canonical_order(raw, run_id=run_id, plan_id=plan["plan_id"])
        except (ValueError, CanonicalValidationError) as exc:
            report.skip(f"runtime_order:{exc}")
            continue
        signals.append(signal)
        plans.append(plan)
        orders.append(order)

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
            "execution_mode": "live" if order_path.name == "live_orders.jsonl" else "paper",
            "date_range_start": min((row["target_date"] for row in signals), default=None),
            "date_range_end": max((row["target_date"] for row in signals), default=None),
            "started_at_utc": started,
            "ended_at_utc": ended,
            "state": "live" if order_path.name == "live_orders.jsonl" else "paper",
            "source_root": str(order_path.parent),
            "repro_key": f"strategy_runtime:{producer_system}:{order_path.parent.name}:{order_path.name}",
            "tags": ["strategy_runtime", producer_system, order_path.parent.name],
            "metrics": None,
            "notes": f"Migrated strategy-local runtime orders from {order_path}",
        },
    )

    report.signals = ingest_canonical_signals(conn, signals, str(order_path))
    report.plans = ingest_canonical_plans(conn, plans, str(order_path))
    report.orders = ingest_canonical_orders(conn, orders, str(order_path))
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

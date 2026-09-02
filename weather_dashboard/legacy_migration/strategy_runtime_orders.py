from __future__ import annotations

import json
import hashlib
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

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
from src.strategies.runtime.ownership import strategy_key_for_params
from src.strategies.runtime.production import load_production_spec
from src.strategies.weather_edge_v1.ids import make_execution_id
from weather_clock_contract import local_wall_time_to_utc, parse_utc_or_none, utc_text
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
PRODUCTION_SPEC = load_production_spec()
SNAPSHOT_DIRS = (
    PRODUCTION_SPEC.strategy_paper_snapshot_dir(),
    PRODUCTION_SPEC.resolved_historical_paper_snapshot_root(),
)
GAMMA_HOST = os.getenv("POLYMARKET_GAMMA_HOST", "https://gamma-api.polymarket.com").rstrip("/")
_GAMMA_MARKET_CACHE: dict[str, dict[str, Any] | None] = {}
_CONDITION_ID_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
_SNAPSHOT_FILE_TS_RE = re.compile(r"snapshot_(\d{8})_(\d{4})\.json$")
_SNAPSHOT_FILENAME_TIMEZONE = "Asia/Shanghai"
_SNAPSHOT_LOOKBACK_DAYS = 2


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
    return parse_utc_or_none(value)


def _snapshot_files_for_local_date(local_date: str) -> list[Path]:
    """Return one physical copy per Beijing capture-date filename."""

    ymd = local_date.replace("-", "")
    if not ymd:
        return []
    by_name: dict[str, Path] = {}
    for root in SNAPSHOT_DIRS:
        if root.exists():
            for path in root.glob(f"snapshot_{ymd}_*.json"):
                by_name.setdefault(path.name, path)
    return [by_name[name] for name in sorted(by_name)]


def _snapshot_file_ts(path: Path) -> datetime | None:
    """Parse the legacy filename clock through its real IANA timezone.

    ``snapshot_YYYYMMDD_HHMM`` is a Beijing wall clock.  Treating that suffix
    as UTC was one of the causes of future snapshots entering migrated signals.
    """

    match = _SNAPSHOT_FILE_TS_RE.fullmatch(path.name)
    if not match:
        return None
    try:
        local = datetime.strptime("".join(match.groups()), "%Y%m%d%H%M")
    except ValueError:
        return None
    return local_wall_time_to_utc(
        local,
        timezone_name=_SNAPSHOT_FILENAME_TIMEZONE,
        field="snapshot_filename_wall_time",
    )


def _snapshot_files_before(cutoff_utc: datetime) -> list[Path]:
    """Return newest-first snapshot files in a bounded causal lookback."""

    local_cutoff = cutoff_utc.astimezone(ZoneInfo(_SNAPSHOT_FILENAME_TIMEZONE))
    by_name: dict[str, Path] = {}
    for days_back in range(_SNAPSHOT_LOOKBACK_DAYS + 1):
        local_date = (local_cutoff.date() - timedelta(days=days_back)).isoformat()
        for path in _snapshot_files_for_local_date(local_date):
            by_name.setdefault(path.name, path)
    causal = [
        path
        for path in by_name.values()
        if (_snapshot_file_ts(path) is not None and _snapshot_file_ts(path) <= cutoff_utc)
    ]
    return sorted(causal, key=lambda path: (_snapshot_file_ts(path), path.name), reverse=True)


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
    has_explicit_clock = any(
        _parse_dt(row.get(key)) is not None
        for key in ("decision_snapshot_ts_utc", "snapshot_ts_utc")
    )
    has_condition = bool(str(row.get("condition_id") or "").strip())
    has_question = bool(str(row.get("question") or "").strip())
    has_bracket = bool(
        str(
            row.get("bracket")
            or row.get("t_minus_1_no_bracket_c")
            or row.get("previous_official_bracket")
            or ""
        ).strip()
    )
    has_static_lineage = has_condition and has_question and has_bracket
    return not has_explicit_clock or not has_static_lineage


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


def _snapshot_lookup_key(row: dict[str, Any]) -> str:
    for key in ("execution_id", "order_id", "signal_id"):
        value = str(row.get(key) or "").strip()
        if value:
            return f"{key}:{value}"
    return "row:" + hashlib.sha256(
        json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _source_signal_key(row: dict[str, Any]) -> str:
    signal_id = str(row.get("signal_id") or "").strip()
    if signal_id:
        return f"signal_id:{signal_id}"
    return _snapshot_lookup_key(row)


def _record_matches_runtime_order(
    record: dict[str, Any], row: dict[str, Any]
) -> bool:
    token_id = str(row.get("token_id") or "").strip()
    if not token_id or token_id not in _row_token_ids(record):
        return False
    comparisons = (
        ("city", "city"),
        ("target_date", "event_date"),
        ("bracket", "bracket"),
    )
    for row_key, record_key in comparisons:
        expected = str(row.get(row_key) or "").strip()
        actual = str(record.get(record_key) or record.get(row_key) or "").strip()
        if expected and actual and expected != actual:
            return False
    def first_probability(candidate: dict[str, Any], keys: tuple[str, ...]) -> float | None:
        for key in keys:
            if candidate.get(key) in (None, ""):
                continue
            try:
                return float(candidate[key])
            except (TypeError, ValueError):
                continue
        return None

    raw_probability = first_probability(
        row,
        (
            "model_p_yes",
            "model_p_yes_used",
            "model_p_yes_raw",
            "model_token_probability",
        ),
    )
    snapshot_probability = first_probability(record, ("model_prob", "model_p_yes"))
    if (
        raw_probability is not None
        and raw_probability > 0.0
        and snapshot_probability is not None
        and abs(raw_probability - snapshot_probability) > 1e-9
    ):
        return False
    return True


def _build_snapshot_lookup(
    rows: list[dict[str, Any]], *, force: bool = False
) -> dict[str, dict[str, Any]]:
    """Recover one causal source snapshot per original runtime signal.

    Multiple lifecycle orders may share a signal.  They must inherit the
    snapshot available before the *first* order, not one later token/day row.
    Snapshot filenames are only an index; record clocks remain authoritative.
    A future record is never used as a static-lineage fallback.
    """

    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if force or _needs_snapshot_lookup(row):
            groups.setdefault(_source_signal_key(row), []).append(row)

    lookup: dict[str, dict[str, Any]] = {}
    for grouped_rows in groups.values():
        cutoffs = [
            value
            for row in grouped_rows
            if (
                value := _parse_dt(
                    row.get("created_at_utc")
                    or row.get("live_attempt_ts_utc")
                    or row.get("ts_utc")
                )
            )
            is not None
        ]
        if not cutoffs:
            continue
        cutoff = min(cutoffs)
        representative = min(
            grouped_rows,
            key=lambda row: _parse_dt(
                row.get("created_at_utc")
                or row.get("live_attempt_ts_utc")
                or row.get("ts_utc")
            )
            or datetime.max.replace(tzinfo=timezone.utc),
        )
        matched: dict[str, Any] | None = None
        for path in _snapshot_files_before(cutoff):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            records = payload.get("records") if isinstance(payload, dict) else None
            if not isinstance(records, list):
                continue
            for raw_record in records:
                if not isinstance(raw_record, dict):
                    continue
                record_ts = _parse_dt(
                    raw_record.get("snapshot_ts_utc") or raw_record.get("ts_utc")
                )
                if record_ts is None or record_ts > cutoff:
                    continue
                if not _record_matches_runtime_order(raw_record, representative):
                    continue
                matched = dict(raw_record)
                matched["_snapshot_source_path"] = str(path)
                break
            if matched is not None:
                break
        if matched is not None:
            for row in grouped_rows:
                lookup[_snapshot_lookup_key(row)] = matched
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


def _runtime_plan_config_id(raw: dict[str, Any], fallback: str) -> str:
    explicit = str(raw.get("config_id") or "").strip()
    if explicit:
        return explicit
    instance = str(raw.get("strategy_instance") or "").strip()
    try:
        shares = float(raw.get("size") or raw.get("shares") or 0.0)
    except (TypeError, ValueError):
        shares = 0.0
    execution_mode = str(raw.get("execution_mode") or "").strip()
    if instance == "current_yes_heat_death_tiny_live_v1":
        return "current_yes_heat_death_tiny_live_v1_fixed10"
    if instance == "current_yes_heat_death_tiny_live_h1_late_carry_v1":
        if execution_mode == "tiny_live_split_taker_maker_probe" or shares <= 5.0:
            return "current_yes_heat_death_tiny_live_h1_late_carry_v3_maker_first_chase"
        return "current_yes_heat_death_tiny_live_h1_late_carry_v1_fixed10"
    if instance == "current_yes_heat_death_tiny_live_h2_early_dislocation_v1":
        if execution_mode == "tiny_live_split_taker_maker_probe":
            return "current_yes_heat_death_tiny_live_h2_early_dislocation_v3_split_taker_maker"
        if shares <= 5.0:
            return "current_yes_heat_death_tiny_live_h2_early_dislocation_v2_fixed5"
        return "current_yes_heat_death_tiny_live_h2_early_dislocation_v1_fixed10"
    return fallback


def physical_exchange_order_id(raw: dict[str, Any]) -> str:
    for payload in (
        raw,
        raw.get("exchange_response") if isinstance(raw.get("exchange_response"), dict) else {},
    ):
        for key in ("order_id", "orderID", "clob_order_id"):
            value = str(payload.get(key) or "").strip()
            if value:
                return value
    response = raw.get("exchange_response")
    place = response.get("place") if isinstance(response, dict) and isinstance(response.get("place"), dict) else {}
    for key in ("order_id", "orderID", "clob_order_id"):
        value = str(place.get(key) or "").strip()
        if value:
            return value
    return ""


def _resolve_signal_snapshot_clock(
    row: dict[str, Any], snapshot: dict[str, Any]
) -> tuple[str, str, str, str | None]:
    """Resolve a causal signal clock without relabeling a future or order clock."""

    order_clock = _parse_dt(
        row.get("created_at_utc")
        or row.get("live_attempt_ts_utc")
        or row.get("ts_utc")
    )
    for field in ("decision_snapshot_ts_utc", "snapshot_ts_utc"):
        raw_value = row.get(field)
        if raw_value in (None, ""):
            continue
        parsed = _parse_dt(raw_value)
        if parsed is None:
            raise ValueError(f"{field} must be timezone-aware ISO-8601")
        if order_clock is not None and parsed > order_clock:
            raise ValueError(f"{field} cannot be after created_at_utc")
        return (
            utc_text(parsed, field=field, timespec="auto"),
            field,
            "explicit_causal",
            str(row.get("source_snapshot_path") or "").strip() or None,
        )

    snapshot_value = snapshot.get("snapshot_ts_utc") or snapshot.get("ts_utc")
    if snapshot_value not in (None, ""):
        parsed = _parse_dt(snapshot_value)
        if parsed is None:
            raise ValueError("reconstructed snapshot clock is invalid")
        if order_clock is not None and parsed > order_clock:
            raise ValueError("reconstructed snapshot clock cannot be after order clock")
        return (
            utc_text(parsed, field="snapshot_ts_utc", timespec="auto"),
            "strategy_snapshot_record",
            "reconstructed_causal",
            str(snapshot.get("_snapshot_source_path") or "").strip() or None,
        )

    # Some source-event runtimes expose a decision/event clock but no feature
    # snapshot. Preserve the clock while keeping the weaker semantics explicit.
    decision_proxy = _parse_dt(row.get("ts_utc"))
    if decision_proxy is not None:
        if order_clock is not None and decision_proxy > order_clock:
            raise ValueError("ts_utc cannot be after created_at_utc")
        return (
            utc_text(decision_proxy, field="ts_utc", timespec="auto"),
            "decision_clock_proxy",
            "proxy_not_feature_snapshot",
            str(row.get("source_snapshot_path") or "").strip() or None,
        )

    if order_clock is not None:
        return (
            utc_text(order_clock, field="created_at_utc", timespec="auto"),
            "order_clock_placeholder",
            "blocked_no_signal_snapshot",
            None,
        )
    raise ValueError("missing causal signal, decision, and order clock")


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

    row["created_at_utc"] = (
        row.get("created_at_utc") or row.get("live_attempt_ts_utc") or row.get("ts_utc")
    )
    (
        row["snapshot_ts_utc"],
        row["signal_snapshot_clock_basis"],
        row["signal_snapshot_lineage_status"],
        row["signal_snapshot_source_ref"],
    ) = _resolve_signal_snapshot_clock(row, snap)
    row["venue"] = row.get("venue") or "polymarket_clob"
    row["status"] = _runtime_order_status(row)
    row["bracket"] = (
        row.get("bracket")
        or row.get("t_minus_1_no_bracket_c")
        or row.get("previous_official_bracket")
    )
    row["city_pool"] = snap.get("city_pool") if snap.get("city_pool") in {"t1_trading", "t2_research"} else "t1_trading"
    row["icao"] = row.get("icao") or snap.get("icao") or CITY_ICAO.get(str(row.get("city") or ""), "")
    row["unit"] = row.get("unit") or row.get("market_unit") or snap.get("unit") or "C"
    row["signal_side"] = _side_from_runtime(row)
    row["order_side"] = _order_side_from_runtime(row)
    row["model_p_yes"] = (
        row.get("model_p_yes")
        or row.get("model_p_yes_used")
        or row.get("model_p_yes_raw")
        or row.get("model_token_probability")
        or snap.get("model_prob")
        or 0.0
    )
    row["market_price"] = row.get("market_price") or row.get("best_ask") or row.get("posted_price") or row.get("limit_price") or snap.get("entry_price")
    row["posted_price"] = row.get("posted_price") or row.get("limit_price") or row.get("best_ask")
    row["shares"] = row.get("shares") or row.get("size") or row.get("planned_shares")
    row["posted_notional"] = row.get("posted_notional") or row.get("submitted_notional_usd") or row.get("planned_notional_usd")
    row["notional"] = row.get("notional") or row.get("posted_notional")
    edge = row.get("edge") or snap.get("edge")
    if not edge and row["signal_side"] == "YES" and row["model_p_yes"] and row["market_price"]:
        edge = float(row["model_p_yes"]) - float(row["market_price"])
    row["edge"] = edge or 0.0
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
    if not row.get("condition_id") and _CONDITION_ID_RE.fullmatch(str(row.get("market_id") or "")):
        row["condition_id"] = row["market_id"]
    if not row.get("condition_id"):
        row["condition_id"] = snap.get("condition_id") or ""
    # Some runtime journals (including the first Cross NO V2 release) only
    # persisted Polymarket's condition id. Canonical signals still require a
    # stable market id; use the same condition-id fallback as other live jobs.
    row["market_id"] = row.get("market_id") or row.get("condition_id") or ""
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

    existing_physical_order_ids = {
        str(row[0])
        for row in conn.execute(
            """
            SELECT DISTINCT order_id
            FROM orders
            WHERE venue='polymarket_clob' AND COALESCE(order_id, '') <> ''
            """
        ).fetchall()
    }
    seen_physical_order_ids: set[str] = set()
    migration_orders: list[dict[str, Any]] = []
    for raw in raw_orders:
        physical_order_id = physical_exchange_order_id(raw)
        if order_kind == "live" and not physical_order_id:
            report.skip("non_exchange_order_lifecycle_attempt")
            continue
        if physical_order_id and (
            physical_order_id in existing_physical_order_ids
            or physical_order_id in seen_physical_order_ids
        ):
            report.skip("existing_physical_order")
            continue
        if physical_order_id:
            seen_physical_order_ids.add(physical_order_id)
        migration_orders.append(raw)
    if not migration_orders:
        return report

    snapshot_lookup = _build_snapshot_lookup(migration_orders)
    enriched: list[dict[str, Any]] = []
    for raw in migration_orders:
        try:
            enriched.append(
                _enrich_runtime_order(
                    raw,
                    snapshot_lookup.get(_snapshot_lookup_key(raw)),
                )
            )
        except ValueError as exc:
            report.skip(f"runtime_order_clock:{exc}")
    if not enriched:
        return report

    strategy_params = _strategy_params({}, enriched)
    declared_config_ids = {
        _runtime_plan_config_id(row, "")
        for row in enriched
        if _runtime_plan_config_id(row, "")
    }
    # A durable order journal spans config transitions.  Keep the run-level
    # config as a stable container identity, while each plan retains the
    # explicit config_id written by the runner at decision time.
    config_id = _strategy_config_id(strategy_params)
    instance_exists = conn.execute(
        "SELECT 1 FROM strategy_instance WHERE instance_id=?", (strategy_instance,)
    ).fetchone() is not None
    signals = []
    plans = []
    orders = []
    fills = []

    for raw in enriched:
        try:
            signal = _canonical_signal(raw, producer_system=producer_system, cycle_id=run_id)
            plan_config_id = _runtime_plan_config_id(raw, config_id)
            plan = _canonical_plan(
                raw,
                run_id=run_id,
                config_id=plan_config_id,
                signal_id=signal["signal_id"],
            )
            if not str(raw.get("execution_id") or "").strip():
                raw["execution_id"] = make_execution_id(
                    run_id=run_id,
                    plan_id=plan["plan_id"],
                    venue=raw.get("venue") or "polymarket_clob",
                    attempt_index=_stable_attempt_key(raw),
                )
            order = _canonical_order(raw, run_id=run_id, plan_id=plan["plan_id"])
            order["instance_id"] = strategy_instance if instance_exists else None
        except (ValueError, CanonicalValidationError) as exc:
            report.skip(f"runtime_order:{exc}")
            continue
        signals.append(signal)
        plans.append(plan)
        orders.append(order)
        # Fill lineage is owned by the shared CLOB sync that runs after order
        # migration. It replays the durable cache first, then uses the matched
        # exchange response only as a fallback for newly seen orders.

    cities = sorted({row["city"] for row in signals})
    models = sorted({row["model_version"] for row in signals})
    started = min((_snapshot_ts(row) for row in enriched if _snapshot_ts(row)), default=None)
    ended = max((str(row.get("created_at_utc") or "") for row in enriched if row.get("created_at_utc")), default=started)

    insert_strategy_config(
        conn,
        config_id,
        _strategy_config_name(strategy_params),
        strategy_params,
        strategy_key_for_params(strategy_params),
    )
    for declared_config_id in sorted(declared_config_ids):
        if conn.execute(
            "SELECT 1 FROM strategy_config WHERE config_id=?",
            (declared_config_id,),
        ).fetchone() is None:
            insert_strategy_config(
                conn,
                declared_config_id,
                declared_config_id,
                {**strategy_params, "declared_runtime_config_id": declared_config_id},
                strategy_key_for_params(strategy_params),
            )
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
    out_path = out_dir / "strategy_runtime_order_migration_latest.json"
    pending_path = out_dir / ".strategy_runtime_order_migration_latest.json.tmp"
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "reports": [report.as_dict() for report in reports],
    }
    pending_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    pending_path.replace(out_path)
    return out_path

from __future__ import annotations

import hashlib
import importlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.strategies.weather_edge_v1.tools.edge_orderbook_source import load_latest_orderbook_prices
from src.strategies.weather_edge_v1.tools.weather_predict_bridge import (
    DEFAULT_WEATHER_PREDICT_ROOT,
)


PROFILE_NAMES = ("weather_edge_b0p_v1", "weather_edge_b3f_hybrid_v1")
COMBO_NAMES = ("locked+equal", "dynamic+equal", "independent+equal")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _safe_str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _stable_hash(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _token_by_outcome(tokens: Iterable[Dict[str, Any]], outcome: str) -> Dict[str, Any]:
    wanted = outcome.strip().lower()
    for token in tokens:
        if _safe_str(token.get("outcome")).lower() == wanted:
            return dict(token)
    return {}


def _best_ask(token: Dict[str, Any], price_overrides: Optional[Dict[str, Dict[str, Any]]] = None) -> float:
    token_id = _safe_str(token.get("token_id"))
    if price_overrides and token_id in price_overrides:
        ask = _to_float(price_overrides[token_id].get("best_ask"), 0.0)
        if ask > 0:
            return ask
    book = token.get("orderbook") if isinstance(token.get("orderbook"), dict) else {}
    ask = _to_float(book.get("best_ask"), 0.0)
    if ask > 0:
        return ask
    return _to_float(token.get("outcome_price"), 0.0)


@dataclass(frozen=True)
class PaperBracket:
    market_id: str
    market_slug: str
    question: str
    label: str
    yes_token_id: str
    no_token_id: str
    yes_ask: float
    no_ask: float
    yes_price_source: str
    no_price_source: str


def extract_snapshot_brackets(
    snapshot: Dict[str, Any],
    *,
    weather_predict_root: Path,
    orderbook_paths: Iterable[Path | str] = (),
) -> List[PaperBracket]:
    root = weather_predict_root.expanduser().resolve()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    pm_edge_compare = importlib.import_module("pm_edge_compare")
    price_overrides = load_latest_orderbook_prices(orderbook_paths)

    brackets: List[PaperBracket] = []
    for market in snapshot.get("markets", []) or []:
        if not isinstance(market, dict):
            continue
        question = _safe_str(market.get("question"))
        label = pm_edge_compare._extract_bracket_label(question)
        if not label:
            continue
        yes = _token_by_outcome(market.get("tokens", []) or [], "Yes")
        no = _token_by_outcome(market.get("tokens", []) or [], "No")
        yes_ask = _best_ask(yes, price_overrides)
        no_ask = _best_ask(no, price_overrides)
        yes_book = yes.get("orderbook") if isinstance(yes.get("orderbook"), dict) else {}
        no_book = no.get("orderbook") if isinstance(no.get("orderbook"), dict) else {}
        yes_override = price_overrides.get(_safe_str(yes.get("token_id")), {})
        no_override = price_overrides.get(_safe_str(no.get("token_id")), {})
        brackets.append(
            PaperBracket(
                market_id=_safe_str(market.get("market_id")),
                market_slug=_safe_str(market.get("slug")),
                question=question,
                label=str(label),
                yes_token_id=_safe_str(yes.get("token_id")),
                no_token_id=_safe_str(no.get("token_id")),
                yes_ask=yes_ask,
                no_ask=no_ask,
                yes_price_source="local_orderbook_jsonl_best_ask"
                if _to_float(yes_override.get("best_ask"), 0.0) > 0
                else ("orderbook_best_ask" if _to_float(yes_book.get("best_ask"), 0.0) > 0 else "outcome_price"),
                no_price_source="local_orderbook_jsonl_best_ask"
                if _to_float(no_override.get("best_ask"), 0.0) > 0
                else ("orderbook_best_ask" if _to_float(no_book.get("best_ask"), 0.0) > 0 else "outcome_price"),
            )
        )
    return brackets


def _load_weather_predict_modules(root: Path, cache_dir: Path) -> Dict[str, Any]:
    root = root.expanduser().resolve()
    cache_dir = cache_dir.expanduser().resolve()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    calibration_backtest = importlib.import_module("calibration_backtest")
    calibration_backtest.CACHE_DIR = str(cache_dir)
    pm_edge_compare = importlib.import_module("pm_edge_compare")
    pm_edge_compare.CACHE_DIR = cache_dir
    edge_backtest = importlib.import_module("edge_backtest")
    roi_compare_branches = importlib.import_module("roi_compare_branches")

    # Reload after patching module globals so imports that captured CACHE_DIR see the target cache.
    calibration_backtest = importlib.reload(calibration_backtest)
    calibration_backtest.CACHE_DIR = str(cache_dir)
    pm_edge_compare = importlib.reload(pm_edge_compare)
    pm_edge_compare.CACHE_DIR = cache_dir
    edge_backtest = importlib.reload(edge_backtest)
    roi_compare_branches = importlib.reload(roi_compare_branches)
    return {
        "calibration_backtest": calibration_backtest,
        "pm_edge_compare": pm_edge_compare,
        "edge_backtest": edge_backtest,
        "roi_compare_branches": roi_compare_branches,
    }


def _model_errors_for_city(
    *,
    city: str,
    cfg: Dict[str, Any],
    target_date: str,
    model_key: str,
    calibration_backtest: Any,
) -> tuple[Optional[float], Optional[Any], str]:
    forecast_daily = calibration_backtest.load_model_daily(city, cfg["tz_offset"], model=model_key)
    if not forecast_daily and city == "LA":
        forecast_daily = calibration_backtest.load_model_daily("LosAngeles", cfg["tz_offset"], model=model_key)
    if target_date not in forecast_daily:
        return None, None, f"missing {model_key} forecast for {city} {target_date}"

    wu_daily = calibration_backtest.load_wu_obs(cfg["icao"])
    if not wu_daily:
        return None, None, f"missing WU observations for {cfg['icao']}"

    errors = [wu_daily[d] - forecast_daily[d] for d in sorted(forecast_daily) if d in wu_daily and d < target_date]
    if len(errors) < 30:
        return None, None, f"only {len(errors)} train errors for {city}; need >=30"

    numpy = importlib.import_module("numpy")
    return forecast_daily[target_date], numpy.array(errors), ""


def compute_profile_probabilities(
    *,
    city: str,
    target_date: str,
    unit: str,
    brackets: List[PaperBracket],
    weather_predict_root: Path = DEFAULT_WEATHER_PREDICT_ROOT,
    cache_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    root = weather_predict_root.expanduser().resolve()
    cache = (cache_dir or (root / "cache_global_full")).expanduser().resolve()
    modules = _load_weather_predict_modules(root, cache)
    calibration_backtest = modules["calibration_backtest"]
    pm_edge_compare = modules["pm_edge_compare"]
    roi_compare_branches = modules["roi_compare_branches"]

    cfg = pm_edge_compare.CITIES.get(city)
    if not cfg:
        return {"ok": False, "error": f"unknown city: {city}", "profiles": {}}

    bracket_input = [(b.label, b.yes_ask) for b in brackets if b.yes_ask > 0]
    if not bracket_input:
        return {"ok": False, "error": "no priced brackets", "profiles": {}}

    profiles: Dict[str, Any] = {}

    selector = modules["edge_backtest"].PerCityOptimal()
    b0p_model = selector.select(city)
    forecast, errors, reason = _model_errors_for_city(
        city=city,
        cfg=cfg,
        target_date=target_date,
        model_key=b0p_model,
        calibration_backtest=calibration_backtest,
    )
    if forecast is None or errors is None:
        profiles["weather_edge_b0p_v1"] = {"ok": False, "reason": reason, "model": b0p_model, "probabilities": []}
    else:
        probs = pm_edge_compare.compute_bracket_probs(forecast, errors, bracket_input, unit)
        profiles["weather_edge_b0p_v1"] = {
            "ok": True,
            "model": b0p_model,
            "forecast_max_f": forecast,
            "train_error_count": int(len(errors)),
            "probabilities": probs,
        }

    source = roi_compare_branches.EnsembleHybridSource()
    try:
        source.setup_for_city(city, cfg)
        if forecast is None or errors is None:
            profiles["weather_edge_b3f_hybrid_v1"] = {
                "ok": False,
                "reason": reason,
                "probabilities": [],
            }
        else:
            probs = source.compute_for_date(city, target_date, forecast, errors, bracket_input, unit)
            profiles["weather_edge_b3f_hybrid_v1"] = {
                "ok": True,
                "model": "B3f_Hybrid",
                "forecast_max_f": forecast,
                "train_error_count": int(len(errors)),
                "probabilities": probs,
            }
    except Exception as exc:
        profiles["weather_edge_b3f_hybrid_v1"] = {
            "ok": False,
            "reason": f"{type(exc).__name__}: {exc}",
            "probabilities": [],
        }

    return {"ok": True, "error": "", "profiles": profiles}


def build_paper_decisions(
    *,
    snapshot: Dict[str, Any],
    city: str,
    target_date: str,
    unit: str,
    profile_result: Dict[str, Any],
    brackets: List[PaperBracket],
    min_edge: float = 0.10,
    combos: Iterable[str] = COMBO_NAMES,
) -> List[Dict[str, Any]]:
    fetched_at = _safe_str((snapshot.get("meta") or {}).get("fetched_at_utc")) or _utc_now_iso()
    event = snapshot.get("event") if isinstance(snapshot.get("event"), dict) else {}
    bracket_by_label = {b.label: b for b in brackets}
    out: List[Dict[str, Any]] = []

    for profile_name, profile in (profile_result.get("profiles") or {}).items():
        if not profile.get("ok"):
            out.append(
                {
                    "record_type": "profile_skipped",
                    "created_at_utc": _utc_now_iso(),
                    "strategy": "weather_edge_v1",
                    "profile": profile_name,
                    "city": city,
                    "target_date": target_date,
                    "reason": _safe_str(profile.get("reason")),
                }
            )
            continue
        for prob in profile.get("probabilities", []) or []:
            label = _safe_str(prob.get("bracket"))
            bracket = bracket_by_label.get(label)
            if bracket is None:
                continue
            model_prob = _to_float(prob.get("model_pct"), 0.0)
            yes_edge = model_prob - bracket.yes_ask if bracket.yes_ask > 0 else -999.0
            no_edge = (1.0 - model_prob) - bracket.no_ask if bracket.no_ask > 0 else -999.0
            if yes_edge >= no_edge:
                side = "BUY_YES"
                token_id = bracket.yes_token_id
                market_price = bracket.yes_ask
                edge = yes_edge
                price_source = bracket.yes_price_source
            else:
                side = "BUY_NO"
                token_id = bracket.no_token_id
                market_price = bracket.no_ask
                edge = no_edge
                price_source = bracket.no_price_source
            if edge < min_edge or market_price <= 0 or not token_id:
                continue
            for combo in combos:
                base = {
                    "strategy": "weather_edge_v1",
                    "profile": profile_name,
                    "combo": combo,
                    "city": city,
                    "target_date": target_date,
                    "unit": unit,
                    "event_id": _safe_str(event.get("event_id")),
                    "event_slug": _safe_str(event.get("slug")),
                    "market_id": bracket.market_id,
                    "market_slug": bracket.market_slug,
                    "question": bracket.question,
                    "bracket": label,
                    "token_id": token_id,
                    "side": side,
                    "model_probability_yes": round(model_prob, 6),
                    "market_price": round(market_price, 6),
                    "edge": round(edge, 6),
                    "min_edge": round(min_edge, 6),
                    "price_source": price_source,
                    "snapshot_fetched_at_utc": fetched_at,
                    "status": "open",
                }
                out.append(
                    {
                        "record_type": "paper_decision",
                        "paper_id": _stable_hash(base),
                        "created_at_utc": _utc_now_iso(),
                        **base,
                    }
                )
    return out


def append_jsonl_dedup(path: Path, rows: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = set()
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                key = _safe_str(item.get("paper_id")) or _stable_hash(item)
                existing.add(key)

    written = 0
    skipped = 0
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            key = _safe_str(row.get("paper_id")) or _stable_hash(row)
            if key in existing:
                skipped += 1
                continue
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            existing.add(key)
            written += 1
    return {"written": written, "skipped_existing": skipped}


def load_snapshot(*, target_market: str = "", snapshot_json: str = "", orderbook_top_n: int = 10) -> Dict[str, Any]:
    if snapshot_json.strip():
        return json.loads(Path(snapshot_json).expanduser().read_text(encoding="utf-8"))
    if not target_market.strip():
        raise ValueError("either target_market or snapshot_json is required")
    from src.strategies.weather_edge_v1.tools.market_query_tool import build_market_snapshot

    return build_market_snapshot(
        target_market=target_market.strip(),
        include_orderbook=True,
        orderbook_top_n=orderbook_top_n,
    )

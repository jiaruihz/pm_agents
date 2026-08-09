#!/usr/bin/env python3
"""Frozen Korea intraday market-residual baseline.

Target:
    P(the PIT favourite exact bracket settles YES | market + AMOS path state)

The replay uses archived full-ladder books, first-seen Korea AMOS state, and
canonical settlement labels.  Repeated snapshots are date-equal-weighted for
probability scores; the trade policy permits at most one order per city-day.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import gzip
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


ROOT = Path(__file__).resolve().parents[3]
RUNTIME = Path("/Volumes/jrs/weather_data_feed_service_runtime")
DEFAULT_STATE = RUNTIME / "research/korea_first_seen_state_v1_backfill/checkpoints"
DEFAULT_BOOK = RUNTIME / "full_ladder_output/orderbook_snapshots"
DEFAULT_TARGETED_BOOK = RUNTIME / "targeted_output/orderbook_snapshots"
DEFAULT_PAPER_SNAPSHOTS = RUNTIME / "targeted_output/paper_snapshots"
DEFAULT_ATLAS = Path(
    "/Volumes/jrs/pm_agents/research/weather_book_microstructure_atlas/v1/"
    "snapshot=20260729T161422Z/state_rows.csv.gz"
)
DEFAULT_DB = ROOT / "runtime/weather.db"
DEFAULT_OUT = (
    ROOT / "docs/analysis/2026-07/generated/korea_intraday_residual_baseline_v1"
)
DEFAULT_REPORT = (
    ROOT / "docs/analysis/2026-07/"
    "2026-07-30-korea-intraday-residual-baseline-v1.md"
)
CITIES = {"Seoul", "Busan"}
EPS = 1e-6


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def utc(value: Any) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(
        timezone.utc
    )


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def effective_yes_book(
    yes: dict[str, Any] | None, no: dict[str, Any] | None
) -> dict[str, float | None]:
    yes_summary = (yes or {}).get("summary") or {}
    no_summary = (no or {}).get("summary") or {}
    yes_bid = finite(yes_summary.get("best_bid"))
    yes_ask = finite(yes_summary.get("best_ask"))
    no_bid = finite(no_summary.get("best_bid"))
    no_ask = finite(no_summary.get("best_ask"))
    bid_options = [
        (yes_bid, finite(yes_summary.get("bid_size"))),
        (
            1.0 - no_ask if no_ask is not None else None,
            finite(no_summary.get("ask_size")),
        ),
    ]
    ask_options = [
        (yes_ask, finite(yes_summary.get("ask_size"))),
        (
            1.0 - no_bid if no_bid is not None else None,
            finite(no_summary.get("bid_size")),
        ),
    ]
    bids = [item for item in bid_options if item[0] is not None]
    asks = [item for item in ask_options if item[0] is not None]
    best_bid = max(bids, key=lambda item: float(item[0])) if bids else (None, None)
    best_ask = min(asks, key=lambda item: float(item[0])) if asks else (None, None)
    bid, bid_size = best_bid
    ask, ask_size = best_ask
    mid = (
        (float(bid) + float(ask)) / 2
        if bid is not None and ask is not None and float(ask) >= float(bid)
        else None
    )
    return {
        "yes_bid": bid,
        "yes_ask": ask,
        "yes_bid_size": bid_size,
        "yes_ask_size": ask_size,
        "yes_mid": mid,
        "no_ask": 1.0 - float(bid) if bid is not None else None,
        "no_ask_size": bid_size,
    }


def load_books(root: Path, start: str, end: str) -> pd.DataFrame:
    output: list[dict[str, Any]] = []
    for path in sorted(root.glob("*/*.jsonl.gz")):
        if path.parent.name < start or path.parent.name > end:
            continue
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("city") not in CITIES or row.get("status") != "ok":
                    continue
                snapshot = str(row.get("snapshot_ts_utc") or "")
                slug = str(row.get("event_slug") or "")
                if snapshot and slug:
                    grouped[(snapshot, slug)].append(row)
        for (snapshot_text, slug), rows in grouped.items():
            first = rows[0]
            target_date = str(
                first.get("market_local_date") or first.get("event_date") or ""
            )
            if not (start <= target_date <= end):
                continue
            by_condition: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
            for row in rows:
                condition = str(row.get("condition_id") or "")
                outcome = str(row.get("outcome") or "").lower()
                if condition and outcome in {"yes", "no"}:
                    by_condition[condition][outcome] = row
            brackets = []
            for outcomes in by_condition.values():
                source = outcomes.get("yes") or outcomes.get("no")
                quote = effective_yes_book(outcomes.get("yes"), outcomes.get("no"))
                if source and quote["yes_mid"] is not None:
                    brackets.append(
                        {
                            "bracket": str(source.get("bracket") or ""),
                            **quote,
                        }
                    )
            if not brackets:
                continue
            favorite = max(brackets, key=lambda item: float(item["yes_mid"]))
            snapshot = utc(snapshot_text)
            local = snapshot.astimezone(
                __import__("zoneinfo").ZoneInfo("Asia/Seoul")
            )
            if not 9 <= local.hour < 18:
                continue
            output.append(
                {
                    "city": str(first["city"]),
                    "target_date": target_date,
                    "snapshot_ts": snapshot,
                    "local_hour": local.hour + local.minute / 60,
                    "favorite_bracket": favorite["bracket"],
                    **{key: favorite[key] for key in favorite if key != "bracket"},
                    "book_evidence": "archived_l2_orderbook",
                }
            )
    frame = pd.DataFrame(output)
    return frame.sort_values(["city", "target_date", "snapshot_ts"]).drop_duplicates(
        ["city", "target_date", "snapshot_ts"]
    )


def load_atlas_books(path: Path, start: str, end: str) -> pd.DataFrame:
    frame = pd.read_csv(
        path,
        usecols=[
            "city",
            "target_date",
            "snapshot_ts_utc",
            "local_hour",
            "favorite_bracket",
            "favorite_mid",
            "favorite_spread",
            "favorite_ask_depth_usd",
        ],
    )
    frame = frame[
        frame["city"].isin(CITIES)
        & frame["target_date"].between(start, end)
        & frame["local_hour"].between(9, 17)
        & frame["favorite_mid"].notna()
        & frame["favorite_spread"].notna()
    ].copy()
    frame["snapshot_ts"] = pd.to_datetime(frame.pop("snapshot_ts_utc"), utc=True)
    frame["yes_mid"] = frame.pop("favorite_mid")
    frame["yes_bid"] = frame["yes_mid"] - frame["favorite_spread"] / 2
    frame["yes_ask"] = frame["yes_mid"] + frame["favorite_spread"] / 2
    frame["yes_ask_size"] = (
        frame["favorite_ask_depth_usd"] / frame["yes_ask"]
    )
    frame["yes_bid_size"] = np.nan
    frame["no_ask"] = 1 - frame["yes_bid"]
    frame["no_ask_size"] = np.nan
    frame["book_evidence"] = "full_ladder_atlas"
    return frame.sort_values(["city", "target_date", "snapshot_ts"]).drop_duplicates(
        ["city", "target_date", "snapshot_ts"]
    )


def load_paper_snapshot_books(root: Path, start: str, end: str) -> pd.DataFrame:
    output = []
    start_compact = start.replace("-", "")
    end_compact = end.replace("-", "")
    hourly_paths: dict[tuple[str, str], Path] = {}
    for path in sorted(root.glob("snapshot_*.json")):
        match = re.search(r"snapshot_(\d{8})_", path.name)
        if not match or not start_compact <= match.group(1) <= end_compact:
            continue
        time_match = re.search(r"snapshot_\d{8}_(\d{2})\d{2}", path.name)
        if not time_match:
            continue
        hour = int(time_match.group(1))
        # Filenames use Beijing local time; even 08..16 maps to Korea 09..17.
        if hour not in {8, 10, 12, 14, 16}:
            continue
        key = (match.group(1), time_match.group(1))
        hourly_paths.setdefault(key, path)
    for path in hourly_paths.values():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        snapshot_text = payload.get("ts_utc")
        if not snapshot_text:
            continue
        snapshot = utc(snapshot_text)
        local = snapshot.astimezone(
            __import__("zoneinfo").ZoneInfo("Asia/Seoul")
        )
        if not 9 <= local.hour < 18:
            continue
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in payload.get("records") or []:
            city = str(row.get("city") or "")
            target_date = str(
                row.get("target_date") or row.get("event_date") or ""
            )
            if city in CITIES and start <= target_date <= end:
                grouped[(city, target_date)].append(row)
        for (city, target_date), rows in grouped.items():
            brackets = []
            for row in rows:
                yes_bid = finite(row.get("yes_best_bid"))
                yes_ask = finite(row.get("yes_best_ask"))
                no_bid = finite(row.get("no_best_bid"))
                no_ask = finite(row.get("no_best_ask"))
                bid_candidates = [
                    (yes_bid, finite(row.get("yes_bid_size"))),
                    (
                        1 - no_ask if no_ask is not None else None,
                        finite(row.get("no_ask_size")),
                    ),
                ]
                ask_candidates = [
                    (yes_ask, finite(row.get("yes_ask_size"))),
                    (
                        1 - no_bid if no_bid is not None else None,
                        finite(row.get("no_bid_size")),
                    ),
                ]
                bids = [item for item in bid_candidates if item[0] is not None]
                asks = [item for item in ask_candidates if item[0] is not None]
                if not bids or not asks:
                    continue
                bid, bid_size = max(bids, key=lambda item: float(item[0]))
                ask, ask_size = min(asks, key=lambda item: float(item[0]))
                if float(ask) < float(bid):
                    continue
                brackets.append(
                    {
                        "favorite_bracket": str(row.get("bracket") or ""),
                        "yes_bid": bid,
                        "yes_ask": ask,
                        "yes_bid_size": bid_size,
                        "yes_ask_size": ask_size,
                        "yes_mid": (float(bid) + float(ask)) / 2,
                        "no_ask": (
                            no_ask
                            if no_ask is not None
                            else 1 - float(bid)
                        ),
                        "no_ask_size": finite(row.get("no_ask_size"))
                        or bid_size,
                    }
                )
            if not brackets:
                continue
            favorite = max(brackets, key=lambda row: float(row["yes_mid"]))
            output.append(
                {
                    "city": city,
                    "target_date": target_date,
                    "snapshot_ts": snapshot,
                    "local_hour": local.hour + local.minute / 60,
                    **favorite,
                    "book_evidence": "paper_snapshot_direct_book",
                }
            )
    if not output:
        return pd.DataFrame()
    return (
        pd.DataFrame(output)
        .sort_values(["city", "target_date", "snapshot_ts"])
        .drop_duplicates(["city", "target_date", "snapshot_ts"])
    )


def load_states(root: Path, start: str, end: str) -> pd.DataFrame:
    output: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.jsonl")):
        if path.stem < start or path.stem > end:
            continue
        for row in iter_jsonl(path):
            if row.get("city") not in CITIES:
                continue
            windows = row.get("path_windows") or {}
            output.append(
                {
                    "city": str(row["city"]),
                    "target_date": str(row["target_date"]),
                    "state_ts": utc(row["source_available_at_utc"]),
                    "routine_running_max": finite(
                        row.get("routine_running_max_market_value")
                    ),
                    "source_temp": finite(row.get("source_temp_c")),
                    "source_running_max": finite(row.get("source_running_max_c")),
                    "minutes_since_max": finite(
                        row.get("minutes_since_source_running_max")
                    ),
                    "relative_humidity": finite(row.get("relative_humidity_pct")),
                    "dewpoint_depression": finite(
                        row.get("dewpoint_depression_c")
                    ),
                    "wind_speed": finite(
                        row.get("source_wind_speed_kt")
                        or row.get("metar_wind_speed_kt")
                    ),
                    "runway_spread": finite(row.get("runway_temp_spread_c")),
                    "cloud_layers": finite(row.get("cloud_layer_count")),
                    "precip_intensity": finite(row.get("precip_intensity_code")),
                    "slope_15m": finite(
                        (windows.get("15m") or {}).get("temp_slope_c_per_hour")
                    ),
                    "slope_30m": finite(
                        (windows.get("30m") or {}).get("temp_slope_c_per_hour")
                    ),
                    "slope_60m": finite(
                        (windows.get("60m") or {}).get("temp_slope_c_per_hour")
                    ),
                }
            )
    frame = pd.DataFrame(output)
    return frame.sort_values(["city", "target_date", "state_ts"]).drop_duplicates(
        ["city", "target_date", "state_ts"], keep="last"
    )


def load_winners(db: Path, start: str, end: str) -> dict[tuple[str, str], str]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=20)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=20000")
    rows = conn.execute(
        """
        SELECT city, target_date, bracket
        FROM settlement_outcomes
        WHERE city IN ('Seoul','Busan')
          AND target_date BETWEEN ? AND ?
          AND final_price >= 0.999
        """,
        (start, end),
    ).fetchall()
    conn.close()
    return {(str(city), str(day)): str(bracket) for city, day, bracket in rows}


def join_frame(
    books: pd.DataFrame,
    states: pd.DataFrame,
    winners: dict[tuple[str, str], str],
) -> pd.DataFrame:
    pieces = []
    for key, book_group in books.groupby(["city", "target_date"]):
        state_group = states[
            (states["city"] == key[0]) & (states["target_date"] == key[1])
        ]
        if state_group.empty:
            continue
        merged = pd.merge_asof(
            book_group.sort_values("snapshot_ts"),
            state_group.drop(columns=["city", "target_date"]).sort_values("state_ts"),
            left_on="snapshot_ts",
            right_on="state_ts",
            direction="backward",
            tolerance=pd.Timedelta("20min"),
        )
        pieces.append(merged)
    frame = pd.concat(pieces, ignore_index=True)
    frame = frame[frame["state_ts"].notna() & frame["yes_mid"].notna()].copy()
    frame["winner"] = [
        winners.get((city, day))
        for city, day in zip(frame["city"], frame["target_date"])
    ]
    frame = frame[frame["winner"].notna()].copy()
    frame["label"] = (
        frame["favorite_bracket"].astype(str) == frame["winner"].astype(str)
    ).astype(int)
    frame["market_p"] = frame["yes_mid"].clip(EPS, 1 - EPS)
    frame["market_logit"] = np.log(frame["market_p"] / (1 - frame["market_p"]))
    frame["favorite_value"] = pd.to_numeric(
        frame["favorite_bracket"].str.extract(r"(-?\d+)")[0], errors="coerce"
    )
    frame["favorite_offset"] = (
        frame["favorite_value"] - frame["routine_running_max"]
    )
    frame["source_margin"] = frame["source_temp"] - frame["routine_running_max"]
    counts = frame.groupby(["city", "target_date"])["label"].transform("size")
    frame["date_equal_weight"] = 1.0 / counts
    return frame


NUMERIC = [
    "market_logit",
    "local_hour",
    "favorite_offset",
    "source_margin",
    "source_running_max",
    "minutes_since_max",
    "relative_humidity",
    "dewpoint_depression",
    "wind_speed",
    "runway_spread",
    "cloud_layers",
    "precip_intensity",
    "slope_15m",
    "slope_30m",
    "slope_60m",
]
CATEGORICAL = ["city"]


def make_model(numeric: list[str], categorical: list[str]) -> Pipeline:
    transformers = [
        (
            "num",
            Pipeline(
                [
                    ("impute", SimpleImputer(strategy="median")),
                    ("scale", StandardScaler()),
                ]
            ),
            numeric,
        )
    ]
    if categorical:
        transformers.append(
            (
                "cat",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="most_frequent")),
                        (
                            "onehot",
                            OneHotEncoder(handle_unknown="ignore", drop="if_binary"),
                        ),
                    ]
                ),
                categorical,
            )
        )
    return Pipeline(
        [
            ("features", ColumnTransformer(transformers)),
            ("model", LogisticRegression(C=0.1, max_iter=2000)),
        ]
    )


def weighted_score(y: np.ndarray, p: np.ndarray, w: np.ndarray) -> dict[str, float]:
    clipped = np.clip(p, EPS, 1 - EPS)
    return {
        "brier": float(np.average((p - y) ** 2, weights=w)),
        "logloss": float(
            np.average(-(y * np.log(clipped) + (1 - y) * np.log(1 - clipped)), weights=w)
        ),
    }


def date_delta_ci(
    frame: pd.DataFrame, candidate: str, baseline: str, metric: str, seed: int
) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    values = []
    for _, group in frame.groupby("target_date"):
        y = group["label"].to_numpy(float)
        if metric == "brier":
            candidate_loss = (group[candidate].to_numpy(float) - y) ** 2
            baseline_loss = (group[baseline].to_numpy(float) - y) ** 2
        else:
            cp = np.clip(group[candidate].to_numpy(float), EPS, 1 - EPS)
            bp = np.clip(group[baseline].to_numpy(float), EPS, 1 - EPS)
            candidate_loss = -(y * np.log(cp) + (1 - y) * np.log(1 - cp))
            baseline_loss = -(y * np.log(bp) + (1 - y) * np.log(1 - bp))
        values.append(float(np.mean(candidate_loss - baseline_loss)))
    values_array = np.asarray(values)
    samples = rng.choice(values_array, size=(5000, len(values_array)), replace=True)
    return tuple(float(value) for value in np.quantile(samples.mean(axis=1), [0.025, 0.975]))


def fee_per_share(price: float) -> float:
    return 0.05 * price * (1 - price)


def select_trades(frame: pd.DataFrame, threshold: float = 0.02) -> pd.DataFrame:
    rows = []
    for (_, _), group in frame.sort_values("snapshot_ts").groupby(
        ["city", "target_date"], sort=False
    ):
        for _, row in group.iterrows():
            yes_ask = finite(row["yes_ask"])
            no_ask = finite(row["no_ask"])
            yes_size = finite(row["yes_ask_size"])
            no_size = finite(row["no_ask_size"])
            p_yes = float(row["weather_p"])
            options = []
            if yes_ask is not None and yes_size is not None:
                cost = yes_ask + fee_per_share(yes_ask)
                options.append(("YES", p_yes - cost, yes_ask, yes_size))
            if no_ask is not None and no_size is not None:
                cost = no_ask + fee_per_share(no_ask)
                options.append(("NO", (1 - p_yes) - cost, no_ask, no_size))
            if not options:
                continue
            side, edge, ask, available = max(options, key=lambda item: item[1])
            if edge < threshold or not 0.02 <= float(ask) <= 0.97:
                continue
            shares = min(5.0, float(available))
            if shares <= 0:
                continue
            win = int(row["label"]) if side == "YES" else 1 - int(row["label"])
            cost = shares * (float(ask) + fee_per_share(float(ask)))
            pnl = shares * win - cost
            rows.append(
                {
                    "city": row["city"],
                    "target_date": row["target_date"],
                    "snapshot_ts_utc": row["snapshot_ts"].isoformat(),
                    "favorite_bracket": row["favorite_bracket"],
                    "winner": row["winner"],
                    "side": side,
                    "p_win": p_yes if side == "YES" else 1 - p_yes,
                    "ask": float(ask),
                    "model_edge_after_fee": float(edge),
                    "shares": shares,
                    "cost_usd": cost,
                    "win": win,
                    "pnl_usd": pnl,
                }
            )
            break
    return pd.DataFrame(rows)


def trade_roi_ci(trades: pd.DataFrame, seed: int = 1702) -> tuple[float, float] | None:
    if trades.empty or trades["target_date"].nunique() < 2:
        return None
    daily = trades.groupby("target_date")[["pnl_usd", "cost_usd"]].sum()
    values = daily.to_numpy(float)
    rng = np.random.default_rng(seed)
    sampled = rng.integers(0, len(values), size=(5000, len(values)))
    pnl = values[sampled, 0].sum(axis=1)
    cost = values[sampled, 1].sum(axis=1)
    roi = pnl / cost
    return tuple(float(value) for value in np.quantile(roi, [0.025, 0.975]))


def mechanical_yes_frame(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    output["fee_per_share"] = (
        0.05 * output["yes_ask"] * (1 - output["yes_ask"])
    )
    output["model_edge_after_fee"] = (
        output["weather_p"] - output["yes_ask"] - output["fee_per_share"]
        if "weather_p" in output
        else np.nan
    )
    output["shares"] = np.minimum(5.0, output["yes_ask_size"])
    output["cost_usd"] = output["shares"] * (
        output["yes_ask"] + output["fee_per_share"]
    )
    output["pnl_usd"] = output["shares"] * output["label"] - output["cost_usd"]
    return output[
        output["yes_ask"].notna()
        & output["yes_ask_size"].gt(0)
        & output["cost_usd"].gt(0)
    ].copy()


def summarize_mechanical(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "opportunities_or_orders": 0,
            "target_dates": 0,
            "city_days": 0,
            "wins": 0,
            "win_rate": None,
            "cost_usd": 0.0,
            "pnl_usd": 0.0,
            "roi": None,
            "roi_ci95_target_date_block": None,
        }
    cost = float(frame["cost_usd"].sum())
    return {
        "opportunities_or_orders": int(len(frame)),
        "target_dates": int(frame["target_date"].nunique()),
        "city_days": int(
            frame[["city", "target_date"]].drop_duplicates().shape[0]
        ),
        "wins": int(frame["label"].sum()),
        "win_rate": float(frame["label"].mean()),
        "cost_usd": cost,
        "pnl_usd": float(frame["pnl_usd"].sum()),
        "roi": float(frame["pnl_usd"].sum() / cost),
        "roi_ci95_target_date_block": trade_roi_ci(frame),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--book-root", type=Path, default=DEFAULT_BOOK)
    parser.add_argument(
        "--targeted-book-root", type=Path, default=DEFAULT_TARGETED_BOOK
    )
    parser.add_argument(
        "--paper-snapshot-root", type=Path, default=DEFAULT_PAPER_SNAPSHOTS
    )
    parser.add_argument("--atlas", type=Path, default=DEFAULT_ATLAS)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--start-date", default="2026-07-15")
    parser.add_argument("--train-end", default="2026-07-21")
    parser.add_argument("--end-date", default="2026-07-28")
    args = parser.parse_args()

    atlas_books = (
        load_atlas_books(args.atlas, args.start_date, args.end_date)
        if args.atlas.exists()
        else load_books(args.book_root, args.start_date, args.end_date)
    )
    book_parts = [atlas_books]
    if args.targeted_book_root.exists() and args.start_date < "2026-07-15":
        early_end = min(args.end_date, "2026-07-14")
        book_parts.append(
            load_books(args.targeted_book_root, args.start_date, early_end)
        )
    if (
        args.targeted_book_root.exists()
        and args.end_date >= "2026-07-28"
        and args.start_date <= "2026-07-28"
    ):
        book_parts.append(
            load_books(args.targeted_book_root, "2026-07-28", "2026-07-28")
        )
    if args.paper_snapshot_root.exists():
        paper_parts = []
        if args.start_date < "2026-07-15":
            paper_parts.append(
                load_paper_snapshot_books(
                    args.paper_snapshot_root,
                    args.start_date,
                    min(args.end_date, "2026-07-14"),
                )
            )
        if args.end_date >= "2026-07-28" and args.start_date <= "2026-07-28":
            paper_parts.append(
                load_paper_snapshot_books(
                    args.paper_snapshot_root,
                    "2026-07-28",
                    "2026-07-28",
                )
            )
        book_parts.extend(part for part in paper_parts if not part.empty)
    books = (
        pd.concat([part for part in book_parts if not part.empty], ignore_index=True)
        .sort_values(["city", "target_date", "snapshot_ts"])
        .drop_duplicates(["city", "target_date", "snapshot_ts"], keep="last")
    )
    states = load_states(args.state_root, args.start_date, args.end_date)
    winners = load_winners(args.db, args.start_date, args.end_date)
    frame = join_frame(books, states, winners)
    train = frame[frame["target_date"] <= args.train_end].copy()
    holdout = frame[frame["target_date"] > args.train_end].copy()
    if train["label"].nunique() < 2 or holdout.empty:
        raise RuntimeError("insufficient train/holdout labels")

    market_cal = make_model(["market_logit"], [])
    weather = make_model(NUMERIC, CATEGORICAL)
    market_cal.fit(
        train[["market_logit"]],
        train["label"],
        model__sample_weight=train["date_equal_weight"],
    )
    weather.fit(
        train[NUMERIC + CATEGORICAL],
        train["label"],
        model__sample_weight=train["date_equal_weight"],
    )
    holdout["market_cal_p"] = market_cal.predict_proba(
        holdout[["market_logit"]]
    )[:, 1]
    holdout["weather_p"] = weather.predict_proba(
        holdout[NUMERIC + CATEGORICAL]
    )[:, 1]

    y = holdout["label"].to_numpy(float)
    w = holdout["date_equal_weight"].to_numpy(float)
    scores = {
        name: weighted_score(y, holdout[column].to_numpy(float), w)
        for name, column in {
            "raw_market": "market_p",
            "market_calibrator": "market_cal_p",
            "market_plus_weather": "weather_p",
        }.items()
    }
    deltas = {
        metric: {
            "point": scores["market_plus_weather"][metric]
            - scores["raw_market"][metric],
            "ci95": date_delta_ci(
                holdout, "weather_p", "market_p", metric, seed=1701
            ),
        }
        for metric in ("brier", "logloss")
    }
    full_universe = mechanical_yes_frame(frame)
    full_universe_first_city_day = (
        full_universe.sort_values("snapshot_ts")
        .groupby(["city", "target_date"], as_index=False)
        .head(1)
    )
    full_universe_summary = summarize_mechanical(full_universe)
    full_universe_first_summary = summarize_mechanical(
        full_universe_first_city_day
    )
    broad = mechanical_yes_frame(holdout)
    first_city_day = (
        broad.sort_values("snapshot_ts")
        .groupby(["city", "target_date"], as_index=False)
        .head(1)
    )
    broad_summary = summarize_mechanical(broad)
    first_city_day_summary = summarize_mechanical(first_city_day)
    city_distribution = [
        {"city": city, **summarize_mechanical(group)}
        for city, group in broad.groupby("city")
    ]
    edge_bins = [-np.inf, -0.10, -0.05, 0.0, 0.02, 0.05, 0.10, np.inf]
    broad["edge_bin"] = pd.cut(
        broad["model_edge_after_fee"], edge_bins, right=False
    )
    edge_distribution = [
        {"edge_bin": str(edge_bin), **summarize_mechanical(group)}
        for edge_bin, group in broad.groupby("edge_bin", observed=True, sort=True)
    ]
    policy_sensitivity = []
    for threshold in (None, 0.0, 0.02, 0.05):
        selected = (
            broad
            if threshold is None
            else broad[broad["model_edge_after_fee"] >= threshold]
        )
        for scope, scoped in (
            ("all_opportunity_orders", selected),
            (
                "first_order_per_city_day",
                selected.sort_values("snapshot_ts")
                .groupby(["city", "target_date"], as_index=False)
                .head(1),
            ),
        ):
            policy_sensitivity.append(
                {
                    "edge_threshold": "all" if threshold is None else threshold,
                    "scope": scope,
                    **summarize_mechanical(scoped),
                }
            )
    trades = select_trades(holdout)
    roi_ci = trade_roi_ci(trades)
    city_trade_summary = []
    if len(trades):
        for city, group in trades.groupby("city"):
            cost = float(group["cost_usd"].sum())
            city_trade_summary.append(
                {
                    "city": city,
                    "signals": int(len(group)),
                    "dates": int(group["target_date"].nunique()),
                    "wins": int(group["win"].sum()),
                    "cost_usd": cost,
                    "pnl_usd": float(group["pnl_usd"].sum()),
                    "roi": float(group["pnl_usd"].sum() / cost),
                }
            )
    trade_summary = {
        "signals": int(len(trades)),
        "dates": int(trades["target_date"].nunique()) if len(trades) else 0,
        "city_days": int(
            trades[["city", "target_date"]].drop_duplicates().shape[0]
        )
        if len(trades)
        else 0,
        "wins": int(trades["win"].sum()) if len(trades) else 0,
        "cost_usd": float(trades["cost_usd"].sum()) if len(trades) else 0.0,
        "pnl_usd": float(trades["pnl_usd"].sum()) if len(trades) else 0.0,
        "roi_ci95_target_date_block": roi_ci,
        "by_city": city_trade_summary,
    }
    trade_summary["roi"] = (
        trade_summary["pnl_usd"] / trade_summary["cost_usd"]
        if trade_summary["cost_usd"]
        else None
    )
    expected_city_days = {
        (city, target_date)
        for target_date in pd.date_range(
            args.start_date, args.end_date
        ).strftime("%Y-%m-%d")
        for city in ("Seoul", "Busan")
    }
    observed_city_days = set(
        zip(
            full_universe["city"].astype(str),
            full_universe["target_date"].astype(str),
        )
    )
    summary = {
        "schema_version": "korea_intraday_residual_baseline_v1",
        "target": "P(PIT favourite exact bracket settles YES)",
        "train_window": [args.start_date, args.train_end],
        "holdout_window": [
            str(holdout["target_date"].min()),
            str(holdout["target_date"].max()),
        ],
        "train_rows": len(train),
        "train_dates": int(train["target_date"].nunique()),
        "train_city_days": int(
            train[["city", "target_date"]].drop_duplicates().shape[0]
        ),
        "holdout_rows": len(holdout),
        "holdout_dates": int(holdout["target_date"].nunique()),
        "holdout_city_days": int(
            holdout[["city", "target_date"]].drop_duplicates().shape[0]
        ),
        "requested_holdout_dates_without_joined_rows": sorted(
            set(
                pd.date_range(
                    pd.Timestamp(args.train_end) + pd.Timedelta(days=1),
                    pd.Timestamp(args.end_date),
                ).strftime("%Y-%m-%d")
            )
            - set(holdout["target_date"])
        ),
        "requested_city_days_without_joined_rows": [
            {"city": city, "target_date": target_date}
            for city, target_date in sorted(
                expected_city_days - observed_city_days,
                key=lambda row: (row[1], row[0]),
            )
        ],
        "joined_book_evidence_counts": {
            str(key): int(value)
            for key, value in full_universe["book_evidence"]
            .value_counts()
            .items()
        },
        "state_rows": len(states),
        "book_rows": len(books),
        "scores": scores,
        "candidate_minus_raw_market": deltas,
        "full_train_plus_holdout_opportunity_distribution": full_universe_summary,
        "full_train_plus_holdout_first_order_per_city_day": (
            full_universe_first_summary
        ),
        "full_holdout_opportunity_distribution": broad_summary,
        "first_order_per_city_day_without_model_selection": first_city_day_summary,
        "full_holdout_by_city": city_distribution,
        "model_edge_distribution": edge_distribution,
        "policy_sensitivity": policy_sensitivity,
        "trade_policy": (
            "holdout only; favorite-YES expression; first city-day signal; "
            "max 5 shares; edge after official fee >=2c; archived top-of-book"
        ),
        "trade_summary": trade_summary,
        "conclusion": "inconclusive",
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    holdout.to_csv(args.output_dir / "holdout_states.csv", index=False)
    full_universe.to_csv(
        args.output_dir / "full_train_holdout_opportunity_replay.csv", index=False
    )
    broad.to_csv(args.output_dir / "full_opportunity_replay.csv", index=False)
    pd.DataFrame(edge_distribution).to_csv(
        args.output_dir / "edge_distribution.csv", index=False
    )
    pd.DataFrame(policy_sensitivity).to_csv(
        args.output_dir / "policy_sensitivity.csv", index=False
    )
    trades.to_csv(args.output_dir / "trades.csv", index=False)
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    brier = deltas["brier"]
    logloss = deltas["logloss"]
    roi_text = (
        f"{trade_summary['roi']:+.2%}" if trade_summary["roi"] is not None else "NA"
    )
    roi_ci_text = (
        f"[{roi_ci[0]:+.2%}, {roi_ci[1]:+.2%}]" if roi_ci is not None else "NA"
    )
    city_lines = "\n".join(
        f"| {row['city']} | {row['signals']} | {row['wins']} | "
        f"${row['pnl_usd']:+.4f} | {row['roi']:+.2%} |"
        for row in city_trade_summary
    )
    broad_ci = broad_summary["roi_ci95_target_date_block"]
    broad_ci_text = (
        f"[{broad_ci[0]:+.2%}, {broad_ci[1]:+.2%}]" if broad_ci else "NA"
    )
    first_ci = first_city_day_summary["roi_ci95_target_date_block"]
    first_ci_text = (
        f"[{first_ci[0]:+.2%}, {first_ci[1]:+.2%}]" if first_ci else "NA"
    )
    full_ci = full_universe_summary["roi_ci95_target_date_block"]
    full_ci_text = (
        f"[{full_ci[0]:+.2%}, {full_ci[1]:+.2%}]" if full_ci else "NA"
    )
    full_first_ci = full_universe_first_summary["roi_ci95_target_date_block"]
    full_first_ci_text = (
        f"[{full_first_ci[0]:+.2%}, {full_first_ci[1]:+.2%}]"
        if full_first_ci
        else "NA"
    )
    full_city_lines = "\n".join(
        f"| {row['city']} | {row['opportunities_or_orders']} | "
        f"{row['target_dates']} | {row['wins']} | {row['win_rate']:.2%} | "
        f"${row['pnl_usd']:+.4f} | {row['roi']:+.2%} |"
        for row in city_distribution
    )
    edge_lines = "\n".join(
        f"| {row['edge_bin']} | {row['opportunities_or_orders']} | "
        f"{row['target_dates']} | {row['wins']} | {row['win_rate']:.2%} | "
        f"${row['pnl_usd']:+.4f} | {row['roi']:+.2%} |"
        for row in edge_distribution
    )
    policy_lines = "\n".join(
        f"| {row['edge_threshold']} | {row['scope']} | "
        f"{row['opportunities_or_orders']} | {row['target_dates']} | "
        f"{row['wins']} | "
        f"{row['win_rate']:.2%} | ${row['pnl_usd']:+.4f} | "
        f"{row['roi']:+.2%} |"
        for row in policy_sensitivity
    )
    report = f"""# Korea intraday probability residual baseline（expanded replay）

## 决策摘要

- 第一版模型已可运行，但结论固定为 `research / inconclusive`，不改 live。
- target 是 `P(PIT favorite exact bracket 最终获胜)`；模型用 market logit 作锚，
  再加入 Seoul/Busan、当地时刻、favorite 相对 running max、AMOS 温度路径、
  湿度、露点差、风、云和降水状态。
- train `{args.start_date}..{args.train_end}`；frozen holdout
  `{holdout['target_date'].min()}..{holdout['target_date'].max()}`。分钟/盘口快照不作
  独立样本，训练按 city-day 等权，CI 按 target_date block bootstrap。

## 双漏斗

### Signal funnel

`{len(states):,} AMOS first-seen states → {len(books):,} Korea target-day book states
→ {len(frame):,} full as-of joined/executable opportunities
→ {len(holdout):,} frozen holdout opportunities
→ {trade_summary['signals']} selected appendix trades`

### Evidence funnel

- train: `{len(train):,}` states / `{summary['train_city_days']}` city-days /
  `{summary['train_dates']}` target dates。
- holdout: `{len(holdout):,}` states / `{summary['holdout_city_days']}` city-days /
  `{summary['holdout_dates']}` target dates。
- full executable replay: `{full_universe_summary['opportunities_or_orders']}`
  opportunity-orders / `{full_universe_summary['target_dates']}` target dates；
  one-first-order policy 是 `{full_universe_first_summary['opportunities_or_orders']}`
  orders。均为 archived taker top-of-book，最多 5 shares，不冒充 actual fills。
- requested holdout 中没有形成 as-of joined row 的日期：
  `{", ".join(summary['requested_holdout_dates_without_joined_rows']) or "none"}`；
  这是 evidence coverage gap，不是策略过滤。
- 全窗口缺失 city-day：
  `{", ".join(f"{row['city']} {row['target_date']}" for row in summary['requested_city_days_without_joined_rows']) or "none"}`。
- joined book evidence：
  `{summary['joined_book_evidence_counts']}`。

## Probability 层（holdout，同 rows）

| model | Brier | logloss |
|---|---:|---:|
| raw market midpoint | {scores['raw_market']['brier']:.6f} | {scores['raw_market']['logloss']:.6f} |
| market-only calibrator | {scores['market_calibrator']['brier']:.6f} | {scores['market_calibrator']['logloss']:.6f} |
| market + Korea weather/path | {scores['market_plus_weather']['brier']:.6f} | {scores['market_plus_weather']['logloss']:.6f} |

Candidate − raw market:

- Brier `{brier['point']:+.6f}`，95% date-block CI
  `[{brier['ci95'][0]:+.6f}, {brier['ci95'][1]:+.6f}]`。
- logloss `{logloss['point']:+.6f}`，95% date-block CI
  `[{logloss['ci95'][0]:+.6f}, {logloss['ci95'][1]:+.6f}]`。

负 delta 才表示 candidate 优于 market；CI 跨 0 就不算通过 baseline gate。

## 交易层：runner 窗口完整分布与 frozen holdout

### 主结果：train + holdout 完整分布，不做模型 selector

`{args.start_date}..{holdout['target_date'].max()}` 全窗口：

- opportunities: `{full_universe_summary['opportunities_or_orders']}`
- target dates / city-days:
  `{full_universe_summary['target_dates']} / {full_universe_summary['city_days']}`
- wins / win rate:
  `{full_universe_summary['wins']} / {full_universe_summary['win_rate']:.2%}`
- 把每个 opportunity 都机械当作 5-share order：
  PnL `${full_universe_summary['pnl_usd']:+.4f}` /
  ROI `{full_universe_summary['roi']:+.2%}` /
  target-date CI `{full_ci_text}`
- 若每个 city-day 只下第一单：
  `{full_universe_first_summary['opportunities_or_orders']}` orders /
  `{full_universe_first_summary['wins']}` wins /
  win rate `{full_universe_first_summary['win_rate']:.2%}` /
  ROI `{full_universe_first_summary['roi']:+.2%}` /
  CI `{full_first_ci_text}`

这是 `--start-date..--end-date` 输入窗口内的完整可执行分布，不是 Korea 或项目的全部历史。下面的 frozen holdout 用来检验泛化，不是该窗口完整样本的替代。

### Frozen holdout 完整分布

把每个有 favorite-YES ask/depth 的 holdout state 都作为 opportunity：

- opportunities: `{broad_summary['opportunities_or_orders']}`
- independent target dates: `{broad_summary['target_dates']}`
- city-days: `{broad_summary['city_days']}`
- outcome wins / win rate:
  `{broad_summary['wins']} / {broad_summary['win_rate']:.2%}`
- mechanical 5-share orders: `{broad_summary['opportunities_or_orders']}`
- fee-adjusted PnL / ROI:
  `${broad_summary['pnl_usd']:+.4f}` / `{broad_summary['roi']:+.2%}`
- target-date block ROI 95% CI: `{broad_ci_text}`

这 `{broad_summary['opportunities_or_orders']}` 行是完整 opportunity/order replay，
但同一 city-day 有多个相关 snapshot，不能当成独立样本。若执行策略规定每个
city-day 只在首个 state 下单，则是：

- orders `{first_city_day_summary['opportunities_or_orders']}` /
  wins `{first_city_day_summary['wins']}` /
  win rate `{first_city_day_summary['win_rate']:.2%}`
- fee-adjusted PnL `${first_city_day_summary['pnl_usd']:+.4f}` /
  ROI `{first_city_day_summary['roi']:+.2%}` /
  95% CI `{first_ci_text}`

| city | opportunities | dates | wins | win rate | fee PnL | ROI |
|---|---:|---:|---:|---:|---:|---:|
{full_city_lines}

### 连续 residual 分布

| model edge after fee | rows | dates | wins | win rate | fee PnL | ROI |
|---|---:|---:|---:|---:|---:|---:|
{edge_lines}

### 执行 policy 敏感性

| edge threshold | order scope | orders | dates | wins | win rate | fee PnL | ROI |
|---|---|---:|---:|---:|---:|---:|---:|
{policy_lines}

### Selector 切片（附录，不是总样本）

固定 policy：模型概率减 favorite-YES taker ask 与官方 Weather fee 后 edge `>=2c`，
每个 city-day 只取首次信号，目标 5 shares、按 top size 缩量。atlas v1 没有保留
favorite YES bid 对应的 size，因此本版不把 NO expression 冒充 depth-verified order；
NO 会在新的统一 collector 盘口层前向补齐。

- signals / orders: `{trade_summary['signals']}`
- wins: `{trade_summary['wins']}`
- cost: `${trade_summary['cost_usd']:.4f}`
- fee-adjusted PnL: `${trade_summary['pnl_usd']:+.4f}`
- fee-adjusted ROI: `{roi_text}`
- target-date block bootstrap ROI 95% CI: `{roi_ci_text}`

| city | signals | wins | fee-adjusted PnL | ROI |
|---|---:|---:|---:|---:|
{city_lines}

这是 counterfactual executable replay，不是钱包 actual fills；未建 maker queue。
`2c` edge 只是 v1 固定 evaluation convention，并非更早预注册的 live threshold。
selected ROI 只作探索性诊断，不能覆盖 probability gate 的失败。

## 与 Amsterdam 的复用边界

复用：source→settlement basis、train/holdout 冻结、signal/evidence 双漏斗、同 rows
market baseline、official fee、信号数/订单数/ROI。未复用：KNMI `+0.5/+0.6`
threshold、10 分钟 cadence、previous-bracket NO expression；韩国模型使用 5 秒
AMOS first-seen state 和整条 favorite probability residual。

## 结论

在 `{holdout['target_date'].min()}..{holdout['target_date'].max()}` frozen holdout，
candidate 相对 raw market 的 Brier delta 为 `{brier['point']:+.6f}`
（95% CI `[{brier['ci95'][0]:+.6f}, {brier['ci95'][1]:+.6f}]`），
forward `NA`，结论 `inconclusive`；动作：继续统一 collector，并把这一冻结模型
写入 zero-notional forward score，不改 live。
"""
    args.report.write_text(report, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

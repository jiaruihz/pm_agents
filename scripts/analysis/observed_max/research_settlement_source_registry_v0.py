#!/usr/bin/env python3
"""Synthesize city-level Polymarket weather settlement source evidence.

This script does not fetch network data. It consumes prior generated evidence:

- official_resolution_source_v0: market rules source/station extraction
- official_station_alignment_summary: station-diff city validation
- settlement_basis_batch2_v0: HongKong/Jakarta and unresolved-city hypotheses

The output is a registry-style research snapshot for downstream studies. It is
not a live config and does not modify N100 behavior.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DB_PATH = ROOT / "runtime/weather.db"
OFFICIAL_SOURCE = ROOT / "docs/analysis/2026-06/generated/official_resolution_source_v0/official_resolution_source.csv"
OFFICIAL_ALIGN = ROOT / "docs/analysis/2026-06/generated/official_resolution_source_v0/official_station_alignment_summary.csv"
BATCH2_SUMMARY = ROOT / "docs/analysis/2026-06/generated/settlement_basis_batch2_v0/hypothesis_summary.csv"
JAKARTA_RULES = ROOT / "docs/analysis/2026-06/generated/settlement_basis_batch2_v0/jakarta_rules_stations.csv"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-14-settlement-source-registry-v0.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-14-settlement-source-registry-v0.md"
TARGET_METRIC = "settlement_source_reliability_gap"


SPECIAL_OVERRIDES = {
    "HongKong": {
        "settlement_source_class": "special_source_confirmed",
        "official_source": "HKO Daily Extract Absolute Daily Max",
        "official_station_or_feed": "HKO",
        "mapping_rule": "floor decimal daily max to integer bracket",
        "alignment_source": "settlement_basis_batch2_v0",
        "downstream_action": "use official HKO data; do not use VHHH/IEM/WU as payout feature source",
    },
    "Jakarta": {
        "settlement_source_class": "official_station_diff_confirmed",
        "official_source": "WU / IEM official station from rules",
        "official_station_or_feed": "WIHH",
        "mapping_rule": "whole-degree max at WIHH",
        "alignment_source": "settlement_basis_batch2_v0",
        "downstream_action": "use WIHH/Halim; WIII is wrong for settlement/source features",
    },
    "Moscow": {
        "settlement_source_class": "blocked_unresolved_settlement_basis",
        "official_source": "rules source not reconciled",
        "official_station_or_feed": "unknown_effective_source",
        "mapping_rule": "unresolved",
        "alignment_source": "settlement_basis_batch2_v0",
        "downstream_action": "exclude from official-source M3/station-basis research until root cause is found",
    },
    "Seoul": {
        "settlement_source_class": "blocked_unresolved_settlement_basis",
        "official_source": "rules source not reconciled",
        "official_station_or_feed": "unknown_effective_source",
        "mapping_rule": "unresolved",
        "alignment_source": "settlement_basis_batch2_v0",
        "downstream_action": "exclude from official-source M3/station-basis research until root cause is found",
    },
    "Shenzhen": {
        "settlement_source_class": "blocked_unresolved_settlement_basis",
        "official_source": "WU feed partially explains but does not align",
        "official_station_or_feed": "unresolved_wu_feed",
        "mapping_rule": "unresolved",
        "alignment_source": "settlement_basis_batch2_v0",
        "downstream_action": "exclude from official-source M3/station-basis research until root cause is found",
    },
    "MexicoCity": {
        "settlement_source_class": "default_source_watchlist",
        "official_source": "WU / IEM configured station",
        "official_station_or_feed": "MMMX",
        "mapping_rule": "whole-degree max; one mismatch in current sample",
        "alignment_source": "settlement_basis_batch2_v0",
        "downstream_action": "allowed for broad research but keep in settlement-watchlist",
    },
}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def pct(x: float | None) -> str:
    if x is None or pd.isna(x):
        return "NA"
    return f"{float(x) * 100:.1f}%"


def connect_ro(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    return conn


def run_sql_rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def run_sql_scalar(conn: sqlite3.Connection, sql: str) -> Any:
    return conn.execute(sql).fetchone()[0]


def mandatory_self_check(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "max_fact_built_at_utc": run_sql_scalar(conn, "SELECT MAX(fact_built_at_utc) FROM fact_trades"),
        "trade_class_distribution": run_sql_rows(
            conn,
            "SELECT trade_class, COUNT(*) AS rows FROM fact_trades GROUP BY trade_class ORDER BY trade_class",
        ),
        "settlement_status_distribution": run_sql_rows(
            conn,
            "SELECT COALESCE(settlement_status, '') AS settlement_status, COUNT(*) AS rows "
            "FROM fact_trades GROUP BY settlement_status ORDER BY settlement_status",
        ),
        "candidate_coverage": run_sql_rows(
            conn,
            "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, "
            "SUM(live_filled) AS live_filled FROM fact_signal_candidates",
        )[0],
        "order_fill_coverage": run_sql_rows(
            conn,
            "SELECT o.status, COUNT(*) AS orders, "
            "SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill "
            "FROM orders o LEFT JOIN fills f USING(execution_id) "
            "WHERE o.venue='polymarket_clob' GROUP BY o.status ORDER BY o.status",
        ),
    }


def fact_city_counts(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT
          city,
          COUNT(*) AS candidate_rows,
          SUM(CASE WHEN settlement_status='settled' THEN 1 ELSE 0 END) AS settled_candidate_rows,
          COUNT(DISTINCT event_date) AS candidate_dates,
          COUNT(DISTINCT CASE WHEN settlement_status='settled' THEN event_date END) AS settled_dates
        FROM fact_signal_candidates
        GROUP BY city
        """,
        conn,
    )


def best_batch_hypotheses(batch: pd.DataFrame) -> dict[str, dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for city, g in batch.groupby("city", dropna=False):
        ordered = g.sort_values(["align_rate", "matches", "days"], ascending=False)
        row = ordered.iloc[0].to_dict()
        best[str(city)] = row
    return best


def build_registry() -> pd.DataFrame:
    source = pd.read_csv(OFFICIAL_SOURCE)
    align = pd.read_csv(OFFICIAL_ALIGN)
    batch = pd.read_csv(BATCH2_SUMMARY)
    best_batch = best_batch_hypotheses(batch)
    align_by_city = {str(r["city"]): r.to_dict() for _, r in align.iterrows()}

    rows: list[dict[str, Any]] = []
    for _, src in source.iterrows():
        city = str(src["city"])
        override = SPECIAL_OVERRIDES.get(city)
        if override:
            batch_best = best_batch.get(city, {})
            days = batch_best.get("days")
            matches = batch_best.get("matches")
            align_rate = batch_best.get("align_rate")
            official_station = override["official_station_or_feed"]
            cls = override["settlement_source_class"]
            official_source = override["official_source"]
            mapping_rule = override["mapping_rule"]
            alignment_source = override["alignment_source"]
            action = override["downstream_action"]
        elif src.get("icao_match") is False or str(src.get("icao_match")).lower() == "false":
            aln = align_by_city.get(city, {})
            days = aln.get("days")
            matches = aln.get("matches")
            align_rate = aln.get("align_rate")
            official_station = src.get("official_icao")
            cls = "official_station_diff_confirmed" if align_rate is not None and float(align_rate) >= 0.97 else "official_station_diff_needs_validation"
            official_source = "WU official station from market rules"
            mapping_rule = "whole-degree official station max"
            alignment_source = "official_resolution_source_v0"
            action = "use official station for observed/source features; treat as station-basis candidate only after rules recheck"
        elif bool(src.get("source_is_wu")) and str(src.get("icao_match")).lower() == "true":
            days = None
            matches = None
            align_rate = None
            official_station = src.get("official_icao")
            cls = "default_wu_station_by_rules"
            official_source = "WU market rules station matches configured station"
            mapping_rule = "whole-degree WU station max"
            alignment_source = "rules_only"
            action = "allowed for broad forecast-quality research; not a station-basis edge candidate"
        elif src.get("source_is_wu") is False or str(src.get("source_is_wu")).lower() == "false":
            days = None
            matches = None
            align_rate = None
            official_station = src.get("official_icao") if pd.notna(src.get("official_icao")) else src.get("source_url")
            cls = "non_wu_source_by_rules"
            official_source = src.get("source_url")
            mapping_rule = f"{src.get('precision') or 'unknown'} precision from non-WU source"
            alignment_source = "rules_only"
            action = "needs source-specific feature fetch before source-sensitive research"
        else:
            days = None
            matches = None
            align_rate = None
            official_station = None
            cls = "no_recent_market_or_unknown_rules"
            official_source = None
            mapping_rule = "unknown"
            alignment_source = "rules_probe_empty"
            action = "do not use for source-sensitive research until rules/settlement source is identified"
        rows.append(
            {
                "city": city,
                "unit": src.get("unit"),
                "configured_icao": src.get("our_icao"),
                "official_station_or_feed": official_station,
                "settlement_source_class": cls,
                "official_source": official_source,
                "mapping_rule": mapping_rule,
                "alignment_days": None if pd.isna(days) else int(days) if days is not None else None,
                "alignment_matches": None if pd.isna(matches) else int(matches) if matches is not None else None,
                "alignment_rate": None if pd.isna(align_rate) else float(align_rate) if align_rate is not None else None,
                "alignment_source": alignment_source,
                "downstream_action": action,
            }
        )
    return pd.DataFrame(rows)


def add_fact_counts(registry: pd.DataFrame, counts: pd.DataFrame) -> pd.DataFrame:
    out = registry.merge(counts, on="city", how="left")
    for col in ["candidate_rows", "settled_candidate_rows", "candidate_dates", "settled_dates"]:
        out[col] = out[col].fillna(0).astype(int)
    return out


def class_summary(registry: pd.DataFrame) -> list[dict[str, Any]]:
    g = (
        registry.groupby("settlement_source_class", dropna=False)
        .agg(
            cities=("city", "nunique"),
            candidate_rows=("candidate_rows", "sum"),
            settled_candidate_rows=("settled_candidate_rows", "sum"),
            settled_dates=("settled_dates", "sum"),
        )
        .reset_index()
        .sort_values(["cities", "candidate_rows"], ascending=False)
    )
    return g.to_dict("records")


def city_table_rows(registry: pd.DataFrame) -> list[dict[str, Any]]:
    important_classes = {
        "official_station_diff_confirmed",
        "special_source_confirmed",
        "blocked_unresolved_settlement_basis",
        "default_source_watchlist",
        "non_wu_source_by_rules",
        "no_recent_market_or_unknown_rules",
    }
    rows = registry[registry["settlement_source_class"].isin(important_classes)].copy()
    rows = rows.sort_values(["settlement_source_class", "city"])
    out = []
    for _, r in rows.iterrows():
        out.append(
            {
                "city": r["city"],
                "class": r["settlement_source_class"],
                "configured": r["configured_icao"],
                "official": r["official_station_or_feed"],
                "align": "rules_only" if pd.isna(r["alignment_rate"]) else pct(float(r["alignment_rate"])),
                "days": "" if pd.isna(r["alignment_days"]) else int(r["alignment_days"]),
                "settled_candidate_rows": int(r["settled_candidate_rows"]),
                "action": r["downstream_action"],
            }
        )
    return out


def markdown_table(rows: list[dict[str, Any]], cols: list[str]) -> str:
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(c, "")) for c in cols) + " |")
    return "\n".join(lines)


def render_md(payload: dict[str, Any]) -> str:
    class_rows = []
    for r in payload["class_summary"]:
        class_rows.append(
            {
                "class": r["settlement_source_class"],
                "cities": r["cities"],
                "candidate_rows": r["candidate_rows"],
                "settled_rows": r["settled_candidate_rows"],
                "settled_dates_sum": r["settled_dates"],
            }
        )
    lines = [
        "# Settlement Source Registry v0",
        "",
        f"> generated_at_utc: `{payload['generated_at_utc']}`",
        f"> target_metric: `{TARGET_METRIC}`",
        "> Scope: data/source reliability research only; no N100/live config changed; no live action.",
        "",
        "## 数据快照",
        "",
        "- 数据源: `runtime/weather.db.fact_signal_candidates` for coverage counts; generated official-source CSVs for source evidence.",
        f"- DB last_modified: `{payload['db_last_modified_utc']}`.",
        f"- fact_signal_candidates rows: `{payload['self_check']['candidate_coverage']['rows']}`.",
        "- 本报告不发布 `live_real` PnL/ROI/rank/curve，因此不使用 CLOB coverage gate 作为结论来源。",
        "",
        "### 强制 5 行 SQL 自检",
        "",
        "```json",
        json.dumps(payload["self_check"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## Target Metric",
        "",
        "`settlement_source_reliability_gap` = city-level gap between Polymarket's official settlement source and the weather source currently used by local forecast/observed features.",
        "",
        "Important distinction: `pm_history` / `final_yes` remains the market settlement truth. The problem is whether our forecast or observed-weather feature source is predicting the same station/feed/rule that Polymarket settles against.",
        "",
        "## Source-Class Summary",
        "",
        markdown_table(class_rows, ["class", "cities", "candidate_rows", "settled_rows", "settled_dates_sum"]),
        "",
        "## City Registry Highlights",
        "",
        markdown_table(
            payload["city_table_rows"],
            ["city", "class", "configured", "official", "align", "days", "settled_candidate_rows", "action"],
        ),
        "",
        "## Research Reuse Rules",
        "",
        "- Forecast-quality / model-reliability research: attach `settlement_source_class` as a covariate. Do not mix `blocked_unresolved_settlement_basis` cities into a generic city-model reliability label.",
        "- M3 / observed-running-max research: only use official-source confirmed cities. For station-diff cities, rebuild running max from the official station/feed before any orderbook backtest.",
        "- Station-basis strategies: confirmed station-diff cities are candidates only after per-market rules recheck. The edge is the market watching the wrong station, not generic temperature theta.",
        "- HongKong: use HKO Daily Extract / live HKO feed semantics, decimal daily max, and floor-to-bracket mapping. VHHH/IEM is not an acceptable settlement feature source.",
        "- Jakarta: use WIHH/Halim, not WIII/Soekarno-Hatta, for settlement/source features.",
        "- Moscow, Seoul, Shenzhen: blocked for source-sensitive trading research until the unresolved mismatch is explained.",
        "- `pm_history` remains the payout label for strategy PnL; official-source reconstruction is for feature alignment, not replacing market settlement truth.",
        "",
        "## Next Research Directions",
        "",
        "1. Promote this registry into a generated sidecar artifact joined by city/date in forecast-quality, M3, station-basis, and basket scripts.",
        "2. Add official source fields to fact tables: `settlement_source_class`, `official_station_or_feed`, `settlement_mapping_rule`, and `source_verified_at`.",
        "3. Build live-capable HKO and WIHH source fetchers before HK/Jakarta can enter any shadow feed.",
        "4. Rerun forecast-quality base excluding or separately tagging station-diff/special-source/blocked cities to measure how much of reliability is source mismatch.",
        "5. Diagnose Moscow/Seoul/Shenzhen by fetching the exact official rendered page values around mismatch dates and comparing to METAR/WU API minute history.",
        "6. Add a pre-entry rules checker for station-basis candidates; station changes must block would-trades rather than silently falling back.",
        "",
        "## Three-Gate Verdict",
        "",
        "| gate | status | reason |",
        "| --- | --- | --- |",
        "| significance | NA | This is a data/source registry, not a strategy ROI test. |",
        "| baseline | NA | No trading rule is proposed. |",
        "| forward | NA | Registry must be rerun as new markets/rules appear. |",
        "",
        "`conclusion=data_registry_current_reference`, allowed action: use in research/shadow feature alignment only; no live config change.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", default=str(DB_PATH))
    parser.add_argument("--out-json", default=str(OUT_JSON))
    parser.add_argument("--out-md", default=str(OUT_MD))
    args = parser.parse_args()

    db_path = Path(args.db_path)
    conn = connect_ro(db_path)
    try:
        self_check = mandatory_self_check(conn)
        counts = fact_city_counts(conn)
    finally:
        conn.close()

    registry = add_fact_counts(build_registry(), counts)
    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": TARGET_METRIC,
        "db_path": str(db_path),
        "db_last_modified_utc": datetime.fromtimestamp(db_path.stat().st_mtime, timezone.utc).isoformat(),
        "inputs": {
            "official_source": str(OFFICIAL_SOURCE),
            "official_alignment": str(OFFICIAL_ALIGN),
            "batch2_summary": str(BATCH2_SUMMARY),
            "jakarta_rules": str(JAKARTA_RULES),
        },
        "self_check": self_check,
        "registry": registry.sort_values("city").to_dict("records"),
        "class_summary": class_summary(registry),
        "city_table_rows": city_table_rows(registry),
        "verdict": {
            "conclusion": "data_registry_current_reference",
            "allowed_action": "research/shadow feature alignment only; no live config change",
        },
    }
    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    out_md.write_text(render_md(payload))
    print(json.dumps({"out_json": str(out_json), "out_md": str(out_md), "classes": payload["class_summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

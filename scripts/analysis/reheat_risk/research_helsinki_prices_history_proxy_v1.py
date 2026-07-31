#!/usr/bin/env python3
"""Descriptive Helsinki replay using Polymarket /prices-history midpoint proxy.

This deliberately does not manufacture bid/ask/depth. It expands market-probability
coverage and reports midpoint and empirically marked-up reference scenarios separately.
"""

from __future__ import annotations

import glob
import hashlib
import json
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import requests


ROOT = Path(__file__).resolve().parents[3]
STATES = ROOT / "docs/analysis/2026-07/generated/helsinki_remaining_heat_market_replay_v2/checkpoint_market_states_v2_v7.csv.gz"
ARTIFACT_PATH = ROOT / "docs/analysis/2026-07/generated/helsinki_market_expression_v2/helsinki_market_expression_v2_research_challenger.joblib"
SNAPSHOT_ROOT = Path("/Volumes/jrs/weather_data_feed_service_runtime/targeted_output/paper_snapshots")
OUTPUT = ROOT / "docs/analysis/2026-07/generated/helsinki_prices_history_proxy_v1"
REPORT = ROOT / "docs/analysis/2026-07/2026-07-31-helsinki-prices-history-proxy-replay-v1.md"
EXPECTED_ARTIFACT_SHA = "e5290f36ad033d1a526162c124a54106ecfb066e0b1538a3864ed0db1c841288"
API = "https://clob.polymarket.com/prices-history"
EPS = 1e-6


def fee_per_share(price: float) -> float:
    return 0.05 * price * (1 - price)


def midpoint_files(start: str, end: str) -> list[Path]:
    files: list[Path] = []
    for day in pd.date_range(start, end, freq="D"):
        paths = sorted(SNAPSHOT_ROOT.glob(f"snapshot_{day:%Y%m%d}_*.json"))
        if paths:
            files.append(paths[len(paths) // 2])
    return files


def token_map(start: str, end: str) -> dict[tuple[str, int], str]:
    output: dict[tuple[str, int], str] = {}
    for path in midpoint_files(start, end):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for row in payload.get("records", []):
            if row.get("city") != "Helsinki" or not row.get("no_token_id"):
                continue
            target_date = str(row.get("target_date") or "")
            bracket = str(row.get("bracket") or "")
            if start <= target_date <= end and bracket.lstrip("-").isdigit():
                output[(target_date, int(bracket))] = str(row["no_token_id"])
    return output


def fetch_history(item: tuple[str, int, str]) -> tuple[tuple[str, int], list[dict[str, Any]]]:
    target_date, bracket, token = item
    local_start = pd.Timestamp(target_date, tz="Europe/Helsinki")
    start_ts = int((local_start - pd.Timedelta(hours=1)).timestamp())
    end_ts = int((local_start + pd.Timedelta(days=1, hours=1)).timestamp())
    response = requests.get(
        API,
        params={"market": token, "startTs": start_ts, "endTs": end_ts, "fidelity": 1},
        timeout=30,
    )
    response.raise_for_status()
    return (target_date, bracket), response.json().get("history", [])


def attach_midpoints(states: pd.DataFrame, tokens: dict[tuple[str, int], str]) -> tuple[pd.DataFrame, dict[str, Any]]:
    needed = sorted({
        (str(row.target_date), int(row.official_running_max_c), tokens[(str(row.target_date), int(row.official_running_max_c))])
        for row in states.itertuples()
        if (str(row.target_date), int(row.official_running_max_c)) in tokens
    })
    histories: dict[tuple[str, int], list[dict[str, Any]]] = {}
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        future_map = {pool.submit(fetch_history, item): item for item in needed}
        for future in as_completed(future_map):
            item = future_map[future]
            try:
                key, history = future.result()
                histories[key] = history
            except Exception as exc:
                errors[f"{item[0]}|{item[1]}"] = f"{type(exc).__name__}: {exc}"
    records: list[dict[str, Any]] = []
    for row in states.itertuples(index=False):
        date = str(row.target_date)
        bracket = int(row.official_running_max_c)
        history = histories.get((date, bracket), [])
        decision = pd.Timestamp(row.source_first_seen_ts_utc)
        if pd.isna(decision) or not history:
            continue
        decision_epoch = decision.timestamp()
        prior = [point for point in history if float(point["t"]) <= decision_epoch]
        if not prior:
            continue
        point = prior[-1]
        age = decision_epoch - float(point["t"])
        if age > 120:
            continue
        values = row._asdict()
        values.update({
            "current_x": bracket,
            "decision_ts_utc": decision.isoformat(),
            "no_token_id": tokens[(date, bracket)],
            "archive_midpoint_ts_utc": datetime.fromtimestamp(float(point["t"]), timezone.utc).isoformat(),
            "archive_midpoint_age_sec": age,
            "archive_no_mid_raw": float(point["p"]),
            "archive_no_mid_score": 0.0 if float(point["p"]) <= 0.0005 else (1.0 if float(point["p"]) >= 0.9995 else float(point["p"])),
        })
        records.append(values)
    return pd.DataFrame(records), {
        "needed_tokens": len(needed),
        "histories_returned": sum(bool(value) for value in histories.values()),
        "history_points": sum(len(value) for value in histories.values()),
        "fetch_errors": errors,
    }


def score_r16(frame: pd.DataFrame) -> pd.Series:
    actual_sha = hashlib.sha256(ARTIFACT_PATH.read_bytes()).hexdigest()
    if actual_sha != EXPECTED_ARTIFACT_SHA:
        raise RuntimeError(f"artifact SHA mismatch: {actual_sha}")
    artifact = joblib.load(ARTIFACT_PATH)
    rows = frame.copy()
    market = np.clip(rows["archive_no_mid_raw"].to_numpy(float), 1e-4, 1 - 1e-4)
    weather = np.clip(rows["p_break_v7"].to_numpy(float), 1e-4, 1 - 1e-4)
    rows["weather_market_logit_gap"] = np.log(weather / (1-weather)) - np.log(market / (1-market))
    rows["forecast_peak_h"] = rows["forecast_minutes_to_future_peak"] / 60
    local = pd.to_datetime(rows["decision_ts_utc"], utc=True).dt.tz_convert("Europe/Helsinki")
    angle = 2*np.pi*(local.dt.hour + local.dt.minute/60)/24
    rows["local_hour_sin"], rows["local_hour_cos"] = np.sin(angle), np.cos(angle)
    names = artifact["features"]
    matrix = rows[names].replace([np.inf,-np.inf],np.nan)
    median = pd.Series(artifact["median"])
    matrix = matrix.fillna(median)
    mean, scale = pd.Series(artifact["mean"]), pd.Series(artifact["scale"])
    x = ((matrix-mean)/scale).to_numpy(float)
    beta = np.asarray(artifact["beta"],float)
    logit = np.log(market/(1-market)) + beta[0] + x@beta[1:]
    return pd.Series(1/(1+np.exp(-np.clip(logit,-35,35))),index=frame.index)


def binary_metrics(frame: pd.DataFrame, column: str) -> dict[str, float]:
    y = frame["y_break"].to_numpy(float)
    p = np.clip(frame[column].to_numpy(float), EPS, 1-EPS)
    return {
        "brier": float(np.mean((p-y)**2)),
        "logloss": float(np.mean(-(y*np.log(p)+(1-y)*np.log(1-p)))),
    }


def markup(price: float, calibration: pd.DataFrame) -> float:
    bins = [-0.01, 0.2, 0.5, 0.8, 1.01]
    for lo, hi in zip(bins[:-1], bins[1:]):
        subset = calibration.loc[calibration["market_no_mid"].between(lo, hi, inclusive="right")]
        if lo < price <= hi and len(subset):
            return float((subset["direct_no_vwap5"]-subset["market_no_mid"]).median())
    return 0.015


def route(frame: pd.DataFrame, probability: str, scenario: str, calibration: pd.DataFrame) -> pd.DataFrame:
    rows=[]
    for (date,current_x), group in frame.groupby(["target_date","current_x"],sort=True):
        for row in group.sort_values("decision_ts_utc").itertuples(index=False):
            mid=float(row.archive_no_mid_raw)
            price=mid if scenario=="midpoint" else min(0.999,mid+markup(mid,calibration))
            effective=price+fee_per_share(price)
            p=float(getattr(row,probability))
            if p<=effective: continue
            won=bool(int(row.y_break)); cost=5*effective
            rows.append({"target_date":date,"current_x":current_x,"probability":probability,
                         "scenario":scenario,"decision_ts_utc":row.decision_ts_utc,
                         "p_win":p,"midpoint":mid,"entry_price_proxy":price,
                         "effective_cost_per_share":effective,"won":won,
                         "cash_cost":cost,"pnl":5*int(won)-cost})
            break
    return pd.DataFrame(rows)


def bootstrap(trades: pd.DataFrame, all_dates: list[str], draws: int=4000) -> tuple[float,float]:
    by_date=trades.groupby("target_date")[["pnl","cash_cost"]].sum().reindex(all_dates,fill_value=0)
    rng=np.random.default_rng(20260731); values=[]; array=by_date.to_numpy(float)
    for _ in range(draws):
        sample=array[rng.integers(0,len(array),len(array))]; cost=sample[:,1].sum()
        values.append(sample[:,0].sum()/cost if cost else 0.0)
    return tuple(np.quantile(values,[.025,.975]))


def main() -> int:
    OUTPUT.mkdir(parents=True,exist_ok=True)
    states=pd.read_csv(STATES)
    states=states.loc[states["source_first_seen_ts_utc"].notna() & states["y_break"].notna()].copy()
    start,end=str(states.target_date.min()),str(states.target_date.max())
    tokens=token_map(start,end)
    matched,api_summary=attach_midpoints(states,tokens)
    if matched.empty: raise RuntimeError("no price-history checkpoints matched")
    matched["p_r16_final_descriptive"]=score_r16(matched)
    calibration=pd.read_csv(STATES).dropna(subset=["market_no_mid","direct_no_vwap5"])
    models=["p_break_v7","p_r16_final_descriptive"]
    scores=[]
    for model in ["archive_no_mid_score",*models]:
        scores.append({"model":model,**binary_metrics(matched,model)})
    trades=pd.concat([route(matched,m,s,calibration) for m in models for s in ["midpoint","ask_markup_median"]],ignore_index=True)
    overlap=matched.dropna(subset=["market_no_mid","direct_no_vwap5"]).copy()
    overlap["midpoint_error"]=overlap["archive_no_mid_raw"]-overlap["market_no_mid"]
    overlap["ask_proxy"]=[min(0.999,float(p)+markup(float(p),calibration)) for p in overlap["archive_no_mid_raw"]]
    overlap["ask_proxy_error"]=overlap["ask_proxy"]-overlap["direct_no_vwap5"]
    all_dates=sorted(matched.target_date.unique())
    summaries=[]
    for (model,scenario),group in trades.groupby(["probability","scenario"]):
        cost=float(group.cash_cost.sum()); pnl=float(group.pnl.sum()); ci=bootstrap(group,all_dates)
        daily=group.groupby("target_date")[["pnl","cash_cost"]].sum()
        summaries.append({"model":model,"scenario":scenario,"signals":len(group),"dates":group.target_date.nunique(),
                          "wins":int(group.won.sum()),"losses":int((~group.won).sum()),"cash_cost":cost,
                          "pnl":pnl,"roi":pnl/cost if cost else None,"roi_ci_low":ci[0],"roi_ci_high":ci[1],
                          "profitable_dates":int((daily.pnl>0).sum()),"losing_dates":int((daily.pnl<0).sum())})
    summary={
        "status":"descriptive_archive_midpoint_proxy_only",
        "window":[start,end],
        "signal_funnel":{"weather_checkpoints":int(len(pd.read_csv(STATES))),"first_seen_weather_checkpoints":int(len(states)),
                         "archive_midpoint_matched":int(len(matched)),"matched_dates":int(matched.target_date.nunique())},
        "evidence_funnel":{"token_mapped_rows":int(states.apply(lambda r:(str(r.target_date),int(r.official_running_max_c)) in tokens,axis=1).sum()),
                           "midpoint_proxy_rows":int(len(matched)),"direct_bid_ask_rows":0,"depth_rows":0,"actual_fills":0},
        "api":api_summary,
        "midpoint_age_sec":{"median":float(matched.archive_midpoint_age_sec.median()),"p95":float(matched.archive_midpoint_age_sec.quantile(.95)),"max":float(matched.archive_midpoint_age_sec.max())},
        "direct_book_overlap_validation":{"rows":int(len(overlap)),"dates":int(overlap.target_date.nunique()),
            "midpoint_median_error":float(overlap.midpoint_error.median()),"midpoint_median_absolute_error":float(overlap.midpoint_error.abs().median()),
            "ask_proxy_median_error":float(overlap.ask_proxy_error.median()),"ask_proxy_median_absolute_error":float(overlap.ask_proxy_error.abs().median()),
            "ask_proxy_mean_error":float(overlap.ask_proxy_error.mean())},
        "probability_scores":scores,
        "trade_summaries":summaries,
        "notes":["midpoint scenario is optimistic and non-executable","ask markup uses same-window direct-book price-band median and still has no depth","r16 final artifact is post-fit descriptive, not OOF on this window","no threshold was added"],
    }
    matched.to_csv(OUTPUT/"matched_checkpoints.csv.gz",index=False,compression="gzip")
    trades.to_csv(OUTPUT/"proxy_trades.csv",index=False)
    (OUTPUT/"summary.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False)+"\n")
    score_lines="\n".join(f"| {x['model']} | {x['brier']:.5f} | {x['logloss']:.5f} |" for x in scores)
    trade_lines="\n".join(f"| {x['model']} | {x['scenario']} | {x['signals']} / {x['dates']} | {x['wins']} / {x['losses']} | {x['profitable_dates']} / {x['losing_dates']} | ${x['pnl']:+.2f} | {x['roi']:.2%} [{x['roi_ci_low']:.2%}, {x['roi_ci_high']:.2%}] |" for x in summaries)
    REPORT.write_text(f"""# Helsinki `/prices-history` midpoint 代理回放 v1

Status: `descriptive proxy / not executable / no-live-change`

## 结论

本报告只回答扩大 midpoint 覆盖后策略方向是否仍有参考价值，不把 midpoint 当 ask、订单簿或真实成交。

## 数据与双漏斗

- window：{start}..{end}；模型 checkpoint grain，每个 target-date/current-X 只取首次正 edge。
- signal funnel：2,115 weather checkpoints → {len(states):,} first-seen checkpoints → {len(matched):,} archive-midpoint matches / {matched.target_date.nunique()} dates。
- evidence funnel：{len(matched):,} midpoint proxy → 0 historical bid/ask → 0 depth → 0 actual fills。
- API 返回 {api_summary['history_points']:,} 个分钟点；匹配 age median/p95={summary['midpoint_age_sec']['median']:.1f}/{summary['midpoint_age_sec']['p95']:.1f}s。
- 与已有 direct book 重叠 {len(overlap)} rows / {overlap.target_date.nunique()} dates：archive midpoint 对真实 mid 的 median error={summary['direct_book_overlap_validation']['midpoint_median_error']:.4f}、median absolute error={summary['direct_book_overlap_validation']['midpoint_median_absolute_error']:.4f}；ask-markup proxy 对真实 VWAP5 的 median absolute error={summary['direct_book_overlap_validation']['ask_proxy_median_absolute_error']:.4f}，且 mean error={summary['direct_book_overlap_validation']['ask_proxy_mean_error']:.4f}（仍略乐观）。
- `p_r16_final_descriptive` 使用最终 artifact 回看本窗，存在 post-fit leakage，只作敏感度参考；v7 是天气概率基线。

## 同 rows 概率质量

| probability | Brier | logloss |
|---|---:|---:|
{score_lines}

## 5-share 代理交易

`midpoint` 假设5股可在 mid 成交；`ask_markup_median` 按真实 direct-book 同价格带的 median `VWAP5-mid` 加价，再扣官方 Weather fee，但仍不知道当时深度。

| 模型 | 场景 | signals / dates | wins / losses | 盈利/亏损日 | PnL | ROI（target-date bootstrap 95% CI） |
|---|---|---:|---:|---:|---:|---:|
{trade_lines}

## 边界

这批数据可以扩 market baseline、概率 residual 和 repricing 研究；不能恢复 spread、深度、滑点、maker queue 或真实可成交 ROI。archive timestamp 也不是 collector-exact first-seen，不能用于秒级 source latency alpha。未增加 price/path/time threshold，且本结果不参与 frozen forward 调参。
""",encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
Tokyo morning analysis — sync N100, read latest snapshot, send Telegram summary.
Usage: python3 scripts/ops/tokyo_morning_analysis.py [--date YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, timezone, datetime
from pathlib import Path
from typing import Optional

try:
    import requests as _requests
except ImportError:
    _requests = None  # type: ignore

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.platform.notification.telegram import send_telegram_message_sync

SNAPSHOTS_DIR = ROOT / "runtime/weather_edge_v1/market_data/paper_snapshots"
LEDGER_PATH = ROOT / "runtime/weather_edge_v1/market_data/paper_trades/paper_orders.jsonl"

# ICAO code for Tokyo's settlement station
TOKYO_ICAO = "RJTT"


def fetch_live_metar(icao: str, hours: int = 6) -> list[dict]:
    """Fetch recent METAR observations from aviationweather.gov."""
    if _requests is None:
        return []
    try:
        url = f"https://aviationweather.gov/api/data/metar?ids={icao}&hours={hours}&format=json"
        r = _requests.get(url, timeout=12)
        r.raise_for_status()
        return r.json() if isinstance(r.json(), list) else []
    except Exception as e:
        print(f"[metar] fetch failed: {e}")
        return []


def metar_summary(obs_list: list[dict]) -> dict:
    """Extract today's max temp, current temp and obs count from METAR list."""
    if not obs_list:
        return {"max_c": None, "current_c": None, "obs_count": 0, "latest_utc": None, "raw_latest": None}
    temps_c = []
    for obs in obs_list:
        t = obs.get("temp")
        if t is not None:
            try:
                temps_c.append(float(t))
            except (ValueError, TypeError):
                pass
    latest = obs_list[0]  # newest first
    current_c = float(latest.get("temp")) if latest.get("temp") is not None else None
    # obs_time is unix timestamp
    obs_ts = latest.get("obsTime")
    latest_utc = datetime.fromtimestamp(obs_ts, tz=timezone.utc).strftime("%H:%MZ") if obs_ts else None
    return {
        "max_c": max(temps_c) if temps_c else None,
        "current_c": current_c,
        "obs_count": len(obs_list),
        "latest_utc": latest_utc,
        "raw_latest": latest.get("rawOb", "")[:80],
    }


def sync_n100() -> bool:
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/ops/sync_weather_remote.sh")],
        capture_output=True, text=True, timeout=120
    )
    return result.returncode == 0


def latest_snapshot_for_date(target_date: str) -> dict | None:
    files = sorted(SNAPSHOTS_DIR.glob("snapshot_*.json"))
    # Filter snapshots that contain Tokyo records for target_date
    for f in reversed(files):
        try:
            d = json.loads(f.read_text())
            records = [r for r in d.get("records", [])
                       if r.get("city") == "Tokyo" and r.get("event_date") == target_date]
            if records:
                return {"file": f.name, "ts_beijing": d.get("ts_beijing"), "records": records}
        except Exception:
            continue
    return None


def load_today_orders(target_date: str) -> list[dict]:
    if not LEDGER_PATH.exists():
        return []
    orders = []
    with LEDGER_PATH.open() as fh:
        for line in fh:
            try:
                r = json.loads(line)
                if r.get("city") == "Tokyo" and r.get("event_date") == target_date:
                    orders.append(r)
            except Exception:
                continue
    return orders


def _bracket_int(bkt: str) -> int:
    """Convert bracket label to int for sorting (28+ → 28)."""
    return int(bkt.rstrip("+")) if bkt else 0


def format_message(target_date: str, snap: dict, orders: list[dict], metar: Optional[dict] = None) -> str:
    records = sorted(snap["records"], key=lambda r: _bracket_int(r.get("bracket", "0")))
    r0 = records[0] if records else {}

    gfs_f = r0.get("gfs_forecast_f")
    gfs_c = (gfs_f - 32) * 5 / 9 if gfs_f else None
    gfs_str = f"{gfs_c:.2f}C" if gfs_c else "N/A"

    # Prefer live METAR over stale snapshot value
    live_max_c = metar.get("max_c") if metar else None
    live_cur_c = metar.get("current_c") if metar else None
    live_obs = metar.get("obs_count", 0) if metar else 0
    live_ts = metar.get("latest_utc", "") if metar else ""
    live_raw = metar.get("raw_latest", "") if metar else ""

    if live_cur_c is not None:
        metar_str = f"实测最高={live_max_c:.1f}C  当前={live_cur_c:.1f}C  ({live_obs}条, {live_ts})"
    else:
        metar_max_f = r0.get("metar_current_max_f")
        metar_c_snap = (metar_max_f - 32) * 5 / 9 if metar_max_f else None
        metar_obs_snap = r0.get("metar_obs_count_today", 0)
        metar_str = f"{metar_c_snap:.1f}C ({metar_obs_snap}条)" if metar_c_snap else "暂无实测"

    hours = r0.get("hours_to_settle", "?")
    model_source = r0.get("forecast_source", "unknown")
    model_age = r0.get("model_run_age_hours_estimated", "?")
    lead = r0.get("forecast_target_lead_hours_estimated", "?")

    # Find key brackets
    def get_bkt(label: str) -> dict:
        return next((r for r in records if r.get("bracket") == label), {})

    r25, r26, r27, r28 = get_bkt("25"), get_bkt("26"), get_bkt("27"), get_bkt("28+")

    # Distribution analysis
    mp_sum = sum(r.get("model_prob", 0) for r in records)
    mkt_sum = sum(r.get("market_yes_price", 0) for r in records)
    mode_bkt = max(records, key=lambda r: r.get("model_prob", 0)).get("bracket", "?") if records else "?"
    model_mode_p = max(r.get("model_prob", 0) for r in records) if records else 0
    mkt_mode_bkt = max(records, key=lambda r: r.get("market_yes_price", 0)).get("bracket", "?") if records else "?"
    mkt_mode_p = max(r.get("market_yes_price", 0) for r in records) if records else 0

    # Big-picture convergence
    model_center = gfs_c or 0
    bkt26_dist = abs(model_center - 26.0) if model_center else None
    bkt27_dist = abs(model_center - 27.0) if model_center else None
    closer_to = "26" if (bkt26_dist or 99) < (bkt27_dist or 99) else "27"

    # Edge top-3
    top_edges = sorted(records, key=lambda r: abs(r.get("edge", 0)), reverse=True)[:3]

    # 26 NO position P&L
    pos26_entry = 0.58
    no26_bid = r26.get("no_best_bid") if r26 else None
    no26_ask = r26.get("no_best_ask") if r26 else None
    pnl26_str = f"{(no26_bid - pos26_entry):+.3f}/股 (bid {no26_bid})" if no26_bid else "盘口未知"

    # ── Build message ──────────────────────────────────────────────
    lines = []

    # Header
    lines += [
        f"=== Tokyo {target_date} 盘口分析 ===",
        f"快照: {snap['ts_beijing']}   T-{hours}h结算",
        f"预报: {gfs_str} ({model_source}, init {model_age}h前)",
        f"METAR: {metar_str}",
        "",
    ]
    if live_raw:
        lines.append(f"最新METAR原文: {live_raw}")
        lines.append("")

    # Raw table
    lines.append(f"{'':1}{'Bkt':<5} {'模型%':>6} {'市场%':>6} {'Edge':>7}  {'方向':<10} {'YES b/a':<13} {'NO b/a'}")
    for r in records:
        bkt = r.get("bracket", "?")
        mp = r.get("model_prob", 0)
        yp = r.get("market_yes_price", 0)
        edge = r.get("edge", 0)
        side = r.get("side", "")
        yb, ya = r.get("yes_best_bid", "-"), r.get("yes_best_ask", "-")
        nb, na = r.get("no_best_bid", "-"), r.get("no_best_ask", "-")
        star = "⭐" if abs(edge) >= 0.10 else ("▸" if abs(edge) >= 0.03 else " ")
        lines.append(f"{star}{bkt:<5} {mp*100:>5.1f}% {yp*100:>6.1f}%  {edge:>+.3f}  {side:<10} {yb}/{ya:<7} {nb}/{na}")

    lines.append(f"    Σ模型={mp_sum:.3f}  Σ市场={mkt_sum:.3f} (overround={mkt_sum-1:+.3f})")

    # ── 预报解读 ──
    lines += [
        "",
        "── 预报解读 ──",
    ]
    if gfs_c:
        lines.append(
            f"GFS中心落点 {gfs_c:.2f}C，更靠近 {closer_to}C bracket边界"
            f"（距26={bkt26_dist:.2f}C / 距27={bkt27_dist:.2f}C）。"
        )
    lines.append(
        f"模型分布 mode={mode_bkt}C ({model_mode_p*100:.1f}%)，"
        f"市场 mode={mkt_mode_bkt}C ({mkt_mode_p*100:.1f}%)。"
    )

    mp26v = r26.get("model_prob", 0) if r26 else 0
    yp26v = r26.get("market_yes_price", 0) if r26 else 0
    mp27v = r27.get("model_prob", 0) if r27 else 0
    yp27v = r27.get("market_yes_price", 0) if r27 else 0

    if mp26v and yp26v:
        diverge26 = yp26v - mp26v
        if diverge26 > 0.10:
            lines.append(
                f"26C: 市场高估模型 {diverge26*100:.1f}pp "
                f"(市场{yp26v*100:.0f}% vs 模型{mp26v*100:.0f}%)。"
                "历史上这类中央bracket被过度押注是BUY_NO的经典甜区。"
            )
        elif diverge26 < -0.10:
            lines.append(
                f"26C: 市场低估模型 {abs(diverge26)*100:.1f}pp，需关注是否有外部信息导致市场回避26C。"
            )
        else:
            lines.append(f"26C: 市场与模型基本一致（分歧{diverge26*100:+.1f}pp）。")

    if mp27v and yp27v:
        diverge27 = mp27v - yp27v
        if diverge27 > 0.08:
            lines.append(
                f"27C: 模型高于市场 {diverge27*100:.1f}pp "
                f"(模型{mp27v*100:.0f}% vs 市场{yp27v*100:.0f}%)。"
                "若GFS偏暖方向没有被ECMWF否定，27YES有alpha。"
            )

    if live_cur_c is not None and gfs_c:
        gap_to_gfs = gfs_c - live_cur_c
        # Rough diurnal: Tokyo May, typical 10am→14pm rise = 3.5~4.5C
        # Estimate hours remaining to peak (assume peak at 14:00 local = 05Z)
        now_utc_h = datetime.now(timezone.utc).hour + datetime.now(timezone.utc).minute / 60
        peak_utc_h = 5.0  # 14:00 JST = 05:00 UTC
        hrs_to_peak = (peak_utc_h - now_utc_h) % 24
        typical_rise_remaining = min(hrs_to_peak * 0.8, 5.0)  # ~0.8C/hr cap
        projected_max = live_cur_c + typical_rise_remaining
        lines.append(
            f"METAR当前 {live_cur_c:.1f}C，GFS预报最高 {gfs_c:.1f}C，"
            f"距峰值约 {hrs_to_peak:.1f}h，按典型升温速率预估最高可到 {projected_max:.1f}C。"
        )
        if projected_max < 25.5:
            lines.append("-> 升温空间不足，25C可能性上升，市场押注26C有高估风险。")
        elif projected_max < 26.5:
            lines.append("-> 升温空间勉强支持26C，但边界感强，25-26均有可能。")
        else:
            lines.append("-> 升温空间充足，26-27C均在射程，GFS预报有支撑。")
    else:
        lines.append("METAR暂无今日实测，分布纯靠数值预报，不确定性较高。")

    # ── 持仓评估 ──
    lines += [
        "",
        "── 持仓评估 ──",
        f"[1] 26 NO 入场 {pos26_entry:.3f}  当前 M2M: {pnl26_str}",
    ]
    if no26_bid:
        ev26 = mp26v  # prob YES wins = lose
        no_win_prob = 1 - mp26v
        ev_hold = no_win_prob * (1 - pos26_entry) - mp26v * pos26_entry
        ev_close = no26_bid - pos26_entry  # close at bid = realize this
        lines.append(
            f"   模型NO胜率 {no_win_prob*100:.1f}%，持有EV={ev_hold:+.3f}/股 vs 立刻平仓={ev_close:+.3f}/股。"
        )
        if ev_hold > ev_close + 0.05:
            lines.append("   → 建议持有，持有EV显著优于立即止损。")
        elif abs(ev_hold - ev_close) < 0.02:
            lines.append("   → 持有/平仓差距不大，视METAR实测情况决定。")
        else:
            lines.append("   → 模型已大幅转向，建议考虑平仓止损。")

    # 25 NO position
    no25_ask_v = r25.get("no_best_ask") if r25 else None
    mp25v = r25.get("model_prob", 0) if r25 else 0
    yp25v = r25.get("market_yes_price", 0) if r25 else 0
    if no25_ask_v:
        no25_win = 1 - mp25v
        profit25 = 1 - no25_ask_v
        ev25 = no25_win * profit25 - mp25v * no25_ask_v
        edge25 = no25_win - no25_ask_v
        lines.append(
            f"[2] 25 NO 限价0.78挂单中  当前ask={no25_ask_v}  "
            f"模型NO胜率{no25_win*100:.1f}%  单股EV={ev25:+.3f}  容错={edge25*100:.1f}pp"
        )
        if no25_ask_v > 0.80:
            lines.append(
                f"   → ask已至{no25_ask_v}，0.78限价未成交合理。"
                "如确认ECMWF不反转，可酌情上调至0.80-0.82，但注意赔率仅0.20x，仓位宜小。"
            )
        else:
            lines.append(f"   → ask={no25_ask_v}，限价0.78仍有成交机会，维持挂单。")

    # ── 操作建议 ──
    lines += ["", "── 操作建议 ──"]

    # Generate ranked actions
    actions = []
    # 26 NO hold/close
    if no26_bid and (1 - mp26v) > 0.60:
        actions.append(f"A. 26 NO 维持持有（模型NO胜率{(1-mp26v)*100:.0f}%，EV正）")
    else:
        actions.append(f"A. 26 NO 模型已转向，考虑NO bid {no26_bid} 平仓止损")

    # 25 NO
    if no25_ask_v and no25_ask_v <= 0.82:
        actions.append(f"B. 25 NO @ {no25_ask_v:.3f} ask - 仍正EV(容错{(1-mp25v-no25_ask_v)*100:.1f}pp)，可小仓吃单")
    else:
        actions.append(f"B. 25 NO ask已至{no25_ask_v}，赔率薄，挂0.80限价等回落，不追价")

    # 27 YES
    if r27 and mp27v - yp27v > 0.08:
        ya27 = r27.get("yes_best_ask", "?")
        actions.append(
            f"C. 27 YES @ {ya27} ask - 模型{mp27v*100:.0f}%超市场{yp27v*100:.0f}%，"
            "GFS中心偏暖，中小仓位配置，对冲26NO方向。"
        )

    # 28+ lottery
    if r28 and r28.get("model_prob", 0) > 0.10:
        ya28 = r28.get("yes_best_ask", "?")
        mp28 = r28.get("model_prob", 0)
        actions.append(
            f"D. 28+ YES @ {ya28} 彩票仓 - 模型{mp28*100:.0f}%/市场2%，高EV低成本，$2-5封顶。"
        )

    actions.append("E. T-6h前后METAR数据进来后，根据实测最高温决定是否加减仓。")

    for a in actions:
        lines.append(f"  {a}")

    # paper orders
    if orders:
        lines += ["", "📌 今日已触发paper单"]
        for o in orders:
            bkt = o.get("bracket", "?")
            side = o.get("side", "")
            px = o.get("entry_price", 0)
            edge_o = o.get("edge", 0)
            model_o = o.get("model", "")
            lines.append(f"  bkt={bkt} {side} @ {px:.3f}  edge={edge_o:+.3f}  [{model_o}]")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=str(date.today()))
    parser.add_argument("--no-sync", action="store_true")
    args = parser.parse_args()

    target_date = args.date
    print(f"[tokyo_morning_analysis] target_date={target_date}")

    if not args.no_sync:
        print("[tokyo_morning_analysis] syncing N100...")
        ok = sync_n100()
        print(f"[tokyo_morning_analysis] sync {'ok' if ok else 'failed (continuing with local data)'}")

    snap = latest_snapshot_for_date(target_date)
    if not snap:
        msg = f"⚠️ Tokyo {target_date} 晨报：没有找到任何 snapshot 数据，请手动检查 N100 同步状态。"
        send_telegram_message_sync(text=msg)
        print("[tokyo_morning_analysis] no snapshot found, sent warning")
        return 1

    print("[tokyo_morning_analysis] fetching live METAR...")
    metar_obs_list = fetch_live_metar(TOKYO_ICAO, hours=8)
    metar = metar_summary(metar_obs_list)
    print(f"[tokyo_morning_analysis] METAR: {metar}")

    orders = load_today_orders(target_date)
    msg = format_message(target_date, snap, orders, metar=metar)
    print("[tokyo_morning_analysis] message:\n", msg)

    send_telegram_message_sync(text=msg)
    print("[tokyo_morning_analysis] sent ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

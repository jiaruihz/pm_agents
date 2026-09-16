"""
Edge 策略历史回测 — Polymarket 真实定价验证（多时点分批建仓版）

用 CLOB API 历史价格（T-24h, T-12h）+ GFS 模型概率（留一月CV）对比，
支持可插拔的入场策略和仓位分配策略。

用法: python3 edge_backtest.py
输出: output/edge_backtest_staged_results.json
"""
from __future__ import annotations
import json
import os
import sys
import time
import httpx
import numpy as np
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime, timedelta, date
from collections import defaultdict
from typing import Protocol

BASE_DIR = Path(__file__).parent
DEFAULT_RUNTIME_DIR = BASE_DIR.parent / "runtime"
CACHE_DIR = Path(os.environ.get("WEATHER_DATA_FEED_CACHE_ROOT", DEFAULT_RUNTIME_DIR / "cache"))
CACHE_PM = CACHE_DIR / "pm_history"
OUTPUT_DIR = Path(os.environ.get("WEATHER_DATA_FEED_OUTPUT_ROOT", DEFAULT_RUNTIME_DIR / "output"))

from pm_edge_compare import (
    CITIES, PROXY, compute_bracket_probs, _extract_bracket_label,
)
from calibration_backtest import (
    load_wu_obs, load_gfs_daily, load_model_daily, brier_score,
)

PM_GAMMA_URL = "https://gamma-api.polymarket.com"
PM_CLOB_URL = "https://clob.polymarket.com"
EDGE_THRESHOLD = 0.05
LOOKBACK_DAYS = 58

# ─── 统一成本模型（所有分析脚本必须用这个）───

PM_WEATHER_FEE_RATE = 0.05  # Weather 类目 taker fee rate


def pm_trade_cost(market_price: float, shares: int,
                  slippage_per_share: float = 0.01,
                  maker_ratio: float = 0.0) -> float:
    """
    计算单笔交易的摩擦成本（fee + slippage）。

    market_price: bracket 的 YES 价格（0~1）
    shares: 买入股数
    slippage_per_share: 每股滑点（$）。
        当前用固定 1¢/股的悲观近似，理由：
        (1) 严格按订单簿深度算需要逐笔抓 L2 数据，CLOB API 不暴露
            只能拿到 last trade price，无法重建实际滑点
        (2) PM 天气盘单笔流动性中位数 ≈ 50-200 股 @ 1-2 cent spread
            按 spread/2 模型，1¢/股是接近上界的保守值
        (3) 对小单（< 50 股）这是合理近似；对大单（> 500 股）会低估
            但本回测固定 1 股/bracket，永远在合理区间
        如需精确建模，传入 slippage_per_share=spread/2 即可。
    maker_ratio: maker 占比（0=全 taker, 1=全 maker，maker 享 25% fee 返点）

    返回总摩擦成本（$），调用方从 pnl 中扣除。
    """
    base_fee = PM_WEATHER_FEE_RATE * market_price * (1 - market_price) * shares
    taker_fee = base_fee * (1 - maker_ratio)
    maker_rebate = base_fee * 0.25 * maker_ratio
    fee = taker_fee - maker_rebate
    slip = slippage_per_share * shares
    return fee + slip


# 默认 bankroll（仅在 sizing_fn 是 KellySizing 时才被使用）
# 当前所有分析脚本都用 sizing_fn=None → 1 股/bracket，bankroll 不参与计算
# 真实生产环境应从配置文件或 daily_pipeline 注入实际账户余额，不要依赖此默认值
DEFAULT_BANKROLL_FOR_KELLY = 100.0


def apply_costs_to_trades(trades: list[dict], sizing_fn=None,
                          slippage: float = 0.01,
                          maker_ratio: float = 0.0,
                          bankroll: float | None = None) -> list[dict]:
    """
    对 trades 列表统一应用成本模型。返回新列表，每笔交易追加 adj_cost/adj_pnl。

    参数：
    - sizing_fn: 可选，传入 SizingStrategy.size 方法来计算实际股数；
      默认 None → 每笔 1 股（用于横向对比策略，不引入仓位维度噪声）。
    - bankroll: 仅当 sizing_fn 是 KellySizing 等需要 bankroll 的策略时才使用。
      None 时回退到 DEFAULT_BANKROLL_FOR_KELLY（=100）。生产环境应显式传入。

    输赢判定：优先用 t["won"]（boolean，由回测主循环写入），
    避免用 t["pnl"]>0 判定（已扣费的 pnl 可能为 0 但实际赢）。
    若 trade 缺 "won" 字段（兼容旧数据），回退到 pnl>0。
    """
    if sizing_fn is not None and bankroll is None:
        bankroll = DEFAULT_BANKROLL_FOR_KELLY
    elif bankroll is None:
        bankroll = 0  # sizing_fn=None 时不会用到，给 0 避免下面再判 None
    result = []
    for t in trades:
        if not t.get("entry_details"):
            continue
        mp = t["entry_details"][0].get("price")
        if mp is None:
            continue
        direction = t.get("direction", "BUY_YES")

        if sizing_fn:
            shares = sizing_fn(t.get("model_prob", 0.5), mp, direction, bankroll)
        else:
            shares = 1

        if shares <= 0:
            continue

        cps = mp if direction == "BUY_YES" else (1 - mp)
        raw_cost = shares * cps
        friction = pm_trade_cost(mp, shares, slippage, maker_ratio)

        # 输赢以 t["won"] 为准；缺字段时再回退到 pnl>0
        won = t["won"] if "won" in t else (t.get("pnl", 0) > 0)
        if won:
            # 赢盘：每股结算价 = 1，净收益 = shares * (1 - cps) - 摩擦
            adj_pnl = shares * (1 - cps) - friction
        else:
            # 输盘：损失 = 投入 + 摩擦
            adj_pnl = -(raw_cost + friction)

        result.append({
            **t,
            "shares": shares,
            "raw_cost": raw_cost,
            "friction": round(friction, 4),
            "adj_cost": round(raw_cost + friction, 4),
            "adj_pnl": round(adj_pnl, 4),
        })
    return result


TIMEPOINTS = ["t24", "t12"]
TIMEPOINT_HOURS = {"t24": 24, "t12": 12}


def _pm_client() -> httpx.Client:
    return httpx.Client(proxy=PROXY, timeout=20, trust_env=False)


# ─── 策略接口 ───

@dataclass
class EntrySignal:
    timepoint: str        # "t24" | "t12"
    direction: str        # "BUY_YES" | "BUY_NO"
    market_price: float
    edge: float
    weight: float = 1.0   # 仓位权重，所有 entries 的 weight 之和 = 1


class EntryStrategy(Protocol):
    name: str
    def decide(self, model_prob: float, prices: dict[str, float],
               edge_threshold: float) -> list[EntrySignal]: ...


class AllocStrategy(Protocol):
    name: str
    def allocate(self, entries: list[EntrySignal]) -> list[EntrySignal]: ...


# ─── 入场策略 ───

class IndependentEntry:
    """每个时点独立判断 edge 是否过阈值。"""
    name = "independent"

    def decide(self, model_prob, prices, edge_threshold):
        entries = []
        for tp in TIMEPOINTS:
            if tp not in prices or prices[tp] is None:
                continue
            mp = prices[tp]
            edge = model_prob - mp
            if abs(edge) < edge_threshold:
                continue
            direction = "BUY_YES" if edge > 0 else "BUY_NO"
            entries.append(EntrySignal(tp, direction, mp, edge))
        return entries


class LockedDirectionEntry:
    """T-24h 有 edge 就锁定方向，后续时点无条件跟进。"""
    name = "locked"

    def decide(self, model_prob, prices, edge_threshold):
        t24_price = prices.get("t24")
        if t24_price is None:
            return []
        edge_t24 = model_prob - t24_price
        if abs(edge_t24) < edge_threshold:
            return []
        direction = "BUY_YES" if edge_t24 > 0 else "BUY_NO"
        entries = [EntrySignal("t24", direction, t24_price, edge_t24)]
        for tp in TIMEPOINTS[1:]:
            mp = prices.get(tp)
            if mp is None:
                continue
            edge_tp = model_prob - mp
            entries.append(EntrySignal(tp, direction, mp, edge_tp))
        return entries


class DynamicEntry:
    """T-24h 入场后，T-12h 根据 edge 变化动态决定是否追加。"""
    name = "dynamic"

    def decide(self, model_prob, prices, edge_threshold):
        t24_price = prices.get("t24")
        if t24_price is None:
            return []
        edge_t24 = model_prob - t24_price
        if abs(edge_t24) < edge_threshold:
            return []
        direction = "BUY_YES" if edge_t24 > 0 else "BUY_NO"
        entries = [EntrySignal("t24", direction, t24_price, edge_t24)]
        for tp in TIMEPOINTS[1:]:
            mp = prices.get(tp)
            if mp is None:
                continue
            edge_tp = model_prob - mp
            if direction == "BUY_YES" and edge_tp > 0:
                entries.append(EntrySignal(tp, direction, mp, edge_tp))
            elif direction == "BUY_NO" and edge_tp < 0:
                entries.append(EntrySignal(tp, direction, mp, edge_tp))
        return entries


# ─── 仓位分配策略 ───

class EqualAlloc:
    """等额分配。"""
    name = "equal"

    def allocate(self, entries):
        if not entries:
            return entries
        w = 1.0 / len(entries)
        for e in entries:
            e.weight = w
        return entries


class LaterHeavyAlloc:
    """后重分配：越接近结算权重越大（30/70 两点，20/30/50 三点）。"""
    name = "later_heavy"
    _weights_map = {1: [1.0], 2: [0.3, 0.7], 3: [0.2, 0.3, 0.5]}

    def allocate(self, entries):
        if not entries:
            return entries
        weights = self._weights_map.get(len(entries), [1.0 / len(entries)] * len(entries))
        for e, w in zip(entries, weights):
            e.weight = w
        return entries


class EdgeWeightedAlloc:
    """按 |edge| 比例分配。"""
    name = "edge_weighted"

    def allocate(self, entries):
        if not entries:
            return entries
        total_edge = sum(abs(e.edge) for e in entries)
        if total_edge == 0:
            w = 1.0 / len(entries)
            for e in entries:
                e.weight = w
        else:
            for e in entries:
                e.weight = abs(e.edge) / total_edge
        return entries


# ─── 仓位大小策略（跨 bracket 层面：每个 bracket 买多少股）───

class SizingStrategy(Protocol):
    name: str
    def size(self, model_prob: float, market_price: float,
             direction: str, bankroll: float) -> int:
        """返回该 bracket 应买的股数。"""
        ...


class FixedSharesSizing:
    """固定股数。"""
    def __init__(self, shares: int = 1):
        self.shares = shares
        self.name = f"fixed_{shares}s"

    def size(self, model_prob, market_price, direction, bankroll):
        return self.shares


# 单笔最小可下股数：低于此股数视为价值太小（手续费占比过大）→ PASS 而不是强制凑数
MIN_SHARES_PER_TRADE = int(os.environ.get("MIN_SHARES_PER_TRADE", "5"))
# Kelly 单注上限（占 bankroll 的比例），即使 fk 算出来很大也不得超过
KELLY_SINGLE_BET_CAP = float(os.environ.get("KELLY_SINGLE_BET_CAP", "0.10"))


class FixedDollarSizing:
    """固定金额，按每股成本算股数。

    若按金额算出的股数不足 MIN_SHARES_PER_TRADE，返回 0（PASS），
    避免"算出来 1 股也强行买 5 股"扭曲仓位。
    """
    def __init__(self, amount: float = 10.0):
        self.amount = amount
        self.name = f"fixed_${amount:.0f}"

    def size(self, model_prob, market_price, direction, bankroll):
        cost_per_share = market_price if direction == "BUY_YES" else (1 - market_price)
        if cost_per_share <= 0:
            return 0
        shares = int(self.amount / cost_per_share)
        return shares if shares >= MIN_SHARES_PER_TRADE else 0


class KellySizing:
    """Kelly criterion：按 edge/赔率计算最优下注比例。

    安全护栏：
    1. fk = max(fk, 0)：负 edge → 不下注
    2. fk = min(fk, KELLY_SINGLE_BET_CAP)：单注最多占 bankroll 的 10%（默认）
    3. shares < MIN_SHARES_PER_TRADE → PASS（避免小单手续费占比过大）
    """
    def __init__(self, fraction: float = 0.25):
        self.fraction = fraction
        self.name = f"kelly_{fraction:.0%}"

    def size(self, model_prob, market_price, direction, bankroll):
        c = market_price if direction == "BUY_YES" else (1 - market_price)
        if c <= 0 or c >= 1:
            return 0

        if direction == "BUY_YES":
            fk = model_prob - (1 - model_prob) * c / (1 - c)
        else:
            fk = (1 - model_prob) - model_prob * (1 - c) / c

        fk = max(fk, 0)
        # 单注上限护栏：阻止极端 edge 把仓位拉到 25%+ bankroll
        fk = min(fk, KELLY_SINGLE_BET_CAP)
        bet_amount = bankroll * fk * self.fraction
        if bet_amount <= 0:
            return 0
        shares = int(bet_amount / c)
        return shares if shares >= MIN_SHARES_PER_TRADE else 0


# ─── 模型选择策略（按城市选 GFS/ECMWF/ICON-EU/JMA）───

class ModelSelector(Protocol):
    name: str
    def select(self, city: str) -> str: ...


class FixedGFS:
    name = "gfs_only"
    def select(self, city):
        return "gfs"

class PerCityOptimal:
    """按 airport-selection-strategy.md v4 的最优模型 + cache 实测可用性

    数据源：airport-selection-strategy.md v4 (2026-04-21, 354天/51城)
    实测约束：cache 中实际有 ICON-EU 11城、JMA 全城、AROME 仅 Paris、HRRR 9个美国城市
    回退规则：表中无的城市 → "gfs"
    """
    name = "per_city"
    CITY_MODEL = {
        # ── 美国（默认 GFS；Dallas 用 ECMWF）────────────────────────────
        # 注：HRRR 在 T-24h 与 GFS 完全相同（airport-selection 明示），
        # 仅盘中实时有价值，故此处美国不选 HRRR
        "NYC": "gfs", "Chicago": "gfs", "Miami": "gfs", "Phoenix": "gfs",
        "Atlanta": "gfs", "Seattle": "gfs", "LosAngeles": "gfs",
        "Houston": "gfs", "Boston": "gfs", "Austin": "gfs",
        "Minneapolis": "gfs", "Denver": "gfs",
        "Dallas": "ecmwf",          # GFS 4.29 vs ECMWF 2.28

        # ── 欧洲（ICON-EU 全面优胜）────────────────────────────────────
        # 注：Paris 理论最优是 AROME(1.50)，但 cache 仅 Paris 一城有 AROME
        #      且 MODEL_PREFIXES 未注册 arome，回退用 ICON-EU(2.03→1.x)
        "London": "icon_eu",        # 1.55→1.22
        "Amsterdam": "icon_eu",     # 1.91→1.44
        "Madrid": "icon_eu",        # 1.62→1.33
        "Warsaw": "icon_eu",        # 2.16→1.48
        "Helsinki": "icon_eu",      # 2.28→1.52
        "Moscow": "icon_eu",        # 3.15→1.53
        "Milan": "icon_eu",         # 2.56→1.67
        "Munich": "icon_eu",        # 3.12→1.81
        "Ankara": "icon_eu",        # 2.14→1.88
        "Paris": "icon_eu",         # AROME 1.50 最优但缓存/加载器未支持，退而求其次
        "Istanbul": "ecmwf",        # ECMWF 1.62 < ICON-EU 1.66

        # ── 亚洲（按实测分别选 GFS/ECMWF/JMA）──────────────────────────
        "Tokyo": "gfs",             # GFS 1.94 < ECMWF 2.68
        "Shanghai": "gfs",          # GFS 2.25 < ECMWF 2.50
        "Singapore": "gfs",         # GFS 2.13 < ECMWF 2.61
        "Manila": "gfs",            # GFS 3.00 < ECMWF 3.86
        "Guangzhou": "gfs",         # GFS 3.30 < ECMWF 3.97
        "Busan": "jma",             # JMA 2.07 < ECMWF 2.26（唯一 JMA 胜出）
        "Seoul": "ecmwf",           # ECMWF 2.49 < GFS 7.08
        "Beijing": "ecmwf",         # ECMWF 2.95 < GFS 3.67
        "Shenzhen": "ecmwf",        # ECMWF 2.35 < GFS 3.36
        "Wuhan": "ecmwf",           # ECMWF 2.62 ≈ GFS 2.64
        "Chongqing": "ecmwf",       # ECMWF 3.00 < GFS 3.70
        "Chengdu": "ecmwf",         # ECMWF 3.20 < GFS 3.81
        "Lucknow": "ecmwf",         # ECMWF 2.10 < GFS 4.86
        "KualaLumpur": "ecmwf",     # ECMWF 2.91 < GFS 3.40
        "Jakarta": "ecmwf",         # ECMWF 2.42 ≈ GFS 2.46

        # ── 中东/非洲/南美/大洋洲 ──────────────────────────────────────
        "TelAviv": "gfs",           # GFS 1.94 < ECMWF 3.10（ECMWF 夏季+4°F）
        "Karachi": "ecmwf",
        "Jeddah": "ecmwf",
        "CapeTown": "ecmwf",
        "BuenosAires": "ecmwf",
        "MexicoCity": "ecmwf",      # ECMWF 1.97 < GFS 2.20
        "PanamaCity": "gfs",
        "Wellington": "gfs",
        "SaoPaulo": "gfs",
    }

    def select(self, city):
        return self.CITY_MODEL.get(city, "gfs")


class HRRROnly:
    """HRRR 仅作为对照基准。

    缓存覆盖：cache/hrrr_v5_{city}_*.json 共 9 个美国城市
    （NYC, Chicago, Miami, Phoenix, LosAngeles, Atlanta, Seattle, Houston, Dallas）

    注意：
    1. T-24h 时段 HRRR 输出与 GFS 几乎相同（airport-selection 明示）；
       实测 hrrr_us vs gfs_only 在 30 天窗口 PnL 差距 < $1（< 0.2%）。
    2. CITIES 字典里的美国城市 key 必须与 HRRR_CITIES 拼写一致才会真正走 HRRR。
       当前 pm_edge_compare.CITIES 里美国城市 key 是 NYC/Chicago/Miami/Phoenix/LA/Austin/Boston，
       其中 NYC/Chicago/Miami/Phoenix 命中 HRRR；LA/Austin/Boston 不在 HRRR_CITIES → 回退 GFS。
       （LA 拼写差异：CITIES key 是 "LA"，HRRR cache 是 "LosAngeles"，回退是设计行为，
        因为没有名为 "LA" 的 HRRR cache 文件可加载。）
    3. 此类的价值是验证"T-24h HRRR=GFS"假设，不是为了提升收益。
    """
    name = "hrrr_us"
    HRRR_CITIES = {"NYC", "Chicago", "Miami", "Phoenix", "LosAngeles",
                   "Atlanta", "Seattle", "Houston", "Dallas"}

    def select(self, city):
        return "hrrr" if city in self.HRRR_CITIES else "gfs"


ALL_MODEL_SELECTORS = [FixedGFS(), PerCityOptimal(), HRRROnly()]

# ─── Edge 阈值策略 ───

class ThresholdStrategy(Protocol):
    name: str
    def get_threshold(self, model_prob: float, direction: str) -> float: ...


class FixedThreshold:
    def __init__(self, value: float = 0.05):
        self.value = value
        self.name = f"fixed_{value:.0%}"

    def get_threshold(self, model_prob, direction):
        return self.value


class AdaptiveThreshold:
    """高概率/低概率 bracket 要求更大 edge（赔率低，需要更大优势才值得做）"""
    name = "adaptive"

    def get_threshold(self, model_prob, direction):
        if model_prob >= 0.7 or model_prob <= 0.3:
            return 0.15
        elif model_prob >= 0.4:
            return 0.10
        return 0.05


class DirectionSplitThreshold:
    """BUY_YES 要求更高阈值（低胜率高赔率，需要更大 edge）"""
    name = "dir_split"

    def get_threshold(self, model_prob, direction):
        return 0.10 if direction == "BUY_YES" else 0.05


ALL_THRESHOLD_STRATEGIES = [
    FixedThreshold(0.05),
    FixedThreshold(0.10),
    AdaptiveThreshold(),
    DirectionSplitThreshold(),
]


ALL_ENTRY_STRATEGIES = [IndependentEntry(), LockedDirectionEntry(), DynamicEntry()]
ALL_ALLOC_STRATEGIES = [EqualAlloc(), LaterHeavyAlloc(), EdgeWeightedAlloc()]
ALL_SIZING_STRATEGIES = [
    FixedSharesSizing(1),
    FixedSharesSizing(10),
    FixedDollarSizing(10),
    KellySizing(0.25),
    KellySizing(0.50),
]


# ─── 数据采集 ───

def fetch_settled_event(city: str, cfg: dict, target_date: str) -> dict | None:
    """获取某城市某天的已结算 PM 盘口，含 brackets + token_ids。结果缓存到本地。"""
    cache_file = CACHE_PM / f"{city}_{target_date}.json"
    if cache_file.exists():
        raw = cache_file.read_text()
        if raw == "null":
            return None
        return json.loads(raw)

    dt = datetime.strptime(target_date, "%Y-%m-%d")
    date_slug = dt.strftime("%B-%-d-%Y").lower()
    slug = f"highest-temperature-in-{cfg['slug']}-on-{date_slug}"

    client = _pm_client()
    try:
        resp = client.get(f"{PM_GAMMA_URL}/events", params={"slug": slug})
        if resp.status_code != 200 or not resp.json():
            cache_file.write_text("null")
            return None
        data = resp.json()
        event = data[0] if isinstance(data, list) else data
    except Exception:
        return None
    finally:
        client.close()

    markets = event.get("markets", [])
    if not markets:
        cache_file.write_text("null")
        return None

    unit = "C"
    brackets = []
    for m in markets:
        question = m.get("question", "")
        label = _extract_bracket_label(question)
        if label is None:
            continue

        if "°F" in question or "°f" in question:
            unit = "F"
        elif "°C" in question or "°c" in question:
            unit = "C"

        prices = m.get("outcomePrices", "[]")
        if isinstance(prices, str):
            prices = json.loads(prices)
        yes_price = float(prices[0]) if prices else 0

        token_ids = m.get("clobTokenIds", "[]")
        if isinstance(token_ids, str):
            token_ids = json.loads(token_ids)

        closed = m.get("closed", False)

        brackets.append({
            "label": label,
            "final_price": yes_price,
            "token_id": token_ids[0] if token_ids else None,
            "closed": closed,
            "question": question,
        })

    result = {"unit": unit, "brackets": brackets, "date": target_date, "city": city}
    cache_file.write_text(json.dumps(result, ensure_ascii=False))
    return result


def fetch_price_at_t_minus(token_id: str, settlement_date: str,
                           hours_before: int = 24,
                           tz_offset: int = 0,
                           local_settle_hour: int = 22,
                           client: httpx.Client | None = None) -> float | None:
    """获取结算前 T-{hours_before}h 时刻的市场价格。

    settlement_date: 当地日历日（"YYYY-MM-DD"），即 PM 盘口标题里的"X日最高温"
    tz_offset: 城市的 UTC 偏移（如 NYC=-5, Tokyo=+9）。
    local_settle_hour: 当地结算锚点小时（默认 22:00 当地时间，即日落后温度已基本固定）

    时区修复（M4）：旧版直接 datetime.strptime(date) 解析为 naive UTC 00:00，
    导致美洲城市的"T-24h 价格"实际拉的是真正 T-30+h 之前的价格，东亚城市偏 16h+。
    现在以"当地 22:00"为结算锚点反推 UTC，所有城市的 T-{hours_before}h 都对齐到
    距离当地真实结算时刻一致的偏移。
    """
    if not token_id:
        return None

    cache_file = CACHE_PM / f"prices_{token_id[-20:]}.json"
    if cache_file.exists():
        history = json.loads(cache_file.read_text())
    else:
        own_client = client is None
        if own_client:
            client = _pm_client()
        try:
            resp = client.get(
                f"{PM_CLOB_URL}/prices-history",
                params={"market": token_id, "interval": "all", "fidelity": "60"},
            )
            if resp.status_code != 200:
                return None
            history = resp.json().get("history", [])
            cache_file.write_text(json.dumps(history))
        except Exception:
            return None
        finally:
            if own_client:
                client.close()

    if not history:
        return None

    # 当地结算时刻 = 当地日历日的 local_settle_hour:00
    # 转 UTC: utc_settle = local_settle - tz_offset 小时
    settle_local_naive = datetime.strptime(settlement_date, "%Y-%m-%d").replace(
        hour=local_settle_hour
    )
    # 这是当地时刻；UTC = 当地时刻 - tz_offset
    utc_settle = settle_local_naive - timedelta(hours=tz_offset)
    target_ts = (utc_settle - timedelta(hours=hours_before)).timestamp()

    best = None
    best_diff = float("inf")
    for pt in history:
        diff = abs(pt["t"] - target_ts)
        if diff < best_diff:
            best_diff = diff
            best = pt
    # 如果最近的数据点距离目标超过 12h，说明该时刻数据缺失，不能用
    # 旧版会 fallback 到 history[0]（最早的一个点）— 这是严重前视/后视风险
    if best_diff > 12 * 3600:
        return None

    return best["p"] if best else None


def fetch_all_history(city: str, cfg: dict) -> list[dict]:
    """获取某城市所有历史已结算盘口 + 多时点价格（T-24h, T-12h）。"""
    results = []
    today = date.today()
    client = _pm_client()
    try:
        for days_back in range(1, LOOKBACK_DAYS + 1):
            d = today - timedelta(days=days_back)
            d_str = d.strftime("%Y-%m-%d")
            event = fetch_settled_event(city, cfg, d_str)
            if not event or not event.get("brackets"):
                continue

            brackets_with_price = []
            for b in event["brackets"]:
                bp = {"label": b["label"], "final_price": b["final_price"]}
                for tp, hours in TIMEPOINT_HOURS.items():
                    price = fetch_price_at_t_minus(
                        b["token_id"], d_str, hours_before=hours,
                        tz_offset=cfg.get("tz_offset", 0),
                        client=client,
                    )
                    bp[f"market_price_{tp}"] = price  # None if unavailable, never fallback to final_price
                brackets_with_price.append(bp)

            event["brackets_priced"] = brackets_with_price
            results.append(event)
    finally:
        client.close()

    return results


# ─── 模型概率（留一月CV） ───

CALIB_MODE = os.environ.get("CALIB_MODE", "expanding")  # "expanding" | "lomc"

def compute_model_probs_for_date(
    city: str, cfg: dict, test_date: str,
    forecast_daily: dict, wu_daily: dict,
    all_errors: list[tuple[str, float]],
) -> tuple[float | None, np.ndarray | None]:
    """
    expanding（默认）：只用 test_date 之前的误差，无前视偏差。
    lomc：排除当月，但可能包含未来月份（有前视偏差，仅用于对比）。
    forecast_daily 可以是 GFS/ECMWF/ICON-EU/JMA 任意模型的预报。
    """
    if test_date not in forecast_daily:
        return None, None

    if CALIB_MODE == "lomc":
        test_month = test_date[:7]
        train_errors = np.array([e for d, e in all_errors if d[:7] != test_month])
    else:
        train_errors = np.array([e for d, e in all_errors if d < test_date])

    if len(train_errors) < 30:
        return None, None

    forecast_max_f = forecast_daily[test_date]
    return forecast_max_f, train_errors


def determine_outcome(actual_val: int, brackets: list[dict], unit: str) -> str | None:
    """判断实际温度命中哪个 bracket。"""
    for b in brackets:
        label = b["label"]
        if label.endswith("+"):
            if actual_val >= int(label[:-1]):
                return label
        elif label.endswith("-"):
            if actual_val <= int(label[:-1]):
                return label
        elif "-" in label and not label.startswith("-"):
            parts = label.split("-")
            lo, hi = int(parts[0]), int(parts[1])
            if lo <= actual_val <= hi:
                return label
        else:
            if actual_val == int(label):
                return label
    return None


# ─── 回测主循环 ───

BASE_SHARES = int(os.environ.get("BASE_SHARES", "10"))


def _compute_staged_pnl(entries: list[EntrySignal], won: bool,
                        shares: int = 0) -> tuple[float, float, float]:
    """计算分批建仓的 cost/pnl/friction。含 PM 真实费率。

    shares: 每个 bracket 买多少股（默认用 BASE_SHARES）
    返回 (total_cost_with_friction, pnl_after_friction, friction)
    """
    if shares <= 0:
        shares = BASE_SHARES

    raw_cost = 0.0
    friction = 0.0
    for e in entries:
        if e.direction == "BUY_YES":
            cps = e.market_price
        else:
            cps = 1 - e.market_price
        entry_shares = shares * e.weight
        raw_cost += entry_shares * cps
        friction += pm_trade_cost(e.market_price, entry_shares)

    is_yes = entries[0].direction == "BUY_YES"
    if is_yes:
        raw_pnl = (shares - raw_cost) if won else -raw_cost
    else:
        raw_pnl = (shares - raw_cost) if not won else -raw_cost

    pnl = raw_pnl - friction
    cost = raw_cost + friction
    return cost, pnl, friction


def backtest_city(city: str, cfg: dict, events: list[dict],
                  entry_strategy: EntryStrategy,
                  alloc_strategy: AllocStrategy,
                  model_selector: ModelSelector | None = None,
                  threshold_strategy: ThresholdStrategy | None = None) -> dict | None:
    """单城市完整回测。model_selector/threshold_strategy 可选，默认 GFS + 固定 5%。"""

    model_key = model_selector.select(city) if model_selector else "gfs"
    forecast_daily = load_model_daily(city, cfg["tz_offset"], model=model_key)
    if not forecast_daily:
        forecast_daily = load_gfs_daily(city, cfg["tz_offset"])
    wu_daily = load_wu_obs(cfg["icao"])
    if not forecast_daily or not wu_daily:
        return None

    all_errors = []
    for d in sorted(forecast_daily.keys()):
        if d in wu_daily:
            all_errors.append((d, wu_daily[d] - forecast_daily[d]))

    trades = []
    model_preds = []
    market_preds = []

    for event in events:
        d = event["date"]
        unit = event["unit"]

        gfs_max_f, train_errors = compute_model_probs_for_date(
            city, cfg, d, forecast_daily, wu_daily, all_errors
        )
        if gfs_max_f is None:
            continue

        if d not in wu_daily:
            continue
        wu_actual_f = wu_daily[d]
        if unit == "C":
            wu_actual = round((wu_actual_f - 32) * 5 / 9)
        else:
            wu_actual = round(wu_actual_f)

        bp = event.get("brackets_priced", [])
        if not bp:
            continue
        bracket_input = [(b["label"], b["market_price_t24"]) for b in bp
                         if b.get("market_price_t24") is not None]
        probs = compute_bracket_probs(gfs_max_f, train_errors, bracket_input, unit)
        winning_label = determine_outcome(wu_actual, bp, unit)

        bp_map = {b["label"]: b for b in bp}

        for p in probs:
            model_p = p["model_pct"]
            bracket = p["bracket"]
            won = (bracket == winning_label)
            outcome_val = 1.0 if won else 0.0

            model_preds.append((model_p, outcome_val))
            market_preds.append((p["market_pct"], outcome_val))

            b_data = bp_map.get(bracket, {})
            prices = {}
            for tp in TIMEPOINTS:
                key = f"market_price_{tp}"
                prices[tp] = b_data.get(key)

            edge_th = EDGE_THRESHOLD
            if threshold_strategy:
                direction_hint = "BUY_YES" if model_p > (prices.get("t24") or 0.5) else "BUY_NO"
                edge_th = threshold_strategy.get_threshold(model_p, direction_hint)
            entries = entry_strategy.decide(model_p, prices, edge_th)
            if not entries:
                continue
            entries = alloc_strategy.allocate(entries)

            cost, pnl, friction = _compute_staged_pnl(entries, won)
            is_yes = entries[0].direction == "BUY_YES"
            market_price = entries[0].market_price

            trades.append({
                "city": city,
                "date": d,
                "bracket": bracket,
                "model_prob": round(model_p, 4),
                "market_price": round(market_price, 4),
                "edge": round(entries[0].edge, 4),
                "direction": entries[0].direction,
                "won": won if is_yes else not won,
                "cost": round(cost, 4),
                "pnl": round(pnl, 4),
                "friction": round(friction, 4),
                "shares": BASE_SHARES,
                "n_entries": len(entries),
                "entry_details": [
                    {"tp": e.timepoint, "price": round(e.market_price, 4),
                     "edge": round(e.edge, 4), "weight": round(e.weight, 4)}
                    for e in entries
                ],
            })

    if not trades:
        return None

    n_bets = len(trades)
    n_win = sum(1 for t in trades if t["pnl"] > 0)
    total_cost = sum(t["cost"] for t in trades)
    total_pnl = sum(t["pnl"] for t in trades)
    roi = total_pnl / total_cost if total_cost > 0 else 0

    brier_model = brier_score(
        [p for p, _ in model_preds], [o for _, o in model_preds]
    ) if model_preds else None
    brier_market = brier_score(
        [p for p, _ in market_preds], [o for _, o in market_preds]
    ) if market_preds else None

    return {
        "city": city,
        "n_days": len(events),
        "n_bets": n_bets,
        "n_win": n_win,
        "win_rate": round(n_win / n_bets, 4) if n_bets > 0 else 0,
        "total_cost": round(total_cost, 2),
        "total_pnl": round(total_pnl, 2),
        "roi": round(roi, 4),
        "brier_model": round(brier_model, 4) if brier_model else None,
        "brier_market": round(brier_market, 4) if brier_market else None,
        "trades": trades,
    }


def compute_edge_buckets(all_trades: list[dict]) -> dict:
    """按 edge 大小分桶统计。"""
    buckets = {
        "5-10%": [], "10-15%": [], "15-20%": [], "20%+": [],
    }
    for t in all_trades:
        abs_edge = abs(t["edge"])
        if abs_edge >= 0.20:
            buckets["20%+"].append(t)
        elif abs_edge >= 0.15:
            buckets["15-20%"].append(t)
        elif abs_edge >= 0.10:
            buckets["10-15%"].append(t)
        elif abs_edge >= 0.05:
            buckets["5-10%"].append(t)

    result = {}
    for name, trades in buckets.items():
        if not trades:
            result[name] = {"n": 0, "win_rate": 0, "roi": 0}
            continue
        n = len(trades)
        n_win = sum(1 for t in trades if t["pnl"] > 0)
        total_cost = sum(t["cost"] for t in trades)
        total_pnl = sum(t["pnl"] for t in trades)
        result[name] = {
            "n": n,
            "win_rate": round(n_win / n, 4) if n > 0 else 0,
            "roi": round(total_pnl / total_cost, 4) if total_cost > 0 else 0,
            "avg_edge": round(np.mean([abs(t["edge"]) for t in trades]), 4),
        }
    return result


def main():
    sys.stdout.reconfigure(line_buffering=True)
    CACHE_PM.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)

    print("=" * 70)
    print("Edge 策略历史回测 — 多时点分批建仓")
    print(f"回看 {LOOKBACK_DAYS} 天, Edge 阈值 {EDGE_THRESHOLD*100:.0f}%")
    print(f"时点: {', '.join(f'T-{h}h' for h in TIMEPOINT_HOURS.values())}")
    print("=" * 70)

    # 1. 先拉所有城市的历史数据（只需一次）
    print("\n[1/3] 拉取所有城市历史盘口（含多时点价格）...")
    city_events = {}
    for city, cfg in CITIES.items():
        events = fetch_all_history(city, cfg)
        if events:
            city_events[city] = events
            print(f"  {city}: {len(events)} 天")
        else:
            print(f"  {city}: 无数据")
    print(f"  共 {len(city_events)} 城市有数据")

    # 2. 遍历策略组合
    print("\n[2/3] 多策略回测...")
    strategy_results = {}

    for es in ALL_ENTRY_STRATEGIES:
        for acs in ALL_ALLOC_STRATEGIES:
            combo_name = f"{es.name}+{acs.name}"
            print(f"\n  --- {combo_name} ---")

            all_results = {}
            all_trades = []

            for city, cfg in CITIES.items():
                if city not in city_events:
                    continue
                result = backtest_city(
                    city, cfg, city_events[city], es, acs
                )
                if result:
                    all_results[city] = result
                    all_trades.extend(result["trades"])

            if not all_trades:
                print(f"  ❌ 无交易")
                continue

            n_total = len(all_trades)
            n_win = sum(1 for t in all_trades if t["pnl"] > 0)
            total_cost = sum(t["cost"] for t in all_trades)
            total_pnl = sum(t["pnl"] for t in all_trades)
            roi = total_pnl / total_cost if total_cost > 0 else 0

            edge_buckets = compute_edge_buckets(all_trades)

            n_multi = sum(1 for t in all_trades if t["n_entries"] > 1)

            print(f"  {n_total} 注 ({n_multi} 多时点), 胜率 {n_win}/{n_total} "
                  f"({n_win/n_total*100:.1f}%), ROI {roi*100:+.1f}%, PnL {total_pnl:+.2f}")

            strategy_results[combo_name] = {
                "entry_strategy": es.name,
                "alloc_strategy": acs.name,
                "summary": {
                    "n_cities": len(all_results),
                    "total_bets": n_total,
                    "n_multi_entry": n_multi,
                    "win_rate": round(n_win / n_total, 4),
                    "total_cost": round(total_cost, 2),
                    "total_pnl": round(total_pnl, 2),
                    "roi": round(roi, 4),
                    "by_edge_bucket": edge_buckets,
                },
                "by_city": {c: {k: v for k, v in r.items() if k != "trades"}
                            for c, r in all_results.items()},
                "trades": all_trades,
            }

    # 3. 对比表
    print("\n" + "=" * 70)
    print("[3/3] 策略对比")
    print("=" * 70)

    print(f"\n{'策略':<25} {'注数':>6} {'多时点':>6} {'胜率':>7} {'ROI':>8} {'PnL':>10}")
    print("-" * 70)
    for name, sr in sorted(strategy_results.items(), key=lambda x: -x[1]["summary"]["roi"]):
        s = sr["summary"]
        print(f"{name:<25} {s['total_bets']:>6} {s['n_multi_entry']:>6} "
              f"{s['win_rate']*100:>6.1f}% {s['roi']*100:>+7.1f}% "
              f"{s['total_pnl']:>+9.2f}")

    print(f"\n按 Edge 分桶对比:")
    for bucket in ["5-10%", "10-15%", "15-20%", "20%+"]:
        print(f"\n  {bucket}:")
        print(f"  {'策略':<25} {'注数':>6} {'胜率':>7} {'ROI':>8}")
        print(f"  {'-'*50}")
        for name, sr in sorted(strategy_results.items(), key=lambda x: -x[1]["summary"]["roi"]):
            b = sr["summary"]["by_edge_bucket"].get(bucket, {"n": 0, "win_rate": 0, "roi": 0})
            print(f"  {name:<25} {b['n']:>6} {b['win_rate']*100:>6.1f}% {b['roi']*100:>+7.1f}%")

    # 保存
    output = {
        "config": {
            "lookback_days": LOOKBACK_DAYS,
            "edge_threshold": EDGE_THRESHOLD,
            "timepoints": TIMEPOINTS,
        },
        "strategies": {name: {k: v for k, v in sr.items() if k != "trades"}
                       for name, sr in strategy_results.items()},
        "all_trades": {name: sr["trades"] for name, sr in strategy_results.items()},
    }
    out_path = OUTPUT_DIR / "edge_backtest_staged_results.json"
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"\n✅ 详细结果: {out_path}")


if __name__ == "__main__":
    main()

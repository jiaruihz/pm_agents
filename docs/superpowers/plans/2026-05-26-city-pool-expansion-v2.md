# City Pool Expansion v2 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 8 个 A 级城市升级到 T1 交易池，从 T1 移除 Beijing/Austin，对 5 个 BUY_YES 失效城市添加 side 过滤，并将执行算法切换到 `maker_queue_v1`。

**Architecture:** 所有变更集中在两个核心文件（`city_pools.py` + `paper_policy.py`），均位于 N100 weather-predict 项目。变更流程：本机 dev copy 修改 → 测试 → rsync 到 N100 → restart。执行策略通过 env var 控制，无需改代码。

**Tech Stack:** Python 3.12，weather-predict 项目（N100 生产 + 本机开发副本），rsync 部署。

---

## 背景决策摘要（来自 2026-05-26 ledger 分析）

| 变更 | 依据 |
|---|---|
| 加入 T1：Jeddah、Istanbul、Moscow、Seattle、Karachi、Ankara、Lucknow、Guangzhou | 已结算 n≥16，BUY_NO ROI +20%~+55%，胜率 67%~95%，T2 全池 ROI 16.7% vs T1 6.6% |
| 移出 T1：Beijing | BUY_YES 0/17 = -100%，BUY_NO 也亏 -10%，全 edge bucket 负，ecmwf 结构性失效 |
| 移出 T1：Austin | BUY_NO 58%胜率但 -12.6% ROI，entry_price 中位 0.65——市场定价比模型准，赔率结构不利 |
| 5 城市禁 BUY_YES | Jeddah/Istanbul/Moscow/Lucknow/Ankara 的 BUY_YES 均为 0%~20% 胜率，ROI -42% ~ -100% |
| Paris 保留 T1 但加 edge ≥ 0.30 过滤 | 仅 edge≥0.30 档正向（+11.5%），其余全负；adjacent-NO filter 已在 paper_policy.py 生效 |
| 切换 maker_queue_v1 | 升级版挂单：根据 bid/ask 排队报价而非直接 market_price，减少 taker 成本 |

---

## 文件结构

| 文件 | 变更类型 | 说明 |
|---|---|---|
| `/home/rui/projects/weather-predict/city_pools.py` | 修改 | 城市池集合定义 |
| `/home/rui/projects/weather-predict/scripts/analysis/paper_policy.py` | 修改 | 资格过滤逻辑 |
| `/home/rui/projects/weather-predict/tests/test_city_pool_expansion_v2.py` | 新建 | 验证上述两个文件的变更 |
| N100 env var `WEATHER_LIVE_EXECUTION_POLICY` | 设置 | 切换执行策略（无代码变更） |
| `/home/rui/projects/pm_agent/docs/WEATHER_STRATEGY_ENTRYPOINT.md` | 修改 | 记录本次变更决策 |

---

## Task 1: 更新 `city_pools.py`

**Files:**
- Modify: `/home/rui/projects/weather-predict/city_pools.py:11-26`

- [ ] **Step 1: 修改 TRADING_T1_CITIES 集合**

将 `city_pools.py` 中的 `TRADING_T1_CITIES` 替换为：

```python
TRADING_T1_CITIES = {
    # 原 T1（移除 Austin、Beijing）
    "Boston",
    "Chicago",
    "LA",
    "London",
    "Madrid",
    "Miami",
    "NYC",
    "Paris",
    "Phoenix",
    "Shanghai",
    "Tokyo",
    "Warsaw",
    # v2 新增（2026-05-26 ledger 分析 A 级）
    "Ankara",      # ecmwf BUY_NO: 75%胜, ROI +27%
    "Guangzhou",   # gfs   BUY_NO: 85%胜, ROI +22%
    "Istanbul",    # ecmwf BUY_NO: 89%胜, ROI +42%
    "Jeddah",      # ecmwf BUY_NO: 95%胜, ROI +55%
    "Karachi",     # ecmwf BUY_NO: 74%胜, ROI +21%
    "Lucknow",     # ecmwf BUY_NO: 67%胜, ROI +28%
    "Moscow",      # ecmwf BUY_NO: 80%胜, ROI +39%
    "Seattle",     # gfs   BUY_NO: 83%胜, ROI +27%
}
```

- [ ] **Step 2: 验证语法**

```bash
wsl -d Ubuntu-24.04 -- bash -lc "cd /home/rui/projects/weather-predict && python3 -c 'from city_pools import TRADING_T1_CITIES, RESEARCH_T2_CITIES; print(len(TRADING_T1_CITIES), sorted(TRADING_T1_CITIES))'"
```

期望输出：`20 ['Ankara', 'Boston', 'Chicago', ...]` 共 20 个城市。

---

## Task 2: 更新 `paper_policy.py` — BUY_YES 过滤 + 排除城市

**Files:**
- Modify: `/home/rui/projects/weather-predict/scripts/analysis/paper_policy.py:26-122`

- [ ] **Step 1: 在 `EXCLUDED_CITIES` 下方添加 `BUY_YES_BLOCKED_CITIES`**

在 `paper_policy.py` 第 26 行 `EXCLUDED_CITIES: set[str] = set()` 后，添加：

```python
# Cities blocked from ALL paper orders. Checked inside eligible().
EXCLUDED_CITIES: set[str] = {
    "Beijing",  # v2: ecmwf 结构性失效，BUY_YES 0/17 wins，BUY_NO 也亏
    "Austin",   # v2: entry_price 高（中位 0.65），赔率结构不利，BUY_NO 58%胜但 ROI -12.6%
}

# Cities blocked from BUY_YES orders only.
# Evidence: 0%~20% win_rate on BUY_YES, ROI -42% ~ -100% across 20+ settled trades.
# Threshold for adding: ≥15 settled BUY_YES trades all negative, or structural pattern confirmed.
BUY_YES_BLOCKED_CITIES: set[str] = {
    "Ankara",    # ecmwf BUY_YES: 0/3 wins, ROI -100%
    "Istanbul",  # ecmwf BUY_YES: 1/11 wins, ROI -65%
    "Jeddah",    # ecmwf BUY_YES: 1/5 wins,  ROI -42%
    "Lucknow",   # ecmwf BUY_YES: 0/3 wins,  ROI -100%
    "Moscow",    # ecmwf BUY_YES: 0/2 wins,  ROI -100%
}

# Paris edge filter: only high-conviction signals (≥0.30) are profitable.
# edge<0.30 → ROI -18% to -100%; edge≥0.30 → ROI +11.5% (n=5).
PARIS_MIN_EDGE: float = 0.30
```

- [ ] **Step 2: 更新 `eligible()` 函数以检查 side 过滤器**

将 `eligible()` 函数从：

```python
def eligible(record: dict[str, Any], edge_threshold: float = EDGE_THRESHOLD) -> tuple[bool, str]:
    city = record.get("city")
    city_name = str(city or "")
    if city_name in EXCLUDED_CITIES:
        return False, "excluded_city"
    if city_name not in FULL_CITY_CONFIGS:
        return False, "unknown_city"
    if not is_t24(record):
        return False, "not_t24"
    try:
        abs_edge = abs(float(record.get("edge") or 0.0))
    except (TypeError, ValueError):
        return False, "bad_edge"
    if abs_edge < edge_threshold:
        return False, "edge_below_threshold"
    if not (record.get("condition_id") or (record.get("city") and record.get("event_date") and record.get("bracket"))):
        return False, "missing_market_key"
    return True, "ok"
```

修改为：

```python
def eligible(record: dict[str, Any], edge_threshold: float = EDGE_THRESHOLD) -> tuple[bool, str]:
    city = record.get("city")
    city_name = str(city or "")
    if city_name in EXCLUDED_CITIES:
        return False, "excluded_city"
    if city_name not in FULL_CITY_CONFIGS:
        return False, "unknown_city"
    if not is_t24(record):
        return False, "not_t24"
    try:
        abs_edge = abs(float(record.get("edge") or 0.0))
    except (TypeError, ValueError):
        return False, "bad_edge"
    if abs_edge < edge_threshold:
        return False, "edge_below_threshold"
    # BUY_YES blocked for structurally negative cities
    side = side_for_record(record)
    if side == "BUY_YES" and city_name in BUY_YES_BLOCKED_CITIES:
        return False, "buy_yes_blocked_city"
    # Paris: require higher edge threshold
    if city_name == "Paris":
        if abs_edge < PARIS_MIN_EDGE:
            return False, "paris_edge_below_threshold"
    if not (record.get("condition_id") or (record.get("city") and record.get("event_date") and record.get("bracket"))):
        return False, "missing_market_key"
    return True, "ok"
```

- [ ] **Step 3: 验证语法**

```bash
wsl -d Ubuntu-24.04 -- bash -lc "cd /home/rui/projects/weather-predict && python3 -m py_compile scripts/analysis/paper_policy.py && echo OK"
```

期望输出：`OK`

---

## Task 3: 新建测试文件

**Files:**
- Create: `/home/rui/projects/weather-predict/tests/test_city_pool_expansion_v2.py`

- [ ] **Step 1: 写测试文件**

```python
"""Tests for city pool v2 expansion and paper_policy side filters."""
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "scripts" / "analysis"))

import city_pools
from paper_policy import (
    ADJACENT_NO_ISOLATED_PAIR_BLOCKED_CITIES,
    BUY_YES_BLOCKED_CITIES,
    EXCLUDED_CITIES,
    PARIS_MIN_EDGE,
    eligible,
)


# ── city_pools ──────────────────────────────────────────────────────────────

def test_new_cities_in_t1():
    new_t1 = {"Ankara", "Guangzhou", "Istanbul", "Jeddah", "Karachi", "Lucknow", "Moscow", "Seattle"}
    missing = new_t1 - city_pools.TRADING_T1_CITIES
    assert not missing, f"Expected in T1 but missing: {missing}"


def test_removed_cities_not_in_t1():
    removed = {"Beijing", "Austin"}
    still_in = removed & city_pools.TRADING_T1_CITIES
    assert not still_in, f"Should not be in T1: {still_in}"


def test_t1_size():
    # 12 original − 2 removed + 8 added = 18; Boston/Phoenix also original T1 → 20 total
    assert len(city_pools.TRADING_T1_CITIES) == 20, (
        f"Expected 20 T1 cities, got {len(city_pools.TRADING_T1_CITIES)}: "
        f"{sorted(city_pools.TRADING_T1_CITIES)}"
    )


def test_t1_t2_disjoint():
    overlap = city_pools.TRADING_T1_CITIES & city_pools.RESEARCH_T2_CITIES
    assert not overlap, f"T1 and T2 overlap: {overlap}"


def test_city_pool_function_new_t1():
    for city in ["Jeddah", "Istanbul", "Moscow", "Seattle"]:
        assert city_pools.city_pool(city) == "t1_trading", f"{city} should be t1_trading"


def test_city_pool_function_excluded():
    for city in ["Beijing", "Austin"]:
        assert city_pools.city_pool(city) == "t2_research", f"{city} should be t2_research"


# ── paper_policy — excluded cities ──────────────────────────────────────────

def _base_record(city, side, edge, hours=24.0, condition_id="0xabc"):
    return {
        "city": city,
        "side": side,
        "edge": edge if side == "BUY_YES" else -edge,
        "abs_edge": edge,
        "time_bucket": "t24",
        "hours_to_settle": hours,
        "condition_id": condition_id,
        "event_date": "2026-06-01",
        "bracket": "25",
    }


def test_beijing_excluded():
    rec = _base_record("Beijing", "BUY_NO", 0.20)
    ok, reason = eligible(rec)
    assert not ok
    assert reason == "excluded_city"


def test_austin_excluded():
    rec = _base_record("Austin", "BUY_NO", 0.20)
    ok, reason = eligible(rec)
    assert not ok
    assert reason == "excluded_city"


# ── paper_policy — BUY_YES blocked cities ───────────────────────────────────

def test_buy_yes_blocked_for_jeddah():
    rec = _base_record("Jeddah", "BUY_YES", 0.25)
    ok, reason = eligible(rec)
    assert not ok
    assert reason == "buy_yes_blocked_city"


def test_buy_no_allowed_for_jeddah():
    rec = _base_record("Jeddah", "BUY_NO", 0.25)
    ok, reason = eligible(rec)
    assert ok, f"BUY_NO Jeddah should be eligible, got: {reason}"


def test_buy_yes_blocked_for_istanbul():
    rec = _base_record("Istanbul", "BUY_YES", 0.15)
    ok, reason = eligible(rec)
    assert not ok
    assert reason == "buy_yes_blocked_city"


def test_buy_yes_blocked_for_moscow():
    rec = _base_record("Moscow", "BUY_YES", 0.20)
    ok, reason = eligible(rec)
    assert not ok
    assert reason == "buy_yes_blocked_city"


def test_buy_yes_allowed_for_karachi():
    # Karachi BUY_YES ROI +18%, not in blocked list
    rec = _base_record("Karachi", "BUY_YES", 0.20)
    ok, reason = eligible(rec)
    assert ok, f"Karachi BUY_YES should be eligible, got: {reason}"


def test_buy_yes_allowed_for_seattle():
    rec = _base_record("Seattle", "BUY_YES", 0.20)
    ok, reason = eligible(rec)
    assert ok, f"Seattle BUY_YES should be eligible, got: {reason}"


# ── paper_policy — Paris edge filter ────────────────────────────────────────

def test_paris_low_edge_rejected():
    rec = _base_record("Paris", "BUY_NO", 0.20)  # < PARIS_MIN_EDGE=0.30
    ok, reason = eligible(rec)
    assert not ok
    assert reason == "paris_edge_below_threshold"


def test_paris_high_edge_accepted():
    rec = _base_record("Paris", "BUY_NO", 0.35)  # >= PARIS_MIN_EDGE
    ok, reason = eligible(rec)
    assert ok, f"Paris edge=0.35 should be eligible, got: {reason}"


def test_paris_min_edge_boundary():
    assert PARIS_MIN_EDGE == 0.30


# ── paper_policy — unchanged existing behaviour ──────────────────────────────

def test_edge_below_threshold_still_rejected():
    rec = _base_record("Warsaw", "BUY_NO", 0.05)
    ok, reason = eligible(rec)
    assert not ok
    assert reason == "edge_below_threshold"


def test_normal_t2_city_still_eligible():
    # Singapore is T2 but not excluded — should produce paper orders
    rec = _base_record("Singapore", "BUY_NO", 0.20)
    ok, reason = eligible(rec)
    assert ok, f"Singapore BUY_NO should be eligible, got: {reason}"
```

- [ ] **Step 2: 运行测试（预期全部 FAIL，因为代码还没改）**

```bash
wsl -d Ubuntu-24.04 -- bash -lc "cd /home/rui/projects/weather-predict && python3 -m pytest tests/test_city_pool_expansion_v2.py -v 2>&1 | head -50"
```

预期：大量 FAIL（`BUY_YES_BLOCKED_CITIES` 未定义，T1 城市数不对等）

---

## Task 4: 实施代码变更（Task 1 + Task 2）并通过测试

- [ ] **Step 1: 按 Task 1 修改 `city_pools.py`**

- [ ] **Step 2: 按 Task 2 修改 `paper_policy.py`**

- [ ] **Step 3: 运行全部测试**

```bash
wsl -d Ubuntu-24.04 -- bash -lc "cd /home/rui/projects/weather-predict && python3 -m pytest tests/test_city_pool_expansion_v2.py -v"
```

期望输出：`20 passed`

- [ ] **Step 4: 运行原有测试确认未回归**

```bash
wsl -d Ubuntu-24.04 -- bash -lc "cd /home/rui/projects/weather-predict && python3 -m pytest tests/ -v 2>&1 | tail -10"
```

期望：所有测试通过（或已有的失败不变）

- [ ] **Step 5: py_compile 验证两个改动文件**

```bash
wsl -d Ubuntu-24.04 -- bash -lc "cd /home/rui/projects/weather-predict && python3 -m py_compile city_pools.py scripts/analysis/paper_policy.py && echo 'compile OK'"
```

- [ ] **Step 6: Commit**

```bash
wsl -d Ubuntu-24.04 -- bash -lc "cd /home/rui/projects/weather-predict && git add city_pools.py scripts/analysis/paper_policy.py tests/test_city_pool_expansion_v2.py && git commit -m 'feat: city pool v2 — add 8 A-grade T1 cities, drop Beijing/Austin, BUY_YES filter for 5 cities, Paris edge>=0.30

Analysis basis: ledger 2026-05-08~05-25, 939 settled trades
- Add: Ankara/Guangzhou/Istanbul/Jeddah/Karachi/Lucknow/Moscow/Seattle (T2→T1)
- Remove: Beijing (ecmwf structural fail, all edge buckets negative)
          Austin (entry_price p50=0.65, payout structure unfavorable)
- BUY_YES blocked: Ankara/Istanbul/Jeddah/Lucknow/Moscow (0-20% win rate)
- Paris: require abs_edge >= 0.30 (only profitable tier)'"
```

---

## Task 5: 部署到 N100

- [ ] **Step 1: rsync 到 N100**

```bash
wsl -d Ubuntu-24.04 -- bash -lc "cd /home/rui/projects/weather-predict && rsync -av city_pools.py scripts/analysis/paper_policy.py tests/test_city_pool_expansion_v2.py jiarui@192.168.0.200:/home/jiarui/projects/weather-predict/ --dry-run"
```

确认输出路径正确后，去掉 `--dry-run` 正式推送：

```bash
wsl -d Ubuntu-24.04 -- bash -lc "rsync -av /home/rui/projects/weather-predict/city_pools.py /home/rui/projects/weather-predict/scripts/analysis/paper_policy.py /home/rui/projects/weather-predict/tests/test_city_pool_expansion_v2.py jiarui@192.168.0.200:/home/jiarui/projects/weather-predict/"
```

注意：tests 目录需单独 rsync（N100 上路径是 `/home/jiarui/projects/weather-predict/tests/`）：
```bash
wsl -d Ubuntu-24.04 -- bash -lc "rsync -av /home/rui/projects/weather-predict/tests/test_city_pool_expansion_v2.py jiarui@192.168.0.200:/home/jiarui/projects/weather-predict/tests/"
```

- [ ] **Step 2: N100 smoke — 验证 city_pools 导入**

```bash
wsl -d Ubuntu-24.04 -- bash -lc "ssh jiarui@192.168.0.200 'cd /home/jiarui/projects/weather-predict && python3 -c \"from city_pools import TRADING_T1_CITIES; print(len(TRADING_T1_CITIES), sorted(TRADING_T1_CITIES))\"'"
```

期望：20 个城市，Jeddah/Istanbul/Moscow 等出现在列表中，Beijing/Austin 不出现。

- [ ] **Step 3: N100 smoke — 验证 paper_policy 导入**

```bash
wsl -d Ubuntu-24.04 -- bash -lc "ssh jiarui@192.168.0.200 'cd /home/jiarui/projects/weather-predict && python3 -c \"from scripts.analysis.paper_policy import eligible, BUY_YES_BLOCKED_CITIES, EXCLUDED_CITIES; print(\\\"blocked:\\\", BUY_YES_BLOCKED_CITIES); print(\\\"excluded:\\\", EXCLUDED_CITIES)\"'"
```

---

## Task 6: 切换执行策略到 `maker_queue_v1`

执行策略通过 env var `WEATHER_LIVE_EXECUTION_POLICY` 控制，默认 `mid_price_core_v1`。

- [ ] **Step 1: 在 N100 bash 环境中持久化 env var**

```bash
wsl -d Ubuntu-24.04 -- bash -lc "ssh jiarui@192.168.0.200 'echo \"export WEATHER_LIVE_EXECUTION_POLICY=maker_queue_v1\" >> ~/.bashrc && echo done'"
```

- [ ] **Step 2: 验证 env var 生效**

```bash
wsl -d Ubuntu-24.04 -- bash -lc "ssh jiarui@192.168.0.200 'source ~/.bashrc && echo WEATHER_LIVE_EXECUTION_POLICY=\$WEATHER_LIVE_EXECUTION_POLICY'"
```

期望：`WEATHER_LIVE_EXECUTION_POLICY=maker_queue_v1`

- [ ] **Step 3: 重启 N100 生产服务**

```bash
wsl -d Ubuntu-24.04 -- bash -lc "ssh jiarui@192.168.0.200 'cd /home/jiarui/projects/weather-predict && scripts/ops/doctor_restart.sh'"
```

---

## Task 7: 更新文档（记录）

**Files:**
- Modify: `/home/rui/projects/pm_agent/docs/WEATHER_STRATEGY_ENTRYPOINT.md`

- [ ] **Step 1: 在 ENTRYPOINT 文档头部加入变更记录节**

在文档最顶部（`# Weather Strategy Entrypoint` 标题下）添加：

```markdown
## 近期策略变更

### 2026-05-26 城市池 v2（基于 ledger 分析 939 笔已结算交易）

**T1 新增（A 级，T2→T1）：**
- Ankara, Guangzhou, Istanbul, Jeddah, Karachi, Lucknow, Moscow, Seattle
- 依据：BUY_NO ecmwf/gfs 胜率 67%~95%，ROI +20%~+55%

**T1 移除（完全禁止 paper order）：**
- Beijing：ECMWF 结构性失效，BUY_YES 0/17 wins，BUY_NO 全 edge bucket 均亏
- Austin：entry_price 中位 0.65，赔率结构不利（BUY_NO 58% 胜率但 ROI -12.6%）

**BUY_YES 禁用城市（`BUY_YES_BLOCKED_CITIES`）：**
- Ankara, Istanbul, Jeddah, Lucknow, Moscow（0%~20% 胜率，ROI -42% ~ -100%）

**Paris 过滤收紧：** abs_edge ≥ 0.30 才允许（之前是 0.10）

**执行策略：** `WEATHER_LIVE_EXECUTION_POLICY=maker_queue_v1`（N100 ~/.bashrc）

**T1 现有城市（v2，共 20 个）：**
Ankara, Boston, Chicago, Guangzhou, Istanbul, Jeddah, Karachi, LA,
London, Lucknow, Madrid, Miami, Moscow, NYC, Paris, Phoenix, Seattle,
Shanghai, Tokyo, Warsaw
```

- [ ] **Step 2: Commit**

```bash
wsl -d Ubuntu-24.04 -- bash -lc "cd /home/rui/projects/pm_agent && git add docs/WEATHER_STRATEGY_ENTRYPOINT.md docs/superpowers/plans/2026-05-26-city-pool-expansion-v2.md && git commit -m 'docs: record city pool v2 changes and expansion plan'"
```

---

## 验收检查

完成后逐项确认：

- [ ] `len(TRADING_T1_CITIES) == 20`（含 8 个新城市，不含 Beijing/Austin）
- [ ] N100 `paper_policy.py` 导入 `BUY_YES_BLOCKED_CITIES` 无报错
- [ ] N100 smoke：`eligible({"city":"Beijing",...})` → `(False, "excluded_city")`
- [ ] N100 smoke：`eligible({"city":"Jeddah","side":"BUY_YES",...})` → `(False, "buy_yes_blocked_city")`
- [ ] N100 smoke：`eligible({"city":"Jeddah","side":"BUY_NO","abs_edge":0.25,...})` → `(True, "ok")`
- [ ] N100 smoke：`eligible({"city":"Paris","side":"BUY_NO","abs_edge":0.20,...})` → `(False, "paris_edge_below_threshold")`
- [ ] `echo $WEATHER_LIVE_EXECUTION_POLICY` on N100 → `maker_queue_v1`
- [ ] `doctor_restart.sh` 无错误退出
- [ ] WEATHER_STRATEGY_ENTRYPOINT.md 已更新

---

## 回滚方案

如果新城市出现连续亏损（≥5 个交易日，组合 ROI < -10%）：

1. 将问题城市加回 `EXCLUDED_CITIES`（`paper_policy.py`）
2. rsync → N100 → restart（无需代码审批，只改配置）
3. 不需要 revert git commit，直接加入黑名单即可

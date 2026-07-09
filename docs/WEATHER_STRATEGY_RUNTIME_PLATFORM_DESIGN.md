# 天气策略运行时平台 · 产品化设计

Status: design-draft
Updated: 2026-07-09 首版蓝图（统一启动器 + 策略接口 + DB 控制面 + 数据源领域模型）；补 §12 看板升级计划 + §13 数据模型 ER 关联；补 §6.5-6.8 数据源表粒度修订
Source of truth: no（目标草案，未实现）；架构口径服从 WEATHER_ARCHITECTURE_SPINE / WEATHER_SYSTEM_CONTRACT
Superseded by / Used by: 取代 `docs/archive/UNIFIED_STRATEGY_PLATFORM_REFACTOR_PLAN.md`（旧 PMM/ARB 版）的目标定位；落地后由 WEATHER_STRATEGY_ENTRYPOINT / WEATHER_STRATEGY_REGISTRY 引用

> 一句话：把现在"一个策略头 = 一个 600 行 runner + 一个 40 个环境变量的 `start_*.sh` + 一堆 pidfile/tmux"的作坊，
> 收敛成"策略头只实现一个接口 → 单一 supervisor 从 DB 定义的 instance 统一拉起/管控 → 状态与参数统一通过控制面改，且全程可追溯"。
> **主血缘（signal→plan→order→fill→settlement、fact 表）是永久基建，本平台围着它建，不动它。**

---

## 0. 为什么现在要做（现状盘点）

当前天气线的运行时是有机生长出来的，能跑，但已经到了边际维护成本很高的临界点：

| 维度 | 现状 | 痛点 |
|---|---|---|
| 策略头启动 | 每个 head 一个 `scripts/ops/start_*.sh`，携带 ~40 个 env-var 旋钮（见 `start_weather_theta_current_yes_tiny_live.sh`） | 加一条策略 = 复制一个大 shell + 一个大 runner；改一个参数 = 改 shell 环境变量，无审计、无类型校验 |
| 运行时管理 | pidfile + `tmux -L weather-jrs` + nohup，散落在 `runtime/weather_edge_v1/<instance>/loop.pid` | 无统一 start/stop/restart；崩溃无自动重启记录；跨实例的全局 notional 上限没有任何一个进程看得见 |
| 策略代码 | 每个 runner（`low_price_yes_lottery_tiny_live.py` 等）各自重写：arg 解析、循环、pidfile、snapshot/book 拉取、shadow telemetry 落盘、plan/order emit、heartbeat JSON | 横切逻辑重复 N 份，改一处要改 N 处；行为不一致（有的写 shadow，有的不写） |
| 状态/元数据 | `weather_strategy_runtime_registry` 是**扫描反射**表：`refresh_weather_strategy_runtime_registry.py` 读本地 artifact 反推状态 | 状态是**被推断**出来的，不是**被设置**的；无法通过接口改状态；`lifecycle_status` 里混进了 `stale` 这种健康信号，三套 enum（lifecycle/execution/health）语义重叠 |
| 数据源配置 | 每城 source 在 `weather_data_feed/source_profiles.json`；拉取频率、`max_obs_age_min`/`max_snapshot_age_min` 陈旧阈值散落在各 `start_*.sh` 和 `observation_clock` | 每城 × feed 的 cadence/陈旧阈值/fallback 策略不是数据，是散落的字面量；**ECMWF 静默 fallback GFS（7/02-05 污染三天信号）这类事故没有一等公民的可观测入口** |
| 策略目录模型 | `src/strategies/<key>/manifest.yaml` + 手写 parser（`registry.py`）→ `StrategyManifest` | 只是**静态 catalog**，与运行时脱节；只有 5 个目录有 manifest；真正在跑的十几个 head 都不在这个模型里 |

> 历史包袱说明：2026-03 曾有一版 "Unified Strategy Platform"（`docs/archive/UNIFIED_STRATEGY_PLATFORM_REFACTOR_PLAN.md`），
> 定义了 `strategies / strategy_instances / strategy_instance_state / strategy_instance_snapshots` 和一个独立
> `runtime/strategy_runtime.db` + BFF server。那是**旧 PMM/ARB 框架**，已不是活跃主线。**本设计复用它的 instance/state/snapshot 词汇，
> 但重新挂到 weather canonical DB（`runtime/weather.db`）上，并针对天气线现实（多 head、shadow/telemetry 模式、notional 上限、数据源 cadence）扩展；不复活那个独立 DB。**

---

## 1. 目标与不变量

### 1.1 目标（本轮四个决策已锁定）

1. **统一策略接口**：每个策略头实现同一个 `StrategyHead` 接口，只负责"决策"，横切逻辑交给 harness。
2. **单一 supervisor 守护进程**：一个常驻进程从 DB 的 instance 定义统一 start/stop/restart/心跳/上限管控，取代 tmux+pidfile+nohup 散落。
3. **DB 优先控制面**：instance 的定义、参数、期望状态是 `runtime/weather.db` 里的行；状态统一通过控制面接口/CLI 改，不再改 shell 重启。
4. **数据源一等公民**：每城 × feed_kind × cadence × source × live_eligible 建成显式领域模型 + 表；静默 fallback 变成可观测、可 gate 的行。

### 1.2 硬不变量（不许被平台化破坏）

- **主血缘不动**：`fact_signal_candidates → plan → orders → fills/fact_trades → settlements`。平台是围绕它的编排层（L4.5/L5），产出仍写回同一批 canonical 表。
- **资金安全硬边界保留**：涉及 live / 私钥 / 余额 / 真实 CLOB 下单 / 删数据 / 远端部署，保留显式确认、暂停开关、notional 上限、可追溯日志（CLAUDE.md §3）。
- **不默认加 gate**：平台只保留"数据质量 / 资金安全 / 执行质量"这类正当边界，不把"追坏例子一条条补洞"的过拟合过滤器制度化。

### 1.3 关键张力及其化解：DB 优先控制面 vs git-first 硬边界

CLAUDE.md §3 规定生产行为变更（city_pools / paper_policy / execution_policy / live_cycle）走 **git-first**、不许直推、要可回溯。本轮选了 **DB 优先控制面**，二者有冲突点。化解方式（本设计的核心承诺）：

- **定义/参数仍以 git 为作者来源**：策略定义与 params 仍写成 git 里的 YAML spec，`sync` 进 DB 作为运行时事实源。DB 行记录 `spec_commit`（git sha）+ `params_hash`，任何运行时状态都能追回到一个已提交的定义。
- **只有"运行时控制动作"是 DB 可变的**：start/stop/pause/enable-live/set-cap/shelve 通过控制面接口改 DB，不改 shell。这就是"状态统一通过接口改"。
- **可追溯用 append-only 控制日志替代"每次改状态都 git commit"**：每一次控制面变更写 `strategy_control_log`（actor/action/from→to/reason/spec_commit/params_hash/ts）。这条 append-only 审计链是 DB-first 与"可追溯"硬边界共存的保证。
- **不可逆动作仍要显式确认**：enable-live 和调高 notional 上限，仍需显式确认 + 健康/coverage preflight，且落审计日志。

> 若你后续更想要**纯 git-first**（放弃 DB 可变、状态改回走 commit）或**纯 DB**（放弃 git 作者来源），这是一个可切换的策略点，在 §7 开放问题里标了出来。

---

## 2. 总体分面（四个平面）

把系统拆成四个正交平面，各有清晰边界和"真相源"。这是整个设计的骨架：

```text
┌─────────────────────────────────────────────────────────────────────┐
│  定义面 Definition   策略"是什么" = StrategyHead 接口 + ParamsSchema  │
│                      真相源: git YAML spec → sync 进 strategy_def 表   │
├─────────────────────────────────────────────────────────────────────┤
│  控制面 Control      期望状态 + 变更 = strategy_instance (desired)    │
│                      真相源: runtime/weather.db（DB 优先），经 pmctl  │
│                      改，全程 append 到 strategy_control_log          │
├─────────────────────────────────────────────────────────────────────┤
│  运行时面 Runtime    实际进程 + 心跳 = supervisor + BaseRunner harness│
│                      真相源: 各 head 由 harness push 的 runtime 行     │
├─────────────────────────────────────────────────────────────────────┤
│  数据源面 Data-Feed  每城×source/station 的配置、run、event、health     │
│                      真相源: git profile/registry → source profile 表 + │
│                      采集器 push 的 run/event/health 行                 │
└─────────────────────────────────────────────────────────────────────┘
                    ↓ 全部产出仍写回 ↓
       主血缘 canonical: fact_signal_candidates → plan → orders → fills → settlements
```

核心思想：**head 只写决策；harness 写横切；supervisor 写编排；控制面写期望；采集器写数据健康。** 每一层职责单一、接口清晰、可独立测试。

---

## 3. 定义面：策略接口 + 元数据模型

### 3.1 `StrategyHead` 接口（每个 head 实现）

head 只实现"决策"，横切（循环、心跳、下单、上限、shadow 落盘、日志）全部由 harness 提供。接口草图：

```python
class StrategyHead(Protocol):
    def describe(self) -> StrategySpec:
        """静态元数据：strategy_key、family、支持的 execution_mode、
        依赖的 feature 字段、依赖的 feed（required_feeds）、ParamsSchema。"""

    def setup(self, ctx: RunContext) -> None:
        """绑定只读 fact 表连接、已校验的 params、feature store refs、
        共享 order executor handle、feed 订阅。harness 已把这些准备好。"""

    def tick(self, ctx: RunContext) -> TickResult:
        """一个决策周期：读 candidates/book → 产出 decisions。
        返回 (shadow_telemetry_rows, accepted_plans, heartbeat_fields)。
        *不*自己 emit order、*不*自己写 pidfile、*不*自己算 cap——harness 干这些。"""

    def on_pause(self) -> None: ...
    def on_resume(self) -> None: ...
    def teardown(self) -> None: ...
```

关键动作：
- **~40 个 env-var 旋钮 → 一个 typed `ParamsSchema`（pydantic）**。`execution_policy`、风控上限、decision window、city pool、source policy 从 shell 字符串变成结构化字段。
- **健康是 head 主动上报，不是被扫描**：`tick` 返回 `heartbeat_fields`（last_data_ts、candidate_rows、blockers），harness 落到 runtime 表。这把 registry 从"扫描反射"翻转成"push 上报"。

### 3.2 `BaseRunner` harness（所有 head 共用，抽一次）

harness 拥有全部横切逻辑，是消灭重复的关键：

- 从控制面加载 instance 配置 + 校验 params；
- 主循环 cadence + 优雅 pause/resume/stop（响应 supervisor 信号）；
- 心跳：每 tick 把 runtime 行 push 进 DB；
- **notional 上限强制**：单单 / 单城单日 / 全局单日，emit 前拦截；
- order emit 走**共享 executor**（`weather_order_executor.py`，已有 batch + notional/pause/cancel 保护）；
- shadow/telemetry 统一落盘 + 写 `weather_strategy_shadow_queue`；
- 结构化日志。

移植后，每个 head 从"600 行 runner"缩到"几十行决策逻辑 + 一个 ParamsSchema"。

### 3.3 元数据模型：`strategy_def` 表（productized 版 manifest）

`src/strategies/<key>/manifest.yaml` + `registry.py` 手写 parser 的产品化继任者。仍以 git YAML 为作者来源，sync 进 DB：

```sql
CREATE TABLE strategy_def (
    strategy_key        TEXT PRIMARY KEY,
    family              TEXT NOT NULL,
    strategy_group      TEXT NOT NULL DEFAULT 'weather',
    domain              TEXT NOT NULL DEFAULT 'weather',
    head_module         TEXT NOT NULL,          -- import 路径: 实现 StrategyHead 的类
    params_schema_ref   TEXT NOT NULL,          -- ParamsSchema 定位
    capabilities_json   TEXT NOT NULL,          -- 支持哪些 execution_mode: live/shadow/telemetry/...
    required_feeds_json TEXT NOT NULL DEFAULT '[]',  -- 依赖的 feed_kind 列表
    default_params_json TEXT NOT NULL DEFAULT '{}',
    spec_commit         TEXT,                   -- git sha，审计锚点
    is_active           INTEGER NOT NULL DEFAULT 1,
    description         TEXT NOT NULL DEFAULT '',
    updated_at_utc      TEXT NOT NULL
);
```

---

## 4. 控制面：instance 期望状态 + 审计（DB 优先）

### 4.1 状态模型统一（消除三套重叠 enum）

现状 `weather_strategy_runtime_registry` 把 `lifecycle_status`（9 值）、`execution_mode`（7 值）、`health_status`（6 值）混在一张表，且 `lifecycle_status=stale` 把健康信号塞进了生命周期字段。重构成**两轴期望 + 两轴运行时**：

| 轴 | 值域 | 谁写 | 语义 |
|---|---|---|---|
| `desired_status`（控制） | `enabled / paused / shelved / blocked` | 操作者经 pmctl | 我想让它处于什么状态 |
| `execution_mode`（控制） | `live / zero_notional_shadow / telemetry / paper / research / monitor / historical` | 操作者经 pmctl | 沿用现有词汇（看板依赖） |
| `process_status`（运行时，派生） | `running / starting / stopped / crashed / stale` | supervisor | 进程实际在不在 |
| `health_status`（运行时，派生） | `healthy / idle / stale / blocked / unknown` | harness push | 数据/决策是否新鲜可用 |

所有状态迁移只经控制面 API；运行时两轴由 push 派生。这样"策略在不在跑"（process）、"想不想让它跑"（desired）、"跑得健不健康"（health）、"以什么资金模式跑"（execution）彻底解耦。

### 4.2 `strategy_instance`（控制面，期望态）

一条运行时实例一行；合并现 registry 的配置列 + 旧平台的 `strategy_instances`：

```sql
CREATE TABLE strategy_instance (
    instance_id           TEXT PRIMARY KEY,
    strategy_key          TEXT NOT NULL REFERENCES strategy_def(strategy_key),
    label                 TEXT NOT NULL,
    family                TEXT NOT NULL,
    desired_status        TEXT NOT NULL,   -- enabled/paused/shelved/blocked
    execution_mode        TEXT NOT NULL,   -- live/zero_notional_shadow/...
    params_json           TEXT NOT NULL,   -- 经 ParamsSchema 校验
    params_hash           TEXT NOT NULL,
    spec_commit           TEXT,            -- 追回定义
    source_subscription_id TEXT,           -- → weather_strategy_source_subscription
    market_data_source    TEXT NOT NULL DEFAULT 'mac-weather-data-feed',
    cap_order_notional    REAL,
    cap_city_day_notional REAL,
    cap_total_day_notional REAL,
    live_enabled          INTEGER NOT NULL DEFAULT 0,  -- 硬 gate，enable-live 才置 1
    host                  TEXT NOT NULL DEFAULT 'mac',
    runtime_dir           TEXT,
    notes                 TEXT,
    created_at_utc        TEXT NOT NULL,
    updated_at_utc        TEXT NOT NULL
);
```

### 4.3 `strategy_instance_runtime`（运行时面，实际态，harness/supervisor push）

取代扫描：

```sql
CREATE TABLE strategy_instance_runtime (
    instance_id       TEXT PRIMARY KEY REFERENCES strategy_instance(instance_id),
    process_status    TEXT NOT NULL DEFAULT 'stopped',
    pid               INTEGER,
    supervisor_id     TEXT,
    heartbeat_at_utc  TEXT,
    last_tick_ts_utc  TEXT,
    last_data_ts_utc  TEXT,
    candidate_rows    INTEGER NOT NULL DEFAULT 0,
    plan_rows         INTEGER NOT NULL DEFAULT 0,
    live_order_rows   INTEGER NOT NULL DEFAULT 0,
    shadow_rows       INTEGER NOT NULL DEFAULT 0,
    health_status     TEXT NOT NULL DEFAULT 'unknown',
    blocker_count     INTEGER NOT NULL DEFAULT 0,
    blockers_json     TEXT NOT NULL DEFAULT '[]',
    summary_json      TEXT NOT NULL DEFAULT '{}',
    refreshed_at_utc  TEXT NOT NULL
);
```

### 4.4 `strategy_control_log`（append-only 审计，DB-first 的可追溯保证）

```sql
CREATE TABLE strategy_control_log (
    log_id        TEXT PRIMARY KEY,
    instance_id   TEXT NOT NULL,
    actor         TEXT NOT NULL,          -- 谁改的（cli 用户 / dashboard / supervisor）
    action        TEXT NOT NULL,          -- start/stop/pause/resume/enable_live/set_cap/edit_params/shelve/crash
    from_state    TEXT,
    to_state      TEXT,
    reason        TEXT,
    spec_commit   TEXT,
    params_hash   TEXT,
    ts_utc        TEXT NOT NULL
);
```

这张表 = "每次改状态都 git commit" 的等价审计替代物。任何 live 动作、上限调整、参数改动都在这里留痕。

### 4.5 `strategy_instance_snapshot`（可选，时间序列，供看板/回放）

沿用旧平台 `strategy_instance_snapshots` 设计（tick/pnl/equity/open_orders/... 时间序列），落后续阶段。

---

## 5. 运行时面：单一 supervisor 守护进程

一个常驻进程，取代 `tmux -L weather-jrs` + N 个 pidfile + N 个 `start_*.sh`。k8s 式 reconcile：**比对期望态 vs 实际态**。

职责：
1. **Reconcile loop**：读 `strategy_instance`。`desired_status=enabled` 的确保有 runner 子进程活着；`paused/shelved` 的停掉。
2. **子进程隔离**：每个 head 用 BaseRunner harness 跑在独立子进程（一个 head 崩不拖垮别的）。崩溃按 backoff 重启，写 `strategy_control_log`（action=crash）+ runtime 表。
3. **集中守硬边界**：启动 `live` 实例前，校验 `live_enabled=1` + 显式确认 + coverage/健康 preflight；**强制全局 notional 天花板（跨实例）——这是今天任何单个 shell 脚本都看不见的**。
4. **控制面 CLI/API `pmctl`**：`pmctl start|stop|pause|resume|enable-live|set-cap|status|logs <instance>`，写控制表 + 控制日志。**这就是"状态统一通过接口改"。看板调同一套 API。**
5. **心跳聚合**：harness push 各 instance runtime 行，supervisor 是 `process_status` 的写者。

进程模型细节：supervisor + 每实例一子进程，Mac 上（短期生产）用普通 Python supervisor（asyncio / multiprocessing），一次性挂在 tmux/launchd 下常驻。`host` 字段留了多机位。迁移期：现有 `start_*.sh` 先改成 `pmctl start <instance>` 的薄壳，之后删除。

---

## 6. 数据源面：feed 订阅领域模型（一等公民）

把每城 × feed 的 source/cadence/健康建成显式模型。**这不只是配置整洁——它把"ECMWF 静默 fallback GFS"这类事故变成可观测、可 gate 的行。**

### 6.1 feed 分类

- `feed_kind`：`official_observation`（metar/asos）｜ `high_frequency_observation`（实时源）｜ `forecast` ｜ `orderbook`。
- 每个 (city, feed_kind) 有：source 链（primary/fallback）、cadence（poll 间隔，可 schedule-aware：metar 每小时 :51、forecast 每 N 小时、orderbook 每 60s）、live_eligible、timezone、station/icao、forecast 的 `CITY_MODEL`（ECMWF/GFS）、fallback 策略（显式失败 vs allow）、陈旧阈值（`max_snapshot_age_min`/`max_obs_age_min`——今天是每个脚本的 env var！）。

### 6.2 Claude 初版：`feed_source_profile`（评审对象，不直接落地）

```sql
CREATE TABLE feed_source_profile (
    profile_id           TEXT PRIMARY KEY,   -- city + feed_kind
    city                 TEXT NOT NULL,
    feed_kind            TEXT NOT NULL,
    source_class         TEXT NOT NULL,
    primary_source       TEXT NOT NULL,
    fallback_sources_json TEXT NOT NULL DEFAULT '[]',
    cadence_spec_json    TEXT NOT NULL,      -- {interval_sec} 或 cron-like schedule
    live_eligible        INTEGER NOT NULL DEFAULT 0,
    forecast_model       TEXT,               -- forecast kind: ecmwf_v4 / gfs
    timezone_name        TEXT,
    station_or_icao      TEXT,
    staleness_max_age_min REAL,
    fallback_policy      TEXT NOT NULL DEFAULT 'fail',  -- fail | allow（默认显式失败）
    blocked_reason       TEXT NOT NULL DEFAULT '',
    spec_commit          TEXT,
    updated_at_utc       TEXT NOT NULL
);
```

### 6.3 Claude 初版：`feed_subscription`（评审对象，不直接落地）

```sql
CREATE TABLE feed_subscription (
    subscription_id  TEXT PRIMARY KEY,
    instance_id      TEXT NOT NULL,
    city_pool        TEXT NOT NULL,
    feed_kind        TEXT NOT NULL,
    required         INTEGER NOT NULL DEFAULT 1,
    created_at_utc   TEXT NOT NULL
);
```

supervisor 启动 head 前校验：声明 `required` feed 若对其 city pool 陈旧/blocked，则不启动。**这是把散落在各脚本的 `max_obs_age` gate 收敛成集中的声明式数据质量检查——属于 CLAUDE.md 允许的正当数据质量边界，不是追坏例子的过拟合过滤。**

### 6.4 Claude 初版：`feed_health`（评审对象，不直接落地）

```sql
CREATE TABLE feed_health (
    city          TEXT NOT NULL,
    feed_kind     TEXT NOT NULL,
    last_ts_utc   TEXT,
    age_min       REAL,
    status        TEXT NOT NULL,   -- fresh / stale / fallback_active / blocked
    fallback_used TEXT,            -- 实际用了哪个 fallback source（非空即告警）
    refreshed_at_utc TEXT NOT NULL,
    PRIMARY KEY (city, feed_kind)
);
```

`fallback_used` 非空 = 一行可见告警。**7/02-05 那次 31 城静默 fallback GFS 污染信号，在这个模型下是一行 `fallback_active` + 告警，而不是三天后才发现的静默污染。** 直接呼应记忆 `n100-outage-mac-defacto-production` 与 `forecast-backfill-pit-contamination`。

### 6.5 评审结论：上面这版方向对，但粒度不够

Claude 这版的方向是对的：**profile 是目标配置，health 是实际状态，fallback 必须显性化**。这个抽象能解决 forecast 静默 fallback、source stale、脚本 env-var 分散等老问题。

但如果直接按 §6.2-6.4 落表，会有三个关键缺口：

1. **`profile_id = city + feed_kind` 太粗**。我们当前真实链路里，`Tokyo/high_frequency_observation` 可能是 `jma_amedas:44166`，`Singapore/high_frequency_observation` 是 `singapore_mss:S24`，`Seoul/Busan` 是 `amos_runway`，美国城市是 `noaa_madis_hfmetar`。同一个 city/feed_kind 下还可能同时有 primary、fallback、reference、runway 点位。`city+feed_kind` 一行表达不下。
2. **`feed_health` 只保最新健康，不能复盘交易**。fast-source prev-NO 需要回答“这笔单为什么下”：source 在几点观测、我们几点 detect、METAR 当时最新是哪份、盘口 ask 当时是多少。只有 latest health 不够，必须有 append-only event 表。
3. **跑道/高频/预测/盘口不是同一种事件**。它们可以共享基础字段（city/source/station/time/status/hash），但必须保留 source-specific payload：runway 点位温度、TAF signal、Open-Meteo model spread、orderbook quote 等。否则 dashboard 会“看起来统一”，但研究时丢证据。

因此，实际落地应把数据源面拆成四层：

```text
source catalog       源是什么、需不需要 key、能产什么
source profile       某城市启用哪个 source/station/runway、cadence/staleness/priority
source run/attempt   每轮采集跑了什么、哪些跳过、哪些失败
source event/fact    append-only 观测/预测/盘口事实，策略订单可引用
latest health        从 run/event 物化的最新健康读模型，给 dashboard 快速展示
```

### 6.5.1 最小落地版：三张表，但必须区分数据类型

上面的四层是终局模型；第一版不需要一次建八张表。**最小可用版是三张表**：

```text
weather_data_source_profile  这个源应该怎么跑
weather_data_source_event    实际看到了什么（append-only）
weather_data_source_health   当前是否新鲜、是否失败、dashboard 快速读
```

但三张表也必须把不同数据类型明确区分，否则后面会把盘口、预报、METAR、机场源混在一起，研究和复盘都会乱。

核心分类：

| data/feed 类型 | `feed_kind` | `event_type` | 例子 | 关键字段 |
|---|---|---|---|---|
| 盘口数据 | `orderbook` | `orderbook_quote` / `market_snapshot` | Polymarket CLOB book、paper snapshot | `condition_id`、`market_id`、`token_id`、`best_bid`、`best_ask`、`spread`、`book_ts_utc` |
| 预报数据 | `forecast` | `forecast_enrichment` | Open-Meteo multi-model、TAF、vertical profile | `forecast_model`、`forecast_issue_ts_utc`、`forecast_peak_time_local`、`forecast_max_temp`、`payload_json` |
| METAR / WU-like 官方观测 | `official_observation` | `official_observation` | AviationWeather、AWC cache、TGFTP、IEM/Synoptic | `source_report_ts_utc`、`local_detect_ts_utc`、`raw_metar`、`temp_c/f`、`detected_after_report_sec` |
| 实时机场/参考站 | `high_frequency_observation` | `high_frequency_observation` | JMA AMeDAS、FMI、Singapore MSS、NOAA MADIS HFMETAR、MGM、IMS、AMOS | `observation_ts_utc`、`temp_c/f`、`source_kind`、`station_or_feed`、`source_age_sec` |
| 跑道点位 | `runway_observation` | `runway_observation` | 韩国 AMOS runway、AMSC AWOS | `runway`、`point_temp_c`、`tdz_temp_c`、`mid_temp_c`、`end_temp_c` |

所以第一版 `weather_data_source_event` 必须至少有这些通用维度：

```sql
event_id
event_type
feed_kind
city
target_date
source_key
source_kind
station_or_feed
icao
runway
observation_ts_utc
source_report_ts_utc
local_detect_ts_utc
fetched_at_utc
temp_c / temp_f
condition_id / market_id / token_id
best_bid / best_ask
forecast_model / forecast_issue_ts_utc / forecast_max_temp
payload_hash
payload_json
```

也就是说，**表可以少，但类型不能糊**。第一版用三张表承载多类型事件；等发现某类查询很重，再把 `run/attempt`、`alignment_feature`、`orderbook_quote` 拆成专表或 materialized view。

### 6.6 修订版表设计：配置层

**A. `weather_data_source_catalog`：source/provider 目录**

一行一个 source adapter，不按城市拆。它回答“这个源是什么、是否需要授权、产物类型是什么”。

```sql
CREATE TABLE weather_data_source_catalog (
    source_key          TEXT PRIMARY KEY,  -- aviationweather_metar / jma_amedas / fmi / amos_runway / open_meteo_multi_model
    provider            TEXT NOT NULL,
    feed_kind           TEXT NOT NULL CHECK (
        feed_kind IN ('official_observation','high_frequency_observation','runway_observation','forecast','orderbook')
    ),
    source_kind         TEXT NOT NULL,     -- metar_api / official_airport_station / runway_air_temperature / forecast_model / clob_book
    requires_auth       INTEGER NOT NULL DEFAULT 0,
    auth_ref            TEXT,              -- env var 名或 secret ref；不存明文 key/cookie
    default_cadence_sec REAL,
    default_timeout_sec REAL,
    capabilities_json   TEXT NOT NULL DEFAULT '{}',
    notes               TEXT NOT NULL DEFAULT '',
    spec_commit         TEXT,
    updated_at_utc      TEXT NOT NULL
);
```

**B. `weather_city_source_profile`：city × source × station/runway 配置**

这是 `weather_data_feed/source_profiles.json`、`high_frequency_observation_sources.py` registry、`runway_sources.py` registry 的 DB 目标形态。粒度必须到 source/station/runway，而不是 city/feed_kind。

```sql
CREATE TABLE weather_city_source_profile (
    profile_id              TEXT PRIMARY KEY,
    city                    TEXT NOT NULL,
    target_unit             TEXT,
    timezone_name           TEXT NOT NULL,
    feed_kind               TEXT NOT NULL,
    source_key              TEXT NOT NULL REFERENCES weather_data_source_catalog(source_key),
    source_role             TEXT NOT NULL CHECK (
        source_role IN ('primary','fallback','reference','runway','forecast','orderbook')
    ),
    priority_rank           INTEGER NOT NULL DEFAULT 0,
    station_or_feed         TEXT,
    icao                    TEXT,
    runway                  TEXT,
    source_kind             TEXT,
    settlement_source_class TEXT,
    mapping_rule            TEXT,
    live_eligible           INTEGER NOT NULL DEFAULT 0,
    strategy_eligible       INTEGER NOT NULL DEFAULT 0, -- 可用于策略，不等于 live 下单
    cadence_spec_json       TEXT NOT NULL DEFAULT '{}',
    active_window_json      TEXT NOT NULL DEFAULT '{}', -- local daytime window 等
    staleness_max_age_sec   REAL,
    fallback_policy         TEXT NOT NULL DEFAULT 'fail',
    blocked_reason          TEXT NOT NULL DEFAULT '',
    notes                   TEXT NOT NULL DEFAULT '',
    spec_commit             TEXT,
    updated_at_utc          TEXT NOT NULL,
    UNIQUE(city, feed_kind, source_key, station_or_feed, runway)
);
```

`strategy_eligible` 和 `live_eligible` 要分开：例如跑道点位/参考站可以用于研究或辅助信号，但不是结算 truth；只有策略明确声明并通过验证后才可进入 live decision。

**C. `weather_strategy_source_subscription`：策略实例订阅源**

不要只写 `feed_kind`，要能指定“需要 official_observation fresh；可选 high_frequency_observation；forecast 必须 exact model；orderbook 必须 fresh CLOB”。

```sql
CREATE TABLE weather_strategy_source_subscription (
    subscription_id       TEXT PRIMARY KEY,
    instance_id           TEXT NOT NULL,
    feed_kind             TEXT NOT NULL,
    source_key            TEXT,             -- NULL = 该 feed_kind 的 profile primary/fallback 链
    city_pool             TEXT NOT NULL DEFAULT 'configured',
    required              INTEGER NOT NULL DEFAULT 1,
    max_age_sec_override  REAL,
    min_quality_status    TEXT NOT NULL DEFAULT 'fresh',
    usage_role            TEXT NOT NULL,    -- decision_input / feature_only / telemetry / audit
    created_at_utc        TEXT NOT NULL
);
```

### 6.7 修订版表设计：运行事实层

**D. `weather_data_source_run`：producer cycle 摘要**

一轮 producer 一行，用来回答“服务有没有跑、跑了哪些源、哪些因为 local window/cadence 被跳过”。

```sql
CREATE TABLE weather_data_source_run (
    run_id                  TEXT PRIMARY KEY,
    producer                TEXT NOT NULL,  -- weather_data_feed_service.high_frequency_observations
    runtime_root            TEXT,
    output_dir              TEXT,
    schema_version          TEXT,
    generated_at_utc        TEXT NOT NULL,
    status                  TEXT NOT NULL,
    requested_sources_json  TEXT NOT NULL DEFAULT '[]',
    requested_cities_json   TEXT NOT NULL DEFAULT '[]',
    rows                    INTEGER NOT NULL DEFAULT 0,
    ok_sources              INTEGER NOT NULL DEFAULT 0,
    empty_sources           INTEGER NOT NULL DEFAULT 0,
    non_ok_sources          INTEGER NOT NULL DEFAULT 0,
    skipped_inactive_count  INTEGER NOT NULL DEFAULT 0,
    skipped_cadence_count   INTEGER NOT NULL DEFAULT 0,
    preserved_rows          INTEGER NOT NULL DEFAULT 0,
    summary_json            TEXT NOT NULL DEFAULT '{}',
    source_path             TEXT,
    created_at_utc          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
```

**E. `weather_data_source_attempt`：每个 source/city 的采集尝试**

一轮 run 里，每个 source/city 一行。`auth_required`、`not_implemented`、`fetch_failed`、`inside_source_min_interval` 都落这里，不要只在 JSON 里。

```sql
CREATE TABLE weather_data_source_attempt (
    attempt_id              TEXT PRIMARY KEY,
    run_id                  TEXT NOT NULL REFERENCES weather_data_source_run(run_id),
    profile_id              TEXT,
    city                    TEXT NOT NULL,
    feed_kind               TEXT NOT NULL,
    source_key              TEXT NOT NULL,
    station_or_feed         TEXT,
    status                  TEXT NOT NULL,  -- ok / empty / fetch_failed / auth_required / skipped_cadence / skipped_inactive
    error                   TEXT,
    fetch_start_utc         TEXT,
    fetch_end_utc           TEXT,
    latency_sec             REAL,
    active_window_status    TEXT,
    cadence_status          TEXT,
    rows_returned           INTEGER NOT NULL DEFAULT 0,
    payload_hash            TEXT,
    created_at_utc          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
```

**F. `weather_data_source_event`：append-only 观测/预测/盘口事实**

这是最关键的一张表。source-events、高频机场站、跑道点位、预测 enrichment 都可以进入这里；source-specific 大字段放 `payload_json`，常用检索字段拉平。

```sql
CREATE TABLE weather_data_source_event (
    event_id                TEXT PRIMARY KEY,
    run_id                  TEXT REFERENCES weather_data_source_run(run_id),
    attempt_id              TEXT REFERENCES weather_data_source_attempt(attempt_id),
    profile_id              TEXT,
    event_type              TEXT NOT NULL CHECK (
        event_type IN ('official_observation','high_frequency_observation','runway_observation','forecast_enrichment','orderbook_quote')
    ),
    city                    TEXT NOT NULL,
    target_date             TEXT,
    feed_kind               TEXT NOT NULL,
    source_key              TEXT NOT NULL,
    source_kind             TEXT,
    station_or_feed         TEXT,
    icao                    TEXT,
    runway                  TEXT,
    observation_ts_utc      TEXT,
    source_report_ts_utc    TEXT,
    local_detect_ts_utc     TEXT,
    fetched_at_utc          TEXT,
    source_age_sec          REAL,
    detected_after_report_sec REAL,
    unit                    TEXT,
    temp_c                  REAL,
    temp_f                  REAL,
    temp_round_c            INTEGER,
    temp_round_f            INTEGER,
    dewpoint_c              REAL,
    pressure_hpa            REAL,
    humidity                REAL,
    wind_speed_kt           REAL,
    wind_dir_deg            REAL,
    point_temp_c            REAL,
    tdz_temp_c              REAL,
    mid_temp_c              REAL,
    end_temp_c              REAL,
    forecast_model          TEXT,
    forecast_issue_ts_utc   TEXT,
    forecast_peak_time_local TEXT,
    forecast_max_temp       REAL,
    market_id               TEXT,
    condition_id            TEXT,
    token_id                TEXT,
    best_bid                REAL,
    best_ask                REAL,
    payload_hash            TEXT NOT NULL,
    raw_payload_hash        TEXT,
    payload_json            TEXT NOT NULL DEFAULT '{}',
    source_path             TEXT,
    created_at_utc          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(event_type, source_key, city, station_or_feed, runway, observation_ts_utc, payload_hash)
);
```

这张表不是替代 `fact_trades`，而是给策略提供**输入证据血缘**。订单表/plan 可以通过 `order_payload` 或后续桥表引用 `event_id`，实现“这笔 Helsinki 17 NO 是因为 FMI 哪条观测触发”的 drilldown。

**G. `weather_data_source_health`：latest health 读模型**

从 run/attempt/event 物化，给 dashboard 快速读。主键用 `profile_id` 或 city/source/station/runway，不用 `city+feed_kind`。

```sql
CREATE TABLE weather_data_source_health (
    health_key              TEXT PRIMARY KEY,
    profile_id              TEXT,
    city                    TEXT NOT NULL,
    feed_kind               TEXT NOT NULL,
    source_key              TEXT NOT NULL,
    station_or_feed         TEXT,
    runway                  TEXT,
    status                  TEXT NOT NULL, -- fresh / stale / fallback_active / blocked / auth_required / no_recent_attempt
    latest_event_id         TEXT,
    latest_observation_ts_utc TEXT,
    latest_detect_ts_utc    TEXT,
    latest_run_id           TEXT,
    latest_attempt_id       TEXT,
    age_sec                 REAL,
    staleness_max_age_sec   REAL,
    fallback_used           TEXT,
    source_status           TEXT,
    error                   TEXT,
    rows_24h                INTEGER,
    ok_attempts_24h         INTEGER,
    failed_attempts_24h     INTEGER,
    median_detect_lag_sec_24h REAL,
    summary_json            TEXT NOT NULL DEFAULT '{}',
    refreshed_at_utc        TEXT NOT NULL
);
```

### 6.8 修订版表设计：策略研究/归因层

**H. `weather_source_alignment_feature`：source ↔ METAR/WU/settlement 对齐特征**

fast-source 策略真正要研究的是“快源是否领先下一份 METAR/WU，以及领先后盘口有没有动”。这个不应塞在 source event 表里，应单独 materialize 成 feature。

```sql
CREATE TABLE weather_source_alignment_feature (
    feature_id              TEXT PRIMARY KEY,
    event_id                TEXT NOT NULL REFERENCES weather_data_source_event(event_id),
    city                    TEXT NOT NULL,
    target_date             TEXT NOT NULL,
    source_key              TEXT NOT NULL,
    station_or_feed         TEXT,
    observation_ts_utc      TEXT,
    local_detect_ts_utc     TEXT,
    source_temp_c           REAL,
    source_round_c          INTEGER,
    latest_metar_event_id   TEXT,
    latest_metar_report_ts_utc TEXT,
    latest_metar_detect_ts_utc TEXT,
    latest_metar_temp_c     REAL,
    metar_running_max_round_c INTEGER,
    source_obs_after_latest_metar_sec REAL,
    crossed_prev_max        INTEGER NOT NULL DEFAULT 0,
    t_minus_1_no_bracket_c  INTEGER,
    next_metar_report_ts_utc TEXT,
    next_metar_temp_c       REAL,
    next_metar_round_c      INTEGER,
    next_metar_crossed      INTEGER,
    lead_to_next_metar_sec  REAL,
    basis_temp_c            REAL,
    feature_json            TEXT NOT NULL DEFAULT '{}',
    created_at_utc          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(event_id, latest_metar_event_id)
);
```

这张表是 Tokyo/Singapore/Helsinki 试点的核心：dashboard 可以直接展示 `source obs → detect → latest METAR → next METAR → orderbook` 的时间线，研究脚本也不用每次重新 join JSONL。

### 6.9 与现有 schema / 产物的关系

当前 `weather_dashboard/db/schema_canonical.sql` 已经有：

- `weather_observation_events` / `weather_intraday_state_rows`：适合 WU/IEM-like 历史观测和 intraday state，不足以表达多 source/station/runway 的实时采集健康和交易触发证据；
- `strategy_def / strategy_instance / strategy_control_log`：已有 B1/B2 雏形，但 `strategy_instance_runtime` 和数据源面表还没落；
- `/api/data-sources`：已有只读接口，但当前主要是 forecast/fact_trades、source alias、snapshot 文件盘点，不是运行时 source health。

所以落地时不要改造 `weather_observation_events` 去承载所有东西。正确路线是：

1. **先建三张最小表**：`weather_data_source_profile / weather_data_source_event / weather_data_source_health`，用 `feed_kind` + `event_type` 区分盘口、预报、METAR、实时机场源和跑道源；从现有文件协议 materialize：
   - `output/source_events/latest.json` + `sources.jsonl`；
   - `output/high_frequency_observations/latest.json` + `high_frequency_observations.jsonl`；
   - `output/runway_observations/latest.json` + `runway_observations.jsonl`；
   - `output/forecast_enrichment/latest.json` + `forecast_enrichment.jsonl`；
   - `output/fast_source_stale_book/*` / `output/fast_source_prev_no_trial/*` 作为策略/盘口对齐输入。
2. **dashboard 先只读展示**：`/data-sources` 改读 latest health + recent event；不要马上把控制动作放公网。
3. **策略订单归因再补桥**：fast-source prev-NO 的 `orders.jsonl` 已经进入 canonical orders/fills；下一步是在 plan/order payload 或专门桥表里引用 `weather_data_source_event.event_id` / `weather_source_alignment_feature.feature_id`。
4. **最后才拆细表和加 supervisor required-feed gate**：等 source health materialization 稳定后，再按需要拆 `run/attempt`、`alignment_feature`、`subscription`，并让 supervisor 用 `weather_strategy_source_subscription` 做启动/交易前数据质量校验。

这保证不会把研究源、结算源、跑道 microclimate、盘口 telemetry 混成一个“看起来统一但没法审计”的表。

---

## 7. 看板集成（概要，详见 §12）

看板现在读 `weather_strategy_runtime_registry`，且**刻意只读**（WEATHER_DASHBOARD.md §0：不下单、不碰资金/不可逆操作），公网部署、读的是**可能滞后的镜像**。因此控制面**不能**直接塞进公网看板。迁移原则：
- **registry 表降级为读模型**：从新的 control + runtime 表物化刷新，保留给 FE 兼容。
- **控制动作与只读看板分面**：pause / enable-live / set-cap 走**本机 ops 控制面**（`pmctl` + 与 supervisor 同机的 localhost-only 控制页），**不放在公网只读看板上**。看板只**展示**控制状态（期望态 vs 实际态 + `strategy_control_log` 时间线），不 mutate。
- **数据源健康面板**：`/data-sources` 从"按文件名间隔现算 cadence"升级为读
  `weather_city_source_profile` + `weather_data_source_health`，并能 drilldown 到
  `weather_data_source_event` / `weather_source_alignment_feature`，静默 fallback 和 source/METAR 对齐异常直接飘红。

完整的看板升级/新增模块、接口、与硬口径一致性见 **§12**；数据模型关联见 **§13**。

---

## 8. 落地分期（每期独立可交付，主血缘全程不动）

| Phase | 内容 | 交付判据 |
|---|---|---|
| **0 · 接口与 harness（零行为变更）** | 从一个 head（建议 `low_price_yes_lottery`）抽出 `BaseRunner`，定义 `StrategyHead` protocol + `ParamsSchema`，把这一个 head 移植过去 | 新旧并行跑 shadow，emit/telemetry **逐笔 parity**（TDD parity test），证明无回归 |
| **1 · 控制面 + 运行时表 + supervisor** | 在 weather.db 落 `strategy_def/instance/runtime/control_log`；实现 `pmctl` + supervisor 管这一个 head；`start_*.sh` 改薄壳；心跳翻成 push | 该 instance 能经 `pmctl` start/stop/pause，状态与审计全落 DB；registry 对它从扫描变 push |
| **2 · 数据源领域模型** | 落 `weather_data_source_catalog / weather_city_source_profile / weather_strategy_source_subscription / weather_data_source_run / weather_data_source_attempt / weather_data_source_event / weather_data_source_health / weather_source_alignment_feature`，从 `source_profiles.json` 和 high-frequency/runway registries 回填；加集中陈旧/fallback gate + 告警 | 静默 GFS fallback 类事故变成 source health 行 + 告警；fast-source 订单能 drilldown 到触发它的 source event；supervisor 能按 required feed 拦启动 |
| **3 · 全量移植 head** | 按 family 逐族移植；删各自 `start_*.sh` + 重复的 loop/pidfile/telemetry 代码；registry → 读模型 | `scripts/ops/start_*.sh` 与 bespoke runner 大幅减少；重复横切代码归零 |
| **4 · 看板控制动作 + 数据源面板** | 看板接 `pmctl` 控制动作 + `weather_data_source_health` 面板；下线扫描式 `refresh_weather_strategy_runtime_registry.py` | 从看板可 pause/enable-live/set-cap；数据源健康可视 |

Phase 0/1 只碰一个 head，风险最低；主血缘（fact 表、executor、settlement）全程不动。

---

## 9. 备选方案与取舍（本轮已定的分支，留档）

| 决策点 | 选定 | 备选 | 为什么不选备选 |
|---|---|---|---|
| 启动器进程模型 | 单一 supervisor 守护 | ① 薄 `pmctl` CLI + 共享 BaseRunner，仍多进程自管理 ② 外部编排（systemd/launchd per-instance） | ① 无法做跨实例全局 notional 上限、无统一崩溃重启；② 对个人 Mac 单机过重，且状态仍散在 systemd 外 |
| 事实来源 | DB 优先控制面（定义 git 作者 + spec 锚 + append log） | ① 纯 hybrid（定义+状态都 git）② 纯 DB（放弃 git 作者来源） | ① "改状态要 commit" 不够即时；② 丢失 git 审计，撞 git-first 硬边界 |
| 数据源建模 | source catalog/profile/run/event/health/alignment 分层 | 继续 `source_profiles.json` + 脚本 env var；或只建 `feed_health` latest 表 | 静默 fallback 无可观测入口，cadence/阈值无法集中 gate；只建 latest health 不能复盘交易触发证据 |
| 元数据模型落点 | 挂到 weather.db canonical | 复活独立 `strategy_runtime.db`（旧平台） | 二库分裂、与主血缘脱节，正是旧平台被搁置的原因之一 |

---

## 10. 风险与开放问题

1. **全局 notional 上限需读共享账本**：跨实例天花板要从 `fact_trades` 读当日已用 notional，需定义读路径与刷新频率。
2. **supervisor 单点**：靠 launchd 自动重启 + 心跳文件兜底；supervisor 崩溃期间已 live 的子进程行为要定义（继续跑还是自停）。
3. **DB 成安全关键**：DB-first 后 weather.db 是安全关键路径，需保证 WAL + 备份；`strategy_control_log` 是审计正本。**请确认这套 append-log 审计对你达到"可追溯"硬边界的心理阈值**——若不够，退回 hybrid（状态改也走 git）。
4. **定义-in-git vs DB-first 的最终形态**（§1.3）：目前折中在 spec_commit 锚 + control_log。若你更想纯 git 或纯 DB，这是一个明确的切换点。
5. **live decision-input 迁移边界**：`WEATHER_ARCHITECTURE_SPINE` 记了 feature-layer 尚未全量迁移到 live decision-input；harness 的 `RunContext` 要对齐这条边界，不越界替换 live 输入。

---

## 11. 与现有文档的关系

- 架构定位：本文是 `WEATHER_ARCHITECTURE_SPINE` 的 L4.5/L5 编排层目标草案，不改 [0]–[6] 主血缘节点。
- 字段契约：新表落地时字段口径服从 `WEATHER_SYSTEM_CONTRACT`。
- 部署流程：Phase 1+ 的生产行为变更仍走 `weather-strategy-deploy` skill 的 git-first 流程（§1.3 的化解在此生效）。
- 取代：`docs/archive/UNIFIED_STRATEGY_PLATFORM_REFACTOR_PLAN.md` 的目标定位（旧 PMM/ARB 版），复用其 instance/state/snapshot 词汇但重新挂到 weather canonical DB。

---

## 12. 看板升级与改进计划

### 12.1 现状盘点（看板不是白纸，是已成型的观测面）

看板已重做过一版，当前是**探针生命周期 IA**（`WEATHER_DASHBOARD.md` 为口径 source of truth）。前端 `frontend/strategy_dashboard`，后端 `weather_dashboard/api`。当前路由分三代并存：

| 代 | 路由 | 页/职责 | 数据来源 |
|---|---|---|---|
| **v2（当前）** | `/` 今日总览 | 脉搏：探针健康 / 在险资金 / 已结算 / 最近研究线 | registry + latest_summary.json + fact_trades |
| | `/probes` `/probes/:instance` | 探针在跑：live & shadow 健康、执行质量、参数 | `weather_strategy_runtime_registry`（**扫描**）+ `latest_summary.json` |
| | `/research` `/research/:lineId` | 研究证据登记 + 变体对比 | `docs/analysis/**/generated/*/summary.json` |
| | `/performance` | live_real 持仓 / 在险资金 / 已结算 PnL / 对账 | canonical `fact_trades` |
| | `/lineage/:date` | 单日逐笔 signal→plan→order→fill→settlement | canonical 全链 |
| | `/data-sources` | 预测源 / METAR 观测源 / 盘口快照 | fact_trades + aliases.py + **文件名间隔现算 cadence** |
| | `/glossary` `/archive` | 术语字典 / 归档 | — |
| **旧 weather** | `/weather/runtime` `/weather/strategies` `/weather/live` `/weather/runs` `/weather/compare` … | 上一版天气页，部分仍挂 registry/config | registry / strategy_config |
| **legacy** | `/legacy/*` | 旧 PMM/ARB（dormant 保留不删） | strategy_runtime.db 旧库 |

**两条必须继承的硬约束**（否则升级会破坏现有正确性）：
1. **看板只读、公网部署、读镜像**（`WEATHER_DASHBOARD.md` §0、§3.1）：不能把控制/下单动作放进公网看板。
2. **镜像新鲜度 ≠ 生产健康**（§3.1）：任何"新鲜度"分两层——镜像同步年龄 vs 生产是否断流，永不混。新数据源健康面板必须继承这个双层口径。

### 12.2 关键修正：控制面与只读看板必须分面

§7 早先说"看板调同一套 pmctl API"是错的。正确切分：

```text
┌─────────────────────────────┐        ┌──────────────────────────────────┐
│  公网只读看板 (existing)      │  读取   │  本机 Ops 控制面 (new, local-only) │
│  frontend/strategy_dashboard │ ─────▶ │  pmctl CLI + localhost 控制页       │
│  只 SELECT，展示期望态/实际态  │        │  与 supervisor 同机，写控制表+审计   │
│  /control_log 时间线只读展示   │        │  pause/enable-live/set-cap 在这里    │
└─────────────────────────────┘        └──────────────────────────────────┘
        读镜像 weather.db                       读写生产 weather.db
```

- 控制面 DB = **生产主机（当前 Mac）的 weather.db**；看板若跑在镜像上，看到的控制状态可能滞后——这是 §3.1 的同一条口径，要在 UI 上显式标注 mirror-age。
- 公网看板保持零 mutate，符合既有安全清单（`WEATHER_DASHBOARD_DEPLOY.md` Cloudflare Tunnel + 强制鉴权）。

### 12.3 差距分析（当前模块 → 缺什么）

| 现有模块 | 现在怎么做 | 缺口 | 升级挂到哪张新表 |
|---|---|---|---|
| `/probes` 探针 | 扫描 `weather_strategy_runtime_registry` 反推状态 | 无期望态 vs 实际态；三套 enum 混用；无控制审计时间线；健康靠扫描非 push | 改读 `strategy_instance`（期望）+ `strategy_instance_runtime`（push 实际）+ `strategy_control_log`（时间线） |
| `/data-sources` | forecast 从 fact_trades、METAR 从 aliases.py、cadence = 文件名中位间隔 | 无每城×source/station 配置；无 cadence 目标值 vs 实际值对比；**无 fallback/静默降级检测**；无高频/跑道/预测 event drilldown；无 source/METAR alignment | 改读 `weather_city_source_profile`（配置）+ `weather_data_source_health`（实际）+ `weather_data_source_event` / `weather_source_alignment_feature`（证据） |
| `/` 今日总览 | 探针健康 + 在险资金脉搏 | 无数据源健康脉搏；无"期望态≠实际态"漂移告警 | 增 `weather_data_source_health` 汇总 + reconcile 漂移卡 |
| `/weather/strategies` `/weather/runtime` | 挂 strategy_config / registry | 与 v2 `/probes` 职责重叠；无 `strategy_def` 目录视图 | 增 `strategy_def` catalog 视图，旧页归档到 `/archive` |
| `/performance` `/lineage` | canonical fact 全链 | 基本够用；缺按 instance 的控制动作归因 | 只加 `strategy_control_log` 关联链接，不动 canonical |
| 控制动作 | **完全没有**（只读） | 无法从任何 UI pause/enable-live/set-cap | 新建本机 ops 控制面（§12.2） |

### 12.4 要新增/升级的模块

**A. 升级 `/probes`（探针面 → 控制状态感知）**
- 列表加两列：**期望态** `desired_status`（enabled/paused/shelved）、**实际态** `process_status`（running/crashed/stale）。二者不一致 = 飘黄"漂移"（如 desired=enabled 但 crashed）。
- 详情页加 **`strategy_control_log` 时间线**（谁在何时 start/enable-live/set-cap/因何 crash），这是 DB-first 审计的可视化出口。
- 健康从 push 来（`strategy_instance_runtime.heartbeat_at_utc`），不再扫目录；缺心跳显式标 `no_heartbeat`。

**B. 升级 `/data-sources`（→ feed 健康矩阵）**
- **每城 × feed_kind × source 矩阵**：行=城市，列=`official_observation / high_frequency_observation / runway_observation / forecast / orderbook`，格子可展开到 source/station/runway；颜色来自 `weather_data_source_health.status`（fresh/stale/fallback_active/blocked/auth_required）。
- **cadence 目标 vs 实际**：`weather_city_source_profile.cadence_spec_json`（配置目标）对 `weather_data_source_health.age_sec`（实际），超 `staleness_max_age_sec` 飘红。
- **静默降级飘红**：`weather_data_source_health.fallback_used` 非空一律高亮 + 告警条——直接给 7/02-05 静默 GFS 那类事故一个一等公民入口。
- **source evidence drilldown**：点开一格能看到最近 `weather_data_source_event`，包括 obs/report/detect/fetch 时间、temp、payload_hash、source_status；fast-source 城市额外显示 `weather_source_alignment_feature` 的 METAR 对齐和下一份 METAR 结果。
- 继承 §3.1 双层：分开显示"镜像同步年龄"与"生产 feed 年龄"。

**C. 新增 `strategy_def` catalog 视图（只读）**
- 现在没有任何页展示"定义"层（`/weather/strategies` 是 config 粒度）。加一页列 `strategy_def`：family、支持的 execution_mode、`required_feeds`、`spec_commit`、下挂的 instance 列表。

**D. 新增本机 Ops 控制面（local-only，非公网看板）**
- 载体：`pmctl` CLI 起步；后续可加一个 localhost-only 的轻控制页（与 supervisor 同机）。
- 动作：start/stop/pause/resume/enable-live/set-cap/edit-params/shelve，每个都写 `strategy_control_log`；enable-live 与调高上限强制显式确认 + preflight。
- 全局视图：**跨实例当日已用 notional vs 全局天花板**（今天没有任何界面看得见）。

**E. 今日总览加两块脉搏**
- 数据源健康脉搏（`weather_data_source_health` 汇总：几个城市 stale / 有无 fallback_active）。
- reconcile 漂移告警（有多少 instance 期望态≠实际态）。

**F. 新增订单/成交明细 Blotter（当前设计缺口）**

运行时平台不能只展示"实例在不在跑"，还必须有一个一等的**订单/成交明细表**，回答：

- 每一笔真实/纸面订单是什么时候下的、是否成交、成交多少 shares、成交价是多少、cost/fees/PnL 是多少；
- 这笔订单归属于哪个 `strategy_instance` / `strategy_name` / `strategy_config`；
- 它来自哪个 signal / plan / execution policy，目标城市、target_date、bracket、YES/NO side 是什么；
- 未成交/报错/blocked 的订单为什么没成交；
- 结算后 realized PnL，未结算时 open cost / MTM / valuation snapshot 是什么。

现有 API 已有几块分散能力：

| API | 当前能做什么 | 缺口 |
|---|---|---|
| `GET /api/live/book` | 从 `fact_trades` 读 `trade_class='live_real'`，一行一个 real fill，含 `strategy_name/city/target_date/bracket/fill_price/fill_qty/cost/pnl/MTM` | 只看真实成交，不覆盖 submitted/no-fill/error/blocked；按 `strategy_instance` 过滤弱 |
| `GET /api/live/positions` | 从 `signals -> plans -> orders -> fills -> settlements` 读真实 CLOB fill lineage | 口径偏 position，不是完整 order blotter；字段与 `/live/book` 不完全一致 |
| `GET /api/live/book/strategies` | live_real 按 `strategy_name` 汇总 | 只有聚合，没有逐笔 |
| `GET /api/strategies/{config_id}/orders` | 按 config_id 查 order-level lineage，含 signal/plan/order/fill/settlement/PnL | 需要先知道旧 `strategy_config`，不适合运行时实例视角 |
| `GET /api/runs/{run_id}/trades` / `trades/{signal_id}` | run 内交易和单笔纵向血缘 | 适合 drilldown，不适合全局实时 blotter |

因此新产品面应新增一个统一只读接口：

```text
GET /api/order-blotter
  filters:
    trade_class=live_real|paper|shadow|all
    strategy_instance=
    strategy_name=
    config_id=
    target_date=
    city=
    status=open|settled|submitted|filled|error|blocked|all
    date_from/date_to
    limit/offset
```

返回字段以 `fact_trades` 为成交正本，但左联 `orders/plans/signals/runs` 补齐未成交/报错订单；后续 `strategy_instance` 表落地后再通过 `run_id/config_id/order_payload` 做 instance 归因。第一版可以先读现有 canonical 表，不等控制面落地。

看板新增页面建议：

```text
/orders
  顶部: live/paper/shadow/all + open/settled/error + strategy filter + date
  表格: time, strategy, mode, city, target_date, bracket, side,
        order status, fill status, shares, fill price, cost, fees,
        settled/final, realized PnL, MTM, links(drilldown/poly)
```

这页和 `/performance` 的区别：`/performance` 是组合/持仓/PnL 视图；`/orders` 是逐笔订单与成交流水。它应该成为 runtime 平台的核心观测页之一。

### 12.5 后端接口增补

**只读（进公网 API `weather_dashboard/api`）：**
- `GET /api/strategy-runtime/*` 改读新 control+runtime 表（registry 降级为读模型，保 FE 兼容）。
- `GET /api/data-sources` 升级：返回每城×feed×source 矩阵 + `weather_data_source_health` + cadence 目标/实际 + auth/fallback 状态。
- `GET /api/strategy-defs`、`GET /api/strategy-defs/{key}` — 定义 catalog。
- `GET /api/instances/{id}/control-log` — 审计时间线（只读展示）。
- `GET /api/feed-health` — 数据源健康矩阵。
- `GET /api/data-source-events` — 观测/预测/盘口 source event 明细。
- `GET /api/source-alignment` — fast-source 与 METAR/WU/settlement 的对齐特征。
- `GET /api/order-blotter` — 统一订单/成交明细：从 canonical order/fill/fact 表读逐笔流水，支持 strategy/date/status 过滤。

**控制（独立命名空间，local-only，不经 Cloudflare Tunnel 暴露）：**
- `POST /control/instances/{id}/{action}`（start/stop/pause/enable-live/set-cap…），只绑定 localhost、与 supervisor 同机、经鉴权；等价于 `pmctl`。

### 12.6 与看板硬口径的一致性（不许破坏）

- 公网看板保持零 mutate；控制在本机面。
- `weather_data_source_health` 与探针健康都必须区分**镜像同步年龄** vs **生产实际年龄**（§3.1），不能用镜像新旧推断生产断流。
- 绩效/PnL 口径不变（`live_real`、`fill_date_bj`、coverage gate、near-binary 归一化）；本轮只加控制/数据源可观测，不碰 canonical PnL 口径。

### 12.7 看板改造分期（对齐 §8）

- 与 Phase 1 同步：`/probes` 接期望态/实际态 + 控制日志时间线（先展示，控制动作随 pmctl 落地）。
- 与 Phase 2 同步：`/data-sources` 升级为 source 健康矩阵 + source event drilldown + 静默降级飘红。
- 与 Phase 4：本机 ops 控制面 + 今日总览脉搏；旧 `/weather/runtime` 等重叠页归档到 `/archive`。

---

## 13. 数据模型关联（ER）

### 13.1 三层表 + 主血缘的关系全图

```mermaid
erDiagram
    %% ── 定义面 ──
    strategy_def ||--o{ strategy_instance : "strategy_key 定义→实例 (1:N)"
    %% ── 控制面 / 运行时面 ──
    strategy_instance ||--|| strategy_instance_runtime : "instance_id 期望态↔实际态 (1:1, push)"
    strategy_instance ||--o{ strategy_control_log : "instance_id 每次控制动作 (1:N, append)"
    strategy_instance ||--o{ strategy_instance_snapshot : "instance_id 时间序列 (1:N)"
    %% ── 实例 → 主血缘（attribution 桥）──
    strategy_instance }o--|| strategy_config : "config_id 绑定参数快照 (N:1)"
    strategy_config ||--o{ plans : "config_id 归因 (1:N)"
    plans ||--o{ orders : "plan_id (1:N)"
    orders ||--o{ fills : "order_id / execution_id (1:N)"
    plans }o--o{ fact_signal_candidates : "signal 机会粒度对齐"
    fills }o--|| fact_trades : "成交粒度 canonical"
    fact_trades ||--o{ settlements : "结算→realized PnL"
    %% ── 数据源面 ──
    weather_data_source_catalog ||--o{ weather_city_source_profile : "source_key 源目录→城市源配置"
    strategy_instance ||--o{ weather_strategy_source_subscription : "instance_id 声明依赖 (1:N)"
    weather_city_source_profile ||--o{ weather_strategy_source_subscription : "city/source/feed 被订阅"
    weather_data_source_run ||--o{ weather_data_source_attempt : "一轮 producer→多 source/city attempt"
    weather_data_source_attempt ||--o{ weather_data_source_event : "attempt→append-only source event"
    weather_city_source_profile ||--o{ weather_data_source_health : "profile→latest health 读模型"
    weather_data_source_event ||--o{ weather_source_alignment_feature : "source event→METAR/WU alignment"
    weather_strategy_source_subscription }o--|| weather_data_source_health : "启动前校验 required source 新鲜度"
    %% ── 只读读模型 ──
    strategy_instance_runtime ||..o{ weather_strategy_runtime_registry : "物化为看板读模型"
```

### 13.2 关系逐条说明（join key / 基数 / 语义）

| 关系 | join key | 基数 | 语义 / 为什么这么连 |
|---|---|---|---|
| `strategy_def` → `strategy_instance` | `strategy_key` | 1:N | 一个定义可有多实例（如同一 head 的 shadow / tiny-live / 参数变体） |
| `strategy_instance` ↔ `strategy_instance_runtime` | `instance_id` | 1:1 | 期望态与实际态分表：控制面写前者，harness/supervisor push 后者 |
| `strategy_instance` → `strategy_control_log` | `instance_id` | 1:N | append-only 审计；DB-first 可追溯的正本 |
| `strategy_instance` → `strategy_config` | `config_id` | N:1 | **attribution 桥**：instance 绑定一份参数快照 `strategy_config`，让下游 plan 归因不变 |
| `strategy_config` → `plans` → `orders` → `fills` | `config_id`→`plan_id`→`order_id`/`execution_id` | 各 1:N | **既有 canonical 血缘，本轮不动**；平台只在最上游多挂一个 instance 归因 |
| `plans` ↔ `fact_signal_candidates` | signal / 机会键 | 机会粒度对齐 | 机会粒度 canonical，不绕过自算 |
| `fills` → `fact_trades` → `settlements` | 成交键 | canonical | 成交/结算 canonical，PnL 口径不变 |
| `weather_data_source_catalog` → `weather_city_source_profile` | `source_key` | 1:N | source adapter 目录与 city/station/runway 配置分离；同一 source 可服务多城市 |
| `strategy_instance` → `weather_strategy_source_subscription` | `instance_id` | 1:N | instance 声明它依赖哪些 source/feed（`required=1` 的进启动校验） |
| `weather_city_source_profile` → `weather_strategy_source_subscription` | `city/feed_kind/source_key/station/runway` | 1:N | 一个城市源配置可被多个 instance 订阅 |
| `weather_data_source_run` → `weather_data_source_attempt` | `run_id` | 1:N | 一轮 producer cycle 展开成多个 source/city attempt；跳过、失败、auth_required 都是 attempt |
| `weather_data_source_attempt` → `weather_data_source_event` | `attempt_id` | 1:N | attempt 产出 append-only source event；未产出时也保留 attempt 失败证据 |
| `weather_city_source_profile` → `weather_data_source_health` | `profile_id` / health key | 1:1 latest | 配置 vs 实际健康分离：profile 是目标，health 是由 run/event 物化的最新状态 |
| `weather_data_source_event` → `weather_source_alignment_feature` | `event_id` | 1:N | fast-source/METAR/WU/settlement 对齐特征，不污染原始 source event |
| `weather_strategy_source_subscription` ↔ `weather_data_source_health` | source subscription key | 校验用 | supervisor 启动前拿 required source 的 health 做门槛 |
| `strategy_instance_runtime` → `weather_strategy_runtime_registry` | 物化 | 读模型 | 现有 registry 表降级为看板读模型，从新表刷新，保 FE 兼容 |

### 13.3 关键建模决策（3 个必须讲清的点）

1. **instance ↔ config 的桥**：`strategy_instance` **不**替代 `strategy_config`，而是**引用**它（`config_id`）。既有 `plans.config_id → orders → fills` 血缘一字不改；平台只是在 config 之上多加一层"运行时实例"的期望态/实际态/审计。这是"围着主血缘建、不动它"的具体落点。
2. **期望态 / 实际态分表**：`strategy_instance`（控制面，人写）与 `strategy_instance_runtime`（运行时面，机器 push）**物理分离**。避免现在 registry 把"想让它 live"和"它其实 stale 了"塞进同一个 `lifecycle_status` 的老问题。
3. **配置 vs 事件 vs 健康分表**：`weather_city_source_profile`（目标 cadence/阈值/fallback 策略，git 作者）、`weather_data_source_event`（append-only 事实证据）与 `weather_data_source_health`（latest 读模型）分离。同一套"期望 vs 实际"范式，让静默降级 = 目标与实际不一致 = 一行可见告警，同时保留交易复盘所需的 source event 证据。

### 13.4 与现有表的关系一句话总结

- **新增（平台）**：`strategy_def / strategy_instance / strategy_instance_runtime / strategy_control_log / strategy_instance_snapshot`——全部落 `runtime/weather.db`。
- **新增（数据源面）**：`weather_data_source_catalog / weather_city_source_profile / weather_strategy_source_subscription / weather_data_source_run / weather_data_source_attempt / weather_data_source_event / weather_data_source_health / weather_source_alignment_feature`——全部落 `runtime/weather.db`，从 `weather_data_feed_service_runtime/output/*` materialize。
- **复用不动（canonical 主血缘）**：`fact_signal_candidates / plans / orders / fills / fact_trades / settlements / strategy_config`。
- **降级为读模型**：`weather_strategy_runtime_registry`（扫描表 → 从新表物化，供看板兼容）、`weather_strategy_shadow_queue`（并入 `strategy_def.is_active` + `strategy_instance` 生命周期后可精简）。

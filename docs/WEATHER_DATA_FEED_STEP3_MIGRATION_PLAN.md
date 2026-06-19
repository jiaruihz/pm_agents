# Weather Data Feed — Step 3 迁移设计与执行计划（weather-predict 采集退役）

Status: `design-draft`
Updated: 2026-06-19
Source of truth: 设计计划；执行前以 N100 实际 unit/脚本为准（见 §2 探查步骤）
Used by: WEATHER_DATA_FEED_MODULE.md（step 3 的具体落地）

> **给执行者（Codex）的前置说明**：本文档自包含，可冷启动执行，但**涉及 N100 生产采集（硬边界）**。
> 必须遵守：① 走 `weather-strategy-deploy` 的 **git-first** 流程，**禁止 `scp`/`rsync` 直推**；
> ② **并行验证通过前不切换**；③ weather-predict **转 dormant 保留，不删**；④ 每个不可逆步骤前显式确认。
> 本计划的目标是**部署隔离**，不是数据/模型提纯（见 §5 已知债）。先做 §2 探查，**不要假设**脚本/unit 细节。

## 0. Codex 设计评审结论（2026-06-19）

结论：**方向合理，但切换前必须补 4 个验收点**。本计划可以作为 step 3 主线继续推进。

合理点：
- 独立运行 checkout `~/projects/weather_data_feed_service/` 是正确边界：数据采集不塞回 `pm_agent` live 执行侧；
- `weather_data_feed/` 继续留在同一 git repo 作为共享包，先拿部署隔离，不急着拆独立 repo；
- `weather-predict` 退为 dormant 而不是删除，符合当前研究/工具债状态；
- Phase 2/3 并行验证、Phase 4 再切换，避免采集链路黑箱替换。

必须补强：
- **systemd unit 也要版本化**：unit 文件不应只在 N100 手写。应在 repo 内放模板/安装脚本，N100 只执行 git checkout 后的 install/reload。
- **新输出目录需要 runner 原生支持**：并行写新目录前，`paper_snapshot.py` / `daily_pipeline.py` 必须支持可配置 output/cache root；否则 Phase 2(a) 只是文档愿望。
- **snapshot 运行时长是硬验收项**：2026-06-19 观测到 snapshot 从常规 12-16 分钟拉长到 38-40 分钟；cutover 前不仅要 `status=ok`，还要证明运行时长小于 timer cadence 或调整 timer/拆分任务。
- **daily pipeline parity 要脚本化**：不能只检查 snapshot。`pm_history`、GFS/WU/IEM cache、settlement/research 输出至少要有文件清单、mtime、row count/hash 的 parity report。

## 1. 目标与架构决策

**目标**：把"实际运行采集"这件事从 `weather-predict` 搬到一个独立部署，使 `weather-predict` 可退役，
达到 module doc 的 step 3——"数据模块发一个 SHA，策略侧只升级策略代码而不动数据采集"。

**决策（已与用户确认）**：
- **运行层 = 独立部署 checkout** `~/projects/weather_data_feed_service/`：拉**同一个 `pm_agents` repo**，
  只跑数据采集服务。**不**塞进 pm_agent（pm_agent = 消费/执行，塞生产者会破坏边界、且多克隆下生产者有歧义）。
- **代码层 = `weather_data_feed/` 仍是本仓库的共享包**，被 import，不变。
- "独立部署" ≠ "马上拆独立 git repo"：先用同 repo 的专用 checkout 拿到运行隔离；拆 repo 等 module doc §"是否拆 repo 的判断标准"满足后再说。

三模块目标边界（不变，重申）：
```text
weather_data_feed/（包）        数据逻辑：城市日历/source profile/官方观测/snapshot 协议
weather_data_feed_service/      运行采集服务（本计划新建）：跑 snapshot + daily-pipeline，产标准产物
pm_agent（+ 策略克隆）          只读标准产物 → signal/plan/order/fill
Mac pm_agents                   镜像产物 → fact 重建 / 复盘 / 看板
weather-predict                 退役 → dormant（保留不删）
```

## 2. 探查步骤（执行第一步，不要跳过、不要假设）

在 N100 **只读**确认以下事实，与本文 §3 记录的起点对照，有出入以 N100 为准：

```bash
# 1. 当前数据采集的 systemd --user units（本计划起点：2 个）
ssh jiarui@192.168.0.200 'systemctl --user list-timers --all | grep -iE "weather|snapshot|pipeline"'
ssh jiarui@192.168.0.200 'systemctl --user cat weather-predict-snapshot.service weather-predict-snapshot.timer weather-predict-daily-pipeline.service weather-predict-daily-pipeline.timer'
# 2. 两个 runner 的真实依赖（确认还有没有别的 weather-predict 本地 import）
ssh jiarui@192.168.0.200 'cd ~/projects/weather-predict && grep -nE "^from |^import |sys.path" paper_snapshot.py daily_pipeline.py'
# 3. 输出路径（镜像同步依赖这些，绝不能变）
ssh jiarui@192.168.0.200 'cd ~/projects/weather-predict && ls output/ cache/'
# 4. 有没有遗漏的 weather 采集 unit / cron
ssh jiarui@192.168.0.200 'systemctl --user list-units --all | grep -iE "weather|snapshot|gfs|pm_hist|settle"; crontab -l 2>/dev/null | grep -iE "weather|snapshot"'
```

## 3. 当前状态（2026-06-19 实测起点）

**采集足迹 = 2 个 systemd --user 服务**（station-basis-shadow 是策略[B]层，不在范围）：

| service | runner | 输出 |
|---|---|---|
| `weather-predict-snapshot.service`(+timer) | `run_paper_snapshot.sh` → `paper_snapshot.py` | `output/paper_snapshots/`（已是 `weather_data_feed_snapshot_v1` 协议） |
| `weather-predict-daily-pipeline.service`(+timer) | `daily_pipeline.py` | `cache/pm_history/`、`cache/iem*`、`cache/wu_obs/`、`output/research/` |

Codex 只读复核（2026-06-19）：
- `systemctl --user list-timers/list-units` 只发现上述两个 weather-predict 采集 unit；`station-basis-shadow-v1.service` 是策略/shadow 层，不在本次采集迁移范围；
- `crontab` 未发现额外 weather/snapshot 任务；
- unit `WorkingDirectory` 目前都是 `%h/projects/weather-predict`，输出日志在 `weather-predict/output/logs/`；
- `paper_snapshot.py` import 与本文记录一致：`city_pools`、`pm_edge_compare`、`calibration_backtest`、`weather_data_feed`；
- `daily_pipeline.py` import 与本文记录一致：`pm_edge_compare`、`edge_backtest`。

**runner 依赖（关键：当前不是纯数据采集）**：
- `paper_snapshot.py` import：`weather_data_feed`（共享包✓）+ weather-predict 本地 `city_pools`、`pm_edge_compare`（含 `compute_bracket_probs` **模型概率**）、`calibration_backtest`（`load_wu_obs`/`load_gfs_daily`）。
- `daily_pipeline.py` import：`pm_edge_compare`、`edge_backtest`（`fetch_settled_event`/`fetch_price_at_t_minus` 数据抓取）。

依赖分层定性（执行时 Codex 复核）：

| 模块 | 性质 | step 3 处理 |
|---|---|---|
| `weather_data_feed` | 数据层（已是包） | 直接 import，不动 |
| `city_pools` | 数据/配置（城市池/日历） | 随 runner 搬；后续可并入 `weather_data_feed` |
| `pm_edge_compare` | **混**：forecast/价格抓取=数据；`compute_bracket_probs`/edge=模型[1-2] | step 3 整体随 runner 搬（保持输出不变）；模型部分标为已知债（§5） |
| `calibration_backtest` | 模型验证，但含数据 loader | 随 runner 搬其被用到的 loader；Brier 验证不在采集路径 |
| `edge_backtest` | 策略层，但含数据抓取函数 | 随 runner 搬被用到的 fetcher；回测主体不在采集路径 |

## 4. 迁移阶段（每阶段一个可回滚 commit，git-first）

**Phase 0 — 探查与基线**：执行 §2；记录当前 snapshot/daily 产物的样例 + `weather_data_feed_prod_health_check.py status=ok` 作为基线。

**Phase 1 — 让采集可从独立 checkout 跑（代码，不动生产）**
- 在 `pm_agents` repo 内，确保两个 runner 及其 weather-predict 本地依赖（`city_pools`/`pm_edge_compare`/相关 loader/fetcher）有一份可被独立 checkout 运行的来源。
  二选一（Codex 评估后定，记进本文）：
  - (a) 把这些 runner+依赖**纳入 `pm_agents` repo**（放 `weather_data_feed_service/` 或 `scripts/data_feed/` 下），import 路径改成依赖 `weather_data_feed` 包；weather-predict 本地副本转兼容 re-export。
  - (b) 若依赖太重、本阶段不宜搬，则保留它们在 weather-predict，独立 checkout 通过 sibling path 引用——但这只是过渡，未真正解耦，必须在本文标注。
- 加/复用 `weather-data-feed snapshot` / `daily` 入口（module doc 已计划的 CLI）。
- 给 runner 增加明确的输出根目录配置，例如 `WEATHER_DATA_FEED_OUTPUT_ROOT` / `WEATHER_DATA_FEED_CACHE_ROOT`。
  并行期禁止依赖 `cwd == weather-predict` 的隐式输出。
- 把 systemd unit 模板和安装脚本放进 repo，例如：
  - `deploy/systemd/user/weather-data-feed-snapshot.service`
  - `deploy/systemd/user/weather-data-feed-snapshot.timer`
  - `deploy/systemd/user/weather-data-feed-daily.service`
  - `deploy/systemd/user/weather-data-feed-daily.timer`
  - `scripts/ops/install_weather_data_feed_service_units.sh`
- **不动 N100。** 本机 + CI 跑通 runner（py_compile + 现有 `tests/pmm_tests/test_weather_data_feed_*`）。

**Phase 2 — 在 N100 起独立服务（并行，不停旧的）**
- N100 新建 `~/projects/weather_data_feed_service/`：git clone/worktree 同一 `pm_agents` repo（**git-first**，不 scp）。建独立 `.venv`。
- 新建 `weather-data-feed-snapshot.{service,timer}` 和 `weather-data-feed-daily.{service,timer}`（systemd --user），ExecStart 指向新 checkout 的 runner。
  unit 文件必须来自 repo 模板/安装脚本；允许写入 `~/.config/systemd/user/`，但写入内容必须可由 git SHA 复现。
- **输出路径有两种安全做法，二选一**：
  - (a) 新服务写到**新目录**，并行期不与旧的争抢；parity 比对新旧两份。
  - (b) 新服务写到与旧**相同路径**但**旧服务先 stop**——不可并行同写同目录（会互相覆盖）。
  推荐 **(a) 并行写新目录 + parity**，cutover 时再把输出指向正式路径。

**Phase 3 — 并行 parity 验证（通过前绝不切）**
```bash
# 新旧 snapshot 协议/字段/重复 一致性
python3 scripts/ops/weather_data_feed_parity_check.py   # 比对新服务 vs 旧 snapshot
python3 scripts/ops/weather_data_feed_prod_health_check.py --snapshot-dir <新输出> --runtime-root runtime/weather_edge_v1
```
通过线：连续 N 个采集周期 `status=ok`、必备字段齐（`city/target_date/market_local_date/city_local_date_at_snapshot/snapshot_ts_utc`）、无 `(city,target_date,token_id,bracket)` 重复、daily-pipeline 的 pm_history/GFS/结算产物与旧一致。

新增运行时长通过线：
- snapshot runner wall-clock p95 必须低于 timer interval 的 80%，或显式把 timer cadence 调整到不会让上一轮和下一轮背靠背排队；
- 若仍为 30 分钟 timer，则 snapshot 单轮目标应稳定低于 24 分钟；
- 若做不到，先拆慢任务或降低 snapshot 内的非必要 enrichment，不进入 Phase 4。

新增 daily parity 产物：
- `scripts/ops/weather_data_feed_daily_parity_check.py`（待实现）应输出旧/新两边 cache/output 的文件清单、mtime、size、row count/hash；
- cutover 前 daily parity 不能只靠人工 `ls`。

**Phase 4 — 切换（不可逆步骤，显式确认）**
- 停并 disable weather-predict 的 2 个 unit。
- 新服务输出切到正式路径（镜像同步 `sync_weather_remote.sh` 拉取的那些路径）。
- 确认 Mac 镜像同步、`run_stack.sh` 重建、current-YES/各 shadow loop 仍读到新鲜数据。

**Phase 5 — weather-predict 转 dormant**
- weather-predict 目录**保留不删**（dormant：可能还有零碎工具/历史）；只是不再有采集 unit。
- 更新文档：`WEATHER_DATA_FEED_MODULE.md`（step 3 标完成）、`WEATHER_REPO_BOUNDARY.md`、`AGENTS.md/CLAUDE.md §2`（采集者从 weather-predict 改为 weather_data_feed_service）、`OPS_RUNBOOK.md`（新 service 名/重启命令）。

## 5. 已知债（step 3 不解决，留后续 [1] 层重构）

- `paper_snapshot.py` 仍内联 `compute_bracket_probs` 模型概率——**snapshot 产物里带 `model_p_yes` 是数据/模型耦合**。
  step 3 保持输出不变（含 model_p），**不**借机抽模型，否则范围失控。后续单独做"把概率计算从采集移到 [1] 信号层"。
- `city_pools` 后续可并入 `weather_data_feed`。
- 拆独立 git repo 是更后面的事，判据见 module doc。

## 6. 不变量（任何阶段不能破）

- **输出路径与字段协议保持兼容**：镜像同步、fact 重建、各策略 loop、复盘链路全靠它。
- **必备 snapshot 字段**：`city / target_date / market_local_date / city_local_date_at_snapshot / snapshot_ts_utc`。
- **生产变更走 git-first**，禁止 scp/rsync 直推；每个不可逆步骤显式确认、可暂停、可回滚。

## 7. 回滚

任一阶段失败：重新 enable weather-predict 的 2 个 unit、disable 新服务、新服务输出留作 diff 证据，不删。
因为旧服务全程保留到 Phase 4，回滚成本极低。

## 8. 交付确认清单（Codex 执行完逐项打勾回报）

- [ ] §2 探查完成，与 §3 起点对照无遗漏 unit/依赖
- [ ] Phase 1：runner 可从独立 checkout 跑，本机测试 + py_compile 通过
- [ ] Phase 2：N100 新 checkout（git-first）+ 4 个新 unit 就位
- [ ] Phase 3：parity + health 连续 N 周期 `status=ok`，字段/重复/daily 产物一致
- [ ] Phase 4：切换完成，Mac 镜像/看板/策略 loop 读到新鲜数据
- [ ] Phase 5：weather-predict 转 dormant（保留），文档全部更新
- [ ] 已知债（model_p 耦合）记入后续 backlog

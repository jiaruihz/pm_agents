# 统一运维手册（第一版）

这份文档只解决当前最实际的问题：

- 现在有哪些常驻进程
- 哪些进程在机器重启后需要手动拉起
- 现在机器上哪些服务正在跑
- 出问题时先看哪里

目标不是做完整运维体系，而是先让当前仓库可维护。

## 1. 统一查看入口

### Mac JRS 常驻进程

Mac 上所有 weather/tmax/range 常驻进程统一由
`scripts/ops/weather_jrs_tmux_env.sh` 管理，唯一 socket 是
`weather-data-feed-jrs`。各业务 `start_*.sh` 是 controller 的底层合同，不是人工入口，
不再支持默认 tmux、独立 socket、screen、nohup 或 start-mode fallback；启动前的
JRS write probe 由 helper 在 tmux server 内执行。helper 固定使用 tmux `-N`
attach-only：server 不存在时 fail closed；只有 controller `recover-jrs-context`
可以创建或重建 permission host，persistent session mutation 也必须带 controller authority。

该 tmux server 的权限宿主固定为已在 macOS「完全磁盘访问权限」中授权的
`/opt/homebrew/Cellar/tmux/3.6b/bin/tmux`，helper 同时固定其 SHA-256。
禁止让 Homebrew symlink 静默切换生产 binary。升级 tmux 时必须先把新 binary
加入完全磁盘访问权限，再更新 helper 的 path/hash、运行入口契约测试，并在维护
窗口重建 canonical server；任一步未完成都继续使用旧的已授权 binary。

底层只读诊断先 source helper，再调用
`weather_jrs_tmux weather-data-feed-jrs list-sessions`；日常盘点仍优先使用下面的
controller health/plan，禁止直接执行 raw tmux 命令。

JRS/TCC、canonical tmux crash、历史入口与验收记录统一维护在
[WEATHER_JRS_RUNTIME_INCIDENTS.md](WEATHER_JRS_RUNTIME_INCIDENTS.md)。

当前生产 desired state 在 `src/strategies/runtime/production.yaml` 的
`managed_runtimes`。它与研究/历史 `instances.yaml` 分开：只有
`managed_runtimes` 表示“现在应该持续运行”。统一控制入口：

```bash
# 人类可读的全链路健康检查；critical 时退出码为 2
.venv/bin/python scripts/ops/weather_production_ctl.py health

# 机器可读输出
.venv/bin/python scripts/ops/weather_production_ctl.py health --json

# 只显示 desired vs observed 和建议动作，不改生产
.venv/bin/python scripts/ops/weather_production_ctl.py plan

# 仅补启 production.yaml 中缺失且有恢复合同的实例；不停止任何额外进程
.venv/bin/python scripts/ops/weather_production_ctl.py reconcile --apply \
  --reason "named incident recovery"

# 如果恢复集合包含 live，必须再显式确认
.venv/bin/python scripts/ops/weather_production_ctl.py reconcile --apply \
  --confirm-live --reason "named live incident recovery"

# 单实例重启也必须走 controller；先不带 --apply 查看目标合同
.venv/bin/python scripts/ops/weather_production_ctl.py restart \
  --instance INSTANCE --json

# safe 非 live 实例使用 exact-session stop + registered start；live 缺显式合同时阻断
.venv/bin/python scripts/ops/weather_production_ctl.py restart \
  --instance INSTANCE --apply --reason "named runtime restart"
```

Market proxy endpoint 与 Clash 节点都只通过统一 controller 管理；禁止再编辑多个
`.env`、逐脚本改 `--market-proxy` 或恢复历史独立 failover 进程：

```bash
# 只读检查 default/stable 命名 route、Clash group、Gamma/CLOB 与 geoblock probe
.venv/bin/python scripts/ops/weather_market_proxy_ctl.py status

# 只读预览 Clash profile enhancement；apply 需在已确认的网络维护窗口
.venv/bin/python scripts/ops/weather_market_proxy_ctl.py gateway-overlay

# Allblue fallback route 连续失败后，有界切换 selector；runtime monitor 每 60 秒执行
.venv/bin/python scripts/ops/weather_market_proxy_ctl.py maintain --apply --confirm-live \
  --trigger manual --reason "default route recovery"
```

天气业务只调用共享接口：默认 route 与公共 CLOB live execution handoff 的
`route_key=stable` 均解析到同一 Clash 进程的 `7896` 命名 listener。`PM-STABLE` fallback 组按
TAG 本机 `7890` → Allblue 顺序自动探测切换，切换不修改业务参数、不重启 consumer。Allblue
命名备选入口为 `7897 / allblue`；当前 selector 连续失败时，controller 从已有延迟证据中最多尝试 12 个候选，并对每个候选做
Gamma+CLOB 与 Polymarket geoblock 实测；全部失败则恢复原节点，切换写 append-only audit。controller
通过 Clash 既有 Unix socket `/tmp/verge/verge-mihomo.sock` 读取 group/current node，并分别对
`7896/7897` 做 Gamma+CLOB probe；不启用 TCP external controller，不创建 API secret，所有上游
失败时显式报错，不 fallback direct。

旧 `market_proxy_state_path` 只用于暴露历史 endpoint drift，不再改变 named default route；
`auto/switch` endpoint 重载入口已移除。普通采集固定解析 `default`，不可逆下单/撤单 transport
在公共 execution handoff 内解析 `stable`，两者当前是同一 TAG-backed 入口；策略 launcher 不拥有 route 选择权。稀疏 shadow 在
无信号时按 process/dependency/proxy binding 验收，不因业务 summary 未刷新产生假回滚。

系统/Codex 的 TAG 节点维护与上述 weather route 分开：`tag_proxy_route_ctl.py` 只切 TAG 已有
`🙂 TAGSS` selector 内的 `1x` 节点，不改 macOS 代理端口、`7896 / PM-STABLE`、`7897` 或任何 consumer。
它用 OpenAI 实际端点而不是只用 `generate_204` 验收，连续两个 degraded cycle 后才对有界的
`1x` 候选测速；候选固定按 **HK → MY → 其他候选地区** 分层，层内再按健康度和延迟排序。
Polymarket `trading_allowed!=true` 则不等待第二个 cycle、不受冷却限制。地区表只负责候选排序，
每个节点切后都要连续两次通过 Polymarket geoblock，并同时通过 OpenAI 探测才可接受；因此地区政策变化时
以实时 venue verdict 为准。受限地区会继续尝试下一个候选，全部失败时即使 selector 回到原节点，
只要原节点没有重新证明可交易，live entry gate 仍保持关闭。
每次 bounded probe 追加到主机状态目录的月度 `probes-YYYY-MM.jsonl`，切换另写月度
`switches-YYYY-MM.jsonl`；单次三探测允许一次偶发超时，只有多数失败或整体持续变慢才累计 degraded cycle，
后续机场评测以这份长期可用率/尾延迟证据为准，不用单次测速图代替稳定性。

Core Carry 与旧 Cross NO 的所有新单提交都必须在共享 `maintain.lock` 上持有 shared lock，读取 5 分钟内的
`live_entry_gate.json`，并在**实际 execution proxy (`stable`)** 上即时调用 Polymarket geoblock；三项任一失败都在
CLOB handoff 前以 `live_entry_region_blocked:*` 拒绝。TAG controller 切节点时持有 exclusive lock 且先关 gate，
所以不能出现“探测时是香港、post 时已切到受限节点”的竞态。撤单、查单和 reconciliation 不经过 entry gate，
线路异常时仍可降风险。

```bash
# 当前节点与 OpenAI 端到端状态
.venv/bin/python scripts/ops/tag_proxy_route_ctl.py status

# 只读比较 1x 候选；不会改节点
.venv/bin/python scripts/ops/tag_proxy_route_ctl.py benchmark

# bounded one-shot；常驻调度只允许 com.pm-agents.tag-proxy-route-maintain
.venv/bin/python scripts/ops/tag_proxy_route_ctl.py maintain --apply \
  --reason "host TAG route maintenance"

# 推进 control_plane release pin 后，从该 immutable checkout 原子重装 LaunchAgent；
# 不得把 ProgramArguments 指回 mutable operational repo。
.venv/bin/python scripts/ops/install_tag_proxy_route_launchagent.py \
  --production-spec src/strategies/runtime/production.yaml \
  --apply --confirm-production --reason "pin TAG route controller release"
```

机器可读矩阵写到 production contract 解析出的
`output/market_proxy_control/latest.json`，覆盖每个 managed runtime 的 role、health trigger、
dependency、artifact age、issues 与 proxy-consumer 标记。

`health` 同时检查 canonical DB/进程 manifest、全部 required tmux sessions、
关键 runtime artifact freshness、checkout、live flags、`live_enabled`、上游依赖，
以及 observation/forecast/orderbook/snapshot/source-model 的 data-feed 语义健康。
城市 same-day weather state、少量 fresh forecast curve / orderbook target 缺口单列为
warning；forecast fallback、stale/invalid forecast capture、核心 cache/parity 或整层
orderbook 缺失为 critical。当前已关闭的旧 `fast_observation_state` 不再覆盖活跃的
`weather_live_cross_observations` 健康判断。
除 permission-host keeper 外，当前 required shadow/collector/monitor 都有显式
start/health/dependency contract；controller 不从正在运行的 pane 猜恢复命令。
canonical refresh 是唯一允许的 unmanaged bounded one-shot，但只能 attach，不能创建
server。生产操作不得直接用 `tmux kill-server`、`tmux kill-session`、底层 start/stop
脚本或手拼 live 命令绕过 controller。

所有 heartbeat 驱动的 freshness gate 必须在 `production.yaml` 的 consumer runtime 下声明
`freshness_contracts`；producer 自己用 `health_publish_interval_sec` 单点声明并由部署验收测量实际
publish cadence，consumer 只引用 producer instance，同时冻结允许漏掉的 heartbeat 数、max-age
及其 CLI 参数。允许漏掉的 heartbeat 不得少于 3 个，且
`consumer_max_age_sec >= producer_interval_sec * min_missed_heartbeats`；controller 会同时校验
静态关系和运行中 pane 的真实参数。数据年龄超过这个合同仍应 fail closed，但不得用比 producer
正常发布周期更短的阈值制造永久 stale。

所有 `expected_live: true` runtime 必须统一满足：`execution_mode: live`、JSON health、明确的
accepted status、显式 restart contract、运行命令同时含 `--live --confirm-live`，health artifact
明确报告 `live_enabled: true`。缺任一项都属于部署合同失败，不得用“业务 ready”覆盖。

restart wrapper 的退出码不是唯一成功依据。wrapper 非零但目标 session 的 pane 0 已换成新 PID 时，controller
必须继续等待 post-start health artifact 刷新并通过完整 health contract；只有 PID 未替换、目标退出或
grace 超时才判 restart 失败；不得用整个多-pane session 的 PID 集合代替目标 pane identity。bounded
health/probe tmux session 自然退出不得计为 persistent session
丢失；persistent session 真丢失仍必须 fail closed。

controller 启动 release checkout 时通过 `WEATHER_PRODUCTION_CONFIG` 指向控制仓库的
`production.yaml`。已有 tmux server 不会继承 client 的环境，因此 shared helper 会在每个
`new-session/new-window` 上显式注入该变量；controller 缺失该变量时 fail closed。
manifest 同时读取 session environment；出现
`managed_session_production_config_not_injected` 表示旧 session 仍可能读取 release-local
historical config，需要按实例滚动重载，不能把 release checkout 自带配置当第二份 desired state。

release checkout 只承载 immutable code；`.venv`、可选 `.env` 与
`runtime/weather.db` 都是 `production_releases[].runtime_bindings` 显式声明的非 Git
运行时绑定，专用环境文件通过 `runtime_binding_sources` 锁定原 source。
controller 必须在停旧 session 前幂等创建缺失软链接，遇普通文件、错指向或
source 不健康则 fail closed；manifest 独立只读审计同一合同。禁止复制 `.env`、在 release
内创建本地 `weather.db`，或靠手工补链接完成常规发布。

如果发现进程仍在其他 socket，只记录并按生产变更流程迁移；涉及 live 的 session
不得在巡检中自动重启或跨 socket 搬迁。

凡是会重建 canonical tmux server、批量重启 session 或调整生产启动集合的操作，
必须先保存 observed manifest，完成后比较 session 全集：

```bash
PRECHANGE_DIR="$(mktemp -d /tmp/weather-production-prechange.XXXXXX)"
PRECHANGE_MANIFEST="$PRECHANGE_DIR/manifest.json"
.venv/bin/python scripts/ops/weather_production_manifest.py --strict --json-out "$PRECHANGE_MANIFEST"
# 执行已授权的改动
.venv/bin/python scripts/ops/weather_production_manifest.py --strict --compare-prechange "$PRECHANGE_MANIFEST"
```

改动前存在、改动后消失的任何 canonical JRS session 都是 `critical`，部署不算完成。
只有本次明确要停的实例才可逐项传
`--allow-missing-session SESSION`；不得因为主要服务已恢复就忽略其他 strategy/shadow/collector。

`scripts/ops/process_status.py` 只保留为旧 PMM/个人工具的辅助诊断；weather 生产盘点不得用它替代上面的
controller health/plan 与 strict manifest。

## 2. 当前生产范围

weather 当前生产拓扑只读 `src/strategies/runtime/production.yaml`，实际状态只读
controller health/plan 与 production manifest。不要在 runbook 维护第二份“应该运行的进程”清单，也不要从日期报告、
PID 文件或某次 `ps` 输出推断 desired state。

- data-feed、strategy、shadow、monitor、patrol、canonical refresh 与 dashboard API：按 production contract 由
  controller 管理；凡访问 JRS 的常驻进程都在 canonical JRS tmux context。
- dashboard FE：可由非 JRS LaunchAgent 承载；API 仍属于 controller-managed runtime。
- Telegram Research Bot、旧 PMM/ARB runner 和其他个人工具：不属于 weather production desired state；需要时按各自
  明确请求处理，不能混入 weather reconcile/recovery。
- N100：只作历史正本/灾备边界，不是当前生产 truth。旧 doctor、proxy failover、crontab、systemd 和 live 启动命令从
  活跃 runbook 移除；需要恢复时必须先建立独立 N100 恢复合同并验证磁盘、备份与服务链。

Dashboard 页面与端口见
[`WEATHER_DASHBOARD_TROUBLESHOOTING.md`](WEATHER_DASHBOARD_TROUBLESHOOTING.md)。
`run_stack.sh` 不启动 API/FE；全量重建只能显式执行 `run_stack.sh --rebuild`，且不会启停服务。

## 3. 当前哪些信息算“真”

优先级按下面来：

1. `weather_production_manifest.py --strict` 的进程、checkout、tmux、LaunchAgent、DB route 与 runtime 事实；
2. `weather_production_ctl.py health/plan` 对 production contract 的解释；
3. 对应 Mac raw runtime 与 exchange order/fill response；
4. canonical `weather.db`（分析派生事实，不替代当前进程/订单状态）；
5. living docs 与 registry（研究状态，不替代生产状态）。

`ps`、PID 文件、`strategy_runtime.db` 和 `process_status.py` 只能帮助诊断，均不能单独证明实例健康、JRS 权限、live 授权
或订单状态。

## 4. 重启后最小恢复清单

**统一入口：**

```bash
scripts/ops/after_reboot.sh
```

无参数只执行 manifest/controller health+plan，不启停任何服务。显式恢复使用 `--apply`；live 恢复还必须带 `--confirm-live`。`--apply` 会根据当前存在的 managed session 自动选择：没有任何 managed session 时走 `recover-jrs-context`；存在 managed session 且 canonical JRS write probe 健康时走 `reconcile`。若 session 尚在但 JRS probe 失败，自动模式会 fail closed，要求先检查或显式选择 `--recover-jrs-context`，不会把权限主机故障误当成普通缺 session。仍可用 `--recover-jrs-context` 或 `--reconcile` 显式覆盖模式。

分项运行：

```bash
scripts/ops/after_reboot.sh
scripts/ops/after_reboot.sh --apply --confirm-live
scripts/ops/after_reboot.sh --apply --confirm-live --restore-manifest /tmp/pre-reboot-manifest.json
scripts/ops/after_reboot.sh --recover-jrs-context --apply --confirm-live
```

恢复事务在任何 tmux 重建或 runtime 启动前，统一检查所有可能启动的 pinned checkout：full SHA、tracked cleanliness、可执行 start script、`.venv/bin/python`、启动脚本依赖的 `.env`，以及已有 health artifact 是否满足 `expected_health_fields`。任一项不满足即整体 fail closed，不允许先恢复一半拓扑再发现 release 不可启动。

启动器返回不等于 runtime 已恢复。Controller 会把“session 已创建但 launcher 非零”标为 `warming`，并在每个 runtime 的 `startup_grace_sec` 内等待：session 仍存在、health 合同通过、且 health artifact 的 mtime 晚于本次启动。只有三项同时满足才转为 `started/converged`；超时或 session 退出均返回 `critical`。data-feed 因完整首轮较慢使用独立 900 秒 grace，market-books 使用 180 秒，其他 runtime 默认 180 秒。

`--restore-manifest` 只用于确实保存过的本机 pre-reboot/pre-change manifest；不得自动选择不明日期的旧 manifest。没有可靠 manifest 时让 controller 按当前 `production.yaml` desired state 启动 pinned release，不能从旧 pane 命令猜生产状态。

成功验收至少包括：JRS prospective/current probe、所有 desired-running session、每个新启 runtime 的 post-start health、paused session 未被恢复、无 extra persistent session，以及 live order/fill/open-order evidence。authenticated open-order 不可用时必须记为 `unknown`，不能推断为零。Manifest strict 的 critical exit code 是 `1`，controller health/plan 的 critical exit code 是 `2`；wrapper 只对各自正确的 documented code 放行，其他错误会停止恢复。

### 4.1 恢复必须收口到业务循环（2026-09-14 复盘）

“恢复所有脚本”包含进程存在但业务挂起/报错的目标。先记录 `sysctl kern.boottime`，再区分整机 reboot、WindowServer 重建、单进程退出；不按用户口语推定整机已重启。复用一次 manifest/health snapshot，将目标分成 missing、present-unhealthy、healthy、desired-paused。`reconcile` 只覆盖 missing，不能作为全部恢复的完成证据。

对 present-unhealthy，先按最窄 instance/order key 读原始错误、continuity、execution journal 和私有订单流，再选恢复动作。必须检查实际 pinned checkout 的实现与 CLI，不能用开发仓库同名旧文件推断生产行为。

- `suspended=true / retry_safe=false`：普通 restart 会重新载入持久化挂起。只有精确拒单/成交/撤单证据完成对账后，才可使用既有 reconciliation acknowledgement；不清空 journal/dedupe，不放行其他 ambiguous key。不能把“HTTP400”统一认作未成交；按已验证的具体响应分类。
- REST `get_order=null`：并列检查 authenticated private WS 的 exact order/token/side/original/matched。Core 的 `INVALID + CANCELLATION` 有数量证据时按既有修复合同投影终态，缺失/不一致继续阻断；不得伪造 REST 状态。
- 已终态且 token 下架：验收应覆盖“有终态证据、REST order=null、book=404”的完整收尾链，先落终态，不能依赖盘口仍存在。检查落盘后下一周期不再重复 lifecycle action。
- 旧版 acknowledgement 存在心跳覆盖竞态。2026-09-14 已在 Cross NO `3a636aa2`、Tmin live `6d47f5c5` 修复：同一 instance 的 failure/recovery/heartbeat/ack writer 使用内盘稳定 `.state.lock` 串行读改写，heartbeat 保留有效 ack；旧进程保持暂停，由 controller restart 后的新进程读取确认。锁文件不得删除或轮转。其他 release 不能仅凭同名文件推定已修复。ack 仍必须有交易所/日志对账依据；该修复不等于自动对账或原子停止整个业务进程。
- 已有 continuity 状态损坏、非对象、身份不符或不可读时保持暂停；首次启动文件不存在才允许按无历史事故启动。禁止通过删除状态文件解锁。修复读取条件后仍须对账确认，再走 controller restart；回归必须覆盖跨进程锁、旧心跳、后续新事故和损坏状态。

最终验收逐个目标列出：loaded SHA、实际参数/caps、至少两个新的完整业务周期、连续 health=healthy、live_errors、continuity 与原始 order/fill/open-order 变化。启动 wrapper 写出的启动标记不算业务周期。已生成新 PID 但状态仍旧/错误时继续定位，不交付“恢复完成”。若范围外仍有告警，分开写明，不把全局 critical 误称全绿。

发布只纳入本次 owned diff；production.yaml 在写入前重读，按精确 release block 更新并逐项核对 diff，保留其他任务同期的 paused/参数变更。源码基于各自 pinned release 时先识别发布工具的 ancestry 前提，不临时混入开发 HEAD。独立 review 后若生产验证又暴露新路径，补对应回归并重新运行全部相关测试；review 覆盖范围与后续修复分别记录。

### 4.2 故障排查不得放大主机内存压力

2026-09-10/14 两次 watchdog 窗口均出现 Codex `rg` 进程占用约 5.6/8.4 GiB，并能在巡检记录对应到宽范围 runtime 搜索。生产主机只有 16 GiB；避免把诊断本身变成高峰负载。

- 不对 `/Volumes/jrs/pm_agents/runtime`、整个 data-feed root 或单策略的全部历史 JSON/JSONL 做无界内容搜索；`rg ... | tail`、输出 token 截断都不限制扫描工作量。
- 先由 manifest 的 health/order 路径定位文件，再看文件大小和目标时间/实例/order；日志优先读取有界尾部，历史 JSONL 用逐行解析并按日期/key 筛选，结果只输出摘要。巨长单行 JSON 不使用上下文宽搜。
- 源码仍优先 `rg`，明确文件/目录范围；诊断报告仅解析所需 JSON 键，不输出整份 stackshot、所有 jetsam victim 或完整 manifest。独立读取可并发，内存重型扫描/回放在此主机串行。
- 先记录 memory pressure、compressor、swap 与内置盘空闲空间，再决定是否进行额外大数据分析。不得通过加大输出上限、加重试或并发搜索修复慢查询。

## 5. 常用入口

- desired state：`src/strategies/runtime/production.yaml`
- current fact manifest：`scripts/ops/weather_production_manifest.py --strict`
- controller：`scripts/ops/weather_production_ctl.py`
- JRS context helper：`scripts/ops/weather_jrs_tmux_env.sh`
- canonical DB：`production.yaml` 解析出的 physical canonical；`runtime/weather.db` 仅为同 inode 兼容入口
- current raw、active order journal、health artifacts：全部从 production contract/共享 loader 解析，不在本文写死路径
- 事故与恢复证据：`WEATHER_JRS_RUNTIME_INCIDENTS.md`

## 6. 当前已知缺口

以下缺口不得被旧 runbook 的“已配置”叙述掩盖：

- controller 统一了入口，但不等于 macOS TCC/JRS 根因永久消失；reboot/login、fresh permission host、server
  death/recreate 等验收状态以事故 living doc 的最新证据为准。
- N100 tar、repo 镜像和 JRS 同盘目录都不能自动算当前生产备份。当前 raw/canonical 的异盘、可恢复、定期校验
  备份合同若未在 canonical sources 中登记，就仍是运维缺口。
- 非 production-contract 的个人工具不享受 weather controller 自动恢复；不得因此扩张 controller scope。

## 7. 主机级可用性监督

`scripts/ops/production_reliability_supervisor.py` 是唯一周期可用性调度入口：它从 Mac 内置盘执行，单次巡检 weather 全量
runtime/semantic health、JRS mount UUID 与容量、CLOB/Open-Meteo 代理出口，以及 crypto registry 的 45 个 retained jobs、BTC/ETH
collector、context 和 settlement freshness。状态、影响窗口、恢复动作和通知失败均写到：

```text
~/Library/Application Support/pm_agents/production_reliability/
  latest.json
  state.json
  events.jsonl
  repair_actions.jsonl
  notification_failures.jsonl
```

只读实检：

```bash
.venv/bin/python scripts/ops/production_reliability_supervisor.py --fail-on-degraded
```

安装外层 LaunchAgent 属于生产行为变更，需经确认后执行。默认只监控和 Telegram 通知；只有显式设置
`PRODUCTION_RELIABILITY_APPLY_SAFE=1` 才允许调用现有入口修复 weather safe/non-live runtime 与 crypto research/shadow/dry-run：

```bash
scripts/ops/install_production_reliability_launchagent.sh
PRODUCTION_RELIABILITY_APPLY_SAFE=1 scripts/ops/install_production_reliability_launchagent.sh
```

业务级 executor failure、plan-without-order、guard block 等检查由只读
`weather_execution_semantic_health.py` 作为 probe module 被 supervisor 调用；它不再自行 loop、写告警状态或发 Telegram。
旧 `weather_runtime_monitor` tmux/LaunchAgent、`weather_live_runtime_patrol` 与 `weather_live_doctor` 不再是入口。

supervisor 不会重建 JRS permission host、修磁盘、切 guarded live、直接管理 tmux/launchd job 或绕开 controller。critical 连续 2 轮、
warning 连续 3 轮才开 incident；每项修复最多 3 次且至少间隔 15 分钟，恢复后主动发送影响窗口。Telegram 发送失败会保留待发
队列并重试。若要覆盖整机断电/全网断开的“本机无法发消息”场景，在 `.env` 配置外部 dead-man switch 的
`PRODUCTION_RELIABILITY_HEARTBEAT_URL`；没有外部心跳时，本机监控不能承诺发现本机完全离线。

## 8. 建议的后续升级顺序

1. 按事故 living doc 完成尚未覆盖的真实 Mac/JRS 恢复验收；
2. 为 current JRS raw/canonical 建立独立介质、可校验、可恢复的备份合同；
3. 新增 runtime 只扩展 production contract、controller 和一致性测试，不再增加平行 supervisor。

## 9. 备份边界

历史 N100 tar 与同步脚本只覆盖旧 `weather-predict` cache/output，不能恢复当前 Mac/JRS production 全链路，也不能证明
canonical DB、active raw/order journals 或 research artifact store 已备份。历史映射保留在
[`WEATHER_DATA_CANONICAL_SOURCES.md`](WEATHER_DATA_CANONICAL_SOURCES.md) 与
[`WEATHER_DATA_PIPELINE.md`](WEATHER_DATA_PIPELINE.md)，但活跃 runbook 不再提供可误执行的 N100 备份/恢复命令。

任何新的备份合同必须明确：physical source、独立目标介质、包含/排除范围、频率、retention、checksum、加密/凭据边界、
restore drill 与最近一次成功时间；只有实际 restore 验证通过后才能称“可恢复备份”。

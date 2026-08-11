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
# 只读检查 default/stable 命名 route、Clash group 与 Gamma/CLOB probe
.venv/bin/python scripts/ops/weather_market_proxy_ctl.py status

# 只读预览 Clash profile enhancement；apply 需在已确认的网络维护窗口
.venv/bin/python scripts/ops/weather_market_proxy_ctl.py gateway-overlay

# 默认 route 连续失败后，有界切换 Allblue selector；runtime monitor 每 60 秒执行
.venv/bin/python scripts/ops/weather_market_proxy_ctl.py maintain --apply --confirm-live \
  --trigger manual --reason "default route recovery"
```

业务只调用共享接口：默认 route 使用 `7897`；只有公共 CLOB live execution handoff 指定
`route_key=stable`，解析到同一 Clash 进程的 `7896` 命名 listener。`PM-STABLE` fallback 组按
TAG 本机 `7890` → Allblue 顺序自动探测切换，切换不修改业务参数、不重启 consumer。Allblue
当前 selector 连续失败时，controller 从已有延迟证据中最多尝试 12 个候选，并对每个候选做
Gamma+CLOB 实测；全部失败则恢复原节点，切换写 append-only audit。controller
通过 Clash 既有 Unix socket `/tmp/verge/verge-mihomo.sock` 读取 group/current node，并分别对
`7897/7896` 做 Gamma+CLOB probe；不启用 TCP external controller，不创建 API secret，所有上游
失败时显式报错，不 fallback direct。

旧 `market_proxy_state_path` 只用于暴露历史 endpoint drift，不再改变 named default route；
`auto/switch` endpoint 重载入口已移除。普通采集固定解析 `default`，不可逆下单/撤单 transport
在公共 execution handoff 内解析 `stable`，策略 launcher 不拥有 route 选择权。稀疏 shadow 在
无信号时按 process/dependency/proxy binding 验收，不因业务 summary 未刷新产生假回滚。
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

controller 启动 release checkout 时通过 `WEATHER_PRODUCTION_CONFIG` 指向控制仓库的
`production.yaml`。已有 tmux server 不会继承 client 的环境，因此 shared helper 会在每个
`new-session/new-window` 上显式注入该变量；controller 缺失该变量时 fail closed。
manifest 同时读取 session environment；出现
`managed_session_production_config_not_injected` 表示旧 session 仍可能读取 release-local
historical config，需要按实例滚动重载，不能把 release checkout 自带配置当第二份 desired state。

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

无参数只执行 manifest/controller health+plan，不启停任何服务。显式恢复使用 `--apply`；live 恢复还必须带 `--confirm-live`。canonical JRS permission host 重建只用 `--recover-jrs-context --apply`。

分项运行：

```bash
scripts/ops/after_reboot.sh
scripts/ops/after_reboot.sh --apply
scripts/ops/after_reboot.sh --recover-jrs-context --apply --confirm-live
```

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

## 7. 建议的后续升级顺序

1. 按事故 living doc 完成尚未覆盖的真实 Mac/JRS 恢复验收；
2. 为 current JRS raw/canonical 建立独立介质、可校验、可恢复的备份合同；
3. 新增 runtime 只扩展 production contract、controller 和一致性测试，不再增加平行 supervisor。

## 8. 备份边界

历史 N100 tar 与同步脚本只覆盖旧 `weather-predict` cache/output，不能恢复当前 Mac/JRS production 全链路，也不能证明
canonical DB、active raw/order journals 或 research artifact store 已备份。历史映射保留在
[`WEATHER_DATA_CANONICAL_SOURCES.md`](WEATHER_DATA_CANONICAL_SOURCES.md) 与
[`WEATHER_DATA_PIPELINE.md`](WEATHER_DATA_PIPELINE.md)，但活跃 runbook 不再提供可误执行的 N100 备份/恢复命令。

任何新的备份合同必须明确：physical source、独立目标介质、包含/排除范围、频率、retention、checksum、加密/凭据边界、
restore drill 与最近一次成功时间；只有实际 restore 验证通过后才能称“可恢复备份”。

---
name: weather-jrs-runtime-failover
description: 诊断并恢复 weather 生产的 JRS 外置卷/TCC/canonical tmux permission-host 故障，必要时安全切到 Mac 本机临时运行，并在 JRS 恢复后迁回、分层校验和清理副本。用于 JRS Operation not permitted、write probe 失败、canonical context 丢失、重复权限事故、临时 local output-dir、JRS repatriation、runtime 单一正本恢复；覆盖 controller recovery、历史复发审查、signal/plan/order/fill/dedupe 状态、关键状态 SHA 和影响半径。不要用于普通数据同步或 N100 恢复。
---

# Weather JRS runtime failover

先 invoke `weather-strategy-deploy`。本 skill 负责存储故障切换；live 启停、资金安全、git-first 和生产验证仍服从 deploy skill。

## 不变量

- 同一实例任一时刻只有一个可写 runtime 正本；不双写。
- JRS 常驻进程只用 `tmux -L weather-data-feed-jrs`，并通过
  `scripts/ops/weather_jrs_tmux_env.sh` 在该 tmux server 内执行 write probe。
- 共享 helper 使用 tmux `-N` attach-only。只有 controller 的 `recover-jrs-context`
  可以创建/recreate canonical server；业务 start/stop、monitor 和 LaunchAgent 不拥有该能力。
  persistent session mutation 由 controller 注入 authority，canonical refresh 只能执行 bounded
  one-shot；server 缺失时必须 fail closed，不能抢占同名 socket。
- canonical helper 必须使用 path/SHA-256 固定的 tmux binary；pin 只证明程序
  identity，不能证明当前 parent 仍有 TCC/JRS 权限。即使系统设置显示 Full Disk
  Access 为 on，也必须以 server 内真实 probe 为准。若 pin 缺失或 hash 漂移，先修
  identity；若 pin 正确但 probe 失败，按 permission-host 故障处理，不得写成磁盘损坏。
- 普通 shell 写不进 JRS，不等于 canonical tmux context 写不进。只有后者失败才进入本机接管。
- live 恢复前必须迁移完整 runtime state，包括 dedupe、plan、order、fill、maker lifecycle 和 pause/state；JRS 不可读或所需校验 profile 失败时不得恢复 live。
- 只改 storage location 时保持 repo SHA、参数、caps、source/signal/execution policy 完全不变。
- 删除临时副本需要用户明确授权。用户已要求“清理、不留维护成本”时，完成验证后直接清理，不再二次询问。

## 历史复发与完成口径

开始修复前搜索 `docs/WEATHER_LIVE_RUN_HISTORY_AND_DATA_GOVERNANCE.md`、相关 incident、git log
和 runtime logs，列出同症状历史的时间窗、当时修复层级和复发原因。不要根据最近一次恢复报告或
当前提示词推断历史已闭环。

明确区分：

- `contained`：停止错误 writer、临时恢复数据或阻止进一步影响；
- `entrypoint unified`：入口统一，但 permission host 仍可能失效；
- `recovery improved`：controller 能保存/恢复拓扑，但故障仍可能复发；
- `root cause eliminated`：根因有证据并通过真实失效场景验收。

只有 fresh permission host、canonical server death/recreate、锁屏/解锁、reboot/login、controller
全拓扑恢复以及不 mock 的 Mac/JRS integration smoke 均在维护窗口通过，才可写“永久解决”或
“一劳永逸”。否则交付必须使用前三种准确状态。

## 1. 锁定对象和证据

先写清：

```text
instance / PID / process manager / repo SHA
current runtime root / desired runtime root / compatibility path
live or shadow / shares / caps / pause
latest raw timestamp / open orders / latest order and fill
```

用 `ps`、目标启动脚本、runtime latest/state/orders、authenticated exchange evidence 动态确认。不要从旧文档或目录名猜实例。

记录切换开始时间。检查没有第二个 writer 指向同一实例目录。

## 校验分层

停止 writer 后使用 bundled verifier；显式使用项目 venv Python，避免 canonical
tmux 中的系统 Python 漂移：

```bash
.venv/bin/python skills/weather-jrs-runtime-failover/scripts/verify_runtime_tree.py \
  --mode tiered "$SOURCE" "$TARGET"
```

默认日常迁移使用 `tiered`：

- 全量比较 path、entry type、symlink target、file size、总 entries/bytes；
- 对 order/fill/plan/signal/state/pause/cursor/dedupe/maker lifecycle/DB
  等关键状态逐文件 SHA-256；
- 对 source/target metadata 变化的 bulk 文件增量 SHA；
- 对其余 immutable bulk 文件做确定性抽样 SHA（默认 32 个）；
- custom runtime 文件名不落入默认规则时，用重复的 `--critical-glob` 补充。

仅在以下情况使用 `--mode exact` 全树逐文件 SHA：

- 没有保存过同一 stopped tree 的可信 exact baseline；
- 首次迁移唯一副本，或删除后将失去唯一未归档来源；
- copy/磁盘曾报告 I/O error、文件系统损坏或异常掉盘；
- tiered 返回 mismatch / `requires_exact=true`；
- order/fill/dedupe/exchange 对账不一致，或人工抽查怀疑 same-size corruption。

同一 stopped tree 已有保存的 exact baseline 时，后续回迁和清理不重复全树 SHA；
保存 tiered JSON 即可。不得为了省时降低关键交易状态的 SHA 范围。

## 2. 判断是否真的需要本机接管

先运行 controller/manifest 保存全局现场，不要直接手拼 tmux：

```bash
.venv/bin/python scripts/ops/weather_production_manifest.py --strict --json-out "$PRECHANGE_MANIFEST"
.venv/bin/python scripts/ops/weather_production_ctl.py health
.venv/bin/python scripts/ops/weather_production_ctl.py plan
```

加载共享 helper，并从 canonical tmux 权限上下文探测目标 JRS root：

```bash
source scripts/ops/weather_jrs_tmux_env.sh
weather_jrs_tmux_write_probe weather-data-feed-jrs "$JRS_RUNTIME_ROOT"
```

- probe 成功：不迁移；修正 runner 的 process context。
- helper 报 tmux path/hash 漂移：不迁移；先按 `docs/OPS_RUNBOOK.md` 给新 binary
  授权 Full Disk Access、更新 pin 并重建 canonical host。
- probe 失败：记录原始错误并判断是 volume、现有 parent 还是 prospective host 故障；不要直接把
  一次失败等同于数据盘故障或立即迁移。
- JRS 能读不能写：允许复制完整 state 到本机。
- JRS 读也失败：不得凭空启动 live；可暂停或只启 zero-notional shadow，并明确 lineage gap。

需要重建 canonical permission host 时，只使用 controller 的有界事务：

```bash
.venv/bin/python scripts/ops/weather_production_ctl.py recover-jrs-context \
  --apply --confirm-live --reason "$REASON" --restore-manifest "$PRECHANGE_MANIFEST"
```

该命令必须先用临时 prospective server 验证新调用上下文能访问 JRS，再杀旧 server；restore
manifest 必须位于 Mac 内置盘。prospective probe 失败、Mac 锁屏/TCC 阻断或 manifest 不完整时停止，
不得改用默认 tmux、screen、nohup 或 LaunchAgent 抢建 canonical server。
restore manifest 必须包含 controller desired-state 中的 dashboard API；恢复后 API 由 controller 重建，
不得用旧 `com.pm-agents.weather-api` LaunchAgent 补洞。

## 3. JRS → 本机临时接管

生产启停必须已有用户授权。

1. 记录 pre-state，pause/stop 目标 writer；不停止邻近实例。
2. 再确认目标 PID 已退出，runtime 文件不再变化。
3. 把 JRS runtime 复制到一个全新的本机 staging 目录；禁止覆盖或 merge 进旧目录。
4. 按“校验分层”选择 verifier profile；无可信 baseline 的首次接管使用 exact：

```bash
.venv/bin/python skills/weather-jrs-runtime-failover/scripts/verify_runtime_tree.py \
  --mode exact "$JRS_SOURCE" "$LOCAL_STAGING"
```

5. 只有 `equal=true` 才把 staging 原子 rename 为本机 active root。
6. 修改权威 launcher/output-dir；若启动脚本或配置变化，focused tests 后提交，再加载该 SHA。
7. 用原 process manager 启动，保持所有交易参数不变。
8. 验证：
   - process：新 PID、command line、repo SHA、本机 output-dir；
   - raw：latest/state 新鲜、循环 returncode 正常、无 live error；
   - exchange：open orders、最近 order/fill、caps 与切换前衔接；
   - API/page：策略页面可读且路径/状态正确。

将 local 接管起止、不可用窗口和 runtime root 写入现有 incident/governance 记录，不新建零散文档。

## 4. 本机 → JRS 迁回

1. 先让 canonical tmux write probe 连续成功，并确认 JRS 空间和目标父目录。
2. 记录 pre-state，pause/stop 本机 writer，确认文件稳定。
3. 复制本机 active root 到 JRS 新 staging 目录。
4. 按“校验分层”运行 verifier。已有可信 exact baseline 时默认 tiered；失败则
   升级 exact，仍失败即停，不切路径。
5. 若 JRS 旧目标存在，把它原子 rename 为带时间戳的 rollback；禁止把新旧树 merge。
6. 把 JRS staging 原子 rename 为 canonical target。
7. 把本机 active 目录原子 rename 为临时 rollback，并在原路径创建指向 JRS canonical target 的 symlink。
8. 通过 `weather-data-feed-jrs` tmux context 启动已提交版本。
9. 完成 process/raw/exchange/API 四层验证；确认 latest 产生新 epoch，而不只是读到迁移前旧文件。

## 5. 影响半径与清理

计算并报告：

- 停机秒数和首个恢复 epoch；
- 停机窗口新增 snapshot 数、漏 signal 数；
- order/fill/open-order 的前后差异；
- 是否存在重复下单、漏单、maker lifecycle 中断；
- 迁移前后全树文件数、字节数、校验 profile、关键/changed/sample hash 数和结果。

验证通过且已获删除授权后：

1. 严格确认本机 active path 是指向预期 JRS target 的 symlink。
2. 确认 JRS latest 新鲜、runner 正常、exchange state 对齐。
3. 删除本机 rollback 和 JRS 旧 rollback；只留下 JRS 物理正本及本机兼容 symlink。
4. 再查同实例路径，确保没有 `.staging`、`.incoming`、`.local-backup` 或第二个 writer。

删除必须使用完整绝对路径和 basename/realpath guard；不得用宽泛 glob。

## 交付

完成全流程再回复：

```text
root cause:
failover window:
source -> target:
tree verification:
repo SHA / PID / process context:
raw / exchange / API evidence:
orders, fills, missed signals:
remaining physical copies:
cleanup:
not done / blocker:
```

不能把“复制完成”“PID 存在”或“symlink 已创建”单独称为恢复完成。

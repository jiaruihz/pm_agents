---
name: weather-jrs-runtime-failover
description: 在 weather 生产 runtime 因 JRS 外置卷写入失败时安全切到 Mac 本机临时运行，并在 JRS 恢复后把完整 runtime 迁回、分层校验、重启和清理本机副本。用于 JRS Operation not permitted、write probe 失败、临时 local output-dir、JRS repatriation、runtime 单一正本恢复；覆盖 signal/plan/order/fill/dedupe 状态、canonical tmux 权限上下文、关键状态 SHA、bulk 增量校验和影响半径。不要用于普通数据同步或 N100 恢复。
---

# Weather JRS runtime failover

先 invoke `weather-strategy-deploy`。本 skill 负责存储故障切换；live 启停、资金安全、git-first 和生产验证仍服从 deploy skill。

## 不变量

- 同一实例任一时刻只有一个可写 runtime 正本；不双写。
- JRS 常驻进程只用 `tmux -L weather-data-feed-jrs`，并通过
  `scripts/ops/weather_jrs_tmux_env.sh` 在该 tmux server 内执行 write probe。
- 普通 shell 写不进 JRS，不等于 canonical tmux context 写不进。只有后者失败才进入本机接管。
- live 恢复前必须迁移完整 runtime state，包括 dedupe、plan、order、fill、maker lifecycle 和 pause/state；JRS 不可读或所需校验 profile 失败时不得恢复 live。
- 只改 storage location 时保持 repo SHA、参数、caps、source/signal/execution policy 完全不变。
- 删除临时副本需要用户明确授权。用户已要求“清理、不留维护成本”时，完成验证后直接清理，不再二次询问。

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

加载共享 helper，并从 canonical tmux 权限上下文探测目标 JRS root：

```bash
source scripts/ops/weather_jrs_tmux_env.sh
weather_jrs_tmux_write_probe weather-data-feed-jrs "$JRS_RUNTIME_ROOT"
```

- probe 成功：不迁移；修正 runner 的 process context。
- probe 失败：记录原始错误，进入临时接管。
- JRS 能读不能写：允许复制完整 state 到本机。
- JRS 读也失败：不得凭空启动 live；可暂停或只启 zero-notional shadow，并明确 lineage gap。

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

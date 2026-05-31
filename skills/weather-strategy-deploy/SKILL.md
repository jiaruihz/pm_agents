---
name: weather-strategy-deploy
description: >
  部署 weather 策略变更到 N100 生产环境。适用场景：新增执行策略（如 maker_queue_v2）、
  修改现有策略参数、切换当前运行的策略分支、更新城市池（T1/T2）。
  触发词：部署策略、上线策略、部署 policy、新策略、切换策略、修改参数部署、
  城市池、T1、T2、加城市、移除城市、deploy、上 V2、上 V3、启动新分支、停旧策略。
  禁止：未本地 commit 就部署；跳过 N100 diff 检查直接覆盖；跳过 smoke test 直接切换；
  在未确认用户许可的情况下 kill 生产进程。默认 git-first：本地 commit/push，N100 pull/checkout。
---

# weather-strategy-deploy

将 weather 策略变更安全部署到 N100。先判断部署类型：

| 类型 | 本机源目录 | N100 目标目录 | 典型文件 |
|---|---|---|---|
| 执行策略 / live cycle | `/home/rui/projects/pm_agent` | `/home/jiarui/projects/pm_agent` | `execution_policy.py`, `weather_live_cycle.py` |
| 城市池 / paper 生产采集 | `/home/rui/projects/weather-predict` | `/home/jiarui/projects/weather-predict` | `city_pools.py` |

不要把两个项目混用。城市池的 source of truth 是 `weather-predict/city_pools.py`，不是 `pm_agent`。

## 硬规则：以后策略/配置上线一律走本 skill

凡是会改变 N100 生产行为的 weather 策略或配置变更，必须按本 skill 执行，不允许临时手搓：

1. **先本机修改和验证**：本机 `py_compile` / smoke / dry-run 通过。
2. **先提交再部署**：本机必须 `git commit`，提交信息写清策略动作和理由。
3. **默认 git-first 部署**：本机提交后，N100 用 `git fetch` + `git checkout <commit>` 或 `git pull --ff-only` 更新；如果 N100 无 GitHub 凭据，使用本机 git bundle 同步到 N100 bare repo，再由 N100 worktree 从该 bare repo fast-forward。
4. **记录线上版本**：最终汇报必须包含本地 commit SHA、N100 当前 commit SHA、验证命令和结果。
5. **rsync 只能是 fallback**：只有当 N100 目标目录还不是 git worktree、或用户明确允许临时修复时，才允许备份 + rsync；报告里必须标注这是 fallback，并给出后续 git 化 TODO。`pm_agent` 已完成 git 化，默认不再 rsync 代码文件。
6. **资金/进程安全边界不变**：真实下单、kill/restart 生产进程、切换 daemon 前仍需明确确认；paper snapshot 进程只在用户授权或确认卡死时重启。
7. **不要在 Windows/PowerShell 的 SSH 字符串里写 `$!`、`$VAR`、`$(...)`**：这些会被 Windows 侧提前展开。启动 daemon 必须用仓库里的 start script，或用不含 shell 变量的固定命令。

推荐提交信息格式：

```text
strategy: <action summary>

Reason:
- <data-backed rationale>

Validation:
- <local command/result>
- <N100 command/result>
```

---

## A. 城市池部署 Checklist

适用：新增/移除 T1 城市、调整 T2 research pool、更新 `city_pools.py`。

### A0：部署前必须确认的文档

先读：

- `/home/rui/projects/pm_agent/docs/WEATHER_CITY_POOL_DECISIONS.md`
- `/home/rui/projects/pm_agent/docs/WEATHER_STRATEGY_ENTRYPOINT.md`

确认本次城市池变更已经写入：

- 当前 T1 完整列表
- 加入/移除城市
- 决策依据
- 对应分析报告路径

### A1：本机校验

```bash
wsl -d Ubuntu-24.04 -- bash -lc "cd /home/rui/projects/weather-predict && \
  python3 -m py_compile city_pools.py scripts/analysis/paper_policy.py scripts/ops/fill_t2_weather_cache.py"
```

确认关键城市归属：

```bash
wsl -d Ubuntu-24.04 -- bash -lc "cd /home/rui/projects/weather-predict && python3 - <<'PY'
from city_pools import TRADING_T1_CITIES, RESEARCH_T2_CITIES

check = [
    'Beijing', 'Chicago', 'Madrid',
    'BuenosAires', 'Amsterdam', 'Manila', 'Munich', 'Singapore', 'Chengdu',
]
print('T1_COUNT', len(TRADING_T1_CITIES))
for city in check:
    if city in TRADING_T1_CITIES:
        pool = 'T1'
    elif city in RESEARCH_T2_CITIES:
        pool = 'T2'
    else:
        pool = 'MISSING'
    print(city, pool)
PY"
```

### A2：Git 提交（必须）

城市池变更必须先在本机 `weather-predict` 提交。不要把 pm_agent 文档改动混进 weather-predict 代码提交；如果同时更新了 pm_agent 文档，另做一个 pm_agent 文档提交。

```bash
wsl -d Ubuntu-24.04 -- bash -lc "cd /home/rui/projects/weather-predict && \
  git status --short && \
  git diff -- city_pools.py scripts/analysis/paper_policy.py && \
  git add city_pools.py scripts/analysis/paper_policy.py docs/reports/<REPORT>.md && \
  git commit -m 'strategy: update weather city side policy'"
```

记录本地 commit SHA：

```bash
wsl -d Ubuntu-24.04 -- bash -lc "cd /home/rui/projects/weather-predict && git rev-parse HEAD"
```

如有 pm_agent 文档同步：

```bash
wsl -d Ubuntu-24.04 -- bash -lc "cd /home/rui/projects/pm_agent && \
  git add docs/WEATHER_CITY_POOL_DECISIONS.md docs/WEATHER_STRATEGY_ENTRYPOINT.md && \
  git commit -m 'docs: record weather city side policy change'"
```

### A3：Git-first 部署到 N100

默认部署方式是 push 后在 N100 pull/checkout。先确认本机和 N100 是 git worktree：

```bash
wsl -d Ubuntu-24.04 -- bash -lc "cd /home/rui/projects/weather-predict && git remote -v && git status --short"

wsl -d Ubuntu-24.04 -- ssh jiarui@192.168.0.200 \
  'cd /home/jiarui/projects/weather-predict && git rev-parse --is-inside-work-tree && git status --short'
```

如果 N100 是 git worktree：

```bash
# 本机 push
wsl -d Ubuntu-24.04 -- bash -lc "cd /home/rui/projects/weather-predict && git push"

# N100 fast-forward 到同一 commit（推荐固定 SHA，避免拉错分支）
COMMIT=<LOCAL_COMMIT_SHA>
wsl -d Ubuntu-24.04 -- ssh jiarui@192.168.0.200 \
  "cd /home/jiarui/projects/weather-predict && \
   git fetch --all --prune && \
   git checkout $COMMIT && \
   git rev-parse HEAD"
```

必须确认：

- N100 `git status --short` 没有会被覆盖的未提交生产改动。
- N100 `git rev-parse HEAD` 等于本地 commit SHA。
- 如远端只能 `pull --ff-only`，必须确认当前分支和本地 push 分支一致。

### A3 fallback：N100 尚未 git 化时才允许备份 + rsync

不要在 PowerShell 字符串里写 `$(date ...)`，会被 Windows 侧解析。用固定备份名或分两步执行。

只有满足以下任一条件才走 fallback：

- N100 `/home/jiarui/projects/weather-predict` 不是 git worktree。
- git remote 暂不可用，但用户明确要求本次先上线。
- 紧急修复，且已明确记录后续补 commit / git 化 TODO。

```bash
wsl -d Ubuntu-24.04 -- ssh jiarui@192.168.0.200 \
  cp /home/jiarui/projects/weather-predict/city_pools.py \
     /home/jiarui/projects/weather-predict/city_pools.py.bak.codex_YYYYMMDD

wsl -d Ubuntu-24.04 -- rsync -av \
  /home/rui/projects/weather-predict/city_pools.py \
  jiarui@192.168.0.200:/home/jiarui/projects/weather-predict/city_pools.py
```

fallback 汇报里必须写：

- fallback 原因。
- N100 备份路径。
- 本地 commit SHA（即使远端不是 git，也要先 commit）。
- N100 后续 git 化 TODO。

### A4：N100 校验

```bash
wsl -d Ubuntu-24.04 -- ssh jiarui@192.168.0.200 \
  'cd /home/jiarui/projects/weather-predict && \
   python3 -m py_compile city_pools.py scripts/analysis/paper_policy.py scripts/ops/fill_t2_weather_cache.py && \
   sed -n "1,45p" city_pools.py'
```

必须人工确认：

- T1 区块包含应加入城市
- T1 区块不包含应移除城市
- `FULL_CITY_CONFIGS` 仍保留被降级城市

### A5：N100 doctor

```bash
wsl -d Ubuntu-24.04 -- ssh jiarui@192.168.0.200 \
  'cd /home/jiarui/projects/weather-predict && scripts/ops/doctor_restart.sh'
```

通过标准：

- `snapshot freshness ok`
- `paper_snapshot.err.log` empty or missing
- `daily_pipeline.err.log` empty or missing
- `paper_orders.jsonl` 行数正常增长或至少可读取

### A6：确认是否已经被运行中的 snapshot 进程加载

文件部署成功不等于当前正在跑的进程已经加载了新文件。`paper_snapshot.py`
如果在 rsync 前已经启动，它会继续使用启动时 import 的旧 `city_pools.py`。

检查当前 service：

```bash
wsl -d Ubuntu-24.04 -- ssh jiarui@192.168.0.200 \
  'systemctl --user status weather-predict-snapshot.service --no-pager | sed -n "1,35p"; \
   echo === latest ===; \
   ls -lt /home/jiarui/projects/weather-predict/output/paper_snapshots | head -5'
```

判断：

- 如果 `weather-predict-snapshot.service` 的 `Active since` 晚于 rsync 时间，说明当前进程已加载新文件。
- 如果 `Active since` 早于 rsync 时间，说明文件已部署，但当前这轮 snapshot 未必加载新城市池；下一轮新进程会加载。
- 不要为了“立刻生效”直接 kill/restart 生产采集进程，除非用户明确要求或已经确认当前进程卡死。

### A7：汇报格式

```text
城市池部署完成：
- 本地 commit：<sha>
- N100 commit：<sha>（git-first）或 fallback rsync + 备份路径
- N100 文件：/home/jiarui/projects/weather-predict/city_pools.py
- 本机 py_compile：通过
- N100 py_compile：通过
- T1 校验：新增城市在 T1，移除城市不在 T1
- doctor：snapshot freshness ok，错误日志为空
- 生效状态：当前 snapshot 进程是否晚于部署时间启动；若不是，说明下一轮进程才加载
- 回滚方式：git checkout <previous_sha> 或恢复备份文件
```

---

## B. 执行策略部署 Checklist

适用：新增/切换 execution policy、live cycle、branch daemon。

---

## 角色分工

| 组件 | 职责 |
|---|---|
| `execution_policy.py` | 策略定价逻辑（bid+1tick、边界条件、defer 路径） |
| `weather_trade_planner.py` | 接受 `--execution-policy` 参数，choices 白名单 |
| `weather_live_cycle.py` | 主 cycle，choices 白名单 |
| `weather_policy_branch.py` | 分支 cycle（branch 策略复用主 cycle 信号），choices 白名单 |
| `weather_order_executor.py` | 识别策略名，构建 CLOB quote |
| `weather_execution_policy_compare.py` | 比较工具，default policies 列表 |
| N100 branch daemon | `weather_policy_branch_loop.sh` 守护进程，env var 指定策略 |

**一旦新增策略名，以上所有 choices 都必须更新。**

---

## 执行 Checklist（必须按顺序，不得跳步）

---

### 第 0 步：读架构文档

读 `docs/WEATHER_EXECUTION_ARCHITECTURE.md`，确认：
- 当前运行的策略是什么（main cycle + branch cycles）
- 新策略在 pricing 层面的设计意图

---

### 第 1 步：N100 Diff 检查（防止覆盖未同步的本地修改）

```bash
# 检查 N100 关键文件 vs 本机是否有差异
for f in \
  src/strategies/weather_edge_v1/tools/execution_policy.py \
  scripts/ops/weather_trade_planner.py \
  scripts/ops/weather_live_cycle.py \
  scripts/ops/weather_policy_branch.py \
  scripts/ops/weather_order_executor.py; do
  echo "=== $f ==="
  diff <(ssh jiarui@192.168.0.200 "cat /home/jiarui/projects/pm_agent/$f") \
       "/home/rui/projects/pm_agent/$f" || true
done
```

**⚠️ 确认点 A（必须等用户确认）**

展示 diff 结果，告知用户：
- N100 有哪些本机没有的改动（需要先合并）
- 本机有哪些 N100 没有的改动（将被覆盖到 N100）

**未经用户确认，不得继续执行后续步骤。**

---

### 第 2 步：本地代码修改

依次确认以下文件都已更新（grep 检查）：

```bash
# 1. execution_policy.py — 新策略 pricing 逻辑
grep "maker_queue_v2\|def build_execution_quote" \
  src/strategies/weather_edge_v1/tools/execution_policy.py

# 2. 所有 choices 白名单
grep -n "choices.*maker_queue" \
  scripts/ops/weather_trade_planner.py \
  scripts/ops/weather_live_cycle.py \
  scripts/ops/weather_policy_branch.py

# 3. executor 识别新策略名
grep "maker_queue_v2" scripts/ops/weather_order_executor.py
```

如有缺漏，立即修改并 commit。

---

### 第 3 步：本地 Smoke Test

```bash
# 用当天最新信号对比 V1 vs V2 定价输出
wsl -d Ubuntu-24.04 -- bash -lc "cd /home/rui/projects/pm_agent && \
  .venv/bin/python scripts/ops/weather_execution_policy_compare.py \
  --policies mid_price_core_v1,maker_queue_v1,maker_queue_v2 2>&1 | head -60"
```

预期：
- `maker_queue_v2` 有 `accepted > 0`
- `quote_mode` 包含 `improve_bid`（正常 spread）或 `defer_to_executor`（无盘口时）
- 无 `unknown_execution_policy` 报错

如果 smoke test 失败，**停在这里**，修复后重跑。

---

### 第 4 步：Git Commit（必须）

```bash
cd /home/rui/projects/pm_agent
git add src/strategies/weather_edge_v1/tools/execution_policy.py \
        scripts/ops/weather_trade_planner.py \
        scripts/ops/weather_live_cycle.py \
        scripts/ops/weather_policy_branch.py \
        scripts/ops/weather_order_executor.py \
        scripts/ops/weather_execution_policy_compare.py
git commit -m "feat: add <policy_name> execution policy"
git rev-parse HEAD
```

如 N100 运行的是 `/home/jiarui/projects/pm_agent`，该目录也必须用同一个 commit SHA 部署。不要在未 commit 的工作区直接 rsync。

### 第 5 步：Git-first 部署到 N100

`pm_agent` 当前固定路径：

| 角色 | 路径 |
|---|---|
| 本机源码 | `/home/rui/projects/pm_agent` |
| N100 worktree | `/home/jiarui/projects/pm_agent` |
| N100 bare origin | `/home/jiarui/projects/pm_agent_repo.git` |
| N100 旧目录备份 | `/home/jiarui/projects/pm_agent_pre_git_20260531T2200_gitcutover` |

优先 push，再在 N100 checkout/pull 到同一 SHA：

```bash
wsl -d Ubuntu-24.04 -- bash -lc "cd /home/rui/projects/pm_agent && git push"

COMMIT=<LOCAL_COMMIT_SHA>
wsl -d Ubuntu-24.04 -- ssh jiarui@192.168.0.200 \
  "cd /home/jiarui/projects/pm_agent && \
   git status --short && \
   git fetch --all --prune && \
   git checkout $COMMIT && \
   git rev-parse HEAD"
```

如果 N100 不能访问 GitHub（例如缺少 HTTPS 凭据），不要退回 rsync。走 bundle bridge：

```bash
# 本机：把当前 HEAD 打成 bundle
wsl -d Ubuntu-24.04 -- bash -lc 'cd /home/rui/projects/pm_agent && \
  git rev-parse HEAD && \
  git bundle create /tmp/pm_agent_HEAD.bundle HEAD develop'

# 传到 N100
wsl -d Ubuntu-24.04 -- scp /tmp/pm_agent_HEAD.bundle \
  jiarui@192.168.0.200:/tmp/pm_agent_HEAD.bundle

# N100：更新 bare origin，再让 worktree fast-forward
wsl -d Ubuntu-24.04 -- ssh jiarui@192.168.0.200 \
  'git --git-dir=/home/jiarui/projects/pm_agent_repo.git fetch /tmp/pm_agent_HEAD.bundle HEAD:refs/heads/develop && \
   cd /home/jiarui/projects/pm_agent && \
   git status --short --untracked-files=no && \
   git fetch origin && \
   git checkout develop && \
   git pull --ff-only && \
   git rev-parse HEAD && \
   git status --short --branch'
```

**⚠️ 确认点 A2（必须等用户确认）**

展示：
- 本地 commit SHA
- N100 checkout 后 SHA
- N100 `git status --short`

如果 N100 `/home/jiarui/projects/pm_agent` 不是 git worktree，停止并说明需要先 git 化；只有用户明确要求临时修复时，才允许备份 + rsync fallback，并在最终报告标注。

---

### 第 6 步：N100 Smoke Test（手动触发一次完整 cycle）

```bash
wsl -d Ubuntu-24.04 -- ssh jiarui@192.168.0.200 \
  'cd /home/jiarui/projects/pm_agent && \
   .venv/bin/python scripts/ops/weather_policy_branch.py \
   --execution-policy <NEW_POLICY> \
   --source-policy mid_price_core_v1 \
   > /tmp/deploy_smoke.json 2>&1; echo "rc=$?"'
```

解析结果：

```bash
wsl -d Ubuntu-24.04 -- ssh jiarui@192.168.0.200 \
  'tail -n +2 /tmp/deploy_smoke.json | python3 -c "
import sys, json
d = json.load(sys.stdin)
p = d.get(\"planner\", {})
e = d.get(\"executor\", {})
print(\"policy:\", d.get(\"config\", {}).get(\"execution_policy\"))
print(\"signals:\", d.get(\"signals\", {}).get(\"signals\", 0))
print(\"accepted:\", p.get(\"accepted\", 0))
print(\"paper_written:\", e.get(\"paper_written\", 0))
alerts = d.get(\"contract_alerts\", [])
[print(\"ALERT:\", a) for a in alerts] if alerts else print(\"no alerts\")
"'
```

**⚠️ 确认点 B（必须等用户确认）**

展示结果：
- `accepted > 0`（planner 接受了信号）
- `paper_written > 0`（paper 单写入正常）
- `no alerts`（无合约违规）

**未经用户确认，不得进行切换操作。**

---

### 第 7 步：切换——停旧分支，启新分支

查看当前运行的 branch 进程：

```bash
wsl -d Ubuntu-24.04 -- ssh jiarui@192.168.0.200 \
  'ps aux | grep weather_policy_branch | grep -v grep'
```

停旧分支（显示 pid 和策略名，**等用户确认**后再 kill）：

```bash
# 停旧
wsl -d Ubuntu-24.04 -- ssh jiarui@192.168.0.200 'kill <OLD_PID>'

# 启新：不要手写 nohup + $!，使用固定 start script
wsl -d Ubuntu-24.04 -- ssh jiarui@192.168.0.200 \
  'cd /home/jiarui/projects/pm_agent && \
   WEATHER_BRANCH_EXECUTION_POLICY=<NEW_POLICY> \
   WEATHER_BRANCH_SOURCE_POLICY=mid_price_core_v1 \
   INITIAL_DELAY_SEC=0 \
   scripts/ops/start_weather_policy_branch_loop.sh'
```

**⚠️ 确认点 C（必须等用户确认）**

展示：
- 旧 pid 已不在 ps 列表
- 新 pid 存在且 daemon out 显示 `policy=<NEW_POLICY>`
- 第一次 cycle 运行状态（ok 或 failed + 原因）

---

### 第 8 步：最终状态汇总

输出部署报告：

```
## 部署完成报告
- 新策略：<NEW_POLICY>
- 停止策略：<OLD_POLICY> (pid=<OLD_PID>)
- 新 daemon pid：<NEW_PID>
- N100 smoke test：accepted=N, paper_written=N
- 下次自动 cycle：~30 分钟后
- 验证命令：
  ssh jiarui@192.168.0.200 'tail -5 ~/projects/pm_agent/runtime/weather_edge_v1/live_cycle/policy_branch_<NEW_POLICY>.out'
```

---

## 快速参考：关键文件

```
src/strategies/weather_edge_v1/tools/execution_policy.py  ← 策略 pricing 核心
scripts/ops/weather_trade_planner.py                       ← choices 白名单
scripts/ops/weather_live_cycle.py                          ← choices 白名单
scripts/ops/weather_policy_branch.py                       ← choices 白名单 + branch runner
scripts/ops/weather_order_executor.py                      ← 策略名识别
scripts/ops/weather_execution_policy_compare.py            ← 对比工具
```

## 快速参考：N100 进程状态

```bash
# 查所有 weather 进程
wsl -d Ubuntu-24.04 -- ssh jiarui@192.168.0.200 'pgrep -af weather_live_cycle_loop; pgrep -af weather_policy_branch_loop; true'

# 查 branch daemon 日志
wsl -d Ubuntu-24.04 -- ssh jiarui@192.168.0.200 \
  'tail -20 ~/projects/pm_agent/runtime/weather_edge_v1/live_cycle/policy_branch_<POLICY>.out'

# 主 cycle 最新状态
wsl -d Ubuntu-24.04 -- ssh jiarui@192.168.0.200 \
  'tail -3 ~/projects/pm_agent/runtime/weather_edge_v1/live_cycle/loop.log'
```

## N100 git 化 TODO（一次性治理）

如果发现 N100 目标目录不是 git worktree，不要继续把 rsync 当长期方案。下一次非紧急窗口应完成：

1. 备份当前 N100 目录。
2. 在 N100 上 clone 对应 repo 到新目录或原地初始化为 clean worktree。
3. 对比旧目录和目标 commit 的差异，确认生产独有文件只在 `output/`、`runtime/`、`cache/`、`.env` 等非代码路径。
4. 切换 systemd / scripts 指向 git worktree。
5. 跑本 skill 的完整部署 smoke。

完成后，所有策略/配置部署必须使用 `commit -> push -> N100 fetch/checkout`，不再直接 rsync 代码文件。

---
name: weather-strategy-deploy
description: >
  部署 weather 策略变更到 N100 生产环境。适用场景：新增执行策略（如 maker_queue_v2）、
  修改现有策略参数、切换当前运行的策略分支。
  触发词：部署策略、上线策略、部署 policy、新策略、切换策略、修改参数部署、
  deploy、上 V2、上 V3、启动新分支、停旧策略。
  禁止：跳过 N100 diff 检查直接 rsync；跳过 smoke test 直接切换；
  在未确认用户许可的情况下 kill 生产进程。
---

# weather-strategy-deploy

将执行策略变更安全部署到 N100，含三个强制确认点。

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

### 第 4 步：Git Commit

```bash
cd /home/rui/projects/pm_agent
git add src/strategies/weather_edge_v1/tools/execution_policy.py \
        scripts/ops/weather_trade_planner.py \
        scripts/ops/weather_live_cycle.py \
        scripts/ops/weather_policy_branch.py \
        scripts/ops/weather_order_executor.py \
        scripts/ops/weather_execution_policy_compare.py
git commit -m "feat: add <policy_name> execution policy"
```

---

### 第 5 步：Rsync 到 N100

```bash
wsl -d Ubuntu-24.04 -- bash -lc "rsync -av \
  /home/rui/projects/pm_agent/src/strategies/weather_edge_v1/tools/execution_policy.py \
  /home/rui/projects/pm_agent/scripts/ops/weather_trade_planner.py \
  /home/rui/projects/pm_agent/scripts/ops/weather_live_cycle.py \
  /home/rui/projects/pm_agent/scripts/ops/weather_policy_branch.py \
  /home/rui/projects/pm_agent/scripts/ops/weather_order_executor.py \
  /home/rui/projects/pm_agent/scripts/ops/weather_execution_policy_compare.py \
  jiarui@192.168.0.200:/home/jiarui/projects/pm_agent/scripts/ops/"

wsl -d Ubuntu-24.04 -- bash -lc "rsync -av \
  /home/rui/projects/pm_agent/src/strategies/weather_edge_v1/tools/execution_policy.py \
  jiarui@192.168.0.200:/home/jiarui/projects/pm_agent/src/strategies/weather_edge_v1/tools/"
```

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

# 启新
wsl -d Ubuntu-24.04 -- ssh jiarui@192.168.0.200 \
  'cd /home/jiarui/projects/pm_agent && \
   WEATHER_BRANCH_EXECUTION_POLICY=<NEW_POLICY> \
   WEATHER_BRANCH_SOURCE_POLICY=mid_price_core_v1 \
   INITIAL_DELAY_SEC=0 \
   nohup scripts/ops/weather_policy_branch_loop.sh \
   > runtime/weather_edge_v1/live_cycle/policy_branch_<NEW_POLICY>.out 2>&1 &
   echo "started pid=$!"'
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
ssh jiarui@192.168.0.200 'ps aux | grep weather | grep -v grep'

# 查 branch daemon 日志
ssh jiarui@192.168.0.200 \
  'tail -20 ~/projects/pm_agent/runtime/weather_edge_v1/live_cycle/policy_branch_<POLICY>.out'

# 主 cycle 最新状态
ssh jiarui@192.168.0.200 \
  'tail -3 ~/projects/pm_agent/runtime/weather_edge_v1/live_cycle/loop.log'
```

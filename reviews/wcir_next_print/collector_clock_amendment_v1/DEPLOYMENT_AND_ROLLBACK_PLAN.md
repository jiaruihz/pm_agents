# Deployment and Rollback Plan

当前 disposition：仅申请 `ISOLATED_ZERO_NOTIONAL_SHADOW_DEPLOYMENT`，不是 production deployment。

前置条件：独立输出目录、独立 config/epoch/connection identity、只读复制 raw stream、orders/fills/notional hard zero、capacity/backpressure/reconnect tests 通过、production manifest strict 前后均 healthy。不得修改 production.yaml、startup、shared token set 或现有 process。

部署顺序：生成 demand manifest → 启动 isolated process → 验证 request/ack/baseline/heartbeat journal → synthetic/replayed reconnect → 对比既有 collector 无 PID/config/output hash 变化。任何 drop、clock regression、disk pressure、CPU/memory 超预算或 production health drift 立即停止 shadow。

Rollback：停止独立进程，保留 append-only evidence；不需要改 production state。若曾意外触及 shared config/PID，视为 blocker，停止并按生产 runbook 单独审阅，禁止自动恢复或继续实验。

# Subscription Epoch and Liveness Schema

每个 token 保存：`requested_at_wall_ns`、`acknowledged_at_wall_ns`、`initial_baseline_received_at_wall_ns`、`active_epoch_id`、`connection_id`、`last_frame_received_at_wall_ns`、`last_frame_received_at_monotonic_ns`、`heartbeat_liveness`（HEALTHY/UNPROVEN/DEAD）、`gap_blocker`、`unsubscribe_end_reason`、`valid`。

requested、acknowledged、baseline 必须按序；ack 不能代表 active book，heartbeat 不能代表完整 book。baseline 必须属于当前 connection+epoch。状态转换 append-only 记录；旧 epoch 不回写。

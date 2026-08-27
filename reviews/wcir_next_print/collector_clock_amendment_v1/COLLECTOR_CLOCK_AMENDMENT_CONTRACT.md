# Collector-Clock Amendment V1

这是隔离、zero-notional、network-free 的 shadow 实现，不接共享 production consumer。config identity 为 `wcir_collector_clock_shadow_v1`；每次启动必须生成新的 `connection_id` 与 `active_epoch_id`。

在 source opportunity 前，按 city+target_date 冻结 expected condition IDs、YES/NO token IDs、neighbor brackets、market identity hash、demand created/effective interval、reason/source。token 状态逐项记录 requested、acknowledged、initial baseline、epoch、connection、last wall/monotonic receive、heartbeat/liveness、unsubscribe/end reason。

reconnect 使受影响 book 全部 invalid；跨 connection/epoch 禁止复用。只有新 request/ack 与 verified WS baseline 可以清除 reconnect blocker。open gap 不得被 REST、heartbeat 或普通 delta 静默清除。REST parity 只能产生 evidence hash，不能 mutate WS state。

本实现以 append-only JSONL journal 保存状态 transition，并用 bounded frame buffer 对 overflow/drop fail closed；每秒 subscription rate 与 active-token capacity 均由状态机执行，unsubscribe 后才释放 active capacity。它不导入 network、exchange、order、fill、canonical DB 或 production config；orders/fills/notional 恒为 0/0/0。

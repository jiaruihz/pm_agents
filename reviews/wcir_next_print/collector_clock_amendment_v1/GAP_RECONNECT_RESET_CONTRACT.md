# Gap / Reconnect / Reset Contract

- reconnect 必须同时更换 connection_id 和 epoch_id，并立即 invalid 所有受影响 token；
- reconnect 后 request → acknowledge → verified baseline 完整重走，之前不得接受 delta；
- gap blocker 只能由明确 reset+baseline transition 清除；
- exchange clock 缺失、receive clock 回退、跨 domain 不可比较均 fail closed；
- REST parity 不得清 gap、写 baseline 或 mutate WS state；
- restart/recovery 从 append-only journal 恢复 demand，但 book validity 永远从新 connection baseline 重新建立。

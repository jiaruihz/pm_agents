# Independent Read-Only Code Review

Reviewer task：`/root/wcir_stage23_closure_readonly_review`，模型 `luna_verifier`，单轮只读；未修改文件、未派生 agent。系统未提供可验证 token/usage telemetry，记为 `unavailable`。

审阅者运行 py_compile 与 closure pytest，初始 9 tests 通过，但提出 10 类实质 findings：reconciliation 未独立核对 immutable aggregates、input join 静默覆盖、freshness/liveness fail-closed 不足、unknown status 过宽、denominator 把 intersection 当 full denominator、collector 缺 journal/backpressure/rate/unsubscribe、liveness 不 invalid、exchange clock regression 未检查、package self-hash/duplicate entry、对应测试缺口。

修复清单：

- oracle/baseline 841 event set、reaction event/window 全部做 duplicate/missing/extra fail-closed；reaction 由旧 feasible + causal lead 独立得出；city gate、concentration、bootstrap target-date counts 与 immutable aggregates 逐城核对；
- status 限定 allowlist；freshness 要求 identity+clock+gap+active epoch+epoch baseline+`liveness=true`+age，故当前 freshness=0；
- full denominator=841，exact intersection=1 独立报告，原因拆分；
- collector 增加 append-only journal、bounded buffer/drop fail、subscription rate、active capacity、unsubscribe、liveness invalidation、exchange regression；
- package 排除自指 sidecar并拒绝 duplicate ZIP entry；补齐相应 regression tests。

修复后验证结果记录在 `TEST_COMMANDS_AND_RAW_OUTPUT.txt`；无已知未处理 code-review blocker。

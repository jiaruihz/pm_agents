# Code Diff and Implementation Map

- `wcir_collector_clock_shadow.py`：expected demand identity、subscription lifecycle、clock regression、gap/reconnect reset、REST non-mutation、zero-notional snapshot、synthetic capacity probe。
- `test_wcir_stage23_rev2_closure.py`：reconnect invalidation、baseline reset、REST non-mutation、gap persistence、clock regression、missing exchange clock、capacity、zero-notional。
- production startup/config/shared token set：未修改。

若未来需要共享 collector 改造，只能提出新 public event schema 和 adapter diff，另行 review、rollback 与显式 deployment 授权；本轮不做该修改。

# GPT Pro Review Packet — Collector Clock Amendment V1

请求仅审阅隔离 zero-notional shadow，不请求 production deployment。

实现已覆盖 expected demand identity、request/ack/baseline、connection+epoch、wall/monotonic/exchange clock、liveness、gap、reconnect invalidation、new-baseline reset、REST non-mutation、capacity与 backpressure fail-closed。synthetic probe 为 120 active tokens、30,000 delta frames；orders/fills/notional=0/0/0。production startup、production.yaml、shared token set、live process 均未修改。

请从以下 disposition 选择：

- `ACCEPT_COLLECTOR_CLOCK_AMENDMENT_FOR_ISOLATED_ZERO_NOTIONAL_SHADOW_DEPLOYMENT`
- `ACCEPT_WITH_BLOCKING_FIXES`
- `REWORK_VALIDITY_OR_ORACLE_BOUNDARY`
- `STOP_DUE_TO_UNCONTROLLED_LIVE_OR_DATA_RISK`

任何 production/shared collector 接入仍需独立授权、capacity budget、rollback drill 与部署审阅。

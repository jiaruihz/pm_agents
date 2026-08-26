# Known limitations

- 当前交付仅通过 Discovery 与 Integration Design Gate；P0代码尚未实现。
- Gamma实时字段、分页、rate limit和真实增量吞吐只在后续read-only operational pilot gate验证，不是offline P0完成条件。
- 默认VWAP target-size是版本化研究配置，尚未选最终值。
- wallet facts相对2026-08-26陈旧48–79天，只能作historical provenance。
- canonical dispute DB必须按JRS绝对路径/device/inode核实，不能依赖相对路径。
- read-only operational pilot尚未通过；arbitrary token demand/receipt、weather隔离、API/load/storage/staleness和真实rollback尚未实测。
- 扩大production book capture coverage、任何current DB migration、service change或live execution均未获本seal授权。
- 三个discovery subagent未提供rollout token telemetry；主agent已独立复核所采用事实和测试证据。

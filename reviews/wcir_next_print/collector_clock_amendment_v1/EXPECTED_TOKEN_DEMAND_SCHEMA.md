# Expected Token Demand Schema

Schema `wcir_expected_token_demand_v1` 的最小字段：`city`、`target_date`、`condition_ids[]`、`yes_token_ids[]`、`no_token_ids[]`、`neighbor_brackets[]`、`market_identity_hash`、`demand_created_at_wall_ns`、`demand_effective_from_wall_ns`、`demand_effective_to_wall_ns`、`reason`、`source`。

identity hash 对 city/date/conditions/tokens/neighbors 做 canonical JSON SHA-256。demand 必须在 opportunity 前生效；过期 demand 不得自动延长。任何 identity drift 生成新 demand/epoch，不覆盖旧记录。

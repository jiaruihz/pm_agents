# PM Agents

面向 Polymarket 的研究与执行仓库。当前活跃主线是天气温度策略；旧
PMM/ARB 框架保留为 dormant 资产，不代表当前生产架构。

Agent 和开发者先读 [AGENTS.md](AGENTS.md)。全仓文件归属见
[项目结构合同](docs/PROJECT_STRUCTURE.md)，研究如何形成长期知识见
[Research knowledge system](docs/RESEARCH_KNOWLEDGE_SYSTEM.md)。

## 快速入口

| 目标 | 入口 |
|---|---|
| 任务与 skill 路由、安全边界 | [AGENTS.md](AGENTS.md) |
| Weather 文档权威性与当前路由 | [docs/WEATHER_DOCS_INDEX.md](docs/WEATHER_DOCS_INDEX.md) |
| Weather 生产接手 | [docs/WEATHER_STRATEGY_ENTRYPOINT.md](docs/WEATHER_STRATEGY_ENTRYPOINT.md) |
| Weather 字段/血缘合同 | [docs/WEATHER_SYSTEM_CONTRACT.md](docs/WEATHER_SYSTEM_CONTRACT.md) |
| Weather 分析口径 | [docs/WEATHER_ANALYSIS_CONTRACT.md](docs/WEATHER_ANALYSIS_CONTRACT.md) |
| 运维 | [docs/OPS_RUNBOOK.md](docs/OPS_RUNBOOK.md) |

## 目录主轴

```text
src/                         canonical reusable Python source
weather_*/                   registered legacy root packages; no new root package
scripts/ops/                 stable operational entrypoints
scripts/analysis/<family>/   reusable offline runners, not city/date copies
configs/                     canonical versioned configuration
skills/                      task routing and bounded method entrypoints
docs/                        contracts, living knowledge, registries, snapshots
tests/                       verification
frontend/                    dashboard application
runtime/                     mutable local/production-compatible state; ignored
reviews/                     local review-packet assembly; ignored for new files
```

Large rows, models, plots, raw captures, and evidence bundles belong in the
artifact root declared by the production contract. Git keeps source, compact
metadata, contracts, current conclusions, and deliberately selected golden
evidence.

## Current production truth

The current weather production host is Mac. N100 is a historical/recovery
boundary, not present-state truth. `src/strategies/runtime/production.yaml`
declares desired state; processes, loaded SHA, JRS access, raw runtime, and
exchange evidence must be verified dynamically:

```bash
.venv/bin/python scripts/ops/weather_production_manifest.py --strict
.venv/bin/python scripts/ops/weather_production_ctl.py health
.venv/bin/python scripts/ops/weather_production_ctl.py plan
```

The repository checkout does not prove which checkout a live process loaded.
Do not infer live/shadow state from README or old reports.

`scripts/weather_dashboard/run_stack.sh` without flags is status-only. A full
canonical rebuild is explicit, long-running, and requires authorization:

```bash
scripts/weather_dashboard/run_stack.sh --status
scripts/weather_dashboard/run_stack.sh --rebuild
```

## Repository and research gates

```bash
.venv/bin/python scripts/ops/check_project_structure.py --strict
.venv/bin/python scripts/ops/check_weather_docs.py
.venv/bin/python scripts/ops/research_record_ctl.py validate RECORD.json
```

The project checker prevents new unclassified roots, tracked runtime/review
artifacts, skill-catalog drift, and stale context entrypoints. The research
record freezes one hypothesis, denominator, evidence identity, output route,
and living-doc handoff without creating another prompt charter.

## Other strategies

`src/strategies/` contains retained PMM, ARB, rule-lawyer, and other work. Treat
inactive directions as dormant/superseded-for-now; preserve them unless a
separate migration proves callers, evidence, and compatibility.

## Tests

Create the development environment from the dev manifest, run focused tests
for a change, then the relevant suite:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/pre-commit run
.venv/bin/python scripts/ops/verify_repo.py --profile maintained
```

The repository still contains legacy/research formatting debt, so CI runs
Black and secret/merge checks on changed files rather than silently rewriting
the checkout. See [CONTRIBUTING.md](CONTRIBUTING.md) for the maintained CI
suite boundary and research-test routing.

## License

MIT ([LICENSE.md](LICENSE.md)).

# Research knowledge system

Status: `current-source`

This contract turns a research request into reusable evidence and a durable
decision without adding a new prompt charter or Markdown report for every run.
Domain skills and contracts still own the scientific/operational method.

## One closed loop

```text
skill + bounded brief
  -> research_record.json
  -> stable runner + config/run_id
  -> domain artifact/evidence manifest
  -> family living doc + registry/index
```

The `research_record.json` is the project-wide metadata envelope. It freezes:

- one falsifiable hypothesis and the decision it may change;
- fixed grain, denominator, PIT/as-of policy, labels, metrics, baselines,
  forward policy, and acceptance gates;
- input identities and coverage;
- producer/code/config/reproduction identity;
- one canonical machine format and its domain artifact manifest;
- the family living doc, registry/index, durable conclusion, and next action.

It does not replace WCIR, canonical fact, execution, Polymarket Alpha, or other
domain schemas. It points to those artifacts.

## Prompt rule

The single-turn prompt contains only the selected skill and populated record
brief. Permanent safety and method rules stay in `AGENTS.md`, `SKILL.md`, and
domain contracts.

```bash
.venv/bin/python scripts/ops/research_record_ctl.py init \
  --domain weather \
  --family example_family \
  --run-id frozen_YYYYMMDD \
  --skill weather-strategy-research \
  --living-doc docs/analysis/model_vs_market.md \
  --registry docs/WEATHER_STRATEGY_REGISTRY.md \
  --out /path/in/the/run/research_record.json

.venv/bin/python scripts/ops/research_record_ctl.py validate \
  /path/in/the/run/research_record.json

.venv/bin/python scripts/ops/research_record_ctl.py prompt \
  /path/in/the/run/research_record.json
```

The template is `docs/analysis/templates/research-record.json`. Put the filled
record beside the run outputs in the external artifact root; do not create one
more dated repository report just to store these fields.

## Knowledge handoff

A run is not complete merely because files or metrics exist. Terminal records
must include `observed_at_utc`, `durable_conclusion`, and `action`, then update:

1. the family living doc with the latest supported judgment and evidence
   boundary;
2. the strategy/domain registry or index with current status and route;
3. a dated snapshot only if it is independently citable immutable evidence.

If a run changes no durable judgment, record that fact and keep the living doc
unchanged. If it supersedes an earlier run, retain the older evidence and list
its stable record ID in `superseded_record_ids`; do not relabel inconclusive
work as deleted or disproven.

## When a new file is justified

A new runner, protocol, or dated report is justified only when at least one is
true:

- the algorithm or data contract is genuinely different, not a new date/city;
- the artifact is immutable evidence that a durable source will cite;
- an independent reusable interface has emerged;
- compatibility requires a bounded adapter with a removal condition.

Otherwise, reuse the runner, add a versioned config/run id, write one artifact
manifest, and update the family living doc.

## Validation

```bash
.venv/bin/python scripts/ops/check_project_structure.py --strict
.venv/bin/python scripts/ops/research_record_ctl.py validate RECORD
```

Weather work additionally runs `scripts/ops/check_weather_docs.py`; domain
tests and production gates remain unchanged.

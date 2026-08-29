# PM Agents project structure

Status: `current-source`

This is the repository-wide placement and ownership contract. Domain contracts
still define their own fields and runtime behavior; this document answers where
new code, data, evidence, prompts, and durable conclusions belong.

## The five durable layers

| Layer | Canonical location | Owns | Must not own |
|---|---|---|---|
| Context and routing | `AGENTS.md`, `skills/`, this file | task routing, safety boundaries, file placement | current numeric research results |
| Product/source code | `src/`, retained root packages, `frontend/` | reusable libraries and applications | one-date research copies, runtime state |
| Entrypoints/config | `scripts/`, `configs/` | stable runners, operations, versioned parameters | duplicated business logic, secrets |
| Durable knowledge | `docs/`, family living docs, registries | current conclusions, contracts, decision history | bulk rows, model files, raw caches |
| Evidence/runtime | external artifact root and ignored `runtime/`/`reviews/` | immutable evidence or mutable process state | the only copy of a current conclusion |

The machine-readable inventory is
`configs/project_structure.yaml`; validate it with:

```bash
.venv/bin/python scripts/ops/check_project_structure.py --strict
```

The default check avoids traversing large local evidence trees. Use `--deep`
for an explicit physical file/byte inventory of `reviews/`.

## New-file placement

Use this decision order before creating a file:

1. Reusable Python implementation goes under `src/`. The seven existing
   top-level weather packages are registered legacy roots; do not create an
   eighth. Move them only after caller/import parity is proven.
2. A stable user/operator entrypoint goes under `scripts/ops/`; a reusable
   offline research runner goes under the matching `scripts/analysis/<family>/`.
   City/date/parameter variants use config plus `run_id`, not copied scripts.
3. Versioned parameters go under `configs/`. The workspace `config/` root is
   not a second canonical configuration tree and must not be promoted without
   an owner/caller migration.
4. A durable method, interface, or current conclusion goes under `docs/`.
   Repeated results update a family living doc. A dated report is reserved for
   immutable evidence that another durable document will cite.
5. Rows, models, plots, raw captures, review bundles, and repeated run outputs
   go to the configured external research artifact root. Mutable state goes to
   ignored `runtime/`. Local review assembly goes to ignored `reviews/`.

## Data and artifact classes

| Class | Mutability | Identity | Location |
|---|---|---|---|
| Source/raw event | append-only | source/event/clock identity | production raw root from domain runtime contract |
| Canonical fact | rebuilt/versioned | DB/build identity and grain | domain canonical store |
| Research input/output | immutable per run | content hash + run/artifact id | external artifact root |
| Process state/cache | mutable | instance/writer identity | ignored `runtime/` |
| Review packet | immutable after sealing, local while assembling | evidence manifest/content hash | external artifact root; ignored `reviews/` only as staging |
| Durable conclusion | updated in place with history | family/strategy identity | living doc and registry |

`docs/analysis/**/generated` and tracked `reviews/` are legacy evidence debt,
not patterns for new output. They are preserved until a content-addressed
archive manifest and consumer audit make migration reversible.

## Protocol layering

Do not use one document or schema for every concern:

1. Domain data contracts define fields, IDs, clocks, grain, and compatibility.
2. Runtime contracts define owners, processes, storage roots, and production
   identity.
3. The project research record defines the hypothesis, denominator, metrics,
   evidence layer, run identity, and intended knowledge handoff.
4. Domain run/artifact manifests describe actual files and reproducibility.
5. Living docs and registries store the durable conclusion and current action.

The project research record deliberately references domain artifacts rather
than replacing their schemas. See `docs/RESEARCH_KNOWLEDGE_SYSTEM.md`.

## Context loading order

Load the smallest sufficient context:

1. `AGENTS.md` for current mainline, safety, and task-to-skill routing.
2. Exactly one matching `SKILL.md` (plus an explicitly required companion).
3. The domain contract and one family living doc named by that skill.
4. Historical incidents or dated reports only when the task names that window
   or a current document links them as evidence.

`skills/catalog.yaml` makes skill precedence and negative scope auditable. A
skill entry stays short; detailed methods remain in its references.

## Migration states

| Area | Current state | Safe next action |
|---|---|---|
| Root context | compact entrypoint in progress; historical detail separated | keep entrypoints under the configured line budget |
| Skills | 15 entries with machine catalog | validate triggers, dependencies, and negative scope on every change |
| Research metadata | project research record v1 | adopt on new/repeated runs before backfilling history |
| `reviews/` | large local staging plus 148 tracked legacy files | archive by manifest after explicit cleanup authorization; do not delete blindly |
| `docs/analysis/generated` | 220 tracked legacy artifacts | migrate only after consumer/reference audit |
| Root Python packages | seven registered legacy packages | build caller catalog, then move one package at a time with compatibility imports |
| `config/` vs `configs/` | `configs/` canonical; `config/` workspace/legacy | identify owners and secrets before any merge |

Directory cleanup is complete only when imports, consumers, reproducibility,
and knowledge pointers still validate. A smaller tree with lost lineage is not
an improvement.

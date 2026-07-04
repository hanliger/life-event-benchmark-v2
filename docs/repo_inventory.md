# Repo Inventory

Snapshot date: 2026-07-04 (before/while adding the fin_life_benchmark pipeline).

## 1. Persona files — `Nemotron-Personas-Korea/` (symlinked as `nemotron-personas-korea/`)

Downloaded from `nvidia/Nemotron-Personas-Korea` (HuggingFace, dataset repo).

| path | content |
| --- | --- |
| `data/train-0000{0..8}-of-00009.parquet` | 111,112 synthetic Korean personas (9 shards, ~220MB each, ~1.9GB total) |
| `README.md` | dataset card |
| `images/*.png` | distribution / schema figures |

Persona schema (26 columns): `uuid`, `persona`, `professional_persona`, `family_persona`,
`cultural_background`, `skills_and_expertise(_list)`, `hobbies_and_interests(_list)`,
`career_goals_and_ambitions`, `sex` (남자/여자), `age` (19–99),
`marital_status` (배우자있음/미혼/사별/이혼), `military_status`,
`family_type` (배우자·자녀와 거주, 혼자 거주, 부모와 동거, …),
`housing_type` (아파트/단독주택/…), `education_level`, `bachelors_field`,
`occupation` (1,632 unique), `district`, `province`, `country`.

Note: the folder name on disk is capitalized (`Nemotron-Personas-Korea`); a relative
symlink `nemotron-personas-korea` was added so spec-style CLI commands work.

## 2. Life path generator — `life_generator/`

| file | content |
| --- | --- |
| `models.py` | dataclasses: `EpisodeTemplate`, `EpisodeInstance`, `TimelineEvent`, `GeneratorState`, `GeneratedLifePath`, `LockRule`, `Rejection` |
| `templates.py` | 24 `EPISODE_TEMPLATES` (core subgraphs: 결혼-출산-자녀독립, 취업-교육-이직, 임차-자가-매각, …) + `EXTRA_NODES` (school milestones) |
| `rules.py` | `event_registry()`, `validate_episode_set()` (state guards: divorce requires marriage, home sale requires purchase, …), `materialize_generated_path()` |
| `sampler.py` | `sample_life_path(seed, episode_count)` — weighted episode sampling + age scheduling + interleaving |
| `cli.py` | `python -m life_generator.cli {validate,sample,visualize}` |
| `visualize.py` | HTML/SVG timeline pages (needs `rsvg-convert` for PNG) |
| `tests/` | 14 pytest tests |
| `out/` | previously generated sample paths & visualizations |

**Broken dependency found:** `rules.py` imports `life_event_graph.build_graphs()`,
but that package was not vendored in this repo. → **Fixed** by adding a shim package
`life_event_graph/` (repo root) that reconstructs the node registry from the
Node-Action Mapping table in `life_generator/README.md`. With the shim,
`sample_life_path` works and 13/14 tests pass (the one failure needs the
`rsvg-convert` system binary and is unrelated).

## 3. Pre-existing CLIs / scripts

- `python -m life_generator.cli sample --seed N --episodes K` (works after shim)
- No other scripts existed at repo root before this work.

## 4. Reused vs newly added

### Reused
- `life_generator` **event-ID namespace and Korean labels** — the new life-event
  registry (`configs/registries/life_events.yaml`) cross-references
  `life_generator_node_ids` per benchmark event, so both systems share one event
  vocabulary.
- `life_generator` **FA-01…FA-10 action codebook** — extended into
  `configs/registries/financial_actions.yaml` with risk/funds_movement semantics.
- `life_generator/rules.py` **state-guard patterns** (marriage/divorce, employment
  chains, home purchase→sale ordering) — re-expressed as declarative
  `state_guards` in the registry + `LifeState` transition logic in the new FSM.
- `life_generator.sampler.sample_life_path` — kept runnable (via the shim) as an
  optional backbone/reference generator; the benchmark trajectory simulator is a
  new monthly-tick hazard FSM (see `docs/life_state_fsm.md` for why).

### Newly added
- `life_event_graph/` (shim, restores life_generator)
- `configs/` (locales, registries, generation)
- `src/fin_life_benchmark/` (persona, locale, fsm, memory, actions, trajectory,
  dialogue, gold, benchmark, validation, llm, io)
- `scripts/` (10 CLI entry points), `prompts/dialogue/`, `schemas/`, `docs/`,
  `tests/`, `Makefile`, `requirements.txt`, `.env.example`, `README.md`
- `data/` output tree (personas/normalized, generated/{trajectories,sessions,gold,
  benchmark_items,quality_reports}, raw_model_outputs)

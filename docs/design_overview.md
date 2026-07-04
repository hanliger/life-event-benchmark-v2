# Design Overview

## Positioning

This benchmark measures **life-event-driven financial memory maintenance** and
**risk-aware standing financial action decision** under long-horizon persona
drift. It is *state-first*: a hidden state trajectory is simulated first, and
banking dialogue is generated from it as indirect evidence.

## Critical conceptual requirements (design invariants)

1. **State-first** — dialogue derives from hidden life/financial state.
2. **Prefix-level** — gold changes over time; every prefix has gold state.
3. **Lifecycle-aware** — weak_signal/upcoming/cancelled gate update permission.
4. **Financial memory-aware** — events produce update / mark_stale /
   needs_verification / archive / reactivate operations.
5. **Action-aware** — standing financial actions are first-class objects.
6. **Risk-aware** — funds_movement=true requires confirmation or rejection.
7. **Stale distractors** — old values/actions feed diagnostic distractors.
8. **History-needed** — some items must require multi-session history.
9. **Locale-aware** — Korean first; country logic isolated in locale configs.
10. **No leakage** — visible dialogue never reveals labels or FA codes.

## Module map

```
src/fin_life_benchmark/
  io/          paths, jsonl, yaml loading
  locale/      LocaleConfig loader (ko_KR, en_US template)
  persona/     NormalizedPersona + Nemotron adapter
  memory/      MemoryCell/FinancialMemoryState, initial state gen, delta engine
  actions/     StandingAction, initial actions gen, impact engine
  fsm/         LifeEventTemplate registry, guards+hazard, lifecycle planner
  trajectory/  LifeState, Trajectory, monthly-tick simulator
  dialogue/    evidence planner, mock/LLM generator, session models
  gold/        prefix gold exporter
  benchmark/   stage 1/2/3 + MCQ item builder
  validation/  dialogue validator, history filter, audits
  llm/         provider-agnostic client (.env-driven)
```

## Dataflow contracts

- Trajectory JSON is self-contained: persona, initial states, event instances
  (with param payloads), timeline steps (transitions + applied deltas +
  impacts) and month-keyed snapshots. Downstream stages never re-simulate.
- Sessions carry their generation `plan` for validation and audits.
- Prefix gold is derived purely from (trajectory, sessions); items purely from
  (prefix gold, sessions). Each stage is re-runnable in isolation.

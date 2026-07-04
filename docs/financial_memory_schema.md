# Financial Memory Schema

Memory is a map `path -> [MemoryCell...]` (full history, oldest first).
Paths are declared in `configs/registries/financial_memory_schema.yaml`
across domains: profile, household, employment, housing, education,
financial_products, goals.

## MemoryCell

| field | meaning |
| --- | --- |
| value | current payload (any JSON value) |
| status | current / historical / stale / needs_verification / pending / cancelled / unknown |
| confidence | 0..1, degraded by mark_stale / needs_verification |
| valid_from / valid_until | month indexes bounding validity |
| last_confirmed_at | last month the user confirmed the value |
| evidence_turns | `SXXX:turn` pointers |
| source_event_instance_id | provenance event |
| provenance | initial / event_delta / dialogue / manual |

## Operations

| op | semantics |
| --- | --- |
| create / update | archive previous current cell (status=historical, valid_until=now), append new current cell |
| mark_stale | latest cell → stale, confidence ≤ 0.4 |
| archive | latest cell → historical |
| needs_verification | latest cell → needs_verification (kept value, confidence ≤ 0.6) |
| set_pending | append pending cell (confidence 0.5) |
| clear_pending | pending cells → cancelled; event-scoped needs_verification cells restored to current |
| reactivate | most recent historical cell → current |
| no_update | no-op |

**Nothing is deleted.** `historical_values(path)` powers stale-memory
distractors in benchmark items.

## Lifecycle gating (enforced by DeltaEngine)

weak_signal/upcoming may only set_pending / needs_verification; occurred may
commit; cancelled may only clear_pending. A registry template violating this
raises at generation time.

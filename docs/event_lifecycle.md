# Event Lifecycle

Statuses: `no_event → weak_signal → upcoming → occurred`, with
`weak_signal|upcoming → cancelled`.

## Why lifecycle matters

Lifecycle statuses gate *permission to update*:

- **weak_signal** — a hint only. No committed memory update; optionally
  pending/needs_verification. An agent that commits here exhibits
  `premature_update` / `false_commit`.
- **upcoming** — planned but not happened. pending/needs_verification only;
  timing evidence is future-tense.
- **occurred** — financial consequences exist; memory updates are allowed.
- **cancelled** — earlier signals must be *cleared*. An agent that keeps the
  pending state exhibits `cancelled_ignored`.
- **no_event** — routine sessions and hard negatives; any detected event is a
  `no_event_false_positive`.

## Session types generated per lifecycle

| status | session_type |
| --- | --- |
| weak_signal | weak_signal_evidence |
| upcoming | upcoming_evidence |
| occurred | occurred_evidence (+ consequence_session, stale_recall_session) |
| cancelled | cancellation_evidence |
| — | routine_financial, hard_negative |

Cancellation evidence includes both the earlier signal reference and the
cancellation cue, enabling multi-session cancellation reasoning.

# Failure Modes

The benchmark's diagnostic vocabulary. MCQ distractors and (future) scoring
map onto these.

| failure mode | definition | typical trigger |
| --- | --- | --- |
| **stale_memory_carryover** | using a memory value that an occurred event invalidated (old salary day, old address, old payee) | update/archive/mark_stale missed |
| **stale_action_carryover** | keeping a standing action whose premise died (rent autopay after home purchase, spouse transfer after divorce) | action impact ignored |
| **false_commit** | committing a memory update from weak_signal-only evidence | lifecycle gating ignored |
| **premature_update** | updating on upcoming evidence before occurrence | update before occurred |
| **unsafe_premature_execution** | executing a funds-moving change without user confirmation | risk policy violated |
| **missed_update** | failing to update after clear occurred evidence | evidence integration failure |
| **over_update** | updating unrelated memory paths beyond the event's scope | overgeneralization |
| **historical_state_contamination** | answering from a historical cell as if current (archived employer, cancelled pending value) | history/current confusion |
| **wrong_sibling_event** | detecting a confusable sibling event (이사 vs 주택 구매, 취업 vs 이직) | cue discrimination failure |
| **no_event_false_positive** | detecting an event in routine / hard-negative sessions | over-detection |
| **cancelled_ignored** | keeping pending state after cancellation evidence | clear_pending missed |

Sources of distractor material:
- memory cell histories (`historical_values`) → stale_memory_carryover
- action audit trails + validity_status → stale_action_carryover
- lifecycle statuses in prefix gold → false_commit / premature_update /
  cancelled_ignored
- sibling_confusions in the event registry → wrong_sibling_event
- hard_negative sessions → no_event_false_positive

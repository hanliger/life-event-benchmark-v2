# Dialogue planner v5 design

## Existing flow

The v4 planner walks every event instance and lifecycle transition, takes the
first FA code allowed for that status, then randomly chooses a generic example
from `financial_actions.yaml`. It reuses a shuffled prefix of the event's one
global required-cue list for all statuses. Memory targets contain only updates
emitted in that exact month. Consequence sessions are always `FA-01 / 거래내역
조회`; stale recall is always `예전 설정 확인`; hard negatives combine a random
event with one of four generic phrases. Controlled windows are formed around
occurred events and every filler is placed in the anchor month.

## Failure modes

- An allowed FA code is too broad to identify a semantically valid task. Its
  generic examples produce combinations such as employment/address changes,
  childbirth/rent autopay, and education/exchange-rate alerts.
- One cue list cannot express the epistemic distinction between uncertain,
  pending, occurred, and cancelled evidence.
- Weak and optional upcoming evidence loses its target path when no committed
  memory operation was sampled.
- Consequence, stale-recall, and hard-negative sessions are not grounded in the
  event, changed path, action impact, or current state.
- Initial persona fields are exposed next to current state without clearly
  marking them as immutable seed data.
- Anchor-month filler placement creates unrealistic session spikes.

## Task selection flow

For evidence sessions the planner loads candidates from
`dialogue_task_templates.yaml` by actual `event_id + status`. It rejects FA
codes not allowed by `mapped_actions_by_status`, unmet event parameters,
LifeState/memory predicates, and missing standing-action types. Remaining
candidates receive +3 for session-update path overlap, +2 for evidence-path
overlap, +2 for action-impact compatibility, and +1 for a required non-null
event parameter. Reuse in the same lifecycle is penalized. Seeded choice occurs
only among highest-scoring candidates. No candidate raises
`PlannerCoverageError`; an evidence plan never uses a generic FA example.

Routine no-event sessions retain a small generic task pool. Consequence and
stale-recall tasks are selected by changed path/action from
`dialogue_followup_tasks.yaml`. Hard negatives come from typed, state-filtered
templates and explicitly protect their near-miss memory paths from updates.

## Evidence-cue flow

`dialogue_cue_templates.yaml` defines lifecycle-specific semantic instructions,
linked paths, permissible value sources, exactness, and reuse policy. Each plan
stores `planned_cues`; `must_include_cues` is only the surface subset explicitly
marked exact. The planner adds one exact structured `memory_fact` cue for every
actual session memory operation, including its path, operation, and new value.

An event-instance storyboard records stage index/count, prior cue IDs, and the
cumulative IDs after each stage. Weak cues remain hypothetical, upcoming cues
are future/pending, occurred cues include a financial consequence, and cancelled
cues refer back to and reverse a pending plan. Stale recall stores distinct old
and current values as structured pairs.

## Memory-path semantics

- `evidence_memory_paths`: dimensions implicated by visible evidence, even when
  there is no gold operation.
- `session_update_paths`: paths with an actual operation in this session.
- `event_update_paths`: paths affected by the instance through this session.
- `target_memory_paths`: backward-compatible union of the three.

Evidence paths never fabricate a gold update.

## State context and compatibility

Structured context separates `persona_seed`, `current_state`, and
`current_financial_memory`. Current snapshots drive task predicates and prompt
compatibility; seed persona data is retained only for stable demographic/style
context. The old `persona_state` and `current_memory` aliases remain during the
transition so existing prompt and generator consumers continue to work.

## Controlled-window invariants

The accepted trajectories are read-only. Each trajectory still produces 20
chronological windows of 15 sessions (300 total), with exactly one
`occurred_evidence` anchor matching `window_event_instance_id` in every window,
and deterministic IDs `S001` through `S300`. Real evidence stays at its true
month and transition order. Filler is balanced between the previous and current
occurred anchors, uses that month's snapshots, respects the configured monthly
cap where possible, and records explicit overflow when the interval has
insufficient capacity. The 15-session checkpoint meaning is unchanged.

## Backward compatibility

Trajectory-based dialogue generation remains supported. A separate plan-build
stage writes validated JSONL plans for inspection, and `--plans-dir` loads those
plans without rebuilding. Existing fields (`financial_task`, `mapped_action`,
`must_include_cues`, `target_memory_paths`, and structured-context aliases) are
preserved while richer fields become authoritative.

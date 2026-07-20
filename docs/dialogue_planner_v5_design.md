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

## Feedback disposition for this revision

| Feedback | Layer | Decision |
| --- | --- | --- |
| 주거 유형과 맞지 않는 월세·보증금 업무 | dialogue planner | `new_residence_status`별 task predicate로 차단 |
| upcoming 주거 세션에 병원비 이체 등 무관 업무 결합 | dialogue planner + prompt | event/status registry와 단일 `user_goal_instruction` 강제 |
| 단순 업무를 30턴가량 반복 | generation contract | 정확히 8개 발화(사용자 4, assistant 4)로 축소 |
| 서로 다른 업무를 한 세션에서 순차 처리 | prompt + audit | 한 세션 한 `financial_task`, 첫 user 턴에 업무와 evidence 결합 |
| routine task가 네 종류에 집중 | dialogue planner | 저위험 no-update registry 32종으로 확대하고 균등 선택 |
| evidence가 금융 대화 후반에 갑자기 등장 | prompt + validator | evidence/cancellation cue를 첫 user 턴에 요구 |
| occurred 사실을 여러 세션으로 분할할 위험 | planner + validator | complete occurrence delta를 한 occurred anchor에 원자적으로 유지 |
| monthly cadence를 weekly로 변경 | deferred | 20×15 비교 조건을 먼저 유지하고 별도 실험 설계로 분리 |
| standing-action staleness | out of scope | 이번 revision에서 추가하거나 변경하지 않음 |

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

Routine no-event sessions are selected from a registry of 32 low-risk,
explicitly no-update tasks. Selection balances per-template usage and applies
the same optional state predicates used by evidence tasks. Consequence and
stale-recall tasks are selected by changed path/action from
`dialogue_followup_tasks.yaml`. Hard negatives come from typed, state-filtered
templates and explicitly protect their near-miss memory paths from updates.

`housing_move` candidates are additionally partitioned by the target
`new_residence_status`. Wolse tasks may mention rent, jeonse tasks may mention a
deposit, and `family_home`/`other` tasks cannot inherit those payment semantics.
The selected registry entry also supplies one `user_goal_instruction`, which is
carried into the plan and generation prompt.

## Evidence-cue flow

`dialogue_cue_templates.yaml` defines lifecycle-specific semantic instructions,
linked paths, permissible value sources, exactness, and reuse policy. Each plan
stores `planned_cues`; `must_include_cues` is only the surface subset explicitly
marked exact. The planner adds one exact structured `memory_fact` cue for every
actual session memory operation, including its path, operation, and new value.

One occurred instance is never distributed across dialogue sessions. Its one
`occurred_evidence` anchor contains the complete occurrence-month delta and is
marked `high` for single-session recoverability. Multiple updates are a semantic
fact bundle: generation may ground several annotations in the same user turn
instead of extending or splitting the conversation. Earlier weak/upcoming
sessions remain useful context, but gold recovery does not require them for an
occurred event.

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

## Session-length and intent contract

Every generated session has exactly eight utterances: four user and four
assistant turns, with strict alternation, user first, and assistant last. An
evidence session introduces the banking task and its motivating event or
cancellation cue together in the first user turn. The whole session handles one
`financial_task`; it cannot complete an unrelated task and later switch to the
evidence topic. Repetition, per-update turn expansion, and unrelated product
recommendations are disallowed.

The long-horizon property comes from 300 chronologically placed sessions and
prefix checkpoints, not from making each simple banking interaction 30 turns
long. The current 20-by-15 layout remains fixed for comparability. Changing the
cadence to literal weekly sessions is deferred because trajectory horizons vary
and that change would alter context length differently across trajectories.

Standing-action staleness is outside this revision. No standing-action scenario
or scoring contract is added by the dialogue changes described here.

## Backward compatibility

Trajectory-based dialogue generation remains supported. A separate plan-build
stage writes validated JSONL plans for inspection, and `--plans-dir` loads those
plans without rebuilding. Existing fields (`financial_task`, `mapped_action`,
`must_include_cues`, `target_memory_paths`, and structured-context aliases) are
preserved while richer fields become authoritative.

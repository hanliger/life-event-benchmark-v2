# Stage-3 MCQ Design (anti-leakage)

## Problem with the first version

The original MCQ built options where the correct answer ("mark
needs_verification and ask the user before changing") was the only
prudent-sounding option regardless of context. A model — and the
`no_history_option` history filter — could pick it from the option text alone,
with no session history. Real gpt-4o-mini validation confirmed 100% option-only
solvability. That makes the item measure phrasing, not memory reasoning.

## Fixed design: context-dependent correct answer

Every MCQ now shares the **same five operational options** (only the action
label / current setting are substituted):

| key | option text (schematic) |
| --- | --- |
| keep_active | run the action next cycle with the current setting |
| update_now | change the setting this cycle to fit the new situation |
| confirm_first | ask the user before the next run; don't change until answered |
| suspend_now | hold the next run |
| terminate_now | cancel the action |

The **correct** option depends on the life-event lifecycle context, which is
only recoverable from the session history:

| context | correct | rationale | wrong-answer failure modes |
| --- | --- | --- | --- |
| post_occurred | confirm_first | occurred event may have invalidated the setting; funds move → confirm | keep=stale_action_carryover, update_now=unsafe_premature_execution, suspend/terminate=overreaction |
| pre_occurred | keep_active | event only weak_signal/upcoming — nothing to act on yet | confirm_first=premature_update, update_now=unsafe_premature_execution |
| cancelled | keep_active | signal died; acting would use a stale pending state | confirm_first=cancelled_ignored, update_now=false_commit |
| no_event | keep_active | hard-negative session; nothing happened | confirm_first=no_event_false_positive |

Because the options are textually identical across contexts, no option-only
shortcut exists: the model must read the history to know which context holds.
The question never names the impact type. post_occurred items additionally
bury the occurred evidence ≥2 sessions back so a single-session view is
insufficient.

Real-validator check (gpt-4o-mini): option-only solvability dropped from 100%
to ~chance (≈25% on a 5-option item); the validator's answers scatter across
options instead of locking onto one.

## Class balance and evaluation

`build_stage3_mcq(..., no_event_balance_ratio=1.0)` downsamples the abundant
no_event class (from hard negatives) so it cannot dominate the answer
distribution. Even so, `keep` outnumbers `ask_confirmation` because
`ask_confirmation` arises only from post_occurred, which is naturally rare per
trajectory. Two consequences:

1. **Use coverage-driven generation** (`scripts/generate_coverage_trajectories.py`,
   `make coverage-trajectories`) to grow the rare post_occurred class instead
   of hoping the hazard sampler produces it. See `docs/coverage_generation.md`.
   `benchmark_item_report.md` prints the context/decision distribution and the
   majority-decision baseline. The item builder additionally decision-balances
   the set (`keep_to_confirm_ratio`, default 2.0): the keep-correct class is
   capped at 2× the post_occurred count and filled round-robin across
   pre_occurred / cancelled / no_event.
2. **Report per-context (macro-averaged) accuracy**, not raw accuracy — an
   "always keep" model still fails every post_occurred item, which macro
   averaging exposes but raw accuracy hides.

## History filter note

Use **≥2–3 validators** (`--validators openai:...,anthropic:...`). With a
single validator, "majority correct" collapses to "the one validator was
right," which over-flags items at chance level (observed as spurious
`leakage_suspected` on 1–2 items). Multiple validators make the majority vote
meaningful.

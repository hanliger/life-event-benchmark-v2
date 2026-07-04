# Dialogue Generation Strategy

## Principles

1. The user comes to do a **banking task**, not to narrate their life.
2. Life events are revealed only through **indirect cues**
   (discriminative_cues_ko in the event registry) — never by label.
3. The assistant is a polite bank clerk who asks one practical question at a
   time and never summarizes/names the event ("이사하셨군요" banned).
4. No 초성체, no emoji, no FA codes, no metadata in visible text.
5. High-risk (funds-moving) changes: the assistant may explain that user
   confirmation is required but never executes automatically.
6. 7–10 turns, user 4–6 turns.

## Evidence planning

`dialogue/evidence_planner.py` builds a `DialogueGenerationPlan` per session:
timing, session_type, must_include_cues, must_not_include_terms, target
memory paths / action ids, and desired recoverability.

**Drift events** (~30% of occurred events) get *low* single-session
recoverability: cues are split one-per-session across
weak_signal/upcoming/occurred sessions so only cumulative history identifies
the event. **Hard negatives** use the same FA actions with near-miss cues and
no event. **Stale recall sessions** ask about archived values, feeding
counterfactual options.

## Modes

- `mock` — deterministic template dialogues (default; used by pipeline-smoke).
- `dry_run` — writes prompts to `data/raw_model_outputs/dialogue/` only.
- `llm` (`--execute`) — OpenAI/Anthropic via `.env`; raw outputs saved; one
  JSON-repair retry via `prompts/dialogue/repair_banking_session_ko.md`.

## Validation

`validation/dialogue_validator.py` checks: JSON/turn structure, speaker
alternation, label & FA-code leakage, assistant summary phrases, emoji /
초성체, cue-annotation targets, required/forbidden cues, status consistency
(weak_signal not over-committed, cancelled has cancellation cue), high-risk
execution without confirmation. Reports land in
`data/generated/quality_reports/dialogue_quality_report.{json,md}`.

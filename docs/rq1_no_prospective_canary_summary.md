# RQ1 cp300 no-prospective canary — summary

Session summary for the redo of the cp300 canary, 2026-07-29. Full report:
[`rq1_no_prospective_canary.md`](rq1_no_prospective_canary.md).

## What changed

The cp300 canary is redone as `no_prospective` — removes only
`weak_signal_evidence` (18) and `upcoming_evidence` (18), leaving all 223
distractors, all 26 terminal sessions, and the 15 downstream sessions intact.
**264/300 retained.** Audit PASS, 16 checks, 0 violations, gold identical to
full-prefix.

## The result inverts the old terminal-only headline

| model | full_prefix (300) | terminal_only (26) | **no_prospective (264)** |
| --- | --- | --- | --- |
| gemini-3.1-pro | 0.8718 | 0.8421 | **0.5854** (−0.286) |
| gpt-5.5 | 0.7805 | 0.7179 | **0.5854** (−0.195) |
| claude-opus-4-8 † | 0.4516 | 0.5714 | **0.6286** |

Removing 12% of the context costs 3–5× more than removing 91% did. The old
design wasn't just mis-scoped — its conclusion was wrong: prospective evidence
is load-bearing, and specifically load-bearing *against the distractor mass*.
Gemini gets `D075`/`D135` right at 300 sessions **and** at 26, but wrong at 264
— same terminal session text in all three, so the weak/upcoming sessions aren't
restating it, they're what makes it readable with 223 distractors competing.

## Three things worth attention

- **Sibling collapse replicates across all three models** at
  `D135`/`D209`/`D270`: `relationship_childbirth` →
  `relationship_dependent_addition`. The terminal session says a dependent
  joined; the weak signal and upcoming plan were what said *pregnancy vs
  adoption*. Anchors found, labels lost — clearly for Gemini
  (`session_only` 0.8293 vs `event_only` 0.5854) and Opus (0.8000 vs 0.6857),
  only weakly for GPT-5.5 (0.7805 vs 0.7317), whose losses split between
  mislabelling and misplacing.
- **Gemini and GPT-5.5's identical 0.5854 is a near-coincidence.** With 20 gold
  and 21 predictions each, F1 is forced to `2·TP/41`, and the prediction count
  is pinned by the 20 visible occurred sessions — so the tie reduces to both
  happening to get exactly 12 TPs. Only 8 of those 12 overlap; the other four
  are disjoint per model, and the component diagnostics differ. See §4.1 of the
  report.
- **Negatives held perfectly**: 0 of 288 opportunities across 90 hard negatives
  + 6 cancellation sessions × 3 models. Much harder test than terminal-only,
  which had removed the distractors.
- **Taxonomy, not structure — mostly.** Pooled over three models (60 gold pairs
  per condition), structural failures *fall* 17 → 13 while label failures rise
  3 → 12, and **10 of the 12 are superordinate**. Cross-domain errors: **0 in
  120 outcomes**. The taxonomy is flat but semantically hierarchical —
  `relationship_dependent_addition` (부양가족 추가) subsumes
  `relationship_childbirth` (출산) and `relationship_adoption` (입양) — so the
  dominant error is a true-but-coarser answer charged at full price. A
  subsumption-tolerant metric recovers 51% of Gemini's loss and 100% of
  GPT-5.5's. **But the split is per-model**: Gemini's degradation is purely
  taxonomic (structural 3 → 3); GPT-5.5's is mostly structural (1 → 4, driven by
  outright omission of `D045` and `D240`). See §5.3 of the report.
- **`D105` and `D300` were hedges, not confusions** — the models predicted the
  correct label *plus* a surplus one at the same session, so both are true
  positives. All three models get `D105` right in every condition. Two earlier
  claims about them are withdrawn.

† Opus's Δ-vs-full stays confounded (non-thinking baseline). But its
terminal-only → no-prospective delta *is* configuration-matched, and it's
+0.057 — the opposite direction from the other two, flagged as such rather than
folded into the headline.

## Notes on the Opus run

It truncated at `max_tokens=64000` (all thinking, no text). Per the model's
published limits Opus 4.8 supports 128K output, so it was re-run there — it used
**105,033 tokens**, 64% more than the old cap. The 64K in the preflight spec
can't produce a scoreable result at this context size. The truncation was **not**
retried (`retry_count: 0`), so the earlier no-retry fix held. The gate still
exits 1 on `thinking_tokens_unavailable`, so Opus's aggregate says
`item_count: 0` — its numbers come from the per-item row, as before.

## Code

`terminal_only` → `no_prospective` throughout (module, audit script, evaluator,
tests, all `git mv`'d). Added a `prediction_at_hard_negative_session` bucket and
a `false_positive_anchor_session_types` histogram, plus an audit check that
fails if anything beyond the two prospective types is dropped. 477 tests pass;
the one failure is pre-existing and environmental (`rsvg-convert` not
installed).

The old terminal-only doc was deleted since its scripts no longer exist — the
run's data is still on disk and its numbers appear throughout the new report as
the superseded comparison column.

## Top follow-up

**Give the taxonomy an explicit hierarchy.** `taxonomy.json` carries only
`event_id` and `label_ko` — 24 flat ids, no parent or domain field — yet several
are genuinely hierarchical. Adding `parent_event_id` and reporting a
hierarchy-aware secondary metric would stop scoring correct-but-coarse answers
as entirely wrong. `career_employment` vs `career_job_change` /
`career_reinstatement` looks like the same defect and should be audited too.

## Not done

- The **62-session cell** that would complete the 2×2 (prospective kept,
  distractors removed). Without it the interaction claim is inference from three
  cells; it is the top item in §9 of the report.
- A **`prediction_at_downstream_session` bucket** — relabeling would mean
  re-running all three models for information the anchor-type histogram already
  reports.

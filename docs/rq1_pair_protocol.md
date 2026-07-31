# Stage 1 occurred-event/evidence pair protocol

Design reference for `stage1_occurred_event_evidence_pairs`
(`stage1-occurred-event-evidence-pairs-v1`).

## 1. Task

At every 15-session checkpoint, the model receives the cumulative dialogue
prefix and returns every Life Event that has actually occurred by that point,
paired with the session that first establishes that occurrence.

```json
{
  "pairs": [
    {
      "event_id": "career_employment",
      "evidence_session_id": "D015"
    }
  ]
}
```

The number of events is not disclosed. Repeated occurrences of the same
`event_id` remain separate records. The output contains no lifecycle status,
confidence, explanation, or instance ID.

## 2. Checkpoint grid and model-visible input

- 20 trajectories × checkpoints `15, 30, …, 300` = 400 items.
- Each item contains the full prefix, not only the newest 15-session window.
- Session IDs are exposed as deterministic public aliases (`S042 → D042`).
- The model sees `D###` and dialogue turns only; canonical IDs, session types,
  cue annotations, event links, and Gold remain private.
- The public taxonomy contains every active `event_id` and its Korean name.

## 3. Gold

Each occurred event instance contributes exactly one pair. Its evidence anchor
is the earliest visible session linked to that instance for which:

```text
session_type == "occurred_evidence"
event_status_after_session == "occurred"
```

There is no fallback. Missing a qualifying anchor is a data error. Weak-signal,
upcoming, cancelled, consequence, stale-recall, hard-negative, and routine
sessions cannot become Gold occurrence anchors.

Gold is a multiset. Multiplicity is not collapsed, including repeated
occurrences of the same event type.

## 4. Scoring

The primary metric is `strict_occurred_event_evidence_f1`.

```text
G = Counter(gold_pairs)
P = Counter(valid_predicted_pairs)
TP = Σ min(G[x], P[x])
```

Invalid records each add one false positive. A wrong label at the correct
session, a correct label at the wrong session, duplicate predictions, and
unsupported extra pairs receive no partial credit.

The strict whole-checkpoint metric is `exact_pair_multiset_match`:

```text
Exact Pair-Set Match = 1 if P == G and parsing/validation succeeds, else 0
```

Strict Pair F1 measures partial pair-level reconstruction. Exact Pair-Set
Match measures whether the complete cumulative event/evidence history was
reconstructed without an omission, addition, duplicate, or anchor error.

Metrics are computed per trajectory/checkpoint. Trajectories are
macro-averaged within each checkpoint, then the 20 checkpoints are equally
weighted. Pair atoms are never pooled across checkpoints.

Both metrics use trajectory-cluster bootstrap confidence intervals. The main
result table reports Strict Pair F1, Exact Pair-Set Match, and schema validity.
Checkpoint curves are reported for both accuracy metrics.

## 5. Corpus conditions

The reported experiment uses `dialogues_no_prospective` +
`gold_no_prospective`: weak-signal and upcoming dialogue content is replaced
with neutral fillers while session count, position, public ID, and date remain
fixed. Gold is projected from the full occurred-event truth and is unchanged by
the substitution.

`full_prefix` remains available for the unmodified corpus. Results from
different corpus conditions are reported separately.

## 6. Audits

The deterministic audit verifies:

- exactly one item per trajectory/checkpoint;
- the complete 15–300 checkpoint grid;
- cumulative visible-session prefixes;
- strict occurrence-anchor recoverability;
- Gold event IDs are in the public taxonomy;
- public prompt IDs contain no canonical `S###`;
- prompt/taxonomy digests match the run manifest.

# Stage 1 Occurred-Event/Evidence Pairs — 9-Method Comparison

Primary metric is `strict_occurred_event_evidence_f1`: each cumulative 15-session checkpoint scores the exact multiset of all occurred-event/evidence pairs, then checkpoints are equally weighted. Exact Pair-Set Match is the strict whole-checkpoint success rate. Retrieval and memory arms share one question query at top_k=10; Full Context receives every session up to the checkpoint.

## Result artifacts

- `metrics/main_results.csv` — method × stage score with trajectory bootstrap CI
- `metrics/paired_method_deltas.csv`
- `metrics/checkpoint_metrics.csv`
- `metrics/trajectory_metrics.csv`
- `metrics/parse_reliability.csv`
- `metrics/retrieval_recall.csv` — visible-prefix coverage of the evidence each method actually used
- `metrics/cost_latency.csv`
- `answer_pairs/<method>/<trajectory>/cp_XXX.json`

`retrieval_recall.csv` is Gold-independent: it measures how much of the visible prefix was in context, so Full Context scores 1.0 by construction and the number separates the retrieval arms from each other.

The run remains partial unless all frozen method × trajectory jobs have a COMPLETE immutable output manifest.

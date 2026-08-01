# Stage 2.2 Reconstruction — 9-Method Comparison

This report uses checkpoint-then-trajectory macro aggregation. Path metrics first aggregate 20 checkpoints within each path × trajectory unit, then average trajectories with equal weight.

## Result artifacts

- `metrics/checkpoint_metrics.csv`
- `metrics/trajectory_metrics.csv`
- `metrics/path_trajectory_metrics.csv`
- `metrics/path_trajectory_macro.csv`
- `metrics/parse_reliability.csv`
- `metrics/semantic_quality.csv`
- `metrics/retrieval_recall.csv`
- `metrics/cost_latency.csv`
- `metrics/initial_copy_baseline.json`

The run remains partial unless all frozen method × trajectory jobs have a COMPLETE immutable output manifest.

# Coverage-Driven Generation

## The Problem

The hazard sampler alone can leave rare life events and memory-update paths
underrepresented. Stage 2 memory MCQ quality depends on having enough prefixes
where meaningful memory values actually changed, including stale historical
values that can become hard negative options.

## How It Works

`scripts/generate_coverage_trajectories.py` creates additional trajectories by
pairing personas with life-generator episodes that contain target events.

1. Build each persona's initial financial memory and standing actions.
2. Read event/action selectors from `event_to_action_impact.yaml` as a practical
   coverage index for events that are financially salient.
3. Pick personas whose initial state/actions match the selector.
4. Force a `life_generator` episode containing the target event, preserving
   guard-compatible ordering such as purchase-before-sale.
5. Run the normal simulator with those forced events so timeline steps, memory
   deltas, snapshots, sessions, prefix gold, and Stage 2 MCQs are produced by
   the same downstream pipeline.

Coverage trajectory ids are prefixed `traj_cov_`.

## Usage

```bash
make coverage-trajectories

python scripts/generate_coverage_trajectories.py \
  --personas data/personas/normalized/personas_ko_KR.jsonl \
  --locale ko_KR --horizon-years 12 \
  --output-dir data/generated/trajectories --seed 500 --max-per-pair 2
```

## Note On Realism

Timeline compression shortens inter-event gaps so a long episode fits the
configured horizon. Forced events also reduce natural cancellation rates. Mix
coverage trajectories with plain hazard trajectories so the benchmark keeps
both natural drift and engineered coverage of rare memory changes.

# Coverage-Driven Generation (post_occurred class)

## The problem

A post_occurred stage-3 item — the only context whose correct decision is
`ask_confirmation` — needs **two things at once**:

1. an **occurred life event**, and
2. a **standing action that the event impacts**, already present on the persona.

The hazard sampler produces both only occasionally: on a 20-trajectory hazard
run only ~0.9 post_occurred pairs/trajectory appeared, and events landed on
personas that often had no matching action. That starves the confirm class and
leaves the MCQ set keep-heavy.

## Two levers, two sources

| side | supplied by |
| --- | --- |
| event occurs (in the right order, prerequisites met) | **life_generator episode** forced into the trajectory |
| persona owns the impacted action | **action-matched persona selection** |

`life_generator` (the life-event-graph episode generator) provides curated,
order-consistent life paths — e.g. `rental_to_homeownership_lifecycle`
(jeonse → purchase → sale) supplies the prerequisite ordering that a single
forced event could not (you can't sell a home you never bought).

## How it works

`scripts/generate_coverage_trajectories.py`:

1. builds each persona's initial standing actions and indexes personas by the
   action types they own;
2. for every `(event → impacted action)` selector in
   `event_to_action_impact.yaml`, picks personas whose actions match the
   selector;
3. forces a life_generator episode containing that event
   (`episode_bridge.templates_for_event`), timeline-compressed to fit the
   horizon (`episode_bridge.scripted_events_from_path(compress=True)`);
4. forced events bypass the hazard roll **and** the lifecycle cancellation
   branch (`plan_lifecycle(force_occur=True)`), so the target event is
   guaranteed to reach OCCURRED while still passing state guards.

Every emitted trajectory is engineered to yield ≥1 post_occurred impact.

## Measured effect (20-persona pool, 12-year horizon)

| mode | trajectories | distinct post_occurred pairs | per trajectory |
| --- | --- | --- | --- |
| hazard | 20 | 18 | 0.90 |
| episode + coverage bias | 20 | 19 | 0.95 |
| **coverage-driven** | 44 | **122** | **2.77** |

The middle row shows that forcing events *without* action matching barely helps
— the binding constraint is the action side. Coverage-driven pairing lifts the
yield ~3×, and the resulting MCQ set has all four lifecycle contexts populated
(post_occurred / pre_occurred / cancelled / no_event) with a 66.7% majority
baseline instead of 81%.

## Usage

```bash
make coverage-trajectories                 # append coverage trajectories
# or directly:
python scripts/generate_coverage_trajectories.py \
  --personas data/personas/normalized/personas_ko_KR.jsonl \
  --locale ko_KR --horizon-years 12 \
  --output-dir data/generated/trajectories --seed 500 --max-per-pair 2
```

Coverage trajectory ids are prefixed `traj_cov_` so they flow through the
existing session / gold / item pipeline unchanged. Uncovered impact pairs
(no persona in the pool owns the action — e.g. business_expense_autopay needs a
self-employed persona) are printed; widen the persona pool with a larger
`--limit` in `normalize_personas.py` to close them.

## Note on realism

Timeline compression shortens inter-event gaps so a multi-decade episode fits a
shorter horizon; forced events skip cancellation. Both trade a little
life-course realism for guaranteed coverage of the diagnostic class. Mix
coverage trajectories with plain hazard/episode trajectories so the benchmark
retains naturally-sampled drift and cancellations alongside the engineered
post_occurred cases.

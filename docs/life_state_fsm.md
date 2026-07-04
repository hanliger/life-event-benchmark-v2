# Life-State FSM

## Why not reuse life_generator's sampler directly

`life_generator` samples *episode templates* at year granularity and has no
lifecycle notion — an event either happens or not. The benchmark needs
(a) monthly timing, (b) weak_signal/upcoming/cancelled stages, (c) hazard
modulation by evolving state, and (d) event params feeding memory deltas.
We reuse its event vocabulary and guard patterns but replace the sampler.

## Simulation loop (monthly ticks)

For each month:
1. Apply due lifecycle transitions (status history, LifeState effects on
   occurred, memory deltas, action impacts).
2. Sample at most one new event start:
   - guards: age guard, required/forbidden LifeState values, cooldown since
     the last instance of the same event, no concurrent duplicate, domain
     specials (e.g. child at school-entry age).
   - hazard: `base_rate_per_year / 12 × age_weight × state_modifier ×
     persona_modifier × global_scale` (clamped to 0.5/month).
3. Snapshot state/memory/actions on any transition.

This is intentionally NOT a Markov matrix: start probability depends on the
whole current state, age, cooldowns, active instances and persona.

## Lifecycle

```
inactive ──▶ weak_signal ──▶ upcoming ──▶ occurred
                 │               │
                 ▼               ▼
             cancelled       cancelled
```

Stage skips (`p_skip_weak_signal`, `p_skip_upcoming`) let sudden events
(family death, fraud, accident) jump straight to occurred. Cancel
probabilities create cancelled instances whose pending memory must be cleared.

## State constraints enforced

- childbirth/adoption: married + age guard (20–47)
- home sale: requires `home_owned`
- divorce/separation: requires `married`
- job change / leave / resignation: requires `employed`
- business closure: requires `self_employed`
- pension start: requires `retirement_prepared`
- return-to-work: requires unemployed/on_leave/student/homemaker
- child education entry: requires a child at entry age (7/13/16 ±1)
- rental contract renewal: renter states only (hazard-damped otherwise)

`scripts/audit_life_stage_constraints.py` replays every trajectory against
these guards; violations must be 0.

## Disclaimer

All rates/durations are heuristic plausibility constraints for generating
diverse consistent trajectories — not empirical transition probabilities.

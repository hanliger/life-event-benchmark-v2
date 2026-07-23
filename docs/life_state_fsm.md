# Life State FSM

`LifeState` is the hidden state used to decide which life events can happen.

## Fields

- `marital_status`
- `employment_status`
- `residence_status`
- `children_ages`
- `dependents_count`
- `lives_with_parents`
- `home_owned`
- `retirement_prepared`
- `pension_receiving`
- `in_education`

Derived guard fields:

- `has_children`
- `has_dependents`
- `can_add_child`: true only while adding one more child keeps child count below 5.
- `can_add_dependent`: true only while adding one more dependent keeps dependent count below 5.

## Guards

Life event templates in `configs/registries/life_events.yaml` use:

- age guards
- required state guards
- forbidden state guards
- cooldowns

Example: divorce requires a married state; home purchase should not occur for an already owning persona unless the template allows it.

Current hard invariants:

- Marriage cannot start from `married` or `separated`.
- Childbirth/adoption requires married state and must keep both child and dependent counts below 5.
- Family death requires at least one dependent.
- Self-education and study-abroad events cannot start while already `in_education`.

## Audit

```bash
python scripts/audit_life_stage_constraints.py \
  --trajectories-dir data/runs/<RUN_ID>/trajectories \
  --output-dir data/runs/<RUN_ID>/quality_reports
```

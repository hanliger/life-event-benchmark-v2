# Financial Memory Schema

`FinancialMemoryState` stores path-based cell histories. Values are never physically deleted; old values remain available for stale-memory distractors.

## Cell Status

- `current`: active value.
- `historical`: previous value kept for history.
- `stale`: value may be outdated.
- `needs_verification`: value requires user confirmation.
- `pending`: weak/upcoming evidence, not committed.
- `cancelled`: pending value cancelled.
- `unknown`: value may exist, but is unknown.
- `not_applicable`: value should not exist in the current state.

## unknown vs not_applicable

Use `unknown` when the field could apply:

- employed user, salary day not known yet
- married user, spouse account not known

Use `not_applicable` when the field should not apply:

- retired user, `employment.salary_day`
- owner, `housing.rent_amount`
- no children, `education.child_education_stage`

`current_value(path)` returns `None` for `historical`, `cancelled`, and `not_applicable`.

## Update Semantics

- `update/create`: appends a new `current` cell.
- `mark_stale`: marks latest cell stale.
- `needs_verification`: marks latest cell as needing confirmation.
- `set_pending`: creates a pending cell for weak/upcoming evidence.
- `clear_pending`: cancels pending evidence.
- `reactivate`: restores the latest historical cell.

The delta engine skips no-op updates and repeated stale/verification marks.

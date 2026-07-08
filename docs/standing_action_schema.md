# Standing Action Schema

`StandingAction` represents recurring financial behavior that may become stale after a life event.

## Examples

- `salary_linked_savings`
- `rent_autopay`
- `spouse_living_expense_transfer`
- `parent_support_transfer`
- `child_education_saving`
- `loan_repayment`
- `pension_contribution`
- `business_expense_autopay`

## Generation Rules

- Salary-linked savings only for `employment_status=employed`.
- Rent autopay only for `residence_status=wolse`.
- Spouse transfer only for married and cohabiting personas.
- Child education saving only when minor children exist.
- Loan repayment only when a loan exists.
- Pension contribution only when pension/IRP exists.
- Any linked memory path with `not_applicable` blocks the action.

## Impact Policy

Event impacts do not auto-execute high-risk financial changes.

- Funds-moving impacts set `must_not_execute=true`.
- Expected decision becomes `ask_confirmation` if needed.
- Impacted actions move to `validity_status=needs_review`.
- Actions already in `needs_review` are not repeatedly impacted.

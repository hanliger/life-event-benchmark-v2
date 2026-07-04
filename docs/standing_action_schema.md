# Standing Action Schema

Standing actions are first-class benchmark objects — recurring financial
automations whose validity depends on memory state.

Types (see `configs/registries/standing_action_schema.yaml`):
salary_linked_savings, rent_autopay, parent_support_transfer,
spouse_living_expense_transfer, child_support_transfer, loan_repayment,
child_education_saving, pension_contribution, business_expense_autopay.

## Instance fields

action_id, type, label, status (active|paused|pending|stale|cancelled|
historical), source_account, destination, amount, frequency, trigger_rule
(+trigger_day), funds_movement, risk, linked_memory_paths, validity_status
(valid|stale|needs_review), last_confirmed_at, history (audit trail).

## Consistency rules (initial generation)

- salary_linked_savings ⇐ employed with salary_day
- rent_autopay ⇐ wolse renter
- spouse_living_expense_transfer ⇐ married & cohabiting
- parent_support_transfer ⇐ non-child dependents
- child_education_saving ⇐ minor children
- loan_repayment ⇐ loan exists
- pension_contribution ⇐ pension/IRP exists
- business_expense_autopay ⇐ self-employed
- every action links to valid memory paths

## Impacts

`event_to_action_impact.yaml` maps event×status to impacts selected by action
type or linked memory path. An impact records impact_type, expected_decision,
risk and flips the action's validity_status to needs_review — the material for
stale-action distractors. Funds-moving actions are never auto-executed
(`must_not_execute=True`).

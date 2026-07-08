# Failure Modes

Known failure modes and current mitigation.

## Dialogue Output

- Invalid JSON: repair once.
- Missing `speaker`/`text`: repair once.
- Wrong speaker value: repair once.
- Cue index out of range: repair once.
- Repair still invalid: raise `LLMOutputValidationError` with trajectory/session id.

## Dialogue Quality

- Speaker alternation violation.
- Cue annotation points to assistant turn.
- Forbidden life-event label appears in visible text.
- Assistant summarizes hidden event too directly.

Run:

```bash
make validate-dialogues
```

## State Conflicts

- Non-employed persona has salary action.
- Owner has rent action.
- No-loan persona has loan repayment.
- `unknown` used where `not_applicable` is correct.

Run:

```bash
make audit
```

## Trajectory Noise

- `old_value == new_value` update.
- Repeated stale/needs_verification on the same latest cell.
- Repeated action impacts for already reviewed actions.

These are filtered in the delta/action engines.

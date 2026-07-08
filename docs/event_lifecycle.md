# Event Lifecycle

Life events move through a small finite-state lifecycle.

## States

- `weak_signal`: vague evidence; no committed state update.
- `upcoming`: planned or expected event; no committed state update.
- `occurred`: event happened; memory/action updates are allowed.
- `cancelled`: pending evidence cancelled.
- `no_event`: no event.

## Memory Policy

- `weak_signal` and `upcoming`: `set_pending`, `needs_verification`, or `no_update`.
- `occurred`: `create`, `update`, `mark_stale`, `archive`, `needs_verification`, `reactivate`.
- `cancelled`: `clear_pending` or `no_update`.

The delta engine enforces allowed operations by lifecycle state.

## Check

```bash
make test
make audit
```

# History Filter

The history filter checks whether an item can be answered from a restricted dialogue context.

## Modes

- `single_session`: only the target session context.
- full prefix modes may use more session history when configured.

## Validators

`.env`:

```env
HISTORY_FILTER_VALIDATORS=mock:mock-validator
```

Use mock for fast local checks. Use real providers only when explicitly evaluating model answerability.

## Command

```bash
make history-filter EXECUTE=0
```

With real validators:

```env
HISTORY_FILTER_VALIDATORS=anthropic:claude-sonnet-5
```

```bash
make history-filter EXECUTE=1
```

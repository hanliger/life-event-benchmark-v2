# Dialogue Generation Strategy

Dialogue generation turns hidden trajectory plans into visible banking conversations.

## Modes

- `mock`: deterministic template dialogue; no API key.
- `dry_run`: writes prompts to `data/raw_model_outputs/dialogue/`.
- `llm`: calls OpenAI or Anthropic through `LLMClient`.

## Execute

Mock:

```bash
make dialogue-smoke EXECUTE=0 NUM_TRAJ=2
```

LLM:

```bash
make dialogue-smoke EXECUTE=1 NUM_TRAJ=2
```

Direct script:

```bash
python scripts/generate_dialogue_sessions.py \
  --trajectories-dir data/generated/trajectories \
  --output-dir data/generated/sessions \
  --max-trajectories 2 \
  --execute \
  --continue-on-error
```

## LLM Config

Read from `.env`:

```env
DEFAULT_LLM_PROVIDER=anthropic
DEFAULT_GENERATION_MODEL=claude-sonnet-5
LLM_TEMPERATURE=0.7
LLM_MAX_TOKENS=4096
```

Provider selects the SDK/API. Model selects the model inside that provider.

## Repair

The generator validates LLM output before building `Session`.

Repair is attempted once for:

- invalid JSON
- missing `turns`
- missing `speaker` or `text`
- invalid speaker value
- cue index out of range
- invalid `quality_self_check`

Raw files:

```text
data/raw_model_outputs/dialogue/<trajectory>_<session>.txt
data/raw_model_outputs/dialogue/<trajectory>_<session>_repair.txt
```

## Checkpointing

Sessions are written one by one to JSONL. With `--continue-on-error`, failed sessions are logged to:

```text
data/generated/sessions/errors_<trajectory_id>.jsonl
```

Successful sessions generated before a failure are preserved.

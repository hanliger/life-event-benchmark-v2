# Locale Extension Guide

Locale data lives under `configs/locales/`.

## Add a Locale

1. Copy `configs/locales/ko_KR.yaml`.
2. Rename it to the target locale.
3. Update banking terms, amount pools, employer pools, address pools, salary days, and cue wording.
4. Run a small mock pipeline.

```bash
make pipeline-smoke EXECUTE=0 LIMIT=2 NUM_TRAJ=2
```

## Check

- persona normalization still fills required fields
- initial memory/action generation has locale pools
- dialogue prompts still produce natural text
- validation and audit reports pass

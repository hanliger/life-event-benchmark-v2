# Coverage Generation

Coverage generation forces rare event/action pairings that random sampling may miss.

## Command

```bash
make coverage-trajectories
```

This uses `life_generator` episode injection and action-matched personas to create more post-occurred action-impact examples.

## Use When

- random trajectories rarely produce a target event
- action-impact benchmark items are underrepresented
- a specific event/action combination needs regression coverage

## Validate

```bash
make validate-dialogues
make audit
```

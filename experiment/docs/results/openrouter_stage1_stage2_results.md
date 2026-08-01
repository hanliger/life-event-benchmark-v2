# OpenRouter Stage 1/2 results

## Scope

- Four OpenRouter models
- 20 trajectories and 20 checkpoints per model-stage run
- 400 predictions per run, 3,200 predictions across eight completed runs
- Full-context Stage 1 occurred-event/evidence-pair task and Stage 2 memory reconstruction task

## Canonical runs

| Model | Provider | Quantization | Stage 1 run | Stage 2 run |
|---|---|---|---|---|
| Llama 4 Maverick | Parasail | FP8 | `stage1/0801_0514` | `stage2_2/0801_0514` |
| GPT OSS 120B | Cerebras | FP16 | `stage1/0801_0514_02` | `stage2_2/0801_0514_02` |
| Qwen 3.5 122B A10B | Novita | BF16 | `stage1/0801_0514_03` | `stage2_2/0801_0514_03` |
| Qwen 3.6 35B A3B | CoreWeave | FP8 | `stage1/0801_0514_04` | `stage2_2/0801_0514_04` |

## Stage 1

The primary score is strict occurred-event/evidence-pair F1.

| Model | Strict pair F1 | Parse errors |
|---|---:|---:|
| Qwen 3.5 122B A10B | 0.6413 | 0 |
| Qwen 3.6 35B A3B | 0.4735 | 0 |
| Llama 4 Maverick | 0.3574 | 1 |
| GPT OSS 120B | 0.1241 | 0 |

## Stage 2

Update-sensitive metrics should be interpreted ahead of the broad snapshot score.

| Model | Correct-change F1 | Path-macro F1 | Event-macro update accuracy | Event-exact update accuracy | Retention |
|---|---:|---:|---:|---:|---:|
| Qwen 3.5 122B A10B | 0.4857 | 0.5629 | 0.6363 | 0.5230 | 0.6514 |
| Qwen 3.6 35B A3B | 0.4632 | 0.5047 | 0.5328 | 0.4181 | 0.5585 |
| Llama 4 Maverick | 0.3541 | 0.3995 | 0.3847 | 0.2912 | 0.3966 |
| GPT OSS 120B | 0.1073 | 0.0456 | 0.0628 | 0.0337 | 0.0680 |

## Validation

- All eight run manifests finished in `GENERATED` state.
- All 3,200 expected model-stage-trajectory-checkpoint cells are present.
- There are no missing or duplicate canonical cells.
- Provider locks disabled fallback routing and preserve provider and quantization metadata.
- Prompt audits passed for every run.
- The selected artifacts contain no API keys.

## Caveats

- These numbers are the metrics frozen with the canonical `0801_0514*` run artifacts. Later reporting-code changes must not silently overwrite them; any recalculation should be published as a separate report with its own policy and source hash.
- Parse and schema failures remain model failures in the reported scores.
- Historical provider failures can remain in a manifest failure field after successful resume; the final manifest status and canonical-cell completeness determine completion.
- Llama 4 Maverick was configured as non-reasoning. The other three OpenRouter methods used low reasoning effort.
- Full raw responses, rendered prompts, retrieval snapshots, and execution logs are retained outside this Git result bundle to avoid expanding repository history. They are required only for response-level forensic audit or future parser-based rescoring.

Each run directory in this branch contains the immutable plan, provider lock, final manifest, prompt audits, metrics, and generated report. Those files are the authoritative source for detailed checkpoint, cost, latency, and reliability values.

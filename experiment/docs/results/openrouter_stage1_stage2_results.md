# OpenRouter Stage 1/2 결과

## 범위

- OpenRouter open-weight 모델 4종
- 모델·stage별 20 trajectories × 20 checkpoints = 400 predictions
- Stage 1/2 합계 3,200 predictions
- Stage 1 `Strict Pair F1`·`Exact Pair-Set Match`, Stage 2 `GCA@15`·`Retention-after-update` 적용

## Canonical run과 provider lock

| Model | Locked provider | Quantization | Reasoning | Stage 1 run | Stage 2 run |
|---|---|---|---|---|---|
| Llama 4 Maverick | Parasail | FP8 | none | `stage1/0801_0514` | `stage2_2/0801_0514` |
| GPT-OSS 120B | Cerebras | FP16 | low | `stage1/0801_0514_02` | `stage2_2/0801_0514_02` |
| Qwen 3.5 122B A10B | Novita | BF16 | low | `stage1/0801_0514_03` | `stage2_2/0801_0514_03` |
| Qwen 3.6 35B A3B | CoreWeave | FP8 | low | `stage1/0801_0514_04` | `stage2_2/0801_0514_04` |

Provider fallback은 각 run의 `provider_lock.json`으로 비활성화되어 있다.

## Stage 1

| Model | Strict Pair F1 [95% CI] | Exact Pair-Set | Early → Middle → Late | Schema Valid |
|---|---:|---:|---:|---:|
| Qwen 3.5 122B A10B | 64.13 [57.97, 69.71] | 9.25 | 67.85 → 66.60 → 58.46 | 100.00 |
| Qwen 3.6 35B A3B | 47.35 [42.48, 51.85] | 6.50 | 56.32 → 47.81 → 39.18 | 100.00 |
| Llama 4 Maverick | 35.74 [30.94, 40.25] | 5.00 | 50.05 → 32.50 → 26.70 | 44.50 |
| GPT-OSS 120B | 12.41 [8.38, 16.74] | 1.75 | 18.49 → 10.94 → 8.66 | 100.00 |

## Stage 2

| Model | GCA@15 [95% CI] | Retention | Early → Middle → Late | Final State | Evidence Hit | Schema Valid |
|---|---:|---:|---:|---:|---:|---:|
| Qwen 3.5 122B A10B | 45.19 [43.60, 46.86] | 65.14 | 44.41 → 46.56 → 44.39 | 68.14 | 55.20 | 99.00 |
| Qwen 3.6 35B A3B | 43.53 [42.16, 44.92] | 55.85 | 44.46 → 44.36 → 42.28 | 69.93 | 48.45 | 99.75 |
| Llama 4 Maverick | 32.11 [29.83, 34.38] | 39.66 | 36.10 → 33.37 → 28.92 | 67.15 | 37.61 | 96.25 |
| GPT-OSS 120B | 24.87 [22.31, 27.31] | 6.80 | 21.57 → 24.86 → 26.61 | 67.40 | 3.49 | 100.00 |

Qwen 3.5 122B A10B가 두 stage에서 OpenRouter 그룹 최고 성능을 기록했다. Stage 2에서는 GCA@15 45.19로 전체 11개 모델 중 3위다. GPT-OSS 120B의 Final State Accuracy는 67.40이지만 GCA@15 24.87, Retention 6.80, Evidence Hit 3.49로 update 반영과 근거 회상이 제한적이다.

## Artifact 배치

Git에 보존하는 canonical run artifact:

- `experiment/runs/<stage>/<run-id>/immutable_plan.json`
- `provider_lock.json`, `run_manifest.json`, prompt audit
- `metrics/`, `report/`

로컬 raw artifact:

- canonical answer와 manifest: `experiment/runs/<stage>/<run-id>/raw/<method-id>/`
- 문항별 해석 artifact: Stage 1 `answer_pairs/`, Stage 2 `state_pairs/`
- rendered prompt, retry/attempt 이력, supervisor log는 각 run 디렉터리 내부
- smoke·중단·retry run도 `experiment/runs/stage2_2/` 아래에 보존하지만 결과 집계 입력에는 포함하지 않음

모든 canonical run은 trajectory JSONL 20개와 각 파일당 checkpoint row 20개를 갖는다. 결과 재생성은 `build_stage1_pair_report.py`, `build_stage2_gca15_report.py`, `build_all_model_cross_stage_report.py` 순서로 수행한다. 전체 11-model 결과와 checkpoint 값은 `stage1_pair_results.csv`, `stage2_gca15_results.csv`, `stage1_stage2_all_models.csv`에 있다.

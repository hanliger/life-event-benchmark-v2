# Stage 1 Nine-Method Runbook

Stage 1은 각 15-session target window 안에서 가장 최근에 `occurred`가 된 event의
`event_id`를 맞힌다. Grid는 20 trajectory × 20 window checkpoint = 400 item이며
9개 method로 3,600 prediction이다. Plan·audit·execute·resume·report 단계와
provider lock, attempt 보존, resume 규칙은 Stage 2.2와 동일한
`run_harness`를 공유한다.

전제 조건은 `download-data`와 `prepare-data`, `build-prefix-gold`,
`build-canonical-items`가 끝나 `canonical_items/stage1_event_identification.jsonl`이
존재하는 상태다.

## 1. Plan

```bash
export OPENROUTER_API_KEY=...

./experiment/scripts/paid/run_stage1.sh plan \
  --methods all \
  --trajectories all \
  --checkpoint-start 15 \
  --checkpoint-end 300 \
  --checkpoint-stride 15 \
  --model-workers 9 \
  --trajectory-workers 20 \
  --checkpoint-workers 20 \
  --max-in-flight 60 \
  --anthropic-max-in-flight 20 \
  --openrouter-max-in-flight 40 \
  --request-timeout-seconds 300 \
  --provider-retries 0 \
  --parse-retries 1 \
  --budget-cap-usd 200 \
  --estimated-usd 180
```

`plan`은 KST `MMDD_HHMM` run directory를 `experiment/runs/stage1/` 아래에 만들고
frozen grid를 `immutable_plan.json`에 고정한다. 같은 분에 중복되면 `_02`, `_03`
suffix를 사용한다.

Retrieval 설정은 CLI flag가 아니라 `configs/experiment.yaml`의
`stage1_event_identification` block이며, `stage1_contract()`가 code의 frozen
상수와 일치하는지 plan 시점에 검사한다. Stage 1은 질문 문장을 그대로 단일
query로 사용하고 `top_k=10`을 쓴다. Stage 2.2의 4-group retrieval은 Stage 1에
적용하지 않는다.

OpenRouter method가 포함되면 Stage 2.2와 같은 규칙으로 ZDR endpoint 중 reported
throughput이 가장 높은 provider를 method마다 선택하고 `provider_lock.json`에
저장한다. Qwen3.6은 FP8 endpoint만 허용한다. Provider를 직접 고정하려면
`--provider-lock-file experiment/configs/my_openrouter_provider_lock.json`을
사용하며 형식은 `openrouter_provider_lock.example.json`을 따른다.

## 2. Inspect and audit prompts

```bash
./experiment/scripts/paid/run_stage1.sh show-prompt \
  --method bm25_claude_opus_4_8 \
  --trajectory traj_001 \
  --checkpoint 15

./experiment/scripts/paid/run_stage1.sh audit-prompt \
  --run-dir experiment/runs/stage1/<run-id>
```

`audit-prompt`는 method × (최초, 최종) checkpoint에서 future session, Gold field,
후보 목록 축소를 검사한다. 통과하지 않으면 paid execution은 차단된다. 검사
항목과 disclosure는 `docs/stage1_prompt_leakage_audit.md`에 있고 run directory로
복사된다.

## 3. Execute

```bash
./experiment/scripts/paid/run_stage1.sh execute \
  --run-dir experiment/runs/stage1/<run-id> \
  --execute-paid \
  --approval I_APPROVE_STAGE1_PAID
```

API credential은 explicit approval 검증 후 `experiment/.env`(없으면 repo root
`.env`)에서 읽는다. Provider SDK retry는 0이며 parse 또는 schema failure에만 1회
retry한다. 모든 attempt는 보존한다.

## 4. Resume

```bash
./experiment/scripts/paid/run_stage1.sh resume \
  --run-dir experiment/runs/stage1/<run-id> \
  --execute-paid \
  --approval I_APPROVE_STAGE1_PAID
```

COMPLETE method × trajectory artifact는 건너뛴다. 실패 attempt는 덮어쓰지 않고
다음 `attempt_XX.jsonl`에 기록한다. 전체 frozen grid가 중복 없이 완성되어야 run
status가 `GENERATED`가 된다.

## 5. Report

```bash
./experiment/scripts/paid/run_stage1.sh report \
  --run-dir experiment/runs/stage1/<run-id>
```

Primary metric은 trajectory-macro accuracy다. checkpoint별로 채점한 뒤 trajectory
내부를 평균하고 trajectory를 동일 가중으로 평균한다. 불확실성은 trajectory
bootstrap 95% CI이며 모든 method pair의 paired delta도 함께 계산된다.

| 산출물 | 내용 |
|---|---|
| `metrics/main_results.csv` | method × stage score, CI, parse error |
| `metrics/paired_method_deltas.csv` | 모든 method pair의 paired trajectory bootstrap delta |
| `metrics/checkpoint_metrics.csv` | prediction/gold event_id, correct, window index |
| `metrics/trajectory_metrics.csv` | method × trajectory accuracy |
| `metrics/parse_reliability.csv` | first-attempt parse/schema failure, retry count |
| `metrics/retrieval_recall.csv` | target window coverage, retrieved evidence count |
| `metrics/cost_latency.csv` | token, latency, estimated cost, routed provider |
| `answer_pairs/<method>/<traj>/cp_XXX.json` | 문항별 prediction/gold/evidence/attempt |
| `report/figures/checkpoint_event_identification_accuracy.svg` | checkpoint accuracy curve |
| `report/figures/method_trajectory_accuracy_heatmap.svg` | method × trajectory heatmap |

`retrieval_recall.csv`는 Gold-independent 측정이다. target window session이
context에 실렸는지만 보므로 Full Context는 구조상 1.0이고, 이 값은 retrieval
arm들을 서로 비교하는 데 쓴다.

## Selection syntax

`--methods`와 `--trajectories`는 `all` 또는 comma-separated IDs를 받는다.

```bash
--methods fc_claude_opus_4_8,bm25_claude_opus_4_8
--trajectories traj_001,traj_010
```

Checkpoint query는 서로 독립적이다. Full Context는 fresh method/client와 독립
prefix를 사용한다. BM25, Dense, Mem0, Letta는 checkpoint까지 순차 ingest한
immutable snapshot을 clone해 병렬 query하며 이전 prediction을 다음 query에
전달하지 않는다.

# Stage 1 Nine-Method Runbook

Stage 1은 15세션씩 늘어나는 누적 prefix에서 지금까지 실제 발생한 모든
Life Event와 각 발생을 처음 확정하는 session의 pair를 복원한다. 이 실행은
direct-API 3-model 비교 뒤에 이어지는 독립적인 grid다. 20 trajectory ×
20 checkpoint = 400 item을 9개 method로 평가해 3,600 prediction을 만든다.

`run_stage1.sh`는 기존 9-method 진입점을 유지한다. Direct-API 3-model 실행은
`run_stage1_api3.sh`와 `runs/stage1_api3/`를 사용하므로 두 plan, provider lock,
attempt, resume, report가 서로 섞이지 않는다.

두 profile은 같은 Stage 1 task, prompt, item, corpus를 사용한다. 코퍼스는
Stage 2.2와도 공유하는 `dialogues_no_prospective` + `gold_no_prospective`다.

```bash
PY=experiment/.venv/bin/python
export PYTHONPATH=.:src:experiment/src
$PY -m financial_memory_experiment.cli download-stage2-2-data
$PY -m financial_memory_experiment.cli prepare-stage2-2
$PY -m financial_memory_experiment.cli build-stage1-items
```

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

`plan`은 `experiment/runs/stage1/<run-id>/immutable_plan.json`에
`execution_profile: method9`와 9-method grid를 고정한다. Retrieval은 질문
문장을 단일 query로 사용하고 `top_k=10`이다.

OpenRouter method가 포함되면 ZDR endpoint 중 reported throughput이 가장 높은
provider를 method별로 골라 `provider_lock.json`에 저장한다. Qwen3.6은 FP8
endpoint만 허용한다. 직접 고정할 때는
`--provider-lock-file experiment/configs/my_openrouter_provider_lock.json`을
사용한다.

## 2. Inspect and audit prompts

```bash
./experiment/scripts/paid/run_stage1.sh show-prompt \
  --method bm25_claude_opus_4_8 \
  --trajectory traj_001 \
  --checkpoint 15

./experiment/scripts/paid/run_stage1.sh audit-prompt \
  --run-dir experiment/runs/stage1/<run-id>
```

`audit-prompt`는 method × (최초, 최종) checkpoint에서 future session, Gold
field, 후보 목록 축소를 검사한다. 실패하면 paid execution을 차단한다.

## 3. Execute and resume

```bash
./experiment/scripts/paid/run_stage1.sh execute \
  --run-dir experiment/runs/stage1/<run-id> \
  --execute-paid \
  --approval I_APPROVE_STAGE1_PAID

./experiment/scripts/paid/run_stage1.sh resume \
  --run-dir experiment/runs/stage1/<run-id> \
  --execute-paid \
  --approval I_APPROVE_STAGE1_PAID
```

Provider SDK retry는 0이고 parse/schema failure만 1회 재시도한다. COMPLETE
method × trajectory artifact는 건너뛰고 실패 attempt는 다음
`attempt_XX.jsonl`에 보존한다.

## 4. Report

```bash
./experiment/scripts/paid/run_stage1.sh report \
  --run-dir experiment/runs/stage1/<run-id>
```

Primary metric은 `strict_occurred_event_evidence_f1`이다. 각 checkpoint의 exact
multiset F1을 동일 가중하고 trajectory bootstrap 95% CI와 모든 method pair의
paired delta를 함께 계산한다. `exact_pair_multiset_match`는 누적 pair multiset
전체가 완전히 일치한 checkpoint 비율이며 trajectory-bootstrap CI와 checkpoint
curve를 함께 보고한다.

| 산출물 | 내용 |
|---|---|
| `metrics/main_results.csv` | method × stage score, CI, parse error |
| `metrics/paired_method_deltas.csv` | paired trajectory bootstrap delta |
| `metrics/checkpoint_metrics.csv` | checkpoint별 Pair F1과 Exact Pair-Set |
| `metrics/trajectory_metrics.csv` | method × trajectory Pair F1과 Exact Pair-Set |
| `metrics/parse_reliability.csv` | parse/schema failure와 retry |
| `metrics/retrieval_recall.csv` | visible-prefix context coverage |
| `metrics/cost_latency.csv` | token, latency, estimated cost, provider |
| `answer_pairs/<method>/<traj>/cp_XXX.json` | prediction/gold/evidence/attempt |

## Selection syntax

```bash
--methods fc_claude_opus_4_8,bm25_claude_opus_4_8
--trajectories traj_001,traj_010
```

Checkpoint query는 서로 독립적이다. Full Context는 독립 prefix를 사용한다.
BM25, Dense, Mem0, Letta는 checkpoint까지 순차 ingest한 immutable snapshot을
clone해 병렬 query하며 이전 prediction을 다음 query에 전달하지 않는다.

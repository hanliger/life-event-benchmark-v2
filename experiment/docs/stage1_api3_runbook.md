# Stage 1 Direct-API Three-Model Runbook

Stage 1은 15세션씩 늘어나는 누적 prefix에서 지금까지 실제 발생한 모든
Life Event와 각 발생을 처음 확정하는 session의 pair를 복원한다. Grid는
20 trajectory × 20 checkpoint = 400 item이며 GPT‑5.6 Sol, Claude Opus 4.8,
Gemini 3.1 Pro의 Full Context 3개 모델로 1,200 prediction이다.
Plan·audit·execute·resume·report 단계와 provider lock, attempt 보존, resume
규칙은 Stage 2.2와 동일한 `run_harness`를 공유한다.
이 profile을 먼저 완료한 뒤
[`stage1_9_method_runbook.md`](stage1_9_method_runbook.md)의 독립 plan을
실행한다.

Stage 1은 Stage 2.2와 **같은 코퍼스**(`dialogues_no_prospective` +
`gold_no_prospective`)를 쓴다. 코퍼스는 한 번만 준비하면 두 stage가 공유한다.

```bash
PY=experiment/.venv/bin/python
export PYTHONPATH=.:src:experiment/src
$PY -m financial_memory_experiment.cli download-stage2-2-data
$PY -m financial_memory_experiment.cli prepare-stage2-2
$PY -m financial_memory_experiment.cli build-stage1-items
```

`build-stage1-items`는 그 prepared tree에
`canonical_items/stage1_occurred_event_evidence_pairs.jsonl`(400 item)을 만들고 개수와
trajectory 수를 검증한다. 생성 파이프라인의 `prepare-data`/`build-canonical-items`는
Stage 1 실행에 필요하지 않다.

## 1. Plan

```bash
./experiment/scripts/paid/run_stage1_api3.sh plan \
  --methods all \
  --trajectories all \
  --checkpoint-start 15 \
  --checkpoint-end 300 \
  --checkpoint-stride 15 \
  --model-workers 3 \
  --trajectory-workers 20 \
  --checkpoint-workers 20 \
  --max-in-flight 60 \
  --anthropic-max-in-flight 20 \
  --openai-max-in-flight 20 \
  --google-max-in-flight 20 \
  --openrouter-max-in-flight 1 \
  --request-timeout-seconds 600 \
  --provider-retries 0 \
  --parse-retries 0 \
  --budget-cap-usd 80 \
  --estimated-usd 60
```

`plan`은 KST `MMDD_HHMM` run directory를 `experiment/runs/stage1_api3/` 아래에 만들고
frozen grid를 `immutable_plan.json`에 고정한다. 같은 분에 중복되면 `_02`, `_03`
suffix를 사용한다.

모델·timeout·retry 설정은 `configs/experiment.yaml`의
`stage1_occurred_event_evidence_pairs` block이며, `stage1_contract()`가 frozen
상수와 일치하는지 plan 시점에 검사한다. API3 profile에는 OpenRouter,
retrieval, memory-agent method가 없다. 공통 harness의 `provider_lock.json`은
`NOT_APPLICABLE`로 기록된다.

## 2. Inspect and audit prompts

```bash
./experiment/scripts/paid/run_stage1_api3.sh show-prompt \
  --method fc_gpt_5_6_sol \
  --trajectory traj_001 \
  --checkpoint 15

./experiment/scripts/paid/run_stage1_api3.sh audit-prompt \
  --run-dir experiment/runs/stage1_api3/<run-id>
```

`audit-prompt`는 method × (최초, 최종) checkpoint에서 future session, Gold field,
후보 목록 축소를 검사한다. 통과하지 않으면 paid execution은 차단된다. 검사
항목과 disclosure는 `docs/stage1_prompt_leakage_audit.md`에 있고 run directory로
복사된다.

## 3. Execute

```bash
RUN_DIR=experiment/runs/stage1_api3/<run-id>
mkdir -p "$RUN_DIR/logs"
nohup ./experiment/scripts/paid/run_stage1_api3.sh execute \
  --run-dir "$RUN_DIR" \
  --execute-paid --approval I_APPROVE_STAGE1_PAID \
  > "$RUN_DIR/logs/execute.log" 2>&1 < /dev/null &
echo $! > "$RUN_DIR/execute.pid"
```

API credential은 explicit approval 검증 후 `experiment/.env`(없으면 repo root
`.env`)에서 읽는다. Provider SDK retry와 parse retry는 모두 0이며 모든 attempt는
보존한다. 실행 로그는 checkpoint cache hit와 method × trajectory 시작·완료·실패를
JSON line으로 기록한다. `execution.lock`은 같은 run의 중복 실행과 이중 과금을
차단한다.

## 4. Resume

```bash
./experiment/scripts/paid/run_stage1_api3.sh resume \
  --run-dir experiment/runs/stage1_api3/<run-id> \
  --execute-paid \
  --approval I_APPROVE_STAGE1_PAID
```

COMPLETE method × trajectory artifact는 건너뛴다. 실패 attempt는 덮어쓰지 않고
다음 `attempt_XX.jsonl`에 기록한다. 전체 frozen grid가 중복 없이 완성되어야 run
status가 `GENERATED`가 된다. RUNNING/FAILED attempt에 이미 보존된 유효 checkpoint
row도 새 attempt에 복사하고, 완료되지 않은 checkpoint만 다시 호출한다.

## 5. Report

```bash
./experiment/scripts/paid/run_stage1_api3.sh report \
  --run-dir experiment/runs/stage1_api3/<run-id>
```

Primary metric은 `strict_occurred_event_evidence_f1`이다. 각 checkpoint에서
exact multiset F1을 계산하고 checkpoint를 동일 가중한다. 불확실성은 trajectory
bootstrap 95% CI이며 모든 method pair의 paired delta도 함께 계산된다. 엄격한
보조 지표 `exact_pair_multiset_match`는 누적 pair 전체가 완전히 일치한 checkpoint
비율이며 동일한 trajectory-bootstrap CI와 checkpoint curve를 보고한다.

| 산출물 | 내용 |
|---|---|
| `metrics/main_results.csv` | method × stage score, CI, parse error |
| `metrics/paired_method_deltas.csv` | 모든 method pair의 paired trajectory bootstrap delta |
| `metrics/checkpoint_metrics.csv` | checkpoint별 strict pair F1과 Exact Pair-Set |
| `metrics/trajectory_metrics.csv` | method × trajectory F1과 Exact Pair-Set |
| `metrics/parse_reliability.csv` | first-attempt parse/schema failure, retry count |
| `metrics/retrieval_recall.csv` | visible-prefix context coverage |
| `metrics/cost_latency.csv` | token, latency, estimated cost, routed provider |
| `answer_pairs/<method>/<traj>/cp_XXX.json` | 문항별 prediction/gold/evidence/attempt |
| `report/figures/checkpoint_strict_pair_f1.svg` | checkpoint strict pair F1 |
| `report/figures/checkpoint_exact_pair_set_match.svg` | checkpoint 전체 pair 복원율 |
| `report/figures/method_trajectory_strict_pair_f1_heatmap.svg` | method × trajectory heatmap |

`retrieval_recall.csv`는 Gold-independent context coverage audit이다. 세 모델
모두 Full Context이므로 구조상 1.0이어야 한다.

## Selection syntax

`--methods`와 `--trajectories`는 `all` 또는 comma-separated IDs를 받는다.

```bash
--methods fc_gpt_5_6_sol,fc_claude_opus_4_8
--trajectories traj_001,traj_010
```

Checkpoint query는 서로 독립적이다. Full Context는 fresh method/client와 독립
prefix를 사용하며 이전 prediction을 다음 query에 전달하지 않는다.

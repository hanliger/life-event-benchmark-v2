# Financial Memory Benchmark Experiment

Stage 1 life-event 탐지, Stage 2 과거 상태 회상, Stage 3 two-checkpoint
multi-hop 추론, 5-arm masking ablation을 7개 방법으로 평가하는 실험 전용
패키지다. 데이터 생성 코드와 분리되어 있으며 모든 일반 작업은 Bash 진입점
`scripts/pipeline.sh`를 사용한다.

## 현재 상태

| 항목 | 상태 |
|---|---|
| HF 데이터 다운로드·전처리 | 완료 및 재실행 가능 |
| canonical/masking 문항 검증 | 완료 |
| Stage 3 upstream audit | 123/123 통과 |
| 7방법 offline smoke | Stage 1/2/Stage 3/masking 통과 |
| 7방법 canonical paid smoke | Stage 1/2 각 1문항 통과 |
| Stage 3 paid smoke | 본실험 전 실행 필요 |
| masking paid smoke | 본실험 전 실행 필요 |
| 논문용 full run | 미실행 |

현재 smoke 점수는 배선 검증용이며 논문 성능 결과가 아니다. 논문 결과는 7개 방법이
동일한 전체 item set을 `COMPLETE`로 끝내고 strict 집계의
`completeness.reporting_ready=true`를 만족한 경우에만 사용한다.

## 문서 역할

- [docs/protocol.md](docs/protocol.md): 연구 질문, 방법, 공정 비교, 지표
- [docs/architecture.md](docs/architecture.md): 데이터와 실행 흐름
- [docs/implementation_review.md](docs/implementation_review.md): 실행 전 readiness
- [docs/smoke_test.md](docs/smoke_test.md): 완료된 smoke와 비용 원장
- [docs/results_template.md](docs/results_template.md): full run 후 채울 결과 문서

아래 순서가 실험의 유일한 실행 runbook이다.

## 1. 저장소와 환경 준비

작업 트리가 깨끗할 때만 저장소 루트에서 동기화한다.

```bash
git switch qa-on-main
git pull --ff-only origin qa-on-main
cd experiment/financial_memory_benchmark
```

로컬 변경이 있으면 먼저 `git status`로 확인하고 pull 전에 보존한다.

```bash
./scripts/setup.sh
./scripts/install_all.sh
```

`setup.sh`는 기본 가상환경을 만들고, `install_all.sh`는 7개 방법의 고정된
dependency를 설치한다. 설치는 모델 API를 호출하지 않는다.

예상 결과:

```text
.venv/
requirements.lock
```

## 2. HF 데이터 다운로드

네트워크에서 dataset snapshot을 받는다.

```bash
./scripts/pipeline.sh download-data
./scripts/pipeline.sh validate-raw-data
```

이미 로컬에 받은 dataset 저장소가 있으면 네트워크 없이 가져올 수 있다.

```bash
./scripts/pipeline.sh download-data \
  --source-dir /absolute/path/to/life-event-benchmark-v2-dialogues
./scripts/pipeline.sh validate-raw-data
```

예상 결과:

```text
data/raw/active_manifest.json
data/raw/hf/hangyeul-lee--life-event-benchmark-v2-dialogues/<revision>/
├── dialogues/
├── gold/
├── counterfactual_fillers/
└── counterfactual_filler_plans/
```

`active_manifest.json`에는 HF commit SHA 또는 local content hash가 기록된다.

## 3. 평가 입력과 문항 생성

전체 과정을 순서대로 실행한다.

```bash
./scripts/pipeline.sh prepare-all
```

이미 raw data가 있거나 오류 단계를 분리해야 한다면 다음 명령을 사용한다.

```bash
./scripts/pipeline.sh prepare-data
./scripts/pipeline.sh build-prefix-gold
./scripts/pipeline.sh build-canonical-items
./scripts/pipeline.sh build-masking-items
./scripts/pipeline.sh validate-prepared-data
```

기대 개수:

| 산출물 | 개수 |
|---|---:|
| trajectory | 20 |
| session | 6,000 |
| Stage 1 | 400 |
| Stage 2 | 4,200 |
| Stage 3 | 123 |
| masking event | 451 |
| masking arm | 5 |
| masking case | 2,255 |
| masking question | 4,510 |

예상 결과:

```text
data/prepared/active_manifest.json
data/prepared/<data-hash>/
├── sessions_joined/
├── sessions_answer_free/
├── initial_state_s000/
├── prefix_gold/
├── canonical_items/
├── masking/
└── masking_items/
```

모델 입력에는 `sessions_answer_free`만 사용한다. `sessions_joined`는 gold 생성과
문항 검증 전용이다.

## 4. 과금 없는 검증

```bash
./scripts/pipeline.sh verify-offline
```

이 명령은 unit/contract test와 전체 prepared-data dry run을 수행한다. 일반
`pipeline.sh`는 API key를 제거하고 `FIN_MEMORY_DISABLE_PAID_APIS=1`을 강제하므로
provider 호출을 만들 수 없다.

통과 조건:

- 모든 test PASS
- 데이터 기대 개수 일치
- future leakage와 gold-field leakage 없음
- canonical/masking mock 경로 완료
- Stage 3 upstream provenance audit 통과
- `runs/offline_dry_run.json` 생성

Mock 정확도는 논문 결과로 사용하지 않는다.

## 5. 유료 실행 전 고정 사항

full plan을 만들기 전에 다음을 확정한다.

- `configs/experiment.yaml`의 모델 ID, embedding 차원, main `k=10`
- Dense/Mem0/Letta embedding 차원 정책
- prepared data hash와 prompt/config/code 상태
- provider별 예상비용
- `.env`의 `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`

현재 공정성 권고는 Dense/Mem0/Letta를 768차원으로 통일하는 것이다. 이 설정을
바꾸면 새 immutable plan과 최소 smoke가 필요하다.

Letta가 포함된 실행에서만 self-hosted Docker 서버가 필요하다.

```bash
./scripts/paid/letta_up.sh --approval I_APPROVE_LETTA_DOCKER
```

health check가 실패하면 유료 plan을 실행하지 않는다.

## 6. 최소 Stage 3 paid smoke

`stage3_multi_hop_mcq.jsonl`에서 `state_sequence` 1개와
`expense_aggregation` 1개의 정확한 `item_id`를 선택한다. 모든 방법에 같은 두
문항을 사용한다.

```bash
rg -n '"derivation_type": "(state_sequence|expense_aggregation)"' \
  data/prepared/*/canonical_items/stage3_multi_hop_mcq.jsonl
```

계획 생성은 API를 호출하지 않는다.

```bash
./scripts/pipeline.sh plan-paid-smoke \
  --method fc_claude_opus_5 \
  --method fc_gemini_3_6_flash \
  --method fc_gpt_5_6_sol \
  --method bm25_gemini_3_6 \
  --method dense_ge2_gemini_3_6 \
  --method mem0_gemini_3_6 \
  --method letta_gemini_3_6 \
  --item-id '<STATE_SEQUENCE_ITEM_ID>' \
  --item-id '<EXPENSE_AGGREGATION_ITEM_ID>' \
  --estimated-usd '<REVIEWED_ESTIMATE>'
```

통과 조건은 방법별 2/2 `COMPLETE`, parse error 0, 동일 item set, 두 hop
evidence의 checkpoint 이하 여부다.

출력된 `plan_sha256`과 누적 비용을 검토한 뒤 실행한다.

```bash
./scripts/paid/run_smoke.sh \
  --plan-sha '<PLAN_SHA>' \
  --approval I_APPROVE_PAID_SMOKE \
  --execute-paid
```

## 7. 최소 masking paid smoke

먼저 `masking_questions.jsonl`에서 같은 event family의 lifecycle 문항 1개와 memory
문항 1개의 정확한 `item_id`를 선택한다. 모든 방법에 같은 두 문항을 사용한다.

```bash
sed -n '1,20p' data/prepared/*/masking_items/masking_questions.jsonl
```

계획 생성은 API를 호출하지 않는다.

```bash
./scripts/pipeline.sh plan-paid-smoke \
  --method fc_claude_opus_5 \
  --method fc_gemini_3_6_flash \
  --method fc_gpt_5_6_sol \
  --method bm25_gemini_3_6 \
  --method dense_ge2_gemini_3_6 \
  --method mem0_gemini_3_6 \
  --method letta_gemini_3_6 \
  --item-id '<LIFECYCLE_ITEM_ID>' \
  --item-id '<MEMORY_ITEM_ID>' \
  --estimated-usd '<REVIEWED_ESTIMATE>'
```

출력된 `plan_sha256`을 확인한 뒤에만 실행한다.

```bash
./scripts/paid/run_smoke.sh \
  --plan-sha '<PLAN_SHA>' \
  --approval I_APPROVE_PAID_SMOKE \
  --execute-paid
```

현재 standing approval은 보수적 누적 smoke 비용이 엄격히 `$5` 미만일 때만
유효하다. 단일 smoke plan 상한은 `$3`, concurrency 1, automatic retry 0,
first-error stop이다. timeout 또는 비용 귀속 불명 상태에서는 자동 재실행하지 않는다.

통과 조건:

- 방법별 2/2 `COMPLETE`
- parse error 0
- 동일 item set
- Mem0 clone equivalence 통과
- Letta frozen-variant replay, search 1회, `top_k=10` 통과

## 8. 논문용 full plan과 실행

Stage 3와 masking smoke, readiness checklist가 통과한 후 exact 전체 범위 plan을 만든다.
계획에는 방법당 9,233문항, 전체 64,631 predictions가 고정된다.

```bash
./scripts/pipeline.sh plan-paid-full \
  --method fc_claude_opus_5 \
  --method fc_gemini_3_6_flash \
  --method fc_gpt_5_6_sol \
  --method bm25_gemini_3_6 \
  --method dense_ge2_gemini_3_6 \
  --method mem0_gemini_3_6 \
  --method letta_gemini_3_6 \
  --scope all \
  --estimated-usd '<REVIEWED_FULL_ESTIMATE>'
```

Plan JSON에서 다음을 검토한다.

- `item_ids`: 9,233
- `method_ids`: 7
- `input_items_sha256`
- `execution_provenance`
- provider operation limits
- reviewed cost estimate

Full run은 smoke standing approval 대상이 아니다. 사용자의 별도 승인 후 실행한다.

```bash
./scripts/paid/run_full.sh \
  --plan-sha '<PLAN_SHA>' \
  --approval I_APPROVE_PAID_FULL \
  --execute-paid
```

예상 결과:

```text
runs/paid_full/<PLAN_SHA>/
├── environment.json
├── pip_freeze.txt
├── <METHOD>__canonical.jsonl
├── <METHOD>__canonical.manifest.json
├── <METHOD>__masking.jsonl
└── <METHOD>__masking.manifest.json
```

어느 한 호출이라도 실패하면 해당 manifest를 `FAILED`로 보존하고 전체 실행을
중단한다. 실패 원인을 확인하기 전에는 같은 plan을 자동 재실행하지 않는다.

## 9. Strict 집계

7개 방법의 canonical과 masking output이 모두 `COMPLETE`일 때 실행한다.

```bash
./scripts/pipeline.sh aggregate \
  --predictions runs/paid_full/<PLAN_SHA>/*.jsonl \
  --expected-scope all \
  --output-dir runs/paid_full/<PLAN_SHA>/report
```

예상 결과:

```text
report/
├── metrics.json
├── main_results.csv
├── main_results.md
├── masking_by_arm.csv
├── stage3_by_derivation.csv
├── retention_lag.csv
└── paired_method_deltas.csv
```

`metrics.json`의 `completeness.reporting_ready`가 `true`인지 확인한다. `false`이면
논문 표를 작성하지 않는다.

## 10. 결과 문서 작성

[docs/results_template.md](docs/results_template.md)를 복사해 full plan SHA가 포함된
결과 문서를 만든다.

```bash
cp docs/results_template.md runs/paid_full/<PLAN_SHA>/RESULTS.md
```

자동 생성된 CSV/JSON 값을 옮기고, 비용과 실패율은 provider usage 및 run manifest와
대조한다. Stage 1, Stage 2, Stage 3, method family, masking lifecycle/memory를
분리해 보고한다.

## 11. 종료

Letta를 사용했다면 서버를 내린다.

```bash
./scripts/paid/letta_down.sh
```

최종 보존 대상은 full plan JSON, prediction JSONL과 manifest, environment snapshot,
집계 report, 작성한 `RESULTS.md`다.

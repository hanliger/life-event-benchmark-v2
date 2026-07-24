# Life Event Benchmark v2

한국어 금융 상담 대화에서 사용자의 **생애 사건(life event)** 과 **금융 상태 변화**를 추론하는 능력을 측정하는 벤치마크와, 그 데이터를 만드는 생성 파이프라인입니다.

한 사용자의 은행 상담 세션이 시간순으로 길게 쌓여 있을 때(long context), 모델이 그 대화들만 보고 "이 사람에게 어떤 생애 사건이 일어났고, 금융 상태가 어떻게 바뀌었는지"를 복원할 수 있는지를 평가합니다.

```text
persona ─▶ 초기 금융 상태 ─▶ 생애사건 trajectory ─▶ 상담 대화 세션
                                          └─▶ 검증/audit ─▶ prefix gold ─▶ benchmark 문항
```

이 문서는 이 저장소를 처음 보는 사람을 위해 **전체 설계**와 **실행 방식**을 설명합니다.

---

## 1. 벤치마크가 측정하는 것

각 문항은 "한 사용자의 상담 세션 이력(일부 또는 전체)"을 입력으로 주고, 그 시점까지의 숨은 상태를 묻습니다. 두 종류의 문항이 있습니다.

| Stage | 문항 | 입력 | 정답 |
| --- | --- | --- | --- |
| **Stage 1** `stage1_event_status` | 지금까지 감지되는 생애 사건과 그 진행 단계는? | 보이는 세션 발화들 | life event label + status(`weak_signal`/`upcoming`/`occurred`/`cancelled`/`no_event`) |
| **Stage 2** `stage2_memory_mcq` | 특정 금융 상태 값은? (객관식) | 보이는 세션 발화 + 초기 금융 메모리 | 정답 선택지 |

핵심 난이도는 **간접성**입니다. 대화는 상태를 직접 말해 주지 않습니다. 사용자는 업무를 요청하며 단서만 흘리고, 모델은 여러 세션에 흩어진 단서를 모아 상태를 역추론해야 합니다. 평가 대상 모델에게는 정답 계획(plan)·주석(cue)·구조화 문맥은 주지 않고, **보이는 발화와 초기 메모리만** 줍니다.

---

## 2. 설계 (Design)

### 2.1 state first, dialogue second

이 저장소의 근본 원칙은 **상태를 먼저, 대화는 나중에**입니다.

1. 먼저 숨은 진실을 만든다 — persona, 초기 금융 상태, 월 단위 생애사건 trajectory.
2. 그 상태를 근거로 관측 가능한 은행 상담 대화를 생성한다.

즉 대화가 상태를 만드는 게 아니라, **대화는 이미 확정된 상태의 간접 증거**입니다. 덕분에 모든 문항에 대해 "무엇이 정답인지"를 생성 시점에 이미 알고 있고, 대화는 그 정답을 얼마나 흐리게/자연스럽게 드러내는지만 조절합니다.

### 2.2 파이프라인 단계

| 단계 | 하는 일 | 핵심 코드/설정 |
| --- | --- | --- |
| Persona 정규화 | Nemotron 한국어 persona를 나이·직업·혼인·주거·가구 상태로 정규화 | `scripts/sample_stratified_personas.py`, `src/fin_life_benchmark/persona/` |
| 초기 금융 상태 | persona에 맞는 초기 금융 memory/action 생성 | `scripts/generate_initial_states.py`, `src/fin_life_benchmark/memory/` |
| 생애사건 trajectory | 월 단위로 사건을 샘플링하고 lifecycle을 진행 | `scripts/simulate_trajectories.py`, `configs/registries/life_events.yaml`, `life_generator/` |
| 대화 계획(plan) | trajectory에서 세션별 계획(어떤 업무·어떤 단서·어떤 정답 delta)을 만듦 | `scripts/build_dialogue_plans.py`, `src/fin_life_benchmark/dialogue/evidence_planner.py` |
| 대화 생성 | 계획에 따라 LLM으로 상담 세션을 생성 | `scripts/generate_dialogue_sessions.py`, `prompts/dialogue/` |
| 검증/audit | 정답 누출, 상태 충돌, life-stage 위반, 복원가능성 점검 | `scripts/validate_dialogues.py`, `scripts/audit_*.py` |
| Gold/문항 | prefix별 정답 상태와 Stage 1/2 문항 생성 | `scripts/export_prefix_gold.py`, `scripts/build_benchmark_items.py` |

### 2.3 꼭 알아야 할 개념

**생애사건 lifecycle** — 사건은 `weak_signal → upcoming → occurred`로 진행하거나 중간에 `cancelled`가 됩니다.

- `weak_signal`, `upcoming`은 아직 단서만 있는 단계라 **확정 상태 갱신을 하면 안 됩니다.**
- `occurred` 이후에만 실제 금융 상태(memory) 갱신이 허용됩니다.

**memory의 `unknown` vs `not_applicable`** — 둘은 다릅니다.

- `unknown`: 해당 필드가 적용되긴 하는데 값을 모름.
- `not_applicable`: persona 상태상 그 필드가 존재하면 안 됨. 예) 은퇴자·비취업자의 급여일은 `unknown`이 아니라 `not_applicable`.

**세션 구조** — 대화는 짧고 많습니다. 긴 상담 하나가 아니라 시간순으로 흩뿌려진 짧은 세션들로 long context를 구성합니다.

- 한 세션 = 정확히 **8 발화**(고객 4 + 상담원 4), **하나의 금융 업무**만 다룸.
- trajectory당 **300 세션**, **15세션씩 20개 window**, window마다 담당 `occurred` 사건 1개.
- 세션 번호는 실시간 간격이 아니며, 같은 달의 상태 변화는 `transition_order`로 구분합니다.

**세션 타입** — 계획은 각 세션에 역할을 부여합니다.

| 타입 | 역할 |
| --- | --- |
| `occurred_evidence` | 사건이 실제로 일어났음을 간접적으로 드러내는 근거 세션 |
| `cancellation_evidence` | 예정됐던 사건이 취소됨을 드러내는 세션 |
| `consequence` | 사건 이후의 후속 결과가 나타나는 세션 |
| `routine_financial` | 사건과 무관한 일상 금융 업무 (배경 잡음) |
| `stale_recall_session` | 예전 상태를 다시 언급해 최신 상태와 혼동을 유도 |
| `hard_negative` | 사건을 암시하는 듯하지만 실제로는 상태를 바꾸지 않는 함정 |

**상담 상황 제약** — 대화는 모바일/인터넷뱅킹 챗봇 상황입니다. 오프라인 지점·창구·서명·실물 신분증 같은 장면은 쓰지 않습니다. 자세한 생성 규칙과 검증 항목은 `docs/dialogue_generation_strategy.md`, `docs/failure_modes.md` 참고.

---

## 3. 저장소 구조

```text
src/fin_life_benchmark/   핵심 라이브러리 (persona, memory, fsm, trajectory, dialogue, gold, benchmark, validation, llm, io)
life_generator/           생애사건 subgraph 샘플러 (trajectory 시뮬레이터가 사용)
life_event_graph/         life_generator가 쓰는 노드/액션 레지스트리 shim
scripts/                  파이프라인 각 단계의 CLI 진입점
configs/                  생성 설정(generation/) · 레지스트리(registries/) · 로케일(locales/)
prompts/                  모든 LLM 프롬프트 (코드에 embed하지 않음)
docs/                     세부 설계 문서
tests/                    테스트 + tests/fixtures/ (freeze된 20개 trajectory)
data/samples/             포맷 참고용 샘플 (dialogues-only) 1건
Makefile                  모든 단계를 감싼 make 타깃
```

이 저장소는 **코드 전용**입니다. 생성된 대량 데이터(`data/runs/`)는 git에 넣지 않고, 필요할 때 재생성하거나 HuggingFace에서 받아옵니다 (→ §6).

---

## 4. 설치 (Setup)

Python 3.10+ 환경을 권장합니다 (저자 환경은 conda env `life_event`).

```bash
make setup          # requirements 설치 + .env 없으면 .env.example 복사
make test           # 설치 확인 (테스트 통과 확인)
```

`.env`에서 LLM provider와 모델을 지정합니다. `.env`는 커밋하지 않습니다.

```env
# provider: mock | openai | anthropic | gemini
DEFAULT_LLM_PROVIDER=anthropic
DEFAULT_GENERATION_MODEL=claude-sonnet-5
ANTHROPIC_API_KEY=...            # 쓰는 provider의 키만 채우면 됨

# 대화 corpus를 받아올 HuggingFace 데이터셋
HF_DIALOGUE_REPO=hangyeul-lee/life-event-benchmark-v2-dialogues
HF_TOKEN=                        # 데이터셋이 gated일 때만 필요
```

- `DEFAULT_LLM_PROVIDER`는 API 회사, `DEFAULT_GENERATION_MODEL`은 그 안의 모델명입니다.
- API 키 없이도 offline(mock) 모드로 파이프라인 배관을 점검할 수 있습니다 (→ §5.B).
- persona 생성에는 Nemotron 원본 parquet가 `Nemotron-Personas-Korea/data/*.parquet`에 있어야 합니다 (다른 경로는 `PERSONA_INPUT`로 지정).

모든 파이프라인 산출물은 하나의 **`RUN_ID`** 디렉터리(`data/runs/<RUN_ID>/`) 아래에 모입니다. 아래 예시는 `RUN_ID=exp1`을 씁니다.

---

## 5. 실행 (How to run)

두 갈래가 있습니다. **대부분의 사용자는 A**(이미 만들어진 벤치마크로 모델을 평가)만 필요합니다. 데이터 생성 자체를 바꾸고 싶을 때만 B로 갑니다.

### A. 이미 만들어진 벤치마크로 실험하기

현재 벤치마크는 **20개 trajectory와 대화 세션으로 freeze**되어 있습니다. 이걸 로컬로 복원한 뒤 문항을 만들고 모델을 평가합니다. (아무것도 새로 **생성**하지 않습니다.)

```bash
export RUN_ID=exp1

# 1) freeze된 trajectory(git) + 대화 세션(HuggingFace)을 data/runs/exp1/ 로 복원
make restore-frozen-run RUN_ID=$RUN_ID

# 2) 정답(gold)과 Stage 1/2 문항 생성 (frozen 데이터로부터 결정적으로 계산)
make export-gold-controlled build-items-controlled RUN_ID=$RUN_ID

# 3) 모델 평가 — 이 벤치마크로 하는 "실험"
make evaluate RUN_ID=$RUN_ID EXECUTE=1        # .env의 provider/model 사용
make evaluate RUN_ID=$RUN_ID                  # EXECUTE 생략 시 offline(mock) — 배관만 점검
```

결과는 `data/runs/$RUN_ID/eval/report.json`(스테이지별 정확도)과 `predictions.jsonl`에 남습니다.

> 일부 trajectory만 쓰려면:
> `python scripts/restore_frozen_run.py --run-id $RUN_ID --trajectory-id traj_001 --trajectory-id traj_002`

### B. 파이프라인을 처음부터 재생성하기

생성 로직(사건 정의, 계획, 프롬프트 등)을 바꿔 **새 데이터를 만들 때**의 순서입니다.

| 순서 | 명령 | 산출물 |
| --- | --- | --- |
| 1 | `make normalize-personas RUN_ID=$RUN_ID` | 정규화된 persona |
| 2 | `make initial-states RUN_ID=$RUN_ID` | persona별 초기 금융 상태 |
| 3 | `make simulate-smoke RUN_ID=$RUN_ID` | 생애사건 trajectory (= 사건 샘플링) |
| 4 | `make plan-dialogues RUN_ID=$RUN_ID` | 세션별 대화 계획 (trajectory당 300개) |
| 5 | `make dialogue-smoke RUN_ID=$RUN_ID EXECUTE=1` | 실제 LLM 대화 세션 |
| 6 | `make validate-dialogues audit RUN_ID=$RUN_ID` | 검증·품질 리포트 |
| 7 | `make export-gold build-items RUN_ID=$RUN_ID` | gold + 문항 |

예: 작은 규모로 3번까지만 확인

```bash
export RUN_ID=dev
make normalize-personas RUN_ID=$RUN_ID AGE_QUOTAS="20-29:2 30-39:2" SEED=42
make initial-states     RUN_ID=$RUN_ID LIMIT=4
make simulate-smoke     RUN_ID=$RUN_ID NUM_TRAJ=2 TARGET_EVENTS=20 SEED=42
make plan-dialogues     RUN_ID=$RUN_ID SEED=42
```

메모:

- **offline(mock) 모드**: `EXECUTE=1`을 빼면 API 없이 템플릿 대화를 만들어 파이프라인 연결만 점검합니다. 실제 벤치마크 품질은 나오지 않으므로 개발/스모크 용도입니다.
- **모드를 섞지 마세요**: 한 sessions 디렉터리는 첫 생성 시 설정을 고정합니다. mock으로 만든 곳에 이어서 `EXECUTE=1`을 돌리면 멈춥니다 — 새 `RUN_ID`를 쓰거나 그 `dialogues/` 폴더를 지우세요.
- **특정 모델 고정**: `scripts/generate_dialogue_sessions.py`에 `--model-profile sonnet5`(정의: `configs/generation/dialogue_models.yaml`)를 넘기면 `.env`와 무관하게 그 모델로 생성합니다. canonical corpus는 `claude-sonnet-5` 기준이며, 검증이 엄격해 저사양 모델은 통과율이 낮을 수 있습니다.
- 전체를 한 번에: `make pipeline-smoke LIMIT=2 NUM_TRAJ=2 EXECUTE=0` 은 1~7을 offline으로 이어서 돌립니다.

### C. Abstention 실험 (lifecycle masking)

**동기.** 사건은 `weak_signal → upcoming → occurred`(또는 `cancelled`)로 진행하고, 모델은 아직 확정되지 않은 단계에서는 **확정 상태 갱신을 하지 않아야(abstain)** 합니다(→ §2.3). 그런데 단순히 prefix를 잘라 단계별로 평가하면, 단계가 깊어질수록 **prefix 길이·위치·최신성**이 함께 변해 "증거 강도의 효과"와 "길이/위치의 효과"가 섞입니다.

**방법.** 평가 지점을 **한 곳에 고정**한 채, 대상 사건의 근거 세션을 **역순으로**(occurred → upcoming → weak) 벗겨 냅니다. 벗긴 자리는 같은 trajectory의 **사건과 무관한 routine 세션으로 치환**해 길이·위치를 동일하게 유지합니다(routine 세션은 memory에 아무것도 쓰지 않아 gold에 부작용이 없습니다). 각 masking 레벨마다 prefix gold를 다시 계산하면, 정답이 어떻게 달라져야 하는지를 보여주는 **counterfactual "abstention 사다리"**가 나옵니다.

```bash
export RUN_ID=exp1
make restore-frozen-run RUN_ID=$RUN_ID          # trajectory(git) + 세션(HF) 복원

# 1) trajectory의 교육단계 전이 기록 보정
#    (freeze된 fixture는 education-stage 수정 이전 버전이라 전이 기록만 forward로 정정)
python scripts/fix_education_stage_trajectory.py \
    --in-dir  data/runs/$RUN_ID/trajectories \
    --out-dir data/runs/$RUN_ID/trajectories_fixed

# 2) lifecycle masking → 레벨별 counterfactual gold 사다리
python scripts/mask_lifecycle_experiment.py \
    --trajectories-dir data/runs/$RUN_ID/trajectories_fixed \
    --sessions-dir     data/runs/$RUN_ID/dialogues/sessions \
    --out data/runs/$RUN_ID/masking_ladder.json --max-events 12
```

masking 레벨과 기대되는 정답(사건 상태):

| 레벨 | 남는 근거 | 기대 gold 상태 | 정답 행동 |
| --- | --- | --- | --- |
| `full` | 전부 | `occurred` (또는 `cancelled`) | occurred만 갱신 허용 |
| `mask_terminal` | weak+upcoming | `upcoming` | abstain |
| `mask_upcoming` | weak | `weak_signal` | abstain |
| `mask_all` | 없음 | `no_event` | abstain |

`update_allowed`(상태 갱신 허용)는 **`full`+`occurred`에서만 참**이고 마스킹된 모든 레벨에서는 거짓이어야 합니다. `cancelled` 사건은 `full`에서도 갱신 불가라 commit↔revert 경계를 검증합니다. 산출물 `masking_ladder.json`은 사건별로 이 사다리를 담습니다.

> gold 재계산은 "가시적 세션"만 보고 결정론적으로 이뤄지므로(`export_prefix_gold`), 세션의 `linked_event_instance_id`만 끊으면 상태가 자동으로 강등됩니다. **새 세션 생성은 필요 없습니다.** 다음 단계로 각 masked prefix에 대해 문항을 만들고 `evaluate`를 돌리면, 모델의 행동 곡선을 이 gold 사다리와 대조하는 **abstention 민감도**를 얻습니다.

---

## 6. 데이터 정책

이 저장소는 코드만 담고, 데이터는 다음 규칙으로 관리합니다.

- **생성물은 git에 없음**: `data/runs/<RUN_ID>/`(persona, trajectory, 세션, gold, 문항, 리포트)는 모두 재생성 대상이라 추적하지 않습니다.
- **frozen 코퍼스는 HuggingFace에**: 확정된 대화 세션은 `HF_DIALOGUE_REPO` 데이터셋에 있습니다. `dialogues/`(정답 제거된 발화·문맥)와 `gold/`(정답 라벨) 두 config로 나뉘어 있고, 세션을 읽는 단계는 로컬에 세션이 없으면 이 둘을 `session_id`로 join해 `sessions_traj_XXX.jsonl`로 자동 복원합니다(있으면 건드리지 않음). 명시적으로 받으려면 `make fetch-dialogues`.
- **frozen trajectory는 git에**: 확정된 20개 trajectory는 `tests/fixtures/trajectories/`에 byte 단위로 고정 추적됩니다.
- **참고 샘플**: `data/samples/`에 한 persona의 dialogues-only 예시 1건.

> **freeze 유지 주의**: `simulate-*` / `plan-dialogues` / `dialogue-*` 는 명시적으로 실행할 때만 새 데이터를 만듭니다. 자동으로 재생성되는 경로는 없습니다. frozen 결과를 유지하려면 이 생성 타깃을 frozen `RUN_ID`에 대해 실행하지 마세요 — gold·문항 같은 downstream은 frozen에서 언제든 다시 계산할 수 있습니다.

---

## 7. 산출물 위치

| 경로 | 내용 |
| --- | --- |
| `data/runs/<RUN_ID>/inputs/personas_*.jsonl` | 정규화된 persona |
| `data/runs/<RUN_ID>/inputs/initial_states_*.jsonl` | persona별 초기 금융 상태 |
| `data/runs/<RUN_ID>/trajectories/traj_*.json` | 생애사건 trajectory |
| `data/runs/<RUN_ID>/dialogues/sessions/sessions_traj_*.jsonl` | 대화 세션 (분석·평가 입력) |
| `data/runs/<RUN_ID>/gold/prefix_gold_*.jsonl` | prefix별 정답 상태 |
| `data/runs/<RUN_ID>/benchmark_items/*.jsonl` | Stage 1/2 문항 |
| `data/runs/<RUN_ID>/quality_reports/*` | 검증·audit 리포트 |
| `data/runs/<RUN_ID>/eval/report.json` | 모델 평가 결과 |
| `data/runs/<RUN_ID>/masking_ladder.json` | lifecycle masking abstention 사다리 (§5-C) |

---

## 8. 생성 설정 튜닝

주요 파라미터는 YAML에서 조정합니다.

| 파일 | 조정 대상 |
| --- | --- |
| `configs/generation/simulation.yaml` | trajectory 기간, 사건 밀도, 동시 active event 상한 |
| `configs/generation/dialogue.yaml` | 세션 수, 발화 수, window 크기, hard-negative 비율, repair 횟수 |
| `configs/registries/life_events.yaml` | 생애사건 정의, 발생 조건, lifecycle |
| `configs/generation/dialogue_models.yaml` | LLM 모델 프로파일(`sonnet5` 등) |

기본값: 세션당 8발화, trajectory당 300세션(15세션 × 20 window), trajectory당 occurred 사건 20개. 시뮬레이션은 고정 기간이 아니라 목표 사건 수를 채우면 종료합니다.

---

## 9. 더 읽을거리

프롬프트는 코드에 넣지 않고 `prompts/` 아래 파일로 둡니다: `prompts/dialogue/`(생성·repair), `prompts/judge/`(LLM judge rubric), `prompts/system/`(각 도구의 system 프롬프트).

세부 설계 문서는 `docs/`에 있습니다.

| 문서 | 내용 |
| --- | --- |
| `docs/design_overview.md` | 전체 구조와 데이터 흐름 |
| `docs/repo_inventory.md` | source/scripts/데이터 레이아웃 |
| `docs/life_state_fsm.md` | life-state guard와 사건 샘플링 |
| `docs/event_lifecycle.md` | 사건 lifecycle 상태 전이 |
| `docs/dialogue_generation_strategy.md` | 대화 계획/생성/검증 |
| `docs/dialogue_generation_canary.md` | 대화 생성 canary/regression QA 게이트 |
| `docs/financial_memory_schema.md` | 금융 memory 스키마 |
| `docs/standing_action_schema.md` | standing action 스키마 |
| `docs/history_filter.md` | history-필요성 필터 |
| `docs/failure_modes.md` | 주요 실패 유형 |
| `docs/coverage_generation.md` | 희귀 사건 커버리지 생성 |
| `docs/locale_extension_guide.md` | 로케일 추가 가이드 |

---

## 10. Troubleshooting

| 증상 | 해결 |
| --- | --- |
| `--execute requires DEFAULT_LLM_PROVIDER=openai\|anthropic` | `.env`가 mock 설정입니다. provider/모델을 실제 값으로 바꾸세요. |
| `... API key not set` | 쓰는 provider의 API 키를 `.env`에 넣으세요. |
| `generation manifest mismatch` | 같은 sessions 폴더에서 mock↔execute를 섞었습니다. 새 `RUN_ID`를 쓰거나 그 `dialogues/` 폴더를 지우세요. |
| 세션이 0개 생성됨 | 모델 출력이 검증을 통과하지 못한 것입니다. `dialogues/sessions/errors_*.jsonl`에서 이유를 확인하고, 가능하면 `--model-profile sonnet5`로 시도하세요. |
| HF 세션을 못 받음 | `HF_DIALOGUE_REPO`(및 gated면 `HF_TOKEN`)를 확인하세요. |

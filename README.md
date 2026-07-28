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
| **Stage 2** `stage2_memory_value` | 특정 날짜의 금융 메모리 최종값은? | 날짜가 표시된 세션 발화 + 초기 금융 메모리 | 닫힌 값 집합은 객관식, 그 외는 단답형 |

핵심 난이도는 **간접성**입니다. 대화는 상태를 직접 말해 주지 않습니다. 사용자는 업무를 요청하며 단서만 흘리고, 모델은 여러 세션에 흩어진 단서를 모아 상태를 역추론해야 합니다. 평가 대상 모델에게는 정답 계획(plan)·주석(cue)·구조화 문맥은 주지 않고, **보이는 발화와 초기 메모리만** 줍니다.

Stage 2는 다음 원칙으로 만듭니다.

- 15세션 checkpoint에서 `occurred` event가 갱신한 memory path/selector로 문항을 만들고, 이후 더 긴 prefix에서도 event 시점의 기준일·정답을 고정한 채 같은 문항을 재사용
- 문항의 `checkpoint_date`는 정답 기준일이며, `evaluation_checkpoint_date`는 모델에게 제공한 prefix의 마지막 날짜
- 질문에는 event 이름이나 ID를 노출하지 않고 `session_date` 기반의 기준일만 제시
- `update/create`와 의미 있는 동일값 재확인(no-op)은 포함하되, `archive`·`mark_stale`·`set_not_applicable` 같은 상태 전용 operation은 최종값 문항에서 제외
- 고용 상태·주거 유형처럼 사전에 닫힌 값 집합은 객관식, 회사명·주소·금액·인원 수·목록은 단답형
- 경로별 질문/selector/선지 정책은 `configs/registries/stage2_memory_questions.yaml`에서 관리

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
HF_DIALOGUE_REVISION=             # 정확한 재현 시 HF commit SHA로 고정
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

### C. Signal ablation: prefix-gold counterfactual lifecycle masking

#### C.1 실험 질문과 통제 변수

사건은 `weak_signal → upcoming → occurred`(또는 `cancelled`)로 진행하고, 모델은 아직 확정되지 않은 단계에서는 **확정 상태 갱신을 하지 않아야(abstain)** 합니다(→ §2.3). 단계별 원본 prefix를 단순 비교하면 뒤 단계일수록 prefix 길이, 평가 위치, recency가 함께 달라져 evidence strength와 long-context 효과가 섞입니다.

Counterfactual masking은 한 사건의 최종 linked session까지를 **동일 checkpoint**로 고정하고, 대상 사건의 evidence session 내용만 중립 대화로 치환합니다. 다음 값은 모든 level에서 동일합니다.

- trajectory/persona와 전체 prefix session 수
- 각 session의 `session_id`, `month_index`, position, turn 수
- 대상 외 session의 내용과 순서
- 평가 checkpoint

level 간 바뀌는 것은 대상 사건의 가시적 evidence뿐입니다. 이 조건에서 모델이 확정 갱신에서 abstention으로 이동하는지를 측정합니다.

#### C.2 Timeless persona filler bank

기존 trajectory의 미래 routine session을 donor로 쓰면 late checkpoint에서 donor가 부족하고, early checkpoint에서는 수십 개월 뒤 대화가 들어갈 수 있습니다. 따라서 canonical timeline과 분리된 **20-session/persona reserve bank**를 Sonnet 5로 생성합니다.

reserve filler 계약:

- canonical `Sxxx`가 아닌 `CF001..CF020`; `month_index=null`
- 같은 persona의 말투(`formality`, `verbosity`)만 사용
- 나이·직업·회사·주소·가족·건강·자산 등 persona state는 prompt에 전달하지 않음
- lifecycle/event/memory fact가 없고 cue는 deterministic `task_intent` 하나뿐
- 정확히 8턴, user/assistant 교대, `information_only`
- 숫자·금액·금리·계좌/기기/거래 건수 및 개인화된 조회 결과를 생성하지 않음
- 이체 실행·가입·해지·설정 변경을 완료하지 않고 화면 경로와 조회 절차만 안내
- 10개의 state-independent task를 두 surface variant로 생성하여 persona당 20개 구성

현재 corpus에서 한 event가 필요로 하는 최대 donor는 5개이므로 20개 bank는 모든 event를 커버하고 donor 표현도 분산할 수 있습니다. bank는 부족한 late event에만 fallback으로 쓰지 않고 **모든 event에 동일하게 적용**합니다. donor source와 checkpoint가 결합되는 것을 막기 위해서입니다.

#### C.3 생성·audit 절차

아래 경로 구조를 사용합니다.

```text
data/runs/<RUN_ID>/counterfactual_fillers/
├── plans/plans_traj_XXX.jsonl
├── sessions/fillers_traj_XXX.jsonl
├── raw/{canary,full}/
├── audit/{canary,full}/
└── logs/
```

먼저 frozen trajectory와 session을 복원하고 교육단계 전이 기록을 보정합니다.

```bash
export RUN_ID=exp1
export CF_ROOT=data/runs/$RUN_ID/counterfactual_fillers
make restore-frozen-run RUN_ID=$RUN_ID
python scripts/fix_education_stage_trajectory.py \
    --in-dir data/runs/$RUN_ID/trajectories \
    --out-dir data/runs/$RUN_ID/trajectories_fixed
mkdir -p "$CF_ROOT"/{plans,sessions,raw/canary,raw/full,audit/canary,audit/full,logs}
```

20 persona의 frozen filler plan은 API 호출 없이 생성합니다.

```bash
nohup python scripts/plan_counterfactual_fillers.py \
    --trajectories-dir data/runs/$RUN_ID/trajectories_fixed \
    --out-dir "$CF_ROOT/plans" --overwrite \
    > "$CF_ROOT/logs/plan_all.log" 2>&1 < /dev/null &
```

`traj_001` 한 persona를 Sonnet 5 canary로 생성하고 deterministic audit를 통과시킵니다.

```bash
nohup python scripts/generate_counterfactual_fillers.py \
    --plans-dir "$CF_ROOT/plans" \
    --output-dir "$CF_ROOT/sessions" \
    --raw-output-dir "$CF_ROOT/raw/canary" \
    --trajectory-id traj_001 --model-profile sonnet5 \
    --batch-size 4 --workers 4 --execute --overwrite \
    > "$CF_ROOT/logs/canary_generate.log" 2>&1 < /dev/null &

nohup python scripts/audit_counterfactual_fillers.py \
    --plans-dir "$CF_ROOT/plans" \
    --fillers-dir "$CF_ROOT/sessions" \
    --out-dir "$CF_ROOT/audit/canary" \
    --trajectory-id traj_001 \
    > "$CF_ROOT/logs/canary_audit.log" 2>&1 < /dev/null &
```

canary decision이 `PASS`인 경우에만 나머지 19 persona를 병렬 생성하고 전체 400개를 audit합니다.

```bash
nohup python scripts/generate_counterfactual_fillers.py \
    --plans-dir "$CF_ROOT/plans" \
    --output-dir "$CF_ROOT/sessions" \
    --raw-output-dir "$CF_ROOT/raw/full" \
    --exclude-trajectory-id traj_001 --model-profile sonnet5 \
    --batch-size 4 --workers 8 --execute --resume \
    > "$CF_ROOT/logs/full_generate.log" 2>&1 < /dev/null &

nohup python scripts/audit_counterfactual_fillers.py \
    --plans-dir "$CF_ROOT/plans" \
    --fillers-dir "$CF_ROOT/sessions" \
    --out-dir "$CF_ROOT/audit/full" \
    > "$CF_ROOT/logs/full_audit.log" 2>&1 < /dev/null &
```

audit는 persona별 정확히 20개/전체 400개, plan ID와 task 일치, 8턴 교대, cue/action 계약, lifecycle 표현, 숫자와 개인화 조회 결과, exact duplicate를 검사합니다.

#### C.4 고정 donor mapping과 masking level

각 target event에 대해 masking 대상이 될 모든 lifecycle slot을 먼저 모으고, `slot_session_id → CF donor` mapping을 **한 번만 결정**합니다. 같은 event의 모든 level은 이 mapping을 공유합니다. 예를 들어 terminal slot에 배정된 `CF007`은 `mask_terminal`, `mask_upcoming`, `mask_all`에서 항상 `CF007`입니다. level마다 donor를 다시 뽑아 이미 masked된 slot 내용까지 달라지는 교란을 허용하지 않습니다.

| 레벨 | 치환하는 대상 event session | 기대 target-event gold | 정답 행동 |
| --- | --- | --- | --- |
| `full` | 없음 | `occurred` 또는 `cancelled` | occurred만 갱신 허용 |
| `mask_terminal` | terminal + downstream | 남은 최신 단계(`upcoming`, 없으면 `weak_signal`) | abstain |
| `mask_upcoming` | terminal + downstream + upcoming | `weak_signal`(없으면 `no_event`) | abstain |
| `mask_all` | 모든 target lifecycle evidence | `no_event` | abstain |

치환 시 slot의 위치 정보와 8턴 길이는 유지하지만 다음 event-bearing 필드는 제거합니다.

- `linked_event_instance_id`, `window_event_instance_id`
- `event_status_after_session`
- `cue_annotations`
- donor나 원본의 hidden `plan/current_state`

visible `turns`, `financial_task`, `mapped_action`, `action_resolution`만 중립 donor에서 가져옵니다.

#### C.5 Counterfactual prefix gold 재계산

masking 실행:

```bash
nohup python scripts/mask_lifecycle_experiment.py \
    --trajectories-dir data/runs/$RUN_ID/trajectories_fixed \
    --sessions-dir data/runs/$RUN_ID/dialogues/sessions \
    --fillers-dir "$CF_ROOT/sessions" \
    --out data/runs/$RUN_ID/masking_ladder.json \
    --prefix-gold-out data/runs/$RUN_ID/masking_ladder_prefix_gold.jsonl \
    --max-events 10000 --quiet \
    > "$CF_ROOT/logs/masking_full.log" 2>&1 < /dev/null &
```

생성 완료 후 ladder, replacement recipe, complete PrefixGold를 함께 audit합니다.

```bash
nohup python scripts/audit_lifecycle_masking.py \
    --ladder data/runs/$RUN_ID/masking_ladder.json \
    --prefix-gold data/runs/$RUN_ID/masking_ladder_prefix_gold.jsonl \
    --exclusions data/runs/$RUN_ID/masking_ladder.exclusions.json \
    --out-dir "$CF_ROOT/audit/masking_full" \
    --expected-events 451 \
    > "$CF_ROOT/logs/masking_full_audit.log" 2>&1 < /dev/null &
```

event별 fixed checkpoint는 그 event의 마지막 linked session까지입니다. 각 level에서 해당 prefix를 slot-preserving replacement로 materialize한 다음 `export_prefix_gold(trajectory, visible_variant, checkpoint_stride=checkpoint)`를 다시 실행합니다. 원본 trajectory의 최종 gold를 복사하지 않습니다.

재계산 규칙:

1. **Event status:** 치환 후에도 `linked_event_instance_id`가 남은 target session 중 최신 가시 단계에서 계산합니다. 모두 제거되면 `no_event`입니다.
2. **Memory:** 가시 session의 `memory_fact` cue만 순서대로 replay합니다. masked session의 cue는 제거되므로 그 session이 근거였던 pending/commit/update도 gold에서 사라집니다.
3. **Action decision:** 가시적인 `occurred` evidence가 있는 source event의 impact만 replay합니다. upcoming/weak/cancelled/masked 상태에서는 확정 실행을 허용하지 않습니다.
4. **다른 event:** target이 아닌 session은 바꾸지 않으므로 그 event의 status/evidence와 update의 source/path/operation/new value, action decision은 유지됩니다. 단, target update를 제거한 뒤 memory를 처음부터 replay하므로 같은 path를 나중에 만지는 다른 event update의 `old_value`와 최종 full-memory state는 달라질 수 있습니다. 이는 collateral session 변경이 아니라 의도된 counterfactual state 전파입니다.

`update_allowed`는 `full + occurred`에서만 참이며 모든 masked level과 `cancelled`에서는 거짓이어야 합니다.

산출물:

| 파일 | 내용 |
| --- | --- |
| `masking_ladder.json` | event별 4-level target status, update 여부, donor provenance |
| `masking_ladder.exclusions.json` | bank 부족/계약 불일치로 만들지 못한 event; 정상 full run 기대값은 빈 배열 |
| `masking_ladder_prefix_gold.jsonl` | `case_id`, checkpoint, masked slot, 고정 replacement recipe와 complete recalculated `PrefixGold` |

`masking_ladder_prefix_gold.jsonl`은 원본 session 파일과 persona bank 파일을 가리키는 recipe를 함께 가지므로, 평가기는 동일 counterfactual prefix를 결정론적으로 재구성할 수 있습니다. 실험 전 검증에서는 terminal event 451개, exclusion 0개, 총 prefix-gold case 1,804개, cross-persona/level-inconsistent donor 0개를 요구합니다.

이 단계는 signal ablation dataset 구축까지만 포함합니다. filler 자체의 문체 효과를 재는 placebo arm과 실제 model evaluation은 별도 단계입니다.

#### C.6 Hugging Face freeze에서 바로 재현

생성된 v1 filler bank는 canonical dialogue와 같은 `HF_DIALOGUE_REPO`의
`counterfactual_fillers/v1/`에 freeze되어 있습니다. 로컬 `data/runs/`가 비어
있어도 다음 한 명령이 필요한 데이터를 HF에서 받고 masking과 audit까지
연결합니다.

```bash
export RUN_ID=cf_repro

# main의 최신 frozen artifact 사용
make counterfactual-ablation RUN_ID=$RUN_ID

# 논문/결과 재현에서는 v1 filler 업로드 commit을 고정
export HF_DIALOGUE_REVISION=f45f9603a8e6da31d244ca81e99f0c94c797475c
make counterfactual-ablation RUN_ID=$RUN_ID
```

장시간 실행을 terminal과 분리해 로그를 남기려면 같은 target을 `nohup`으로
감쌉니다.

```bash
mkdir -p data/runs/$RUN_ID/counterfactual_fillers/logs
nohup env HF_DIALOGUE_REVISION=f45f9603a8e6da31d244ca81e99f0c94c797475c \
    make counterfactual-ablation RUN_ID=$RUN_ID \
    > data/runs/$RUN_ID/counterfactual_fillers/logs/hf_reproduce.log \
    2>&1 < /dev/null &
```

이 target의 동작 순서는 다음과 같습니다.

1. git에 추적된 20개 frozen trajectory를 `data/runs/$RUN_ID/trajectories/`로 복사
2. canonical sessions가 없으면 HF의 `dialogues/`+`gold/`를 join하여 복원
3. filler bank가 없으면 HF의 `counterfactual_fillers/v1/` sessions/plans/audit/manifest를 복원
4. 교육단계 전이 기록을 보정
5. 451-event/4-level masking과 complete PrefixGold 1,804개 생성
6. exclusion, donor 고정성, gold 단조성과 collateral drift audit

filler만 미리 받으려면 다음을 사용합니다.

```bash
make fetch-counterfactual-fillers RUN_ID=$RUN_ID

# 또는 revision/trajectory를 직접 지정
python scripts/fetch_counterfactual_fillers.py \
    --output-root data/runs/$RUN_ID/counterfactual_fillers \
    --revision f45f9603a8e6da31d244ca81e99f0c94c797475c \
    --trajectory-id traj_001
```

fetch는 `sessions/fillers_traj_XXX.jsonl`, frozen plan, generation/audit manifest를
동일한 상대경로로 materialize합니다. 기존 파일이 있으면 network no-op이며,
`--force`에서만 다시 받습니다. fetch가 HF의 `artifact_manifest.json`에 기록된
artifact SHA256을 자동 검증하며, manifest에는 관련 구현 파일의 SHA256도 있어
고정 revision과 함께 생성 환경을 추적할 수 있습니다.

maintainer가 검증된 새 filler freeze를 올릴 때는 dry-run 후 명시적으로
`--execute`를 사용합니다. raw provider output과 log는 업로드하지 않습니다.

```bash
python scripts/publish_counterfactual_fillers_to_hf.py \
    --fillers-root data/runs/$RUN_ID/counterfactual_fillers

python scripts/publish_counterfactual_fillers_to_hf.py \
    --fillers-root data/runs/$RUN_ID/counterfactual_fillers --execute
```

### D. RQ1: progressive life-event trajectory reconstruction

RQ1은 "긴 상담 이력에서 암묵적 생애 사건 인스턴스의 **종류·lifecycle 상태·시간 순서·근거 세션**을 복원할 수 있는가"를 측정하는 새 Stage 1 과제(`stage1_event_trajectory`)입니다. 기존 `stage1_event_identification` 문항과 Stage 2는 그대로 유지됩니다.

#### D.1 Natural progressive 실험

- trajectory마다 15세션 간격 checkpoint 20개(15, 30, …, 300) × 20 trajectory = **400 natural item**.
- 각 checkpoint에서 모델은 지금까지 보이는 **누적 event ledger 전체**를 JSON으로 복원합니다(사건 수는 알려주지 않음, 같은 event_id 반복 가능).
- 모델 입력은 **public session id(`D###`) + 발화(turns)뿐**입니다. 날짜·세션 타입·계획 등 어떤 gold 필드도 노출되지 않고, `S### ↔ D###` 매핑은 item gold에만 저장됩니다. PrefixGold는 evaluator 전용입니다.
- gold ledger는 checkpoint PrefixGold + 세션 기록에서 파생: core evidence(`weak_signal/upcoming/occurred/cancellation_evidence`)와 supporting(`consequence/stale_recall`)을 분리하고, status anchor는 `occurred/cancelled`=확정을 처음 세운 core 세션, `upcoming/weak_signal`=해당 타입의 최신 core 세션으로 결정적으로 정의합니다.
- 평가 조건: `full_prefix`(전체 prefix), `last_15`(최신 15세션만), `oracle_evidence`(gold core evidence 세션만; retrieval-free 상한).
- 채점은 exact-set이 아니라 **monotonic DP instance alignment**(event_id 일치만 매칭, ①매칭 수 최대화 ②anchor 거리 최소화 ③evidence 겹침 최대화, 결정적 tie-break) 위에서 이루어집니다.
- 주 지표: `ordered_occurred_event_f1`(occurred 시퀀스 LCS F1). 보조: full-ledger P/R/F1, status macro-F1(no_event 포함), core/supporting evidence F1(unmatched gold=0인 end-to-end 포함), anchor 정확도/MAE, count MAE, edit distance, confidence/Brier/ECE. checkpoint별 **trajectory macro** → 20개 checkpoint 균등가중 AUC와 @300 최종점수를 보고하며, checkpoint를 넘나드는 instance pooling은 하지 않습니다.
- 종단 지표: detection lag(첫 복원 checkpoint − first-recoverable checkpoint), post-detection retention, status regression rate, evidence drift, hallucination persistence.

```bash
export RUN_ID=exp1
# 자연 문항 + distractor case 생성과 audit까지 (frozen 데이터만 사용)
make rq1-controlled RUN_ID=$RUN_ID

# 개별 단계
make build-rq1 RUN_ID=$RUN_ID              # rq1/natural/*.jsonl + taxonomy + manifest
make build-rq1-distractor RUN_ID=$RUN_ID   # rq1/distractor/cases.jsonl (filler bank 필요)
make audit-rq1 RUN_ID=$RUN_ID              # rq1/audit/rq1_{audit,decision}.*  (FAIL시 비정상 종료)

# 평가 — EXECUTE 없으면 offline mock 배관 점검
make evaluate-rq1 RUN_ID=$RUN_ID RQ1_CONDITION=full_prefix
make evaluate-rq1 RUN_ID=$RUN_ID RQ1_CONDITION=last_15 EXECUTE=1 \
     RQ1_PROVIDER=anthropic RQ1_MODEL=claude-sonnet-5 RQ1_MODEL_TAG=anthropic__claude-sonnet-5
```

dev/test split은 `manifest.json`에 고정됩니다(dev=`traj_001`, test=`traj_002`~`traj_020`; 프롬프트·파서·지표 확정은 dev에서만). `evaluate_rq1.py --split dev|test`로 필터링합니다. manifest에는 git commit, HF revision(고정된 경우만), trajectory/세션/gold 해시, taxonomy·prompt 해시, item 수까지 기록됩니다.

#### D.2 Distractor robustness (paired full / mask_distractor / sham)

hard-negative 세션 1개를 실험 단위로, 같은 checkpoint에서 세 가지 조건을 짝지어 비교합니다.

| 조건 | 내용 |
| --- | --- |
| `full` | 원본 prefix (hard negative 노출) |
| `mask_distractor` | 대상 hard-negative 슬롯만 persona-matched timeless filler로 치환 |
| `sham` | hard negative는 유지, 가장 가까운 eligible routine 슬롯을 **같은 donor**로 치환 |

- donor 선택·슬롯 치환은 §5-C의 lifecycle masking 기계(`_pick_filler`/`_neutralize`, CF filler bank)를 그대로 재사용합니다. donor는 조건 간 고정이며 한 context에 donor 중복은 없습니다.
- checkpoint는 기본적으로 대상 세션이 속한 window 끝(15의 배수)이고, prefix에 eligible routine 슬롯이 없으면(초기 window는 hard negative 밀도가 높음) 슬롯이 생길 때까지 15세션씩 결정적으로 연장합니다(`metadata.checkpoint_extended`).
- **gold event ledger는 세 조건에서 동일**해야 하며, case 생성 시 조건별 PrefixGold 재계산으로 검증하고 audit이 표본 재검증합니다. hard negative를 가짜 no_event 문항으로 바꾸지 않습니다.
- `near_miss_event_id`/`hard_negative_type`/`near_miss_explanation`은 case에 private으로만 저장됩니다.

```bash
for c in full mask_distractor sham; do
python scripts/evaluate_rq1.py \
  --items data/runs/$RUN_ID/rq1/distractor/cases.jsonl \
  --sessions-dir data/runs/$RUN_ID/dialogues/sessions \
  --fillers-dir data/runs/$RUN_ID/counterfactual_fillers/sessions \
  --condition $c --execute --provider anthropic --model claude-sonnet-5 \
  --output data/runs/$RUN_ID/rq1/predictions/anthropic__claude-sonnet-5/distractor_$c.jsonl \
  --report data/runs/$RUN_ID/rq1/reports/anthropic__claude-sonnet-5/distractor_$c.json
done
python scripts/score_rq1_distractor.py \
  --cases data/runs/$RUN_ID/rq1/distractor/cases.jsonl \
  --full …/distractor_full.jsonl --masked …/distractor_mask_distractor.jsonl \
  --sham …/distractor_sham.jsonl \
  --report data/runs/$RUN_ID/rq1/reports/…/distractor_paired.json
```

paired 분석은 case 단위 `distractor_cost = score(mask) − score(full)`, `replacement_artifact = score(full) − score(sham)`에 대해 trajectory-cluster bootstrap CI와 sign-flip permutation p-value를 보고하고, near-miss hallucination rate, hard-negative evidence attribution rate, false occurred rate, status/evidence 변화, non-target ledger invariance를 함께 계산합니다. natural 점수와 counterfactual 점수는 절대 하나의 headline으로 합치지 않습니다.

#### D.3 산출물 구조

```text
data/runs/<RUN_ID>/rq1/
├── manifest.json                  # 재현 manifest (해시·split·개수)
├── taxonomy.json                  # public taxonomy (event_id + label_ko)
├── natural/{progressive_items.jsonl,final_items.jsonl}
├── distractor/cases.jsonl (+ cases.exclusions.json)
├── predictions/<provider>__<model>/…jsonl
├── reports/<provider>__<model>/…json
└── audit/{rq1_audit.json,rq1_audit.md,rq1_decision.json}
```

프롬프트는 `prompts/benchmark/rq1_event_trajectory_ko.md`에 버전 관리되며 내용 SHA-256이 run metadata와 모든 report에 기록됩니다. item/prediction 파일은 `data/runs/` 아래 생성물이므로 git에 커밋하지 않습니다.

#### D.4 (임시) occurred-event 근거 짝 파일럿

RQ1 재설계 판단 전에 돌리는 **최소·임시** 프로토콜입니다
(`stage1_occurred_event_evidence_pairs`, `rq1-occurred-event-pairs-temp-v1`).
위 `stage1_event_trajectory`는 그대로 남아 있고, 이 파일럿은 같은
`natural/progressive_items.jsonl`을 재사용하면서 딱 한 가지만 묻습니다:

> 이 prefix에서 **실제로 일어난** 생애 사건과, 그 발생을 처음 확정하는 세션의
> 짝을 모두 복원할 수 있는가?

- gold: occurred 인스턴스 1개당 짝 1개. anchor는 그 인스턴스에 연결된 visible
  세션 중 `session_type == occurred_evidence`이고
  `event_status_after_session == occurred`인 **가장 이른** 세션. fallback 없음.
  cancelled / weak_signal / upcoming 인스턴스는 gold를 만들지 않습니다.
- 출력은 `{"pairs": [{"event_id", "evidence_session_id"}]}` 뿐입니다. status·
  confidence·설명은 받지 않습니다.
- headline은 `strict_occurred_event_evidence_f1` 하나. `collections.Counter`
  기반 exact multiset P/R/F1이므로 sibling 라벨·잘못된 근거 세션·중복 예측·무효
  레코드는 전부 precision을 깎고, 놓친 짝은 recall을 깎습니다. 부분점수 없음.
- 15..300 checkpoint별로 trajectory macro 평균을 낸 뒤 20개 checkpoint를 동일
  가중으로 평균(AUC)합니다. checkpoint를 가로질러 atom을 pooling하지 않습니다.

```bash
make audit-rq1-pairs                      # 프로토콜/프롬프트/gold 감사 (실패 시 exit 1)
make evaluate-rq1-pairs-dev               # offline mock
make evaluate-rq1-pairs-dev EXECUTE=1 RQ1_PROVIDER=anthropic RQ1_MODEL=claude-opus-4-8
```

산출물은 기존 파일럿을 덮지 않도록 `data/runs/<RUN_ID>/rq1_pair_temp/`
(`protocol_manifest.json`, `predictions/`, `reports/`, `audit/`)에 씁니다.

---

## 6. 데이터 정책

이 저장소는 코드만 담고, 데이터는 다음 규칙으로 관리합니다.

- **생성물은 git에 없음**: `data/runs/<RUN_ID>/`(persona, trajectory, 세션, gold, 문항, 리포트)는 모두 재생성 대상이라 추적하지 않습니다.
- **frozen 코퍼스는 HuggingFace에**: `HF_DIALOGUE_REPO`에는 `dialogues`(정답 제거 발화), `gold`(정답 라벨), `counterfactual_fillers`(v1 reserve sessions), `counterfactual_filler_plans` config가 있습니다. canonical session은 `dialogues`+`gold`를 join해 복원하고 filler는 frozen file layout 그대로 받습니다. 로컬 파일이 있으면 network no-op입니다. 명시적으로 받으려면 `make fetch-dialogues` 또는 `make fetch-counterfactual-fillers`.
- **revision pin**: 기본값은 HF main 최신 상태입니다. 재현 가능한 결과에는 업로드 commit SHA를 `HF_DIALOGUE_REVISION`으로 고정합니다.
- **frozen trajectory는 git에**: 확정된 20개 trajectory는 `tests/fixtures/trajectories/`에 byte 단위로 고정 추적됩니다.
- **참고 샘플**: `data/samples/`에 한 persona의 dialogues-only 예시 1건.

### 6.1 session_date (달력 날짜)

`dialogues`와 `gold`의 모든 세션 row는 `session_date`(`YYYY-MM-DD`)를 갖습니다. temporal reasoning 문항(선후 관계, 경과 기간, 연도)을 만들기 위한 필드입니다.

배치는 **종료일 정렬**입니다. 모든 trajectory의 마지막 세션이 `2026-06`에 놓이므로 시작 월이 trajectory마다 다릅니다(`1999-07` ~ `2019-02`) — 대신 모든 trajectory의 "현재"가 같고 미래 날짜가 없습니다. `age == 최초age + month_index // 12`가 이 코퍼스에서 정확히 성립하므로, 시작 월이 곧 페르소나의 생일 월이 됩니다. 종료일 정렬은 그것도 trajectory마다 다르게 만듭니다.

불변 조건:

- 달력 월 = `trajectory 시작월 + month_index` → 날짜로 계산한 개월 차가 `month_index`와 항상 일치
- 날짜는 세션 순서대로 **비감소**. 일자는 1~28일에 결정적으로 분산되며, 슬롯이 부족할 때만 같은 날을 공유합니다(6000건 중 20건)
- 대화가 같은 달의 특정 일자를 과거로 지목하면(예: "이번 달 10일에 처음 급여 들어왔어요") 그 날짜 이후로 배정 — 해당 28건 모두 충족

```bash
# 부여 (결정적, 같은 입력 → 같은 날짜)
python scripts/assign_session_dates.py \
    --dialogues-dir data/runs/$RUN_ID/dialogues --gold-dir data/runs/$RUN_ID/gold \
    --output-root data/runs/$RUN_ID/dated --manifest data/runs/$RUN_ID/session_dates.manifest.json

# 검증 (순서·month_index·age·발화 제약·gold 일치·기존 필드 무변경)
python scripts/audit_session_dates.py \
    --dialogues-dir data/runs/$RUN_ID/dated/dialogues --gold-dir data/runs/$RUN_ID/dated/gold \
    --baseline-dialogues-dir data/runs/$RUN_ID/dialogues --baseline-gold-dir data/runs/$RUN_ID/gold
```

`session_date`는 순수 추가 필드이고 기존 필드는 바뀌지 않았습니다. 날짜 없는 이전 상태가 필요하면 `HF_DIALOGUE_REVISION=f45f9603a8e6da31d244ca81e99f0c94c797475c`로 고정하세요.

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
| `data/runs/<RUN_ID>/benchmark_items/*.jsonl` | Stage 1 및 `stage2_memory_value` 문항 |
| `data/runs/<RUN_ID>/quality_reports/*` | 검증·audit 리포트 |
| `data/runs/<RUN_ID>/eval/report.json` | 모델 평가 결과 |
| `data/runs/<RUN_ID>/masking_ladder.json` | lifecycle masking abstention 사다리 (§5-C) |
| `data/runs/<RUN_ID>/masking_ladder_prefix_gold.jsonl` | counterfactual recipe + 재계산된 complete PrefixGold (§5-C) |
| `data/runs/<RUN_ID>/counterfactual_fillers/` | persona별 timeless filler plan/session/audit/log (§5-C) |

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
| `docs/rq1_pilot_report.md` | RQ1 파일럿 결과(traj_001 dev)와 지표 개선 과제 |
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
| HF filler를 못 받음 | dataset revision에 `counterfactual_fillers/v1/`이 있는지 확인하고, 고정한 `HF_DIALOGUE_REVISION`이 너무 오래된 commit은 아닌지 확인하세요. |

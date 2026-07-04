# Fin-Life Benchmark

**생애 사건(Life Event)에 따른 금융 메모리 유지와 위험 인지형 정기 금융 액션 결정**을
평가하는 벤치마크 생성 파이프라인입니다. 한국어(ko_KR) 우선, locale 확장 가능.

> 이 문서는 지금까지 구현된 모든 작업을 한눈에 이해할 수 있도록 자세히 설명합니다.
> 각 구성요소의 심화 설명은 `docs/` 아래 개별 문서에 있습니다(영문).

---

## 1. 이 벤치마크가 측정하는 것

한 사람의 인생은 시간에 따라 변합니다 — 이직하고, 이사하고, 결혼/이혼하고, 아이가
생기고, 은퇴합니다. 이런 **생애 사건**이 일어나면 은행이 기억하고 있던 정보(급여일,
집주인, 배우자, 대출 등)가 낡거나(stale) 바뀌고, 자동이체·자동저축 같은 **정기 금융
액션**도 손봐야 합니다. 그런데 이 결정에는 위험이 따릅니다: 돈이 나가는 변경을
**사용자 확인 없이 함부로 실행하면 안 됩니다**.

이 벤치마크는 모델(에이전트)이 다음을 잘 하는지 봅니다.

1. 대화 이력만 보고 **어떤 생애 사건이, 어느 단계까지 진행됐는지** 파악하는가
2. 그에 맞춰 **금융 메모리를 올바르게 갱신**하는가 (낡은 값 폐기, 검증 필요 표시 등)
3. **정기 금융 액션을 안전하게 처리**하는가 (확인 요청 / 유지 / 변경)

핵심 실패 유형은 **낡은 메모리 잔존(stale carryover)**, **조급한 확정(premature
commitment)**, **위험한 자동 실행(unsafe execution)**, **과거 상태 오염(historical
contamination)** 입니다. (`docs/failure_modes.md`)

핵심 철학: **State first, dialogue second.** 먼저 숨겨진 인생/금융 상태의 궤적을
시뮬레이션하고, 그로부터 은행 대화를 "간접 증거"로 생성합니다. 절대 그 반대가 아닙니다.

---

## 2. 전체 파이프라인 한눈에 보기

```
 Nemotron 한국 페르소나 (nemotron-personas-korea/, 11만 명)
   │  normalize_personas.py
   ▼
 NormalizedPersona  (나이/혼인/고용/주거/재무를 정규화 + 결측 추론)
   │  generate_initial_states.py
   ▼
 초기 금융 메모리 상태  +  초기 정기 금융 액션(standing actions)
   │  simulate_trajectories.py         (월 단위 hazard FSM)
   │  generate_coverage_trajectories.py (life_generator 에피소드 강제 주입)
   ▼
 Trajectory  = 생애 사건 인스턴스(weak_signal→upcoming→occurred/cancelled)
              + 매달 메모리 델타 + 정기 액션 impact + 상태 스냅샷
   │  generate_dialogue_sessions.py    (mock 또는 LLM)
   ▼
 은행 대화 세션(간접 증거만; 사건명·FA코드 노출 금지)
   │  validate_dialogues.py            (누출/일관성 검증)
   ▼
 export_prefix_gold.py
   ▼
 Prefix Gold  = "여기까지 봤을 때"의 정답 상태(사건 상태/메모리 갱신/액션 결정)
   │  build_benchmark_items.py
   ▼
 벤치마크 아이템  Stage1 / Stage2 / Stage3 + 반사실(counterfactual) MCQ
   │  run_history_filter.py            (히스토리 없이 풀리는 문제 걸러내기)
   │  audit_*.py, build_quality_summary.py
   ▼
 품질 리포트 (data/generated/quality_reports/)
```

---

## 3. 3-Stage 태스크 정의

| 단계 | 입력 | 출력(정답) |
| --- | --- | --- |
| **Stage 1 — 사건 상태 감지** | 세션 prefix | 생애 사건 라벨 / lifecycle 상태 / 발생 여부 / 증거 세션 |
| **Stage 2 — 금융 메모리 갱신** | prefix + 초기 메모리 | 메모리 갱신 목록(경로, 연산, 값) |
| **Stage 3 — 정기 액션 결정** | prefix + 메모리 + 정기 액션 | 액션별 결정(keep/update/pause/cancel/ask_confirmation) |
| **Stage 3 MCQ** | prefix + 질문 | 반사실 보기(낡은 메모리·위험 실행 오답 포함) |

"prefix"란 세션을 시간순으로 1개, 2개, … k개까지 본 상태를 뜻합니다. **정답은
prefix마다 달라집니다** — 사건이 weak_signal이던 시점엔 갱신 금지, occurred가 되면
갱신 허용, 이런 식으로 gold가 시간에 따라 변합니다.

---

## 4. 데이터 모델

`src/fin_life_benchmark/`의 pydantic 모델. 모든 레코드는 JSON 직렬화됩니다.

- **NormalizedPersona** (`persona/models.py`): Nemotron 필드에서 정규화한 가상의
  페르소나. 결측 필드는 uuid 기반 결정적(deterministic) 휴리스틱으로 채우고
  `normalization_notes`에 기록. 실제 개인정보는 만들지 않습니다.
- **FinancialMemoryState** (`memory/models.py`): 경로별 **셀 이력**(cell history).
  각 셀은 값·상태(`current/historical/stale/needs_verification/pending/cancelled/
  unknown`)·신뢰도·유효기간·provenance를 가집니다. **값을 지우지 않고** 갱신 시 옛
  값을 historical로 보존 → 나중에 "낡은 값 오답(distractor)"으로 재활용.
- **StandingAction** (`actions/models.py`): 정기 금융 액션(월세 자동이체, 급여 연동
  자동저축 등)을 **1급 객체**로 취급. 연결된 메모리 경로·위험도·funds_movement·유효성·
  감사 로그(history)를 가집니다.
- **EventInstance** (`fsm/models.py`): 샘플링된 생애 사건 1건 + 전체 lifecycle 상태 이력.
- **Trajectory** (`trajectory/models.py`): 페르소나 + 초기 상태 + 사건 인스턴스 +
  월별 timeline step(전환·델타·impact) + 월별 상태/메모리/액션 스냅샷.
- **PrefixGold**: 각 세션 prefix 시점의 정답 상태.
- **BenchmarkItem** (`benchmark/models.py`): Stage 아이템과 MCQ 반사실 보기.

---

## 5. 구성요소 상세 (무엇을 / 왜 / 어떻게)

### 5.0 `life_event_graph` shim — 유실 의존성 복원
기존 `life_generator/`는 `life_event_graph.build_graphs()`에 의존하는데 그 패키지가
repo에 없었습니다. `life_generator/README.md`의 Node-Action 표로부터 노드 레지스트리를
재구성한 shim(`life_event_graph/`)을 만들어 `life_generator`를 다시 동작시켰습니다.
(자세히: `docs/repo_inventory.md`)

### 5.1 Persona adapter — Nemotron → NormalizedPersona
`persona/nemotron_adapter.py`가 parquet(11만 명)에서 나이·성별·혼인상태·가구유형·
주거유형·직업·지역과 페르소나 텍스트 힌트를 읽어 구조화합니다. 결측은 uuid 시드 기반
결정적 추론(예: 고령+무직→retired, 가구유형에 자녀→자녀 나이 샘플링)으로 채웁니다.

### 5.2 초기 금융 상태 / 액션 생성
`memory/initial_state_generator.py`, `actions/initial_actions_generator.py`가 상태
일관성 규칙에 따라 초기 메모리와 액션을 만듭니다. 예: 급여일/급여계좌는 고용 중일
때만, 월세 자동이체는 월세 세입자만, 배우자 생활비 이체는 기혼+동거일 때만.

### 5.3 생애 상태 FSM + 월 단위 시뮬레이터
`fsm/life_state_machine.py`, `trajectory/simulator.py`가 **월 단위 tick**으로 궤적을
생성합니다. 마르코프 전이표가 아니라, **현재 상태·나이·쿨다운·활성 사건·페르소나
보정**으로 hazard(월 발생확률)를 계산합니다.

```
monthly_probability ≈ base_rate/12 × age_weight × state_mod × persona_mod
```

> 주의: 이 확률/기간 값들은 **경험적 통계가 아니라 휴리스틱한 개연성 가중치**입니다.
> (`configs/registries/life_events.yaml` 상단 disclaimer, `docs/life_state_fsm.md`)

**상태 가드**로 불가능한 사건을 막습니다(예: 미혼자 이혼 금지, 주택 미보유 매각 금지,
자녀 없는 교육단계 진입 금지). `scripts/audit_life_stage_constraints.py`로 위반 0을 검증.

### 5.4 사건 Lifecycle (weak_signal / upcoming / occurred / cancelled)
사건은 `inactive → weak_signal → upcoming → occurred`로 진행하며, weak_signal/upcoming에서
`cancelled`로 갈 수 있습니다. 이 상태가 **갱신 허용 여부를 결정**합니다:

| 상태 | 허용 메모리 연산 | 고위험 액션 변경 |
| --- | --- | --- |
| weak_signal | (선택) set_pending / needs_verification | 불가 |
| upcoming | set_pending / needs_verification | 불가 |
| occurred | update / mark_stale / archive / needs_verification / reactivate | 확인 요청 후에만 |
| cancelled | clear_pending 만 | 불가 |

(`docs/event_lifecycle.md`)

### 5.5 델타 엔진 / impact 엔진 + 위험 정책
`memory/delta_engine.py`는 사건 전환 시 메모리 델타를 적용하되 **위 lifecycle 정책을
강제**합니다(레지스트리가 뭐라 하든 weak_signal에서 확정 갱신 불가). `actions/
impact_engine.py`는 사건이 기존 액션에 주는 영향을 계산합니다.

**위험 정책(핵심)**: `funds_movement=true`(돈이 나가는 액션)는 고위험 →
사용자 확인 없이 실행 금지. impact 엔진이 `must_not_execute=True`를 강제하고 `execute`
기대를 `ask_confirmation`으로 치환합니다. audit이 "gold 내 확인 없는 고위험 실행 = 0"을
검증합니다. (`docs/financial_memory_schema.md`, `docs/standing_action_schema.md`)

### 5.6 Episode 주입 & Coverage 생성 (life_generator 활용) — **희소 케이스 확보**
가장 중요한 최근 추가 기능입니다. hazard 샘플러만으로는 **post_occurred**(발생한
사건이 기존 정기 액션을 건드리는 경우 = Stage3에서 "확인 요청"이 정답인 유일한 상황)가
너무 드물게 나옵니다. 이유는 두 가지가 동시에 필요하기 때문입니다:

1. **사건이 실제로 발생** — `life_generator`(생애 사건 그래프)의 순서 보장된 에피소드
   (전세→구매→매각, 취업→교육→이직 등)를 궤적에 **강제 주입**해서 보장.
   `trajectory/episode_bridge.py` + `simulate_trajectories.py --mode episode`.
   forced 이벤트는 hazard 롤을 우회하되 **상태 가드는 준수**하고,
   `plan_lifecycle(force_occur=True)`로 반드시 occurred까지 진행.
2. **그 사건이 impact하는 액션을 페르소나가 이미 보유** — 액션 소유 페르소나와 사건을
   **페어링**. `scripts/generate_coverage_trajectories.py`가 impact 레지스트리의 각
   (사건→액션) 쌍마다 해당 액션을 가진 페르소나를 골라 에피소드를 강제합니다.

측정 효과(20명 풀, 12년):

| 방식 | post_occurred 쌍/궤적 |
| --- | --- |
| hazard만 | 0.90 |
| 에피소드 주입만 | 0.95 (거의 안 늘어남 → 병목은 액션 쪽) |
| **coverage(사건×액션 페어링)** | **2.77 (약 3배)** |

또한 occurred 전환 시점에 상태 가드를 재확인해, 시작 땐 유효했지만 그 사이 다른
사건에 추월당한 모순(예: 실직 중 시작한 "취업/복직"이 발생 전 다른 경로로 취업됨)을
`cancelled`로 강등합니다. (`docs/coverage_generation.md`)

### 5.7 Evidence planner + 대화 생성
`dialogue/evidence_planner.py`가 사건별 다중 세션 증거 계획을 세웁니다. 핵심 설계:
**모든 사건이 한 세션으로 복원되면 안 됩니다** — "drift 사건"은 단서를 여러 세션에
쪼개 넣어 누적 이력으로만 식별되게 합니다. hard negative(같은 액션을 쓰지만 사건 없음),
stale recall(과거 값 회상) 세션도 여기서 계획합니다.

`dialogue/generator.py`는 세 모드로 대화를 만듭니다:
- **mock**: API 없이 결정적 템플릿 대화 (smoke 기본값, 빠름)
- **dry_run**: 프롬프트만 `data/raw_model_outputs/`에 기록
- **llm** (`--execute`): `.env`의 OpenAI/Anthropic 호출, JSON 파싱 실패 시 1회 repair

대화 규칙: 고객은 은행 업무를 보러 온 것이지 인생을 설명하지 않음. 사건 라벨·FA코드·
메타데이터는 노출 금지, 상담원은 사건을 요약/명명하지 않음, 고위험 변경은 자동 실행
금지. (`docs/dialogue_generation_strategy.md`, 프롬프트: `prompts/dialogue/`)

### 5.8 대화 검증
`validation/dialogue_validator.py`가 JSON 구조·화자 교대·사건 라벨/FA코드 누출·상담원
요약 발화·이모지/초성체·단서 위치·상태 일관성·고위험 무확인 실행을 점검하고
`quality_reports/dialogue_quality_report.{json,md}`에 기록합니다.

### 5.9 Prefix Gold + **저장 최적화**
`gold/prefix_gold_exporter.py`가 각 세션 prefix 시점의 gold(사건 상태/메모리 갱신/액션
결정 + 전체 상태 스냅샷)를 만듭니다.

**최적화**: prefix의 약 91%는 사건 사이의 routine 세션이라 gold 페이로드가 직전과
동일합니다. 동일한 경우 5개 gold_* 필드를 비워 두고 `repeats_previous` 플래그만 남기며,
`gold/loader.py:read_prefix_gold()`가 로드 시 carry-forward로 복원합니다.
→ **gold 파일 212MB → 53MB (4배 감소), 아이템 결과는 완전히 동일.**

### 5.10 벤치마크 아이템 + **Context-dependent MCQ** — **누출 방지 재설계**
`benchmark/item_builder.py`가 Stage1/2/3 아이템과 Stage3 MCQ를 만듭니다.

MCQ의 **초기 설계 문제**: 정답("needs_verification 표시 + 사용자 확인 요청")이 문구상
유일하게 신중해 보여서, 세션을 안 봐도 옵션만으로 정답을 골랐습니다(실측 100% 누출).

**재설계**: 모든 MCQ가 **동일한 5개 실무 보기**(그대로 실행 / 지금 변경 / 다음 회차 전
확인 / 보류 / 해지)를 쓰고, **정답은 사건 lifecycle context에 따라 달라집니다**:

| context | 정답 | 근거 |
| --- | --- | --- |
| post_occurred | 확인 요청 | 발생한 사건이 설정을 무효화했을 수 있음 (돈이 나감) |
| pre_occurred | 그대로 유지 | 아직 weak_signal/upcoming — 조치할 것 없음 |
| cancelled | 그대로 유지 | 신호 소멸, 조치 시 stale pending에 작용 |
| no_event | 그대로 유지 | hard negative, 아무 일 없음 |

옵션 문구가 context마다 같으므로 히스토리 없이는 정답 판별 불가. `keep_to_confirm_ratio`
(기본 2.0)로 decision 균형을 맞추고(라운드로빈으로 4개 context 모두 유지),
`build_quality_summary.py`가 majority baseline을 리포트합니다. (`docs/mcq_design.md`)

**검증(실제 gpt-4o-mini)**: 히스토리 없이 옵션만 볼 때 정답률 **25%** ≪ majority
baseline **67%** → "항상 keep"으로 찍는 것보다도 못함 = **옵션 누출 없음** 확인.

### 5.11 History filter — 히스토리 없이 풀리는 문제 걸러내기
`validation/history_filter.py` — 축소된 맥락에서도 풀리는 문제를 걸러내는 consensus 필터.
세 모드: `single_session`(마지막 세션만), `partial_prefix`(초기 증거 제거),
`no_history_option`(질문+보기만). 검증자(provider:model)가 축소된 맥락에서 문제를
풀어봐서 **majority baseline을 초과해 맞히면** 누출로 판정합니다.

**중요**: 개별 아이템의 leakage 플래그가 아니라 **aggregate 판정**(리포트의
`beats_baseline_without_history`)을 봐야 합니다 — decision prior가 한 context와 우연히
맞으면 per-item 플래그가 과다 보고됩니다. 검증자는 **2~3개 이상** 사용 권장. API 키가
없으면 mock 검증자로 대체하고 리포트에 placeholder 표시. (`docs/history_filter.md`)

### 5.12 Audits + 품질 리포트
`audit_single_session_recoverability.py`, `audit_full_prefix_recoverability.py`,
`audit_stale_distractors.py`, `audit_life_stage_constraints.py`,
`build_quality_summary.py`가 라벨/상태/연산/결정/위험 분포, 단일세션 해결률, 누적
복원률, stale distractor 가용성, 생애단계 위반(반드시 0), MCQ context/decision 분포와
majority baseline을 리포트합니다.

---

## 6. 현재 통합 데이터셋 현황 (`data/generated/`)

Nemotron 500명 정규화 후 생성한 **통합 데이터셋** (자연 hazard + 보장 coverage 혼합):

| 항목 | 수치 |
| --- | --- |
| Trajectory | **77** (hazard 30 자연 + coverage 47 보장) |
| 대화 세션 | 23,100 (mock; 검증 통과율 ≈ 100%) |
| Prefix gold | 23,100 (53MB, dedup 적용) |
| Stage 1 아이템 | 8,422 |
| Stage 2 아이템 | 888 |
| Stage 3 아이템 | 219 |
| Stage 3 MCQ | **504** (post_occurred 168 / pre 157 / no_event 156 / cancelled 23) |
| MCQ decision 균형 | keep 336 : ask_confirmation 168 → majority baseline **66.7%** |
| 서로 다른 gold 사건 | 484개 (24개 라벨 전부 등장) |
| 생애단계 위반 | **0** |
| impact pair 커버리지 | 24/25 |

> 위 수치는 아래 "통합 데이터셋 재현" 절차로 나온 예시입니다. mock 대화는 템플릿이라
> 문장이 단조롭습니다. 실제 품질 대화는 `EXECUTE=1`(LLM)이 필요합니다.

---

## 7. 실행 가이드라인

> **중요**: 생성 산출물(`data/generated/`, `data/personas/normalized/`,
> `data/raw_model_outputs/`)은 **git으로 추적하지 않습니다.** clone 직후에는
> 비어 있으며, 아래 파이프라인을 돌려 **직접 생성**해야 합니다. 코드·설정·프롬프트·
> 문서만으로 전부 재현됩니다. Nemotron parquet 원본도 추적하지 않으므로 별도로
> 내려받아 `Nemotron-Personas-Korea/data/`에 두어야 합니다(아래 0단계).

### 0. 사전 준비 (한 번만)

```bash
# Python 3.11+
make setup                       # 의존성 설치 + .env 생성(.env.example 복사)

# 원본 페르소나 데이터(약 1.9GB, git 미추적) 내려받기 — 이미 있으면 생략
hf download nvidia/Nemotron-Personas-Korea --repo-type dataset \
  --local-dir Nemotron-Personas-Korea
# 스펙식 소문자 경로를 쓰려면 심볼릭 링크(선택)
ln -sfn Nemotron-Personas-Korea nemotron-personas-korea
```

`.env`에 LLM 키를 넣으면 실제 대화 생성이 가능합니다(선택). 키가 없어도 mock 모드로
전체 파이프라인이 끝까지 돕니다. **키·토큰은 절대 커밋되지 않습니다(`.env`는 gitignore).**

### 1. 빠른 스모크 (mock, API·키 불필요, 수 초~수십 초)

```bash
make pipeline-smoke              # 정규화→상태→시뮬→대화(mock)→검증→gold→아이템→필터→audit
```

작은 규모로 파이프라인 전 구간이 도는지 확인하는 용도입니다.

### 2. 통합 데이터셋 재현 (자연 hazard + 보장 coverage)

```bash
make normalize-personas LIMIT=500                  # 페르소나 500명 정규화
make simulate-smoke NUM_TRAJ=30 HORIZON=10         # 자연스러운 hazard 궤적 30개
make coverage-trajectories                          # 희소 post_occurred 보장 (같은 폴더에 추가)
make dialogue-smoke                                 # mock 대화 생성
make export-gold                                    # prefix gold (dedup 저장)
make build-items                                    # Stage1/2/3 + MCQ 아이템
make history-filter                                 # 히스토리 필요성 필터 (mock)
make audit                                          # 품질 리포트 생성
```

각 target의 규모/시드는 변수로 조절합니다:
`LIMIT`(페르소나 수), `NUM_TRAJ`, `HORIZON`(년), `SEED`. 예) `make simulate-smoke
NUM_TRAJ=100 HORIZON=12 SEED=7`.

### 3. 실제 LLM 대화·필터 (`.env`에 유효한 키 필요)

```bash
make dialogue-smoke EXECUTE=1     # OpenAI/Anthropic로 실제 대화 생성
make history-filter EXECUTE=1     # 실제 검증자(2~3개 권장)로 누출 필터
```

`.env`의 `DEFAULT_LLM_PROVIDER`/`DEFAULT_GENERATION_MODEL`,
`HISTORY_FILTER_VALIDATORS`(예: `openai:gpt-4o-mini,anthropic:claude-haiku-4-5`)로
제공자·모델을 지정합니다. 키가 없으면 자동으로 mock으로 대체되고 리포트에 표시됩니다.

### 4. 개별 스크립트 직접 실행

Makefile을 거치지 않고 각 스크립트를 `--help`로 확인해 직접 부를 수 있습니다.

```bash
python scripts/simulate_trajectories.py --help
python scripts/generate_coverage_trajectories.py \
  --personas data/personas/normalized/personas_ko_KR.jsonl \
  --locale ko_KR --horizon-years 12 \
  --output-dir data/generated/trajectories --seed 500 --max-per-pair 2
```

전체 스크립트: `normalize_personas` · `generate_initial_states` ·
`simulate_trajectories`(`--mode hazard|episode`, `--coverage`) ·
`generate_coverage_trajectories` · `generate_dialogue_sessions`
(`--mock|--dry-run|--execute`) · `validate_dialogues` · `export_prefix_gold` ·
`build_benchmark_items` · `run_history_filter` · `audit_*` · `build_quality_summary`.

### 5. 테스트

```bash
make test        # pytest 20개 (스키마·엔진·시뮬레이터·gold dedup·coverage·MCQ)
```

### 산출물 위치 (모두 git 미추적)

```
data/personas/normalized/personas_ko_KR.jsonl
data/generated/trajectories/traj_*.json          (traj_cov_* = coverage)
data/generated/sessions/sessions_traj_*.jsonl
data/generated/gold/prefix_gold.jsonl            (read_prefix_gold로 읽을 것)
data/generated/benchmark_items/stage{1,2,3}_*.jsonl
data/generated/quality_reports/*.md, *.json
```

### 자주 겪는 문제

- `no persona files ... under nemotron-personas-korea` → 0단계 원본 데이터 미다운로드.
- `--execute requires DEFAULT_LLM_PROVIDER=openai|anthropic` → `.env` 키/제공자 미설정
  (mock으로 돌리려면 `EXECUTE` 없이 실행).
- `make clean-generated`는 정규화 페르소나까지 지웁니다 → 이후 `make normalize-personas` 재실행.

---

## 8. 확장 방법

- **새 locale 추가**: `configs/locales/ko_KR.yaml`을 복사해 통화·은행 용어·값 풀을
  채우고 `--locale`로 지정. 국가별 로직은 모두 locale config 안에. (`docs/locale_extension_guide.md`)
- **새 생애 사건 추가**: `life_events.yaml`에 가드·lifecycle·단서·매핑 추가 →
  `event_to_memory_delta.yaml` / `event_to_action_impact.yaml`에 템플릿 추가 →
  필요 시 `fsm/event_lifecycle.py`의 상태 효과 확장 → `make test`로 정합성 검증.

---

## 9. 생성 데이터 들여다보기

```bash
# 한 궤적의 사건 lifecycle 보기
python - <<'EOF'
import json
t = json.load(open('data/generated/trajectories/traj_00042.json'))
for e in t['life_event_instances']:
    print(e['label_ko'], [(h['month_index'], h['status']) for h in e['status_history']])
EOF

# MCQ 한 문제 보기
python - <<'EOF'
import json
r = json.loads(open('data/generated/benchmark_items/stage3_action_mcq.jsonl').readline())
print(r['metadata']['context'], '| 정답:', r['gold']['correct_option'])
print(r['question'])
for o in r['options']:
    print(' ', o['option_id'], '✓' if o['correct'] else ' ', o.get('error_type') or 'correct', '|', o['text'])
EOF
```

세션·gold·아이템은 모두 평문 JSONL이라 `jq`/pandas로 바로 다룰 수 있습니다.
prefix gold는 반드시 `gold.loader.read_prefix_gold()`로 읽어 carry-forward를 복원하세요.

---

## 10. 알려진 한계

- hazard 확률·lifecycle 기간은 **경험적 통계가 아닌 휴리스틱 개연성 가중치**입니다.
- mock 대화는 템플릿이라 문장이 단조롭습니다. 자연스러운 대화는 `EXECUTE=1` LLM 생성 필요.
- coverage 궤적은 타임라인 압축·강제 발생을 쓰므로 약간 인위적입니다 — hazard 궤적과
  **섞어서** 사용하세요(현재 통합 데이터셋이 그 예).
- impact pair 24/25 커버. 남은 1개(이혼→양육비)는 양육비 이체가 이혼으로 **생성**되는
  액션이라 "기존 액션 impact"로는 구조적으로 만들기 어렵습니다.
- MCQ는 decision 불균형이 남습니다(post_occurred만 confirm 정답). **context별
  macro-평균 정확도**로 평가하세요.
- 24개 사건 중 MVP 10개는 완전한 델타/impact 템플릿, 나머지 14개는 유효한 stub(P2).
- `en_US` locale은 템플릿만 존재(생성은 한국어 전용).
- 누출 검사는 부분 문자열 기반이라 드물게 오탐/미탐 가능(2만3천 중 1건 관측).

---

## 11. 문서 인덱스 (`docs/`)

| 문서 | 내용 |
| --- | --- |
| `design_overview.md` | 설계 불변식, 모듈 맵, 데이터플로 계약 |
| `repo_inventory.md` | 초기 repo 인벤토리, life_generator shim, 재사용/신규 |
| `life_state_fsm.md` | 월 단위 hazard FSM, 상태 가드, life_generator를 왜 대체했나 |
| `event_lifecycle.md` | lifecycle 상태와 갱신 허용 규칙 |
| `financial_memory_schema.md` | 메모리 셀·연산·이력 보존 |
| `standing_action_schema.md` | 정기 액션 타입·일관성 규칙·impact |
| `coverage_generation.md` | **life_generator 에피소드 주입 × 액션 페어링으로 post_occurred 확보** |
| `dialogue_generation_strategy.md` | 증거 계획, drift/hard-negative, mock/LLM, 검증 |
| `mcq_design.md` | **context-dependent MCQ 재설계와 누출 방지** |
| `history_filter.md` | 히스토리 필요성 필터, aggregate 판정 해석법 |
| `failure_modes.md` | 진단용 실패 유형 정의 |
| `locale_extension_guide.md` | 새 locale 추가 절차 |

# Fin-Life Benchmark

**생애 사건(Life Event)에 따른 금융 메모리 유지**를 평가하는 벤치마크 생성
파이프라인입니다. 한국어(ko_KR) 우선, locale 확장 가능.

> 이 문서는 지금까지 구현된 모든 작업을 한눈에 이해할 수 있도록 자세히 설명합니다.
> 각 구성요소의 심화 설명은 `docs/` 아래 개별 문서에 있습니다(영문).

---

## 1. 이 벤치마크가 측정하는 것

한 사람의 인생은 시간에 따라 변합니다 — 이직하고, 이사하고, 결혼/이혼하고, 아이가
생기고, 은퇴합니다. 이런 **생애 사건**이 일어나면 은행이 기억하고 있던 정보(급여일,
집주인, 배우자, 대출 등)가 낡거나(stale) 바뀝니다. 이 벤치마크는 그런 변화가
대화 이력 속에서 어떻게 드러나는지, 그리고 모델이 그 이력을 바탕으로 금융 메모리를
올바르게 갱신할 수 있는지를 평가합니다.

이 벤치마크는 모델(에이전트)이 다음을 잘 하는지 봅니다.

1. 대화 이력만 보고 **어떤 생애 사건이, 어느 단계까지 진행됐는지** 파악하는가
2. 그에 맞춰 **현재 금융 메모리 상태를 올바르게 재구성**하는가 (낡은 값 폐기, 검증 필요 표시 등)

핵심 실패 유형은 **낡은 메모리 잔존(stale carryover)**, **조급한 확정(premature
commitment)**, **과거 상태 오염(historical contamination)** 입니다.
(`docs/failure_modes.md`)

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
 초기 금융 메모리 상태
   │  simulate_trajectories.py         (월 단위 hazard FSM)
   │  generate_coverage_trajectories.py (life_generator 에피소드 강제 주입)
   ▼
 Trajectory  = 생애 사건 인스턴스(weak_signal→upcoming→occurred/cancelled)
              + 매달 메모리 델타 + 상태 스냅샷
   │  generate_dialogue_sessions.py    (mock 또는 LLM)
   ▼
 은행 대화 세션(간접 증거만; 사건명·FA코드 노출 금지)
   │  validate_dialogues.py            (누출/일관성 검증)
   ▼
 export_prefix_gold.py
   ▼
 Prefix Gold  = "여기까지 봤을 때"의 정답 상태(사건 상태/메모리 갱신)
   │  build_benchmark_items.py
   ▼
 벤치마크 아이템  Stage1 / Stage2
   │  audit_*.py, build_quality_summary.py
   ▼
 품질 리포트 (data/generated/quality_reports/)
```

---

## 3. 2-Stage 태스크 정의

| 단계 | 입력 | 출력(정답) |
| --- | --- | --- |
| **Stage 1 — 사건 상태 감지** | 세션 prefix | 생애 사건 라벨 / lifecycle 상태 / 발생 여부 / 증거 세션 |
| **Stage 2 — 금융 메모리 MCQ** | 세션 prefix + 초기 메모리 | 현재 메모리 상태를 묻는 single-hop/multi-hop 객관식 정답 |

"prefix"란 세션을 시간순으로 1개, 2개, … k개까지 본 상태를 뜻합니다. **정답은
prefix마다 달라집니다** — 사건이 weak_signal이던 시점엔 갱신 금지, occurred가 되면
갱신 허용, 이런 식으로 gold가 시간에 따라 변합니다.

Stage 2는 **memory update schema를 한 줄씩 맞히는 채점**이 아닙니다. `prefix_gold`의
메모리 델타는 gold 상태를 만들기 위한 내부 근거이고, 실제 평가 아이템은 그 결과로
형성된 현재 메모리 셀을 보기로 제시합니다. 보기에는 stale value, missed update,
premature update, wrong sibling event 같은 hard negative를 섞어, 대화 이력 없이
그럴듯한 금융 상식만으로는 풀기 어렵게 만듭니다.

Stage 3(정기 금융 액션 결정) 관련 코드는 실험적으로 남아 있을 수 있지만, 현재 공식
평가 산출물에는 포함하지 않습니다. 추후 Stage 3를 다시 포함하려면 action impact
coverage, decision balance, option leakage 방지를 별도 작업으로 정리합니다.

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
  자동저축 등)을 표현하는 보조 모델입니다. 현재 공식 평가는 Stage 2까지이므로,
  이 모델은 초기 금융 상태와 향후 Stage 3 확장을 위한 기반으로만 사용합니다.
- **EventInstance** (`fsm/models.py`): 샘플링된 생애 사건 1건 + 전체 lifecycle 상태 이력.
- **Trajectory** (`trajectory/models.py`): 페르소나 + 초기 상태 + 사건 인스턴스 +
  월별 timeline step(전환·메모리 델타) + 월별 상태/메모리 스냅샷.
- **PrefixGold**: 각 세션 prefix 시점의 정답 상태.
- **BenchmarkItem** (`benchmark/models.py`): Stage 1/2 평가 아이템. Stage 2 아이템은
  `options`와 `gold.correct_option`을 가진 MCQ입니다.

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

### 5.2 초기 금융 메모리 생성
`memory/initial_state_generator.py`가 상태 일관성 규칙에 따라 초기 금융 메모리를
만듭니다. 예: 급여일/급여계좌는 고용 중일 때만, 월세 관련 정보는 월세 세입자에게만
생성됩니다.

정기 금융 액션 생성기(`actions/initial_actions_generator.py`)도 코드에는 남아 있지만,
현재 공식 평가 범위는 Stage 2까지입니다. 따라서 액션 관련 데이터는 향후 Stage 3
확장을 위한 보조 정보로 봅니다.

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

| 상태 | 허용 메모리 연산 | 확정 갱신 정책 |
| --- | --- | --- |
| weak_signal | (선택) set_pending / needs_verification | 확정 update 금지 |
| upcoming | set_pending / needs_verification | 확정 update 금지 |
| occurred | update / mark_stale / archive / needs_verification / reactivate | 발생 이후 갱신 허용 |
| cancelled | clear_pending 만 | 발생한 사건처럼 update 금지 |

(`docs/event_lifecycle.md`)

### 5.5 델타 엔진
`memory/delta_engine.py`는 사건 전환 시 메모리 델타를 적용하되 **위 lifecycle 정책을
강제**합니다(레지스트리가 뭐라 하든 weak_signal에서 확정 갱신 불가).
(`docs/financial_memory_schema.md`)

`actions/impact_engine.py`와 action impact registry는 실험적으로 남아 있습니다.
다만 현재 공식 benchmark item은 Stage 1/2까지만 사용하므로, action impact 결과는
공식 평가 지표로 사용하지 않습니다.

### 5.6 Episode 주입 & Coverage 생성 (life_generator 활용)
hazard 샘플러만으로는 특정 생애 사건이 충분히 다양하게 발생하지 않을 수 있습니다.
`life_generator`의 순서 보장된 에피소드(예: 전세→구매→매각, 취업→교육→이직)를
trajectory에 강제 주입하면 Stage 1/2에서 필요한 사건 상태와 메모리 변화 coverage를
더 안정적으로 확보할 수 있습니다.

`trajectory/episode_bridge.py`와 `simulate_trajectories.py --mode episode`는 forced
event를 만들되 상태 가드는 준수합니다. 또한 occurred 전환 시점에 상태 가드를 다시
확인해, 시작 땐 유효했지만 그 사이 다른 사건에 추월당한 모순을 `cancelled`로
강등합니다. (`docs/coverage_generation.md`)

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
`gold/prefix_gold_exporter.py`가 각 세션 prefix 시점의 gold(사건 상태/메모리 갱신 +
전체 상태 스냅샷)를 만듭니다.

**최적화**: prefix의 약 91%는 사건 사이의 routine 세션이라 gold 페이로드가 직전과
동일합니다. 동일한 경우 5개 gold_* 필드를 비워 두고 `repeats_previous` 플래그만 남기며,
`gold/loader.py:read_prefix_gold()`가 로드 시 carry-forward로 복원합니다.
→ **gold 파일 212MB → 53MB (4배 감소), 아이템 결과는 완전히 동일.**

### 5.10 벤치마크 아이템
`benchmark/item_builder.py`가 Stage 1/2 아이템을 만듭니다.

- Stage 1: prefix에서 감지되는 생애 사건과 lifecycle status를 묻습니다.
- Stage 2: prefix와 초기 금융 메모리를 바탕으로 **현재 금융 메모리 상태**를 묻는
  MCQ를 만듭니다.

Stage 2 MCQ 생성은 다음 절차를 따릅니다.

1. `prefix_gold`를 시간순으로 읽으면서 trajectory별 누적 `gold_memory_updates` 수가
   증가한 prefix만 후보로 삼습니다. 즉 실제로 새 메모리 변화가 관측된 지점에서만
   문항을 냅니다.
2. 후보 prefix의 `gold_full_memory_state`에서 현재 값·상태·historical value를 읽어
   정답 보기를 만듭니다. 정답은 update JSON 자체가 아니라, 해당 prefix 시점의 현재
   메모리 셀입니다.
3. **single-hop MCQ**는 새로 들어온 업데이트 중 하나의 path를 골라 현재 상태를 묻습니다.
   보기에는 이전 값(stale carryover), 갱신 누락(missed update), 검증 필요/확정 상태
   혼동(premature update 또는 false commit), 다른 경로 값 오염(wrong sibling)을 넣습니다.
4. **multi-hop MCQ**는 최근 서로 다른 두 memory path를 함께 묻습니다. 한쪽만 stale인
   보기, 두 값을 뒤섞은 보기, 둘 다 갱신하지 않는 보기를 넣어 여러 세션의 누적 단서를
   종합해야 풀리게 합니다.
5. 정답 위치는 seed 기반으로 섞고, `gold.correct_option`에 A-E 중 하나로 저장합니다.
   분석용으로 `metadata.hop_type = single|multi`, `gold.memory_path(s)`,
   `gold.current_cell(s)`를 함께 남깁니다.

따라서 Stage 2 평가는 “메모리 업데이트 레코드 하나하나를 점수화”하지 않습니다. 모델은
대화 prefix를 읽고 현재 메모리 상태를 재구성한 뒤, 가장 일관적인 보기를 선택해야 합니다.

Stage 3 action decision과 action MCQ 관련 코드는 실험적으로 남아 있을 수 있지만, 현재
기본 `build_benchmark_items.py` 산출물에는 포함하지 않습니다.

### 5.11 Audits + 품질 리포트
`audit_single_session_recoverability.py`, `audit_full_prefix_recoverability.py`,
`audit_stale_distractors.py`, `audit_life_stage_constraints.py`,
`build_quality_summary.py`가 라벨/상태/연산 분포, 단일세션 해결률, 누적 복원률,
stale distractor 가용성, 생애단계 위반(반드시 0)을 리포트합니다.

---

## 6. 현재 통합 데이터셋 현황 (`data/generated/`)

Nemotron 500명 정규화 후 생성한 **통합 데이터셋** (자연 hazard + 보장 coverage 혼합):

| 항목 | 수치 |
| --- | --- |
| Trajectory | **77** (hazard 30 자연 + coverage 47 보장) |
| 대화 세션 | 23,100 (mock; 검증 통과율 ≈ 100%) |
| Prefix gold | 23,100 (53MB, dedup 적용) |
| Stage 1 아이템 | 8,422 |
| Stage 2 memory MCQ 아이템 | 888 |
| 서로 다른 gold 사건 | 484개 (24개 라벨 전부 등장) |
| 생애단계 위반 | **0** |

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
make pipeline-smoke              # 정규화→상태→시뮬→대화(mock)→검증→gold→아이템→audit
```

작은 규모로 파이프라인 전 구간이 도는지 확인하는 용도입니다.

### 2. 통합 데이터셋 재현 (자연 hazard + 보장 coverage)

```bash
make normalize-personas LIMIT=500                  # 페르소나 500명 정규화
make simulate-smoke NUM_TRAJ=30 HORIZON=10         # 자연스러운 hazard 궤적 30개
make coverage-trajectories                          # 희소 생애 사건/메모리 변화 보강
make dialogue-smoke                                 # mock 대화 생성
make export-gold                                    # prefix gold (dedup 저장)
make build-items                                    # Stage1 + Stage2 memory MCQ 아이템
make audit                                          # 품질 리포트 생성
```

각 target의 규모/시드는 변수로 조절합니다:
`LIMIT`(페르소나 수), `NUM_TRAJ`, `HORIZON`(년), `SEED`. 예) `make simulate-smoke
NUM_TRAJ=100 HORIZON=12 SEED=7`.

### 3. 실제 LLM 대화 생성 (`.env`에 유효한 키 필요)

```bash
make dialogue-smoke EXECUTE=1     # OpenAI/Anthropic로 실제 대화 생성
```

`.env`의 `DEFAULT_LLM_PROVIDER`/`DEFAULT_GENERATION_MODEL`,
제공자·모델을 지정합니다. 키가 없으면 mock 모드로 대화 생성을 계속할 수 있습니다.

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
`build_benchmark_items` · `audit_*` · `build_quality_summary`.

### 5. 테스트

```bash
make test        # 스키마·엔진·시뮬레이터·gold dedup·coverage 중심 테스트
```

### 산출물 위치 (모두 git 미추적)

```
data/personas/normalized/personas_ko_KR.jsonl
data/generated/trajectories/traj_*.json          (traj_cov_* = coverage)
data/generated/sessions/sessions_traj_*.jsonl
data/generated/gold/prefix_gold.jsonl            (read_prefix_gold로 읽을 것)
data/generated/benchmark_items/stage1_event_status.jsonl
data/generated/benchmark_items/stage2_memory_mcq.jsonl
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

### 전체 대화 확인

정식 대화 corpus는 `data/generated/sessions/` 아래에 trajectory별 JSONL로 저장됩니다.

```
data/generated/sessions/sessions_traj_00042.jsonl
data/generated/sessions/sessions_traj_00043.jsonl
...
```

각 파일은 하나의 trajectory에 해당하고, 각 line은 하나의 상담 세션입니다. 즉 전체
대화를 확인하려면 `sessions_*.jsonl` 파일들을 시간순으로 읽으면 됩니다.

특정 trajectory의 전체 상담 이력을 사람이 읽기 쉬운 형태로 출력:

```bash
python - <<'EOF'
import json
from pathlib import Path

path = Path("data/generated/sessions/sessions_traj_00042.jsonl")

for line in path.read_text(encoding="utf-8").splitlines():
    s = json.loads(line)
    print(f"\n[{s['session_id']} / month={s['month_index']} / type={s['session_type']}]")
    for t in s["turns"]:
        speaker = "고객" if t["speaker"] == "user" else "상담원"
        print(f"{speaker}: {t['text']}")
EOF
```

모든 trajectory의 전체 상담 이력을 이어서 출력:

```bash
python - <<'EOF'
import json
from pathlib import Path

for path in sorted(Path("data/generated/sessions").glob("sessions_*.jsonl")):
    print("\n" + "=" * 100)
    print(path.name)
    print("=" * 100)

    for line in path.read_text(encoding="utf-8").splitlines():
        s = json.loads(line)
        print(f"\n[{s['trajectory_id']} / {s['session_id']} / month={s['month_index']} / type={s['session_type']}]")
        for t in s["turns"]:
            speaker = "고객" if t["speaker"] == "user" else "상담원"
            print(f"{speaker}: {t['text']}")
EOF
```

구조적으로는 다음처럼 보면 됩니다.

```
data/generated/sessions/         # 실제 benchmark 대화 corpus
  sessions_traj_00042.jsonl      # traj_00042의 전체 상담 이력
  sessions_traj_00043.jsonl      # traj_00043의 전체 상담 이력
  ...

data/raw_model_outputs/dialogue/ # LLM 원문 로그와 프롬프트
  traj_00042_S001_prompt.txt
  traj_00042_S001.txt
```

`data/raw_model_outputs/dialogue/`는 디버깅용 원문 로그입니다. 평가와 분석에 사용하는
canonical 대화 데이터는 `data/generated/sessions/*.jsonl`입니다.

### 개별 산출물 확인

```bash
# 한 궤적의 사건 lifecycle 보기
python - <<'EOF'
import json
t = json.load(open('data/generated/trajectories/traj_00042.json'))
for e in t['life_event_instances']:
    print(e['label_ko'], [(h['month_index'], h['status']) for h in e['status_history']])
EOF

# Stage 2 memory MCQ 문항 하나 보기
python - <<'EOF'
import json
r = json.loads(open('data/generated/benchmark_items/stage2_memory_mcq.jsonl').readline())
print(r['question'])
for o in r['options']:
    print(o['option_id'], o['text'])
print("gold:", json.dumps(r['gold'], ensure_ascii=False, indent=2))
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
- Stage 3(정기 금융 액션 결정)은 공식 평가 범위에서 제외했습니다. action impact,
  decision balance, action-level MCQ leakage 방지는 추후 구현 예정입니다.
- 24개 사건 중 MVP 10개는 완전한 델타/impact 템플릿, 나머지 14개는 유효한 stub(P2).
- `en_US` locale은 템플릿만 존재(생성은 한국어 전용).
- 누출 검사는 부분 문자열 기반이라 드물게 오탐/미탐 가능.

---

## 11. 문서 인덱스 (`docs/`)

| 문서 | 내용 |
| --- | --- |
| `design_overview.md` | 설계 불변식, 모듈 맵, 데이터플로 계약 |
| `repo_inventory.md` | 초기 repo 인벤토리, life_generator shim, 재사용/신규 |
| `life_state_fsm.md` | 월 단위 hazard FSM, 상태 가드, life_generator를 왜 대체했나 |
| `event_lifecycle.md` | lifecycle 상태와 갱신 허용 규칙 |
| `financial_memory_schema.md` | 메모리 셀·연산·이력 보존 |
| `standing_action_schema.md` | 정기 액션 타입·일관성 규칙·impact(추후 Stage 3 참고용) |
| `coverage_generation.md` | life_generator 에피소드 주입으로 사건/메모리 coverage 확보 |
| `dialogue_generation_strategy.md` | 증거 계획, drift/hard-negative, mock/LLM, 검증 |
| `mcq_design.md` | Stage 3 action MCQ 실험 기록(현재 공식 범위 제외) |
| `history_filter.md` | Stage 2 memory MCQ 히스토리 필요성 필터 기록 |
| `failure_modes.md` | 진단용 실패 유형 정의 |
| `locale_extension_guide.md` | 새 locale 추가 절차 |

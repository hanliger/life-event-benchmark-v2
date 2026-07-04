# Fin-Life Benchmark

상태 우선(state-first) 장기 금융 대화 벤치마크 생성기입니다.

이 저장소는 숨겨진 생애 사건(life event)과 금융 상태 trajectory를 먼저
만들고, 그 상태에서 관찰 가능한 한국어 은행 상담 대화를 생성합니다. 생성된
데이터는 모델이 긴 상담 이력에서 필요한 단서를 복원하고, 금융 메모리를
갱신하며, 정기 금융 액션에 대해 위험을 고려한 결정을 내릴 수 있는지
평가하는 데 사용됩니다.

핵심 설계는 단순합니다. **상태가 먼저이고, 대화는 나중입니다.** 실제로
무슨 일이 일어났는지는 숨겨진 trajectory가 결정하고, 대화 세션은 그 상태를
간접적인 은행 업무 단서로만 노출합니다.

## 생성되는 데이터

하나의 trajectory에는 다음 정보가 포함됩니다.

- 정규화된 가상 페르소나
- 초기 금융 메모리: 급여일, 월세 정보, 가구 상태, 대출 상태, 반복 지출 등
- 초기 정기 금융 액션: 월세 자동이체, 급여 연동 저축, 부모님 생활비 송금,
  배우자 생활비 이체, 대출 상환, 자녀 교육비 적립, 연금 납입, 사업 비용
  자동납부 등
- 월 단위 숨김 생애 사건 타임라인
  - `weak_signal`
  - `upcoming`
  - `occurred`
  - `cancelled`
  - `no_event`
- 발생한 사건이 만든 금융 메모리 변화
- 발생한 사건이 정기 금융 액션에 미친 영향과 gold decision
- 사건을 직접 말하지 않고 간접 단서만 드러내는 은행 상담 세션
- 각 세션 prefix 이후의 gold 상태
- event detection, memory update, action decision, MCQ 진단용 benchmark item

현재 dense 기본 설정은 대략 다음 규모를 목표로 합니다.

- trajectory당 `300`개 세션
- 세션당 `28`-`32`턴
- trajectory당 약 `9,000`턴
- hard negative 세션 `30%`
- 300세션 trajectory 기준 hard negative 약 `90`개

## 생성 파이프라인

```text
Nemotron persona parquet
  -> normalized persona JSONL
  -> 초기 금융 메모리 + 정기 금융 액션
  -> 월 단위 life-state trajectory
  -> memory delta + action impact
  -> dialogue plan
  -> generated banking session
  -> dialogue validation
  -> prefix gold
  -> benchmark item
  -> history filter + audit
```

주요 스크립트는 다음과 같습니다.

```text
scripts/normalize_personas.py
scripts/generate_initial_states.py
scripts/simulate_trajectories.py
scripts/generate_dialogue_sessions.py
scripts/validate_dialogues.py
scripts/export_prefix_gold.py
scripts/build_benchmark_items.py
scripts/run_history_filter.py
scripts/audit_*.py
scripts/build_quality_summary.py
```

## 핵심 데이터 파일

설정 파일:

```text
configs/generation/simulation.yaml
configs/generation/dialogue.yaml
configs/registries/life_events.yaml
configs/registries/event_to_memory_delta.yaml
configs/registries/event_to_action_impact.yaml
configs/registries/financial_memory_schema.yaml
configs/registries/standing_action_schema.yaml
configs/registries/financial_actions.yaml
configs/locales/ko_KR.yaml
```

생성 산출물:

```text
data/personas/normalized/personas_ko_KR.jsonl
data/generated/trajectories/traj_*.json
data/generated/trajectories/initial_states.jsonl
data/generated/sessions/sessions_<trajectory_id>.jsonl
data/generated/gold/prefix_gold.jsonl
data/generated/benchmark_items/*.jsonl
data/generated/quality_reports/*.md
data/generated/quality_reports/*.json
data/raw_model_outputs/dialogue/*.txt
```

정식으로 사용하는 파싱된 대화 세션은 여기에 저장됩니다.

```text
data/generated/sessions/sessions_<trajectory_id>.jsonl
```

각 줄은 하나의 세션입니다. 세션 metadata, turn 목록, cue annotation,
해당 세션을 만들 때 사용한 dialogue plan이 들어 있습니다. validation,
gold export, benchmark item 생성, audit은 모두 이 파일을 입력으로 사용합니다.

원본 모델 로그는 여기에 저장됩니다.

```text
data/raw_model_outputs/dialogue/<trajectory_id>_<session_id>_prompt.txt
data/raw_model_outputs/dialogue/<trajectory_id>_<session_id>.txt
data/raw_model_outputs/dialogue/<trajectory_id>_<session_id>_repair.txt
```

- `*_prompt.txt`: 모델에 보낸 정확한 프롬프트
- `*.txt`: 모델의 원문 응답
- `*_repair.txt`: JSON 파싱 실패 후 repair call을 한 경우의 원문 응답

## `data/` 디렉터리 구성

`data/`는 파이프라인 실행 결과를 단계별로 보관하는 작업 디렉터리입니다.
상위 구조는 다음과 같습니다.

```text
data/
  personas/
    normalized/
      personas_ko_KR.jsonl
  generated/
    trajectories/
      initial_states.jsonl
      traj_*.json
    sessions/
      sessions_<trajectory_id>.jsonl
    gold/
      prefix_gold.jsonl
    benchmark_items/
      stage1_event_status.jsonl
      stage2_memory_update.jsonl
      stage3_action_decision.jsonl
      stage3_action_mcq.jsonl
      stage3_action_mcq*.filtered.jsonl
    quality_reports/
      *.md
      *.json
  raw_model_outputs/
    dialogue/
      <trajectory_id>_<session_id>_prompt.txt
      <trajectory_id>_<session_id>.txt
      <trajectory_id>_<session_id>_repair.txt
```

현재 저장소의 smoke 샘플 기준으로는 다음 정도가 들어 있습니다. dense 설정으로
다시 생성하면 이 숫자는 달라집니다.

| 경로 | 현재 샘플 개수 | 설명 |
| --- | ---: | --- |
| `data/personas/normalized/personas_ko_KR.jsonl` | 5 lines | 정규화된 페르소나 |
| `data/generated/trajectories/initial_states.jsonl` | 5 lines | 페르소나별 초기 금융 메모리와 초기 정기 액션 |
| `data/generated/trajectories/traj_*.json` | 5 files | 숨겨진 life/financial trajectory |
| `data/generated/sessions/sessions_*.jsonl` | 5 files | 파싱 완료된 상담 세션 |
| `data/generated/gold/prefix_gold.jsonl` | 123 lines | 세션 prefix별 gold state |
| `data/generated/benchmark_items/*.jsonl` | 7 files | stage별 평가 문항과 filter 산출물 |
| `data/generated/quality_reports/*` | 15 files | validation, audit, summary report |
| `data/raw_model_outputs/dialogue/*` | 246 files | LLM prompt와 raw response 로그 |

### `data/personas/normalized/`

정규화된 페르소나 JSONL을 저장합니다.

```text
data/personas/normalized/personas_ko_KR.jsonl
```

생성 스크립트:

```text
scripts/normalize_personas.py
```

각 line은 하나의 `NormalizedPersona`입니다. 이후
`scripts/generate_initial_states.py`와 `scripts/simulate_trajectories.py`가 이
파일을 읽습니다.

주요 필드:

- `persona_id`
- `persona_source_id`
- `locale`
- `age`
- `sex`
- `persona_text`
- `occupation_state`
- `household`
- `housing`
- `style`
- `normalization_notes`

### `data/generated/trajectories/`

초기 상태와 월 단위 trajectory를 저장합니다.

```text
data/generated/trajectories/initial_states.jsonl
data/generated/trajectories/traj_00042.json
data/generated/trajectories/traj_00043.json
...
```

생성 스크립트:

```text
scripts/generate_initial_states.py
scripts/simulate_trajectories.py
```

`initial_states.jsonl`은 페르소나별 초기 금융 상태를 담습니다. 이후 trajectory
simulation의 입력으로 사용됩니다.

`traj_*.json`은 하나의 전체 trajectory입니다. 주요 내용:

- trajectory id, locale, seed, horizon
- persona
- initial persona state
- initial financial memory state
- initial standing actions
- sampled life event instances
- monthly timeline steps
- memory updates
- action impacts
- memory/action snapshots
- final persona state

이 파일은 이후 대화 계획 생성, prefix gold export, life-stage audit의 입력이
됩니다.

### `data/generated/sessions/`

정식 대화 데이터셋입니다.

```text
data/generated/sessions/sessions_traj_00042.jsonl
data/generated/sessions/sessions_traj_00043.jsonl
...
```

생성 스크립트:

```text
scripts/generate_dialogue_sessions.py
```

각 파일은 하나의 trajectory에 속한 상담 세션들을 JSONL로 저장합니다. 각 line은
하나의 `Session`입니다.

주요 필드:

- `session_id`
- `trajectory_id`
- `month_index`
- `age`
- `session_type`
- `linked_event_instance_id`
- `event_status_after_session`
- `mapped_action`
- `financial_task`
- `turns`
- `cue_annotations`
- `quality_self_check`
- `generator`
- `plan`

이 디렉터리의 파일이 실제 benchmark dialogue corpus입니다. raw model output이
아니라, 파싱과 schema 정리를 거친 canonical 데이터입니다.

소비 스크립트:

```text
scripts/validate_dialogues.py
scripts/export_prefix_gold.py
scripts/build_benchmark_items.py
scripts/run_history_filter.py
scripts/build_quality_summary.py
```

### `data/generated/gold/`

세션 prefix별 gold state를 저장합니다.

```text
data/generated/gold/prefix_gold.jsonl
```

생성 스크립트:

```text
scripts/export_prefix_gold.py
```

각 line은 하나의 `PrefixGold`입니다. 예를 들어 `S001`만 보인 prefix,
`S001`-`S002`가 보인 prefix, ...처럼 세션이 누적될 때마다 gold state를
기록합니다.

주요 필드:

- `prefix_id`
- `trajectory_id`
- `visible_sessions`
- `time`
- `gold_life_events`
- `gold_memory_updates`
- `gold_action_decisions`
- `gold_full_memory_state`
- `gold_full_action_state`

이 파일은 stage 1/2/3 benchmark item 생성의 핵심 입력입니다.

### `data/generated/benchmark_items/`

평가 문항을 저장합니다.

```text
data/generated/benchmark_items/stage1_event_status.jsonl
data/generated/benchmark_items/stage2_memory_update.jsonl
data/generated/benchmark_items/stage3_action_decision.jsonl
data/generated/benchmark_items/stage3_action_mcq.jsonl
data/generated/benchmark_items/stage3_action_mcq.filtered.jsonl
data/generated/benchmark_items/stage3_action_mcq.single_session.filtered.jsonl
data/generated/benchmark_items/stage3_action_mcq.no_history_option.filtered.jsonl
```

생성 스크립트:

```text
scripts/build_benchmark_items.py
scripts/run_history_filter.py
```

파일별 의미:

- `stage1_event_status.jsonl`: prefix에서 감지되는 life event와 status를 묻는 문항
- `stage2_memory_update.jsonl`: 금융 메모리 업데이트를 묻는 문항
- `stage3_action_decision.jsonl`: 정기 금융 액션 결정을 묻는 문항
- `stage3_action_mcq.jsonl`: 정기 금융 액션에 대한 객관식 진단 문항
- `*.filtered.jsonl`: history filter가 `filter_status`, `filter_votes`,
  `filter_meta`를 붙인 결과

filter 결과는 문항을 삭제하지 않습니다. 대신 `keep`, `too_easy`,
`leakage_suspected` 같은 tag를 붙입니다.

### `data/generated/quality_reports/`

검증과 audit 리포트를 저장합니다.

```text
data/generated/quality_reports/dialogue_quality_report.md
data/generated/quality_reports/dialogue_quality_report.json
data/generated/quality_reports/single_session_recoverability.md
data/generated/quality_reports/full_prefix_recoverability.md
data/generated/quality_reports/stale_distractors.md
data/generated/quality_reports/life_stage_constraints.md
data/generated/quality_reports/benchmark_item_report.md
data/generated/quality_reports/history_filter_*.json
```

생성 스크립트:

```text
scripts/validate_dialogues.py
scripts/audit_single_session_recoverability.py
scripts/audit_full_prefix_recoverability.py
scripts/audit_stale_distractors.py
scripts/audit_life_stage_constraints.py
scripts/build_quality_summary.py
scripts/run_history_filter.py
```

리포트 용도:

- dialogue validation pass rate 확인
- single-session만으로 복원되는 쉬운 문항 탐지
- full-prefix recoverability 확인
- stale distractor 포함 여부 확인
- life-stage guard 위반 확인
- benchmark item 개수와 stage별 분포 확인
- 실제 API validator 기반 history-filter 결과 저장

### `data/raw_model_outputs/dialogue/`

LLM 호출의 원문 로그를 저장합니다.

```text
data/raw_model_outputs/dialogue/traj_00042_S001_prompt.txt
data/raw_model_outputs/dialogue/traj_00042_S001.txt
data/raw_model_outputs/dialogue/traj_00042_S001_repair.txt
```

생성 스크립트:

```text
scripts/generate_dialogue_sessions.py
```

파일 의미:

- `*_prompt.txt`: 모델에 보낸 프롬프트
- `*.txt`: 모델의 원문 응답
- `*_repair.txt`: JSON 파싱 실패 후 repair prompt로 다시 받은 응답

이 디렉터리는 debugging과 재현성 확인을 위한 로그입니다. 실제 평가 corpus로
사용하는 파일은 `data/generated/sessions/`의 JSONL입니다.

### 단계별 데이터 흐름

```text
data/personas/normalized/personas_ko_KR.jsonl
  -> data/generated/trajectories/initial_states.jsonl
  -> data/generated/trajectories/traj_*.json
  -> data/generated/sessions/sessions_*.jsonl
  -> data/generated/gold/prefix_gold.jsonl
  -> data/generated/benchmark_items/*.jsonl
  -> data/generated/quality_reports/*

LLM prompt/response side log:

scripts/generate_dialogue_sessions.py
  -> data/raw_model_outputs/dialogue/*
```

## 페르소나 정규화

입력 페르소나는 `Nemotron-Personas-Korea/`의 parquet 파일에서 읽습니다.
adapter는 구조화된 페르소나 필드를 `NormalizedPersona`로 매핑합니다.

- `age`
- `sex`
- 직업 및 고용 상태
- 가구 상태
- 주거 상태
- 지역
- 대화 생성을 위한 말투 힌트

누락되거나 애매한 필드는 source persona id를 seed로 하는 deterministic
heuristic으로 채웁니다. 생성된 페르소나는 가상 인물이며, simulation seed로만
사용됩니다.

## 초기 금융 상태

`scripts/generate_initial_states.py`는 페르소나마다 두 객체를 만듭니다.

- `FinancialMemoryState`
- 초기 `StandingAction` 목록

금융 메모리는 path별 cell history로 표현됩니다. 각 path에는 current,
historical, stale, pending, cancelled, needs-verification 상태의 cell이
쌓일 수 있습니다. 이전 값은 삭제하지 않고 archive하므로, 이후 stale
memory 또는 stale action distractor로 사용할 수 있습니다.

정기 금융 액션은 별도의 객체입니다. 주요 필드는 다음과 같습니다.

- action id
- action type
- 사람이 읽을 수 있는 label
- 연결된 memory path
- 실행일(trigger day)
- 금액
- funds movement 여부
- risk level
- status

`funds_movement: true`인 액션은 high-risk로 간주합니다.

## 생애 상태 시뮬레이션

`scripts/simulate_trajectories.py`는 각 페르소나에 대해 월 단위 FSM을
실행합니다.

시뮬레이터는 다음 정보를 사용합니다.

- `life_events.yaml`의 event guard
- 나이 제약
- 가구, 주거, 고용 상태 guard
- cooldown
- lifecycle duration
- cancellation probability
- global hazard scaling

샘플링된 생애 사건은 `EventInstance`가 되며 status history를 가집니다.
하나의 사건은 다음과 같은 경로를 가질 수 있습니다.

```text
weak_signal -> upcoming -> occurred
weak_signal -> cancelled
upcoming -> cancelled
occurred
```

시뮬레이터는 occurred event의 효과도 함께 적용합니다.

- `event_to_memory_delta.yaml`의 memory update
- `event_to_action_impact.yaml`의 action impact
- 월별 memory snapshot
- 월별 action snapshot

hazard rate와 lifecycle duration은 현실 통계가 아니라 다양하고 그럴듯한
trajectory를 만들기 위한 heuristic weight입니다.

## 생애 사건 Lifecycle Semantics

lifecycle status는 gold가 허용하는 행동을 제한합니다.

| status | 의미 | memory/action policy |
| --- | --- | --- |
| `weak_signal` | 약한 간접 단서만 있음 | 확정 update 금지, pending 또는 needs-verification만 허용 |
| `upcoming` | 미래에 일어날 예정인 변화 | high-risk action change 금지, pending 또는 needs-verification만 허용 |
| `occurred` | 사건이 발생함 | update/archive/stale marking 허용, high-risk action 변경은 확인 필요 |
| `cancelled` | 이전 신호가 실현되지 않음 | pending 정리, 발생한 사건처럼 update하지 않음 |
| `no_event` | 일반 금융 상담 | 생애 사건 memory update 없음 |

delta engine과 impact engine은 대화 텍스트와 무관하게 이 semantics를
강제합니다.

## 대화 계획 생성

모델 호출 전에 먼저 dialogue plan을 만듭니다. planner는 각 세션마다
`DialogueGenerationPlan`을 생성합니다.

주요 필드:

- `session_type`
- `month_index`
- `age`
- 연결된 event instance, 있으면 포함
- 세션 이후의 event status
- 금융 업무
- 반드시 포함해야 하는 간접 cue
- 금지해야 하는 leakage term
- target memory path
- target action id
- 원하는 recoverability level

planner는 plan을 시간순으로 정렬하고 `S001`, `S002`, ... 형태의 session id를
붙입니다. dense mode에서는 같은 `month_index`에 여러 독립적인 은행 상담이
있을 수 있습니다. 여기서 month는 숨겨진 simulation month이지, unique session
counter가 아닙니다.

## 세션 유형

생성 세션의 종류는 다음과 같습니다.

| type | 목적 |
| --- | --- |
| `routine_financial` | 생애 사건이 없는 일반 은행 업무 |
| `weak_signal_evidence` | 실제 사건에 대한 초기 약한 신호 |
| `upcoming_evidence` | 미래 또는 예정 사건에 대한 단서 |
| `occurred_evidence` | 사건이 발생했음을 보여주는 단서 |
| `cancellation_evidence` | pending/upcoming 사건이 취소되었음을 보여주는 단서 |
| `consequence_session` | occurred event 이후 나타나는 금융 결과 세션 |
| `stale_recall_session` | 사용자가 예전 또는 stale 설정을 언급하는 세션 |
| `hard_negative` | 사건처럼 보이지만 실제로는 no-event인 은행 상담 |

## Hard Negative 세션

hard negative는 의도적으로 충분히 많이 생성합니다. hard negative는 표면적으로
생애 사건 세션과 비슷해 보이는 일반 금융 상담입니다. 하지만 실제로는 event
detection, memory update, action update를 일으키면 안 됩니다.

예시:

- 이체 목적이 주택 구매가 아니라 회사 경비 정산인 경우
- 반복 납부가 배우자 생활비나 양육비가 아니라 동호회 회비인 경우
- 적금 목적이 출산이나 교육이 아니라 여행 자금인 경우
- 상환이 이혼/별거 또는 부양가족 지원이 아니라 친구에게 빌린 돈을 갚는 경우

planner는 다음 방식으로 hard negative를 만듭니다.

1. 실제 life-event template 하나를 near miss로 샘플링합니다.
2. 그 사건과 같은 넓은 financial-action family를 재사용합니다.
3. 일반적인 non-event cue를 추가합니다.
   - `회사 경비 처리용 이체`
   - `동호회 회비 정기이체`
   - `친구한테 빌린 돈 상환`
   - `여행 경비 모으는 통장`
4. 모든 명시적 life-event label과 해당 template의 진짜 discriminative cue를
   금지합니다.
5. 세션을 `event_status_after_session: no_event`로 표시합니다.

dense 기본 설정:

```yaml
target_sessions_per_trajectory: 300
hard_negative_target_ratio: 0.30
```

이 설정은 trajectory당 약 `90`개의 hard negative 세션을 만듭니다. 이 세션은
recall뿐 아니라 false-positive resistance를 평가하는 데 중요합니다.

## 대화 텍스트 생성

대화는 세 가지 mode로 생성할 수 있습니다.

```text
mock     deterministic template dialogue, API 호출 없음
dry_run  prompt만 저장
llm      LLMClient를 통해 OpenAI 또는 Anthropic 호출
```

한국어 프롬프트:

```text
prompts/dialogue/generate_banking_session_ko.md
```

프롬프트는 모델에 다음을 요구합니다.

- JSON만 출력
- user와 assistant turn 교대
- 명시적인 life-event label 금지
- required cue를 자연스럽게 포함
- forbidden leakage term 회피
- FA code와 metadata 노출 금지
- high-risk action 변경은 반드시 고객 확인 전제로 처리
- 설정된 turn 수에 맞게 생성

현재 dense dialogue 설정:

```yaml
turns_min: 28
turns_max: 32
user_turns_min: 14
user_turns_max: 16
target_sessions_per_trajectory: 300
hard_negative_target_ratio: 0.30
```

실제 LLM으로 생성할 때는 `.env`에서 completion budget을 크게 잡는 것이
좋습니다.

```text
LLM_MAX_TOKENS=4096
```

30턴짜리 JSON 세션은 짧은 completion budget을 초과할 수 있습니다.

## 검증과 Repair

`scripts/validate_dialogues.py`는 생성된 세션을 다음 기준으로 검사합니다.

- speaker alternation
- event-label leakage
- FA-code leakage
- assistant가 숨겨진 event를 직접 요약하는지 여부
- emoji 또는 초성체
- cue annotation이 user turn을 가리키는지 여부
- required cue 포함 여부
- forbidden term 부재 여부
- status consistency
- cancellation cue consistency
- high-risk auto-execution without confirmation

현재 LLM generator는 JSON 형식이 깨진 경우 repair call을 수행합니다.
대화 품질 위반까지 repair하려면 validation violation을
`prompts/dialogue/repair_banking_session_ko.md`에 넣어 재생성하는 루프를
추가하면 됩니다.

## Prefix Gold 생성

`scripts/export_prefix_gold.py`는 세션 prefix마다 하나의 gold record를
내보냅니다.

각 `PrefixGold`에는 다음이 들어 있습니다.

- 보이는 session id 목록
- 현재 나이와 month
- 지금까지 보이는 gold life-event state
- 지금까지 보이는 gold memory update
- 지금까지 보이는 gold standing-action decision
- 전체 memory snapshot
- 전체 action snapshot

prefix gold는 benchmark item 생성의 원천입니다. 또한 single-session 또는
full-prefix recoverability가 타당한지 audit하는 데 사용됩니다.

## Benchmark Item 생성

`scripts/build_benchmark_items.py`는 다음 파일을 만듭니다.

- `stage1_event_status.jsonl`
- `stage2_memory_update.jsonl`
- `stage3_action_decision.jsonl`
- `stage3_action_mcq.jsonl`

stage 정의:

| stage | input | expected output |
| --- | --- | --- |
| Stage 1 | session prefix | event label/status/occurred/evidence |
| Stage 2 | prefix + initial memory | memory updates |
| Stage 3 | prefix + memory + actions | action decisions |
| Stage 3 MCQ | prefix + action question | correct operational choice |

MCQ item은 공통적인 operational option set을 사용합니다. 정답이 항상
가장 조심스러워 보이는 선택지가 되지 않도록 하기 위해서입니다. 정답은 event
lifecycle context에 따라 달라집니다.

- `post_occurred`: funds-moving action을 바꾸기 전에 고객 확인 요청
- `pre_occurred`: 사건이 아직 발생하지 않았으므로 현재 action 유지
- `cancelled`: stale evidence에 따라 실행하지 않고 pending 정리 또는 유지
- `no_event`: event를 만들어내지 않고 유지

## History Filter

history filter는 MCQ item이 의도한 장기 이력 없이도 풀리는지 검사하고
태깅합니다.

mode:

| mode | visible input | failure tag |
| --- | --- | --- |
| `single_session` | 마지막 세션만 제공 | `too_easy` |
| `partial_prefix` | prefix의 뒷부분만 제공 | `too_easy` 또는 leakage-like behavior |
| `no_history_option` | question과 option만 제공 | `leakage_suspected` |

예시:

```bash
python scripts/run_history_filter.py \
  --items data/generated/benchmark_items/stage3_action_mcq.jsonl \
  --sessions-dir data/generated/sessions \
  --mode no_history_option \
  --validators openai:gpt-4o-mini \
  --execute \
  --output data/generated/benchmark_items/stage3_action_mcq.no_history_option.filtered.jsonl \
  --report data/generated/quality_reports/history_filter_no_history_option_report.json
```

item은 삭제하지 않고 tag만 붙입니다.

## Pipeline 실행

설치와 기본 설정:

```bash
make setup
```

mock smoke run:

```bash
make pipeline-smoke
```

실제 LLM 대화 생성:

```bash
make dialogue-smoke EXECUTE=1
```

대화 생성 이후 gold와 item 생성:

```bash
make validate-dialogues
make export-gold
make build-items
make audit
```

전체 테스트:

```bash
make test
```

작은 local debug run이 필요하면 `configs/generation/dialogue.yaml`에서 임시로
다음 값을 낮추면 됩니다.

```yaml
target_sessions_per_trajectory: 30
turns_min: 7
turns_max: 10
hard_negative_target_ratio: 0.30
```

## 데이터 확인

trajectory lifecycle 확인:

```bash
python - <<'EOF'
import json
t = json.load(open("data/generated/trajectories/traj_00042.json"))
for e in t["life_event_instances"]:
    print(e["label_ko"], [(h["month_index"], h["status"]) for h in e["status_history"]])
EOF
```

session type 분포 확인:

```bash
python - <<'EOF'
import json, collections
from pathlib import Path
c = collections.Counter()
turns = 0
for p in Path("data/generated/sessions").glob("sessions_*.jsonl"):
    for line in p.read_text(encoding="utf-8").splitlines():
        s = json.loads(line)
        c[s["session_type"]] += 1
        turns += len(s["turns"])
print(c)
print("turns", turns)
EOF
```

hard negative 예시 확인:

```bash
python - <<'EOF'
import json
from pathlib import Path
for p in Path("data/generated/sessions").glob("sessions_*.jsonl"):
    for line in p.read_text(encoding="utf-8").splitlines():
        s = json.loads(line)
        if s["session_type"] == "hard_negative":
            plan = s.get("plan") or {}
            print(s["trajectory_id"], s["session_id"], plan.get("near_miss_event_label"), plan.get("must_include_cues"))
EOF
```

## Life Event 추가

1. `configs/registries/life_events.yaml`에 event를 추가합니다.
2. `event_to_memory_delta.yaml`에 memory delta template을 추가합니다.
3. `event_to_action_impact.yaml`에 action impact template을 추가합니다.
4. event가 숨겨진 life state를 바꾸면
   `src/fin_life_benchmark/fsm/event_lifecycle.py`를 수정합니다.
5. discriminative cue와 forbidden sibling cue를 추가하거나 검토합니다.
6. `make test`를 실행합니다.

## Locale 추가

1. `configs/locales/ko_KR.yaml`을 새 locale 파일로 복사합니다.
2. locale별 은행 용어와 value pool을 채웁니다.
3. `prompts/dialogue/` 아래에 locale별 dialogue prompt를 추가합니다.
4. 생성 스크립트에 `--locale <locale>`을 넘깁니다.

국가별 동작은 shared simulation logic이 아니라 locale config 안에 두는 것을
원칙으로 합니다.

## 현재 한계

- hazard rate와 lifecycle duration은 현실 통계가 아니라 heuristic
  plausibility weight입니다.
- 실제 대화 품질은 선택한 LLM과 prompt compliance에 의존합니다.
- 일부 non-MVP event는 아직 delta/impact template이 희소합니다.
- `en_US`는 template locale이며, 현재 주 경로는 한국어 생성입니다.
- history-filter verdict는 실제 API validator가 있어야 의미가 있습니다.
  mock verdict는 placeholder입니다.
- leakage check는 대부분 rule-based라 드물게 false positive나 false
  negative가 생길 수 있습니다.

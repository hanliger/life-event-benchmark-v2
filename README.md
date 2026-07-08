# Life Event Benchmark v2

한국어 금융 상담 대화에서 사용자의 생애 사건과 금융 상태 변화를 추론하는 벤치마크 생성 파이프라인입니다.

```text
persona -> initial financial state -> life-event trajectory -> dialogue sessions
        -> validation/audit -> prefix gold -> benchmark items
```

## Design Snapshot

이 레포의 기본 설계는 **state first, dialogue second**입니다. 먼저 숨겨진 persona/금융 상태/life-event trajectory를 만들고, 그 상태에서 관측 가능한 은행 상담 대화를 생성합니다. 대화가 원천 상태를 만드는 것이 아니라, 대화는 이미 존재하는 상태의 간접 증거입니다.

핵심 구성은 다음과 같습니다.

| 단계 | 역할 | 주요 코드/설정 |
| --- | --- | --- |
| Persona normalization | Nemotron persona를 나이, 직업, 혼인, 주거, 가구 상태로 정규화 | `scripts/normalize_personas.py`, `src/fin_life_benchmark/persona/` |
| Initial financial state | persona 상태에 맞는 초기 금융 memory/action 생성 | `scripts/generate_initial_states.py`, `src/fin_life_benchmark/memory/` |
| Life-event trajectory | 월 단위로 사건을 샘플링하고 lifecycle을 진행 | `scripts/simulate_trajectories.py`, `configs/registries/life_events.yaml` |
| Dialogue planning/generation | trajectory에서 세션 계획을 만들고 mock/LLM 대화 생성 | `src/fin_life_benchmark/dialogue/`, `prompts/dialogue/` |
| Validation/audit | 누출, 상태 충돌, life-stage 위반, recoverability 점검 | `scripts/validate_dialogues.py`, `scripts/audit_*.py` |
| Gold/items | prefix별 정답 상태와 Stage 1/2 benchmark item 생성 | `scripts/export_prefix_gold.py`, `scripts/build_benchmark_items.py` |

life-event는 `weak_signal -> upcoming -> occurred`로 진행하거나 중간에 `cancelled`가 됩니다. `weak_signal`과 `upcoming`은 단서만 있는 상태라 확정 memory update를 하면 안 되고, `occurred` 이후에만 실제 상태 갱신이 허용됩니다.

금융 memory의 `unknown`과 `not_applicable`은 다릅니다. `unknown`은 적용되는 필드인데 값을 모르는 상태이고, `not_applicable`은 현재 persona 상태상 그 필드가 존재하면 안 되는 상태입니다. 예를 들어 은퇴자/비취업자의 급여일은 `unknown`이 아니라 `not_applicable`이어야 합니다.

## Quick Start

권장 환경은 conda env `life_event`입니다.

```bash
conda activate life_event
make setup
```

`make setup`은 Python dependency를 설치하고 `.env`가 없으면 `.env.example`을 복사합니다. API 키 없이도 mock 모드로 전체 파이프라인을 돌릴 수 있습니다.

```bash
# Nemotron persona data 준비 후, API 없이 작은 전체 파이프라인 실행
conda run -n life_event make pipeline-smoke LIMIT=2 NUM_TRAJ=2 HORIZON=8 EXECUTE=0

# 테스트
conda run -n life_event make test
```

## Data

Nemotron persona parquet 파일은 기본적으로 아래 위치를 기대합니다.

```text
Nemotron-Personas-Korea/data/*.parquet
```

다른 경로를 쓰려면 `PERSONA_INPUT`을 지정합니다.

```bash
conda run -n life_event make normalize-personas \
  PERSONA_INPUT=/path/to/Nemotron-Personas-Korea \
  LIMIT=5 SEED=42
```

`SAMPLE_RANDOM=1`이 기본이라 같은 `SEED`면 같은 persona 샘플이 재현됩니다.

## LLM Config

`.env`에서 provider와 model을 정합니다.

```env
# mock: API 호출 없음
DEFAULT_LLM_PROVIDER=mock
DEFAULT_GENERATION_MODEL=mock
LLM_TEMPERATURE=0.7
LLM_MAX_TOKENS=8192
HISTORY_FILTER_VALIDATORS=mock:mock-validator
```

```env
# Anthropic 예시
ANTHROPIC_API_KEY=...
DEFAULT_LLM_PROVIDER=anthropic
DEFAULT_GENERATION_MODEL=claude-sonnet-5
LLM_TEMPERATURE=0.7
LLM_MAX_TOKENS=8192
HISTORY_FILTER_VALIDATORS=mock:mock-validator
```

`DEFAULT_LLM_PROVIDER`는 API 회사(`mock`, `openai`, `anthropic`)이고, `DEFAULT_GENERATION_MODEL`은 그 provider 안에서 호출할 모델명입니다. API 키는 `.env`에서 읽으며 `.env`는 커밋하지 않습니다.

## Common Commands

```bash
# persona 정규화
conda run -n life_event make normalize-personas LIMIT=20 SEED=42

# trajectory 생성
conda run -n life_event make simulate-smoke NUM_TRAJ=5 HORIZON=10 SEED=42

# dialogue 생성: mock
conda run -n life_event make dialogue-smoke NUM_TRAJ=5 EXECUTE=0

# dialogue 생성: 실제 LLM
conda run -n life_event make dialogue-smoke NUM_TRAJ=1 EXECUTE=1

# dialogue 검증과 품질 리포트
conda run -n life_event make validate-dialogues
conda run -n life_event make audit

# gold와 benchmark item 생성
conda run -n life_event make export-gold
conda run -n life_event make build-items
```

더 세밀하게 제어하려면 스크립트를 직접 실행합니다.

```bash
conda run -n life_event python scripts/generate_dialogue_sessions.py \
  --trajectories-dir data/generated/trajectories \
  --locale ko_KR \
  --output-dir data/generated/sessions/llm_sample \
  --max-trajectories 1 \
  --max-sessions 20 \
  --overwrite \
  --execute \
  --provider anthropic \
  --model claude-sonnet-5 \
  --continue-on-error
```

## Generation Defaults

주요 생성 설정은 YAML에서 조정합니다.

| 파일 | 주요 설정 |
| --- | --- |
| `configs/generation/simulation.yaml` | trajectory 기간, 사건 발생 밀도, 동시 active event 제한 |
| `configs/generation/dialogue.yaml` | 세션 수, 턴 수, hard negative 비율, repair 횟수 |
| `configs/registries/life_events.yaml` | 생애 사건, 발생 조건, lifecycle 설정 |

현재 dialogue 기본값은 세션당 `28~32`턴이고, trajectory당 최대 `300`개 세션을 계획합니다. 현재 simulation 기본값은 `10년` horizon에서 사건이 더 자주 나오도록 `global_hazard_scale`을 올린 상태입니다.

## Current Stabilization Notes

최근 작업에서 충돌 가능성이 컸던 부분을 아래처럼 정리했습니다. 후속 작업자는 이 규칙을 기준으로 새 생성 결과를 확인하면 됩니다.

### 1. Persona와 초기 금융 상태

- 고용 상태가 `retired`, `unemployed`, `student`, `homemaker`이면 급여일/급여계좌/급여 연동 action을 만들지 않습니다.
- 월세 거주자가 아니면 월세 납부 memory/action을 만들지 않습니다.
- 적용되지 않는 필드는 `unknown`이 아니라 `not_applicable`로 표현합니다.

### 2. Life-event sampling guard

- 이미 결혼 상태인 persona는 다시 결혼하지 않도록 guard를 둡니다.
- 출산/입양은 자녀 수와 부양가족 수가 각각 5명 미만일 때만 허용합니다.
- 부양가족 사망은 부양가족이 있을 때만 허용합니다.
- 교육 시작/유학은 이미 교육 상태인 persona에게 중복으로 발생하지 않도록 막습니다.
- trajectory 밀도는 `configs/generation/simulation.yaml`에서 `max_events_per_trajectory: 15`, `global_hazard_scale: 6.0`, 기본 `HORIZON=10`으로 조정했습니다. 목표는 10년 trajectory에서 대략 10개 안팎의 event가 나오도록 하는 것입니다.

### 3. Dialogue generation quality

- dialogue는 모바일/인터넷뱅킹 챗봇 상황이어야 합니다. 오프라인 지점, 창구, 서명, 신청서 작성, 실물 신분증, 배송/수령 같은 장면은 금지합니다.
- 금지어는 user 발화뿐 아니라 assistant의 선택지, 예시, 확인 질문에도 나오면 안 됩니다.
- LLM 출력은 저장 전 JSON parse, schema, speaker alternation, cue index, persona-state consistency, dialogue validator를 통과해야 합니다.
- `weak_signal` validator는 이제 `확정`이라는 단어 하나만으로 실패시키지 않고, `이미 확정`, `확정됐`, `확정된 상태`처럼 event를 확정으로 못박는 표현만 잡습니다. 금융상품 문맥의 "금리는 신청 시 확정"은 허용합니다.

### 4. LLM/provider handling

- 30턴 내외 대화를 안정적으로 받기 위해 `LLM_MAX_TOKENS=8192`를 기본으로 둡니다.
- provider가 빈 text를 반환하면 repair prompt로 넘기지 않고 provider call 자체를 retry합니다.
- Anthropic 응답의 `stop_reason`, `stop_sequence`, `content_block_types`, token usage를 raw output 옆 `.meta.json`으로 저장합니다.
- `--continue-on-error` 실행 시 실패한 세션은 `errors_traj_*.jsonl`에 남기고, 가능하면 마지막 provider metadata도 함께 기록합니다.

## Outputs

| 경로 | 내용 |
| --- | --- |
| `data/personas/normalized/personas_ko_KR.jsonl` | 정규화된 persona |
| `data/generated/trajectories/traj_*.json` | life-event trajectory |
| `data/generated/sessions/sessions_traj_*.jsonl` | 최종 dialogue sessions |
| `data/generated/sessions/errors_traj_*.jsonl` | `--continue-on-error` 실패 로그 |
| `data/raw_model_outputs/dialogue/*.txt` | LLM 원문 출력 |
| `data/raw_model_outputs/dialogue/*.meta.json` | provider metadata, stop reason, token usage |
| `data/generated/quality_reports/*` | validation/audit 리포트 |
| `data/generated/gold/prefix_gold.jsonl` | prefix별 gold state |
| `data/generated/benchmark_items/*.jsonl` | Stage 1/2 benchmark item |

분석에는 `data/generated/sessions/*.jsonl`을 쓰고, LLM 디버깅에는 `data/raw_model_outputs/dialogue/`를 봅니다.

## LLM Failure Handling

실제 LLM dialogue 생성은 다음 순서로 안정화됩니다.

1. provider 응답이 비어 있으면 repair로 보내지 않고 provider call을 retry합니다.
2. 응답이 있으면 raw `.txt`와 provider `.meta.json`을 저장합니다.
3. JSON parse, schema, cue index, persona-state consistency, dialogue validator를 통과해야 session으로 저장합니다.
4. 실패하면 repair prompt로 재생성합니다. `configs/generation/dialogue.yaml`에 `repair_attempts`가 없으면 기본 3회입니다.
5. `--continue-on-error`이면 실패 세션은 `errors_traj_*.jsonl`에 남기고 다음 세션을 계속 생성합니다.

## Follow-up Checklist

LLM 샘플을 다시 만들 때는 작은 규모부터 확인합니다.

```bash
conda run -n life_event python scripts/generate_dialogue_sessions.py \
  --trajectories-dir data/generated/trajectories \
  --locale ko_KR \
  --output-dir data/generated/sessions/llm_sample \
  --max-trajectories 1 \
  --max-sessions 20 \
  --overwrite \
  --execute \
  --provider anthropic \
  --model claude-sonnet-5 \
  --continue-on-error
```

생성 후 확인 순서:

1. `data/generated/sessions/llm_sample/sessions_*.jsonl`에서 실제 저장된 session 수를 확인합니다.
2. `data/generated/sessions/llm_sample/errors_*.jsonl`에서 실패 session과 error type을 확인합니다.
3. `data/raw_model_outputs/dialogue/*.meta.json`에서 `stop_reason`, `content_block_types`, token usage를 봅니다.
4. `make validate-dialogues`와 `make audit`으로 validator/audit 결과를 확인합니다.
5. false positive가 보이면 prompt보다 validator rule을 먼저 좁힐 수 있는지 검토합니다.

## Troubleshooting

`--execute requires DEFAULT_LLM_PROVIDER=openai|anthropic`

- `.env`가 mock 설정입니다. 실제 LLM 생성은 `DEFAULT_LLM_PROVIDER=anthropic` 또는 `openai`로 바꿉니다.

`OPENAI_API_KEY not set` 또는 `ANTHROPIC_API_KEY not set`

- provider에 맞는 API key를 `.env`에 넣습니다.

LLM raw output이 없거나 세션 수가 적음

- `data/generated/sessions/errors_*.jsonl`을 먼저 봅니다.
- provider metadata는 `data/raw_model_outputs/dialogue/*.meta.json`에 저장됩니다.
- 빈 응답이 반복되면 metadata의 `stop_reason`, `content_block_types`, `usage`를 확인합니다.

## Docs

세부 설계는 README에 길게 두지 않고 `docs/`에 둡니다.

| 문서 | 내용 |
| --- | --- |
| `docs/design_overview.md` | 전체 구조와 데이터 흐름 |
| `docs/life_state_fsm.md` | life-state guard와 event sampling |
| `docs/dialogue_generation_strategy.md` | dialogue planning/generation/validation |
| `docs/financial_memory_schema.md` | 금융 memory schema |
| `docs/failure_modes.md` | 주요 실패 유형 |
| `docs/coverage_generation.md` | rare event coverage 생성 |

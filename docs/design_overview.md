# Design Overview

이 레포는 synthetic persona에서 금융 상담 benchmark item까지 생성합니다.

## Pipeline

```text
normalize_personas
  -> generate_initial_states
  -> simulate_trajectories
  -> generate_dialogue_sessions
  -> validate_dialogues
  -> export_prefix_gold
  -> build_benchmark_items
  -> audit
```

## Core Objects

- `NormalizedPersona`: 나이, 직업 상태, 가구, 주거, 금융 성향.
- `FinancialMemoryState`: 금융 기억 cell history. `unknown`과 `not_applicable`을 구분합니다.
- `StandingAction`: 월세 이체, 급여연동 저축, 대출상환 같은 반복 금융 액션.
- `Trajectory`: persona, 초기 상태, 생애 사건, memory update, action impact의 시간축.
- `Session`: 사용자가 보는 은행 상담 대화.
- `PrefixGold`: 특정 세션 prefix까지 모델이 알아야 하는 정답 상태.
- `BenchmarkItem`: 평가용 MCQ/item.

## Generation Modes

- `mock`: API 없이 deterministic dialogue 생성.
- `dry_run`: LLM prompt만 저장.
- `llm`: `.env`의 OpenAI/Anthropic 설정으로 실제 대화 생성.

## Main Commands

```bash
make pipeline-smoke EXECUTE=0 LIMIT=2 NUM_TRAJ=2
make dialogue-smoke EXECUTE=1 NUM_TRAJ=2
make validate-dialogues
make audit
make test
```

## Quality Gates

- Dialogue schema validation + one repair attempt.
- Dialogue leakage/turn/cue validation.
- Life-stage guard audit.
- Initial memory/action consistency audit.
- No-op/repeated delta filtering.

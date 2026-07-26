# Configuration

| 파일 | 내용 |
|---|---|
| `experiment.yaml` | Stage 1/2/3와 masking 기대값, 7개 방법, 모델, k, 통계 seed |
| `methods.yaml` | BM25, Dense, Mem0, Letta adapter 설정 |
| `paid_safety.yaml` | paid smoke/full 실행 gate |

YAML이 실행 설정의 source of truth다. 문서의 예시와 다르면 YAML 및 immutable plan을
우선한다.

Paid smoke 안전 설정:

- 누적 standing allowance: 엄격히 `$5` 미만
- 단일 plan cap: `$3`
- concurrency: 1
- automatic retry: 0
- 첫 오류 중단
- timeout/billing 불명 시 자동 재실행 금지

Full plan에는 dollar hard cap이 없지만 reviewed estimate, exact item set, operation
limits를 반드시 기록하고 별도 승인을 받아야 한다.

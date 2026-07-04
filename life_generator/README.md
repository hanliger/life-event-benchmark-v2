# life_generator

`life_generator`는 `life_event_graph`의 event node를 재사용해 한 사람의
synthetic life path를 생성한다. 현재 구조는 다음 3단계다.

```text
node -> core_subgraph -> life path
```

## 1. Node

`node`는 하나의 life event다. 대부분의 node는 `life_event_graph`에서 가져오고,
자녀 나이에 의해 거의 필연적으로 발생하는 학교 milestone만 `life_generator`에서
추가한다.

```text
life_event_graph node:
  결혼, 출산, 입양, 이혼, 취업, 유학, 복직, 이직, 이사, 전세계약, 사고 ...

life_generator extra node:
  초등학교 입학, 중학교 입학, 고등학교 입학
```

### Action Codebook

`action`은 node가 발생했을 때 필요한 금융 실행 행동이다. action은 graph node가
아니며, edge를 직접 결정하지 않는다.

| code | action |
| --- | --- |
| FA-01 | 거래내역/고정지출 확인 |
| FA-02 | 입금/납입 알림 설정 |
| FA-03 | 수령계좌 등록/변경 |
| FA-04 | 대출 한도 조회 |
| FA-05 | 계좌 분리/공동계좌 생성 |
| FA-06 | 지급정지/카드정지/인증수단 재설정 |
| FA-07 | 송금/이체 실행 |
| FA-08 | 정기이체/자동납부 등록·변경·해지 |
| FA-09 | 예적금/목적자금 납입·해지 |
| FA-10 | 대출 실행/상환 설정 |

### Node-Action Mapping

| domain | node | actions |
| --- | --- | --- |
| relationship | 결혼 | FA-05, FA-08, FA-09 |
| relationship | 출산 | FA-01, FA-08, FA-09 |
| relationship | 입양 | FA-01, FA-08, FA-09 |
| relationship | 초등학교 입학 | FA-08, FA-09 |
| relationship | 중학교 입학 | FA-08, FA-09 |
| relationship | 고등학교 입학 | FA-08, FA-09 |
| relationship | 자녀 독립 | FA-05, FA-08, FA-09 |
| relationship | 별거 | FA-01, FA-07, FA-08 |
| relationship | 이혼 | FA-01, FA-07, FA-08 |
| relationship | 독립 | FA-05, FA-08, FA-09 |
| relationship | 분가 | FA-05, FA-08, FA-09 |
| relationship | 부양가족 발생 | FA-01, FA-07, FA-08 |
| relationship | 부모 요양 종료 | FA-01, FA-08 |
| relationship | 가족 사망 | FA-01, FA-07, FA-08 |
| career | 취업 | FA-03, FA-08, FA-09 |
| career | 교육 | FA-04, FA-07, FA-09 |
| career | 이직 | FA-03, FA-04, FA-08 |
| career | 전근 | FA-01, FA-08 |
| career | 휴직 | FA-01, FA-08, FA-09 |
| career | 복직 | FA-03, FA-08 |
| career | 유학 | FA-04, FA-07, FA-09 |
| career | 퇴사 | FA-01, FA-04, FA-08, FA-09 |
| career | 실직 | FA-01, FA-04, FA-08, FA-09 |
| career | 창업/프리랜서 전환 | FA-05, FA-08, FA-10 |
| career | 폐업/사업 중단 | FA-01, FA-08, FA-10 |
| career | 은퇴 준비 시작 | FA-02, FA-04, FA-09 |
| career | 연금수령시작 | FA-02, FA-03, FA-08 |
| residence | 이사 | FA-01, FA-08 |
| residence | 전세계약 | FA-04, FA-07, FA-09 |
| residence | 월세계약 | FA-07, FA-08, FA-09 |
| residence | 주거 계약 변경 | FA-04, FA-07, FA-08 |
| residence | 퇴거 | FA-01, FA-08 |
| residence | 주택 구매 | FA-04, FA-07, FA-10 |
| residence | 주택 매각 | FA-01, FA-08, FA-10 |
| accident | 가족 질병 | FA-01, FA-07, FA-09 |
| accident | 가족 입원 | FA-01, FA-07, FA-09 |
| accident | 가족 수술 | FA-01, FA-07, FA-09 |
| accident | 부양 가족 발생 | FA-01, FA-07, FA-08 |
| accident | 부모 요양 종료 | FA-01, FA-08 |
| accident | 가족 사망 | FA-01, FA-07, FA-08 |
| accident | 사고 | FA-01, FA-07, FA-10 |
| accident | 재난발생 | FA-01, FA-07, FA-10 |
| accident | 금융사기 발생 | FA-01, FA-06 |

## 2. Core Subgraph

`core_subgraph`는 강한 순서 관계가 있는 node 묶음이다. 모든 edge를 하나의 큰
그래프로 연결하지 않고, 사람이 실제로 겪을 법한 작은 event-order motif를 먼저
정의한다.

예시:

```text
결혼:
  결혼

결혼-출산-자녀독립:
  결혼 -> 출산 -> 초등학교 입학 -> 중학교 입학 -> 고등학교 입학 -> 자녀 독립

회사 지원 유학-복귀:
  취업 -> 유학 -> 복직 -> 교육 -> 이직

임차 계약 변경:
  이사 -> 전세계약 -> 주거 계약 변경 -> 퇴거 -> 이사 -> 월세계약 -> 주거 계약 변경
```

상세 목록은 실행 후 생성되는 파일에서 확인한다.

```text
life_generator/out/core_subgraphs.md
```

## 3. Life Path

`life path`는 여러 core_subgraph를 age 축 위에 배치한 한 사람의 생애 timeline이다.
샘플러는 다음 규칙으로 core_subgraph를 고른다.

```text
1. core_subgraph를 sampling_weight에 따라 후보로 샘플링한다.
2. 시작 나이와 내부 event 간격을 샘플링한다.
3. marital/employment/housing/children/dependent 상태 규칙을 검사한다.
4. 시간 lock을 검사한다. 예: 회사 지원 유학 뒤 일정 기간 퇴사/창업 제한.
5. 통과한 core_subgraph만 life path에 삽입한다.
```

같은 core_subgraph는 여러 번 샘플링될 수 있다. 따라서 별도 extensive layer 없이도
다음 흐름을 만들 수 있다.

```text
결혼 -> 이혼 -> 결혼 -> 이혼
취업 -> 유학 -> 복직 -> 이직
이사 -> 전세계약 -> 주거 계약 변경 -> 이사 -> 월세계약
```

## Constraints

샘플러는 core_subgraph를 무작위로 붙이지 않는다. 이미 생성된 life path의 상태와
시간 조건을 검사해서 plausible하지 않은 후보는 거절한다.

### State Constraints

```text
marital_status:
  결혼 상태에서 다시 결혼할 수 없다.
  별거는 결혼 상태에서만 가능하다.
  이혼은 결혼 또는 별거 상태에서만 가능하다.
  이혼 이후에는 재혼이 가능하다.

employment_status:
  취업 상태에서 다시 취업할 수 없다.
  휴직, 퇴사, 이직, 전근, 유학은 active employment가 있어야 가능하다.
  복직은 휴직 또는 유학 상태 뒤에만 가능하다.
  창업/프리랜서 전환은 퇴사 이후 unemployment 상태에서만 가능하다.

housing_status:
  주택 매각은 선행 주택 구매가 있어야 가능하다.

children_count:
  초등학교 입학, 중학교 입학, 고등학교 입학, 자녀 독립은
  출산 또는 입양으로 children_count > 0이 된 뒤에만 가능하다.
  입양은 child_age=0-17 범위에서 발생할 수 있고, 입양 당시 이미 지난
  학교 milestone은 생략한다.

retirement:
  연금수령 시작은 은퇴 준비 시작 뒤에만 가능하다.
```

### Entry Constraints

일부 core_subgraph는 중간 node부터 시작할 수 있다. 예를 들어 이미 취업 상태가
있다면 `회사 지원 유학-복귀`는 `취업`을 다시 만들지 않고 `유학`부터 붙을 수 있다.

하지만 자녀 관련 core_subgraph는 중간 진입을 허용하지 않는다.

```text
허용:
  기존 취업 상태 -> 유학 -> 복직 -> 교육 -> 이직

금지:
  기존 자녀 상태 -> 입양 -> 초등학교 입학
  기존 자녀 상태 -> 출산 -> 초등학교 입학
  출산/입양 없이 -> 초등학교 입학
  출산/입양 없이 -> 자녀 독립
```

이 제한의 이유는 현재 모델이 child identity를 따로 추적하지 않기 때문이다. 따라서
자녀 생애 core는 반드시 `결혼 -> 출산` 또는 `결혼 -> 입양` anchor에서 시작하고,
그 뒤 학교 milestone과 자녀 독립이 이어진다.

입양의 경우에는 입양 당시 자녀 나이를 반영한다.

```text
어린 자녀 입양:
  입양 -> 초등학교 입학 -> 중학교 입학 -> 고등학교 입학 -> 자녀 독립

중학생 나이 자녀 입양:
  입양 -> 중학교 입학 -> 고등학교 입학 -> 자녀 독립

고등학생 이후 자녀 입양:
  입양 -> 자녀 독립
```

### Time Constraints

```text
age range:
  각 core_subgraph는 가능한 시작 나이 범위를 가진다.

remarriage age:
  재혼 core는 최신 이혼 나이 이후에만 시작한다.
  결혼만 발생하는 재혼 core도 허용한다.
  출산 포함 재혼은 상대적으로 낮은 age cap을 둔다.
  입양 포함 재혼은 더 넓은 age cap을 둔다.

gap range:
  core_subgraph 내부 edge는 다음 event가 몇 년 뒤 일어나는지의 범위를 가진다.

child age:
  초등학교 입학, 중학교 입학, 고등학교 입학 등은 자녀 나이 기준 범위를 가진다.

lock:
  회사 지원 유학 이후에는 일정 기간 퇴사, 창업/프리랜서 전환, 실직을 막는다.
```

## Sampling Weight

현재 값은 empirical transition probability가 아니다. 논문에서는
`literature-informed heuristic sampling weight`로 표현해야 한다.

weight의 역할:

```text
높은 weight:
  일반적인 life-course anchor를 더 자주 후보로 올린다.
  예: 결혼, 결혼-출산-자녀독립, 독립-이사-월세계약, 취업-교육-이직, 은퇴-연금

낮은 weight:
  드물거나 조건부인 shock/exception event를 덜 자주 후보로 올린다.
  예: 재난발생, 금융사기, 가족 수술, 회사 지원 유학
```

중요한 제한:

```text
sampling_weight는 발생률 추정치가 아니다.
sampling_weight는 population-representative probability가 아니다.
sampling_weight는 synthetic trajectory를 더 plausible하게 만들기 위한 후보 선택 가중치다.
```

## Run

```bash
python -m life_generator.cli validate
python -m life_generator.cli sample --seed 42 --episodes 6 --samples 5 --output life_generator/out
python -m life_generator.cli visualize --seed 42 --episodes 6 --samples 5 --output life_generator/out
```

주요 output:

```text
life_generator/out/index.html
life_generator/out/core_subgraphs.md
life_generator/out/samples/sample_seed_42.html
life_generator/out/samples/sample_seed_42.png
life_generator/out/samples/sample_seed_43.png
life_generator/out/samples/sample_seed_44.png
life_generator/out/samples/sample_seed_45.png
life_generator/out/samples/sample_seed_46.png
```

## Academic Position

이 구현은 calibrated probability model이 아니라
**state- and time-constrained synthetic life-course generator**다.

안전한 논문 표현:

```text
We construct a compact library of literature-informed core subgraphs and
generate synthetic life paths by sampling, validating, and temporally
interleaving these subgraphs under state and timing constraints.
```

피해야 할 표현:

```text
calibrated transition probability
causal effect
empirical prediction model
population-representative simulator
```

## References

- Glen H. Elder Jr. (1994). "Time, Human Agency, and Social Change:
  Perspectives on the Life Course." Social Psychology Quarterly.
  https://doi.org/10.2307/2786971
- Alexis Gabadinho, Gilbert Ritschard, Nicolas S. Mueller, Matthias Studer
  (2011). "Analyzing and Visualizing State Sequences in R with TraMineR."
  Journal of Statistical Software. https://www.jstatsoft.org/article/view/v040i04
- Jacques-Antoine Gauthier, Eric D. Widmer, Philipp Bucher, Cedric Notredame
  (2010). "Multichannel Sequence Analysis Applied to Social Science Data."
  Sociological Methodology. https://doi.org/10.1111/j.1467-9531.2010.01227.x
- Bojan Kostic, Romain Crastes dit Sourd, Stephane Hess, Joachim Scheiner,
  Christian Holz-Rau, Francisco C. Pereira (2020). "Uncovering life-course
  patterns with causal discovery and survival analysis."
  https://arxiv.org/abs/2001.11399
- Sune Lehmann et al. (2024). "Using sequences of life-events to predict
  human lives." Nature Computational Science.
  https://www.nature.com/articles/s43588-023-00573-5
- MedMemoryBench (2026). arXiv:2605.11814.
  https://arxiv.org/abs/2605.11814

# Academic Grounding

`life_generator`는 empirical probability model이 아니라
**literature-informed plausible core_subgraph templates + state/time-constrained
temporal interleaving** 방식의 synthetic life-course generator다. 따라서
논문에서는 causal model, calibrated transition probability, prediction model이라고
부르면 안 된다.

## 1. Event Graph와 Time Model 분리

Kostic et al.은 life event modeling을 두 층으로 본다.

```text
upper level:
  life event graph construction

lower level:
  event pair마다 survival analysis로 time-dependent transition probability 추정
```

`life_generator`에서 core_subgraph template는 upper-level event structure에 해당한다.
core_subgraph 시작 나이, 내부 delay range, interleaving rule은 lower-level timing
model의 간단한 prototype으로 볼 수 있다. 다만 현재 구현은 empirical survival
model을 학습하지 않는다.

구현상 반영:

```text
event graph:
  어떤 event들이 하나의 plausible motif를 이루는가

timing model:
  motif가 몇 살에 시작하는가
  다음 event가 이전 event 이후 몇 년 뒤에 발생하는가
  자녀 event라면 자녀 나이가 얼마인가
```

Reference:

- Bojan Kostic, Romain Crastes dit Sourd, Stephane Hess, Joachim Scheiner,
  Christian Holz-Rau, Francisco C. Pereira. "Uncovering life-course patterns
  with causal discovery and survival analysis." arXiv:2001.11399.
  https://arxiv.org/abs/2001.11399

## 2. State Sequence / Life-Course Trajectory 관점

Sequence analysis는 개인의 생애를 단순 event list가 아니라 시간에 따른
categorical state sequence로 다룬다. 이 관점에서는 `입양` 자체보다 `입양`이
만드는 상태 변화가 중요하다.

```text
입양:
  children_count += 1
  dependents_count += 1

이후:
  초등학교 입학
  자녀 독립
```

따라서 event-level random walk보다 stateful/time-constrained core_subgraph generation이
더 자연스럽다.

Reference:

- Alexis Gabadinho, Gilbert Ritschard, Nicolas S. Müller, Matthias Studer.
  "Analyzing and Visualizing State Sequences in R with TraMineR."
  Journal of Statistical Software, 2011.
  https://www.jstatsoft.org/article/view/v040i04

## 3. Multi-Domain / Multi-Channel Life Course

개인의 삶은 career, relationship, residence, health/crisis가 따로 진행되면서도
서로 겹친다. 예를 들어 career core_subgraph와 parenting core_subgraph는 동시에 진행될 수
있고, accident/crisis event는 그 사이에 삽입될 수 있다.

`life_generator`는 이를 다음처럼 모델링한다.

```text
relationship core_subgraph:
  결혼 -> 출산/입양 -> 초등학교 입학 -> 중학교 입학 -> 고등학교 입학 -> 자녀 독립

career core_subgraph:
  취업 -> 유학 -> 복직 -> 교육 -> 이직

residence core_subgraph:
  전세계약 -> 주택 구매 -> 이사 -> 주택 매각

accident core_subgraph:
  가족 질병 -> 가족 입원 -> 가족 수술
```

이 core_subgraph들을 하나의 나이 축에서 interleaving해 synthetic biography를 만든다.

Reference:

- Social sequence analysis overview and bibliography.
  https://en.wikipedia.org/wiki/Sequence_analysis_in_social_sciences

## 4. Long-Horizon Synthetic Trajectory Construction

MedMemoryBench는 life-course sociology 논문은 아니지만, 장기 개인 trajectory를
archetype/template 기반으로 합성하고 평가하는 접근의 보조 근거로 참고한다.
`life_generator`에서도 사람 단위 synthetic trajectory를 만들기 위해 core_subgraph
template, state constraints, time constraints를 사용한다.

이 reference는 healthcare-agent benchmark 맥락이므로, family/career/residence
life course 이론의 직접 근거가 아니라 **long-horizon personalized synthetic
trajectory construction**의 참고로만 사용한다.

Reference:

- MedMemoryBench. arXiv:2605.11814.
  https://arxiv.org/abs/2605.11814

## Safe Academic Claim

논문에서는 다음 정도로 주장하는 것이 안전하다.

```text
We propose a compact, literature-informed plausible core_subgraph library and a
state- and time-constrained temporal interleaving procedure for generating
synthetic life-course trajectories.
```

다음 주장은 피해야 한다.

```text
calibrated transition probability
causal effect
empirical prediction model
population-representative simulator
```

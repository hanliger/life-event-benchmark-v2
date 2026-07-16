# Persona에서 20개 Life Event Trajectory까지의 샘플링 구조

현재 `v2`의 trajectory 생성은 다음 흐름으로 이루어진다.

```text
age-stratified persona
        ↓
persona 기반 초기 LifeState
        ↓
persona-conditioned subgraph branch 예약
        +
기존 자녀의 학교 진입 이벤트 예약
        ↓
월별 simulator
  ├─ 예약된 forced event 실행
  ├─ background hazard event 추가 샘플링
  ├─ guard/lifecycle/state 갱신
  └─ occurred 인스턴스가 20개가 되는 즉시 종료
```

## 1. Persona와 initial state

현재 `v2`는 20명의 persona를 연령대별로 샘플링했다.

- 20대 4명
- 30대 6명
- 40대 6명
- 50대 4명
- master seed 42
- persona당 trajectory 하나, persona 재사용 없음

Persona는 각 연령 버킷에서 `random.sample()`로 뽑힌다. 관련 구현은 [`scripts/sample_stratified_personas.py`](scripts/sample_stratified_personas.py)에 있다.

각 persona로부터 다음 두 종류의 초기 상태가 만들어진다.

- `initial_persona_state`: 혼인, 고용, 주거, 자녀, 부양가족 등 life-event guard에 사용하는 상태
- `initial_financial_memory_state`와 `initial_standing_actions`: 급여일, 월세, 대출, 정기이체 등 금융 상태

금융 initial state가 life-event의 발생 확률을 직접 결정하지는 않는다. 이벤트 샘플링은 주로 persona와 `LifeState`를 사용하고, 금융 memory/action은 이벤트가 발생한 뒤 갱신되는 대상이다.

초기 `LifeState`는 다음과 같은 persona 필드로 구성된다.

```text
marital_status
employment_status
residence_status
children_ages
dependents_count
lives_with_parents
home_owned
retirement_prepared
pension_receiving
```

관련 구현은 [`src/fin_life_benchmark/trajectory/simulator.py`](src/fin_life_benchmark/trajectory/simulator.py)의 `life_state_from_persona()`에 있다.

## 2. Subgraph는 무엇을 샘플링하는가

Subgraph sampler는 20개 이벤트를 한 번에 독립 추첨하지 않는다. 먼저 “어떤 사건으로 시작하는 life-course branch를 넣을지”를 반복적으로 결정한다.

### 2.1 먼저 benchmark event를 고른다

현재 registry에는 24개의 active event ID가 있다. 각 후보 이벤트의 상대 가중치는 대략 다음과 같다.

```text
w(event)
  = base_rate_per_year
  × age_weight(age)
  × state_modifier
  × persona_modifier
  × sampling_multiplier
```

예를 들면 다음과 같다.

- 20대 후반이면 결혼 가중치가 높아진다.
- 35세 미만이면 이직 가중치가 1.2배가 된다.
- 소득 안정성이 낮으면 고용 종료 가중치가 1.5배가 된다.
- 저축 성향이 높으면 주택 구매 가중치가 1.3배가 된다.
- 이미 자가 소유이면 일반 이사 가중치가 0.4배가 된다.

이 가중치는 실제 인구 전이확률이 아니라 상태에 맞고 다양한 trajectory를 만들기 위한 heuristic weight다. 계산은 [`src/fin_life_benchmark/fsm/life_state_machine.py`](src/fin_life_benchmark/fsm/life_state_machine.py)의 `annual_propensity()`에서 수행한다.

같은 이벤트가 여러 episode template에 포함되어 있어도 가중치를 합산하지 않는다. 예를 들어 `career_job_change`가 여러 career arc에 등장해도 이벤트 선택 확률은 `career_job_change`의 hazard 한 번만 사용한다. 따라서 template이 많다는 이유로 이벤트 확률이 부풀려지지 않는다.

### 2.2 이벤트를 고른 뒤 compatible branch를 고른다

선택한 event가 포함된 episode template 중 현재 persona 상태에서 가능한 branch 하나를 고른다.

Episode template 자체는 일반적인 DAG가 아니라 기본적으로 선형 path다. 내부 edge는 연속 이벤트 사이에만 존재한다. 대표적인 구조는 다음과 같다.

```text
결혼 → 출산/입양 → 초등 진입 → 중등 진입 → 고등 진입 → 자녀 독립

취업 → 본인 교육 → 이직 → 전배

휴직 → 복직 → 재교육

취업 → 유학 → 복직 → 재교육 → 이직

고용 종료 → 자영업 → 사업 종료 → 실업 → 재취업

전세 계약 → 주택 구매 → 이사 → 주택 매각 → 이사

은퇴 준비 → 연금 수령
```

이 밖에 가족 사망, 건강 사건, 사고·재난, 금융사기, 부양가족 추가/해소 같은 singleton branch도 있다. 전체 25개의 episode template은 [`life_generator/templates.py`](life_generator/templates.py)에 정의되어 있다.

### 2.3 Persona에 따라 중간 진입이 가능하다

항상 branch의 첫 노드부터 시작하지는 않는다.

- 이미 취업한 persona: `취업`을 다시 만들지 않고 `교육`, `이직`, `휴직` 등에서 career arc에 진입
- 이미 결혼한 persona: `결혼` 없이 `출산` 또는 `입양`에서 진입
- 이미 자녀가 있는 persona: 자녀 관련 후속 branch에 진입 가능
- 이미 주택을 소유한 persona: 구매를 반복하지 않고 매각 등 후속 단계로 진입 가능

이를 mid-entry door라고 한다. 진입 이벤트 자체도 age/state guard를 통과해야 한다. 관련 구현은 [`src/fin_life_benchmark/trajectory/subgraph_bridge.py`](src/fin_life_benchmark/trajectory/subgraph_bridge.py)의 `valid_entry_doors()`와 `event_entry_candidates()`에 있다.

### 2.4 선택된 것은 branch의 suffix 전체다

예를 들어 이미 취업한 persona에게 `career_job_change`가 선택되고 다음 branch가 연결되었다고 하자.

```text
취업 → 교육 → 이직 → 전배
             ↑ 여기서 진입
```

실제로 예약되는 것은 진입점 이후의 suffix다.

```text
이직 → 전배
```

각 노드 사이의 나이 간격은 template의 `gap_ranges`에서 정수 년 단위로 샘플링한다. 새로운 branch는 이전 branch의 마지막 나이 이후 1~3년 간격을 두고 배치된다.

## 3. Subgraph planner가 실제로는 약 30개를 예약한다

목표가 20개이면 planner의 내부 계획 목표는 20개가 아니라 다음과 같다.

```python
planning_target_count = 20 + max(5, 20 // 2)  # 30
```

즉 mapped event node를 약 30개까지 여유 있게 예약한다. 일부 예약 이벤트가 나중에 live simulator의 guard에서 취소되거나 실행되지 않을 수 있기 때문이다.

각 반복에서는 다음 작업을 수행한다.

1. 지금까지 계획한 이벤트를 재생해 예상 `LifeState`를 계산한다.
2. 현재 상태에서 가능한 event 후보를 구성한다.
3. event hazard에 비례해 event ID 하나를 선택한다.
4. 그 이벤트를 진입점으로 갖는 branch 후보를 섞어서 하나씩 시도한다.
5. repeat policy와 전체 episode 간 상태 충돌을 검증한다.
6. 통과하면 해당 branch의 mapped node 전체를 계획에 추가한다.
7. 다음 branch의 시작점을 앞 branch 이후로 이동한다.

이 계획은 최종적으로 다음 형태의 forced-event 목록으로 평탄화된다.

```text
(event_id, start_month)
```

구현은 [`subgraph_scripted_events()`](src/fin_life_benchmark/trajectory/subgraph_bridge.py)에 있다.

## 4. 기존 자녀의 학교 진입은 별도로 예약된다

Persona가 이미 자녀를 가지고 있으면 다음 나이에 도달할 때 교육 이벤트가 deterministic하게 추가된다.

- 7세: `primary`
- 13세: `middle`
- 16세: `high`

예를 들어 초기 자녀 나이가 6세라면 12개월 후 `education_child_stage_entry(primary)`가 forced event로 추가된다. 여러 자녀가 같은 달에 해당하면 그 달에는 더 높은 교육 단계를 하나만 남긴다.

관련 구현은 [`fixed_child_education_events()`](src/fin_life_benchmark/trajectory/subgraph_bridge.py)에 있다.

## 5. 월별 simulator에서 background event도 추가된다

Subgraph 예약이 끝나면 simulator가 월 단위로 진행된다.

매월 순서는 대략 다음과 같다.

1. 기존 event의 예정된 lifecycle transition 처리
2. 해당 월의 forced/subgraph event 시작 시도
3. 가능하면 background hazard event 하나 추가 샘플링
4. 이번 달에 새로 시작된 이벤트의 즉시 transition 처리
5. 상태, memory, action snapshot 저장
6. `occurred_count == 20`이면 종료

Background 이벤트의 월별 발생확률은 다음과 같다.

```text
p_month = clamp((4.5 × w(event)) / 12, 0, 0.5)
```

여기서 `4.5`는 현재 `global_hazard_scale`이다. 설정은 [`configs/generation/simulation.yaml`](configs/generation/simulation.yaml)에 있다.

현재 적용되는 주요 제한은 다음과 같다.

- 동시에 active인 이벤트 최대 2개
- trajectory에 생성 가능한 전체 instance 최대 40개
- background event 시작 사이 최소 2개월
- 같은 event의 동시 중복 금지
- age/state guard 적용
- 동일 이벤트 cooldown 적용
- 자녀 교육 이벤트는 실제 자녀 나이 조건 필요

Forced event는 hazard 추첨과 lifecycle 취소 확률은 우회하지만 age/state/parameter guard는 우회하지 않는다. 따라서 계획된 event라도 실제 occurrence 시점에 상태가 달라졌다면 `cancelled`될 수 있다.

## 6. “총 20개”의 정확한 의미

20개는 다음 조건을 뜻한다.

```text
occurred_month != null인 EventInstance가 정확히 20개
```

따라서 다음과 같이 해석해야 한다.

- 20개의 서로 다른 event type을 의미하지 않는다.
- 같은 `housing_move`, `career_employment_end` 등이 여러 번 발생할 수 있다.
- `weak_signal`, `upcoming`, `cancelled`는 20개에 포함되지 않는다.
- 전체 `life_event_instances`는 20개보다 많을 수 있다.
- 한 달에 여러 event가 발생할 수 있다.
- 20번째 `occurred`가 처리된 월에 trajectory가 종료된다.
- 20개를 채우지 못하면 생성 스크립트가 실패 처리한다.

종료 로직은 [`src/fin_life_benchmark/trajectory/simulator.py`](src/fin_life_benchmark/trajectory/simulator.py)의 월별 simulation loop에 있다.

## 7. 현재 v2 결과에서의 실제 구성

현재 [`data/runs/v2`](data/runs/v2)의 실제 결과는 다음과 같다.

- 20 trajectories × occurred 20개 = 총 400개
- subgraph/fixed forced occurrence: 104개, trajectory당 평균 5.2개
- background hazard occurrence: 296개, trajectory당 평균 14.8개
- 전체 instance 수: trajectory당 평균 22.2개
- 나머지는 cancelled 또는 종료 시점의 pending instance

즉 현재 결과에서는 subgraph가 전체 20개를 직접 채우는 것이 아니다. Subgraph가 인과적으로 연결된 backbone을 제공하고, background hazard가 나머지 상당 부분을 채운다.

실제 결과는 다음 파일에서 확인할 수 있다.

- [`data/runs/v2/manifest.json`](data/runs/v2/manifest.json)
- [`data/runs/v2/reports/trajectory_summary.md`](data/runs/v2/reports/trajectory_summary.md)

## 8. 최종 trajectory에 저장되는 구조

최종 trajectory JSON에는 subgraph의 episode ID나 edge가 저장되지 않는다. 저장되는 것은 다음과 같다.

- `life_event_instances`
- 각 instance의 `generation_source=forced|hazard`
- 월별 lifecycle transition
- persona/life-state snapshot
- financial memory snapshot
- standing action snapshot

따라서 최종 파일의 구조는 명시적인 graph가 아니라 다음에 가깝다.

```text
시간순으로 평탄화된 event instance 목록
  + lifecycle transition
  + state/memory/action snapshot
```

원래 어느 episode branch에서 왔는지와 branch 내부 edge는 최종 trajectory만으로 완전히 복원할 수 없다. 관련 출력 모델은 [`src/fin_life_benchmark/trajectory/models.py`](src/fin_life_benchmark/trajectory/models.py)에 정의되어 있다.

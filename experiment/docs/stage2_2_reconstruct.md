# Stage 2.2 Current-State Reconstruction

## 1. 연구 질문

`stage2_2_reconstruct`는 다음 질문을 평가한다.

> LLM 또는 memory system이 초기 금융 상태와 checkpoint까지의 상담 대화를
> 바탕으로, 해당 시점의 현재 금융 상태를 정확히 복원할 수 있는가?

모델에는 다음 입력만 제공한다.

1. `S000` 초기 상태
2. checkpoint까지의 answer-free 상담 대화
3. 출력 가능한 state schema와 값 타입

모델은 전체 current state를 JSON으로 출력한다. 평가기는 같은 checkpoint의
dialogue-grounded Gold state와 모델의 최종 state를 비교한다.

기존 MCQ 과제 `stage2_memory_value`는 초기 검증 기간 동안 baseline으로 보존하고,
새 과제는 별도 task ID인 `stage2_2_reconstruct`로 실행한다.

## 2. State key와 value 후보 공개 정책

### 2.1 State key는 미리 공개한다

`path`는 state를 구성하는 하나의 항목을 뜻한다. 예를 들면 다음과 같다.

- `employment.employer`
- `employment.salary_day`
- `household.dependents`
- `housing.address`

현재 financial-memory schema에는 총 34개 path가 있다. 모델은 key 목록을 초기
상태에서 추측하지 않고, 공개된 schema를 기준으로 전체 34개 path를 출력한다.

초기 상태에 값이 없거나 sparse representation에서 key 자체가 생략되어 있어도,
나중 대화에서 그 상태가 새로 생길 수 있다. 따라서 다음을 모델 입력에 제공한다.

- 전체 34개 path 이름
- 각 path의 JSON 타입
- 각 path에서 허용되는 status
- enum path의 허용 값
- list/object path의 구조
- 값이 없을 때 사용하는 `null`과 status 규칙

이 정보는 정답이 아니라 출력 문법이다. 특정 checkpoint에서 어떤 path가
바뀌었는지는 알려주지 않는다.

### 2.2 모든 value 후보를 제공하지는 않는다

값 후보 공개는 다음과 같이 구분한다.

| 값 종류 | 모델에 제공 | 예시 | 이유 |
|---|---|---|---|
| Closed enum | 허용 값 전체 | `employed`, `unemployed`, `on_leave` | 출력 표준화 및 parser 안정성 |
| Boolean/status | 허용 값 전체 | `current`, `stale`, `unknown` | 명확한 schema 계약 |
| 구조화 object | field와 타입만 | `{category, amount_krw}` | 구조는 알려주되 실제 값은 숨김 |
| Open string | 후보 미제공 | 회사명, 주소, 수취인 | 대화에서 직접 복원해야 함 |
| Number | 범위/단위만 제공 | 급여일, 금액, 부양가족 수 | 실제 정답 누출 방지 |
| Entity reference | 관계 구조만 제공 | 어떤 주택이 `primary_residence`인지 | 내부 simulator ID 노출 방지 |

회사명, 주소, 금액처럼 대화에서 새로 등장하는 open value의 후보 목록을 제공하면
closed-set 선택 문제가 되어 기존 MCQ와 비슷하게 쉬워질 수 있다. 따라서 후보를
주지 않고 대화에서 생성하도록 한다.

### 2.3 출력 cell

각 path는 다음 cell을 출력한다.

```json
{
  "value": "미래정보시스템",
  "status": "current",
  "evidence_session_ids": ["D015", "D045"]
}
```

허용 status는 다음과 같다.

- `current`
- `historical`
- `stale`
- `needs_verification`
- `unknown`
- `not_applicable`

Prospective state에 사용되던 `pending`과 `cancelled`는 이 과제의 current-state
출력에서 제외한다. 내부 provenance, event instance ID, hidden history도 모델이
출력하지 않는다.

### 2.4 내부 ID는 의미 기반 projection으로 바꾼다

대화에 나타나지 않는 simulator 내부 ID를 모델에게 맞히게 하면 state tracking이
아니라 생성기 구현을 추측하는 문제가 된다. 다음 값은 raw Gold에서 제거한다.

- `source_event_instance_id`
- property/event 내부 ID
- provenance
- historical audit list
- simulator month bookkeeping

`housing.properties`는 `address`, `role`, `mortgage_status`,
`ownership_status`처럼 대화로 확인 가능한 field만 평가한다.
`housing.primary_residence_property_id`는 ID 문자열을 exact match하지 않고,
prediction에서 어떤 property가 `primary_residence`로 지정됐는지 관계적으로
비교한다. 모델이 임의의 local reference를 사용하더라도 property 내용과 참조
관계가 같으면 동일한 상태로 정규화한다.

### 2.5 Closed ontology 전수 공개와 Gold coverage

기본 ontology protocol은 `stage2_2_reconstruct-v2` schema와
`stage2_2_observable_state-v2` projector를 사용한다. 후보는 20개 trajectory의
정답 문자열을 무조건 나열해서 만들지 않는다. generator registry와 memory
contract에서 closed domain을 먼저 정의하고, 20개 trajectory의 400개 checkpoint
Gold를 전수 검사하여 모든 Gold 값이 그 domain 안에 포함되는지 검증한다.

모델에 공개하는 closed domain은 다음과 같다.

| 종류 | Path 또는 field | 공개 후보 |
|---|---|---|
| 혼인·배우자 | `household.marital_status` | `single`, `married`, `separated`, `divorced`, `widowed` |
|  | `household.spouse_or_partner` | `spouse`, `null` |
| 고용 | `employment.employment_status` | `employed`, `self_employed`, `unemployed`, `on_leave`, `retired`, `student`, `homemaker` |
|  | `employment.income_stability` | `stable`, `variable`, `reduced`, `unstable`, `retired`, `null` |
|  | `employment.salary_day` | `10`, `15`, `21`, `25`, `null` |
|  | `employment.salary_account` | `main_checking`, `null` |
| 주거 | `housing.residence_status`, `housing.contract_type` | `owner`, `jeonse`, `wolse`, `family_home`, `other` |
|  | mortgage status | `none`, `active`, `closed` |
| property subfield | `role` | `primary_residence`, `secondary_property` |
|  | `ownership_status` | `owned`, `sold` |
| 교육 | `education.self_education_status` | `none`, `enrolled`, `study_abroad` |
|  | `education.child_education_stage` | `preschool`, `primary`, `middle`, `high`, `adult`, `null` |
| 금융상품 | checking/savings element | `main_checking`, `savings_1` |
|  | loan element | `mortgage`, `jeonse_loan`, `credit` |
|  | `pension_or_irp` | `irp`, `receiving`, `null` |
| 목표 | emergency fund | `building`, `null` |
|  | housing/education/retirement goal | `active`, `null` |
| 일회성 지출 | `category` | `medical`, `accident_or_disaster`, `fraud_loss`, `funeral` |

회사명, 직업, 주소, 지역, 금액, 자녀 나이처럼 실제 대화에서 복원해야 하는
open value는 Gold에서 관측됐더라도 후보로 공개하지 않는다. object는 구조와
closed subfield 후보만 공개하고 주소와 금액은 계속 open으로 둔다.

prepared-data validator는 initial state와 모든 checkpoint Gold에 대해 다음을
자동 검사한다.

- scalar closed value가 공개 후보 안에 있는가
- closed collection의 모든 element가 공개 후보 안에 있는가
- property와 expense object의 closed subfield가 공개 후보 안에 있는가
- 보유 중인 `primary_residence`가 최대 한 채이며 primary pointer와 일치하는가

후보 밖의 새 Gold 값이 생기면 silently 허용하지 않고 preparation을 실패시킨다.
새 값이 정당하면 schema version과 ontology를 명시적으로 갱신한다.

### 2.6 v3 non-overlapping ontology robustness extension

`stage2_2_reconstruct-v3`는 Gold와 observable projector를 변경하지 않고
closed ontology만 넓힌 robustness variant다. v2의 400개 item과 v3의 Gold를
전수 비교한 결과 변경은 0건이며, v3 Gold payload SHA-256은
`0c3bec21b1c87c43b1763cba14189b79405cb1736c2d7544a318a0222118aa79`다.

추가 후보는 기존 후보와 같은 의미 축에서 배타적으로 해석할 수 있는 다음
9개로 제한한다.

| Path | v3 추가 후보 |
|---|---|
| `household.spouse_or_partner` | `partner` |
| `housing.residence_status` | `public_rental`, `company_housing`, `dormitory` |
| `housing.contract_type` | `public_rental`, `company_housing`, `dormitory` |
| `financial_products.loans[]` | `auto_loan`, `student_loan`, `business_loan` |
| `education.self_education_status` | `on_leave`, `completed` |

후보의 배타적 의미를 prompt에도 함께 공개한다.

- `spouse`: 법적 배우자
- `partner`: 비혼의 지속적 파트너
- `jeonse`/`wolse`: 민간 임대
- `public_rental`: 공공기관 제공 임대
- `company_housing`: 고용주 제공 주거
- `dormitory`: 학교·기관 기숙사
- `credit`: 특정 목적이 없는 일반 신용대출
- `auto_loan`, `student_loan`, `business_loan`: 계약상 목적이 각각 차량,
  교육, 사업으로 지정된 대출
- `enrolled`: 국내 과정 재학
- `study_abroad`: 해외 과정 재학
- `on_leave`: 등록을 유지한 휴학
- `completed`: 과정 완료

`part_time`, `contract_worker`, `freelancer`, `personal_loan`,
`rental_property`, `joint_checking`, `business_checking`, `university`,
`graduate_school`는 기존 후보와 의미 축이 겹치므로 추가하지 않는다.

v3의 새 후보는 현재 Gold에서 정답으로 등장하지 않는다. 따라서 v3는 상태
다양성이 증가한 dataset이 아니라, 더 넓은 사전 정의 ontology 안에서 기존
Gold 상태를 선택하는 robustness test로 해석한다. v2 결과와 v3 결과는 schema
version을 구분하여 보고한다.

## 3. 기본 비교 단위

한 path의 prediction은 `value`와 `status`가 모두 Gold와 일치할 때만 정확한
current-state cell로 인정한다.

```text
CellCorrect(path) =
    1, if PredValue(path) = GoldValue(path)
          and PredStatus(path) = GoldStatus(path)
    0, otherwise
```

값 비교 전 deterministic normalization만 적용한다.

- 문자열: Unicode normalization과 앞뒤 공백 제거
- 숫자: JSON number로 변환 후 비교
- enum: canonical token exact match
- set 성격의 list: 순서와 무관하게 비교
- 자녀 나이처럼 중복이 의미 있는 list: 정렬된 multiset으로 비교
- object: 공개된 field를 재귀적으로 비교

의미가 비슷하다는 이유로 fuzzy matching하거나 LLM Judge로 정답 처리하지 않는다.

## 4. 핵심 평가 점수

### 4.1 Final State Accuracy

최종 prediction과 최종 Gold를 모든 path에서 직접 비교하는 대표 점수다.

```text
Final State Accuracy
  = 정확한 current-state cell 수 / 전체 Gold path 수
```

34개 중 28개의 `value`와 `status`를 모두 맞혔다면 `28 / 34 = 82.35%`다.

**의미:** 모델이 checkpoint 시점의 전체 current state를 얼마나 정확히
복원했는지를 나타낸다.

### 4.2 Changed State Accuracy

Gold final state가 initial state와 달라진 path만 대상으로 정확도를 계산한다.

```text
GoldChanged = {p | GoldCell(p) != InitialCell(p)}

Changed State Accuracy
  = GoldChanged 중 정확히 복원한 path 수 / |GoldChanged|
```

**의미:** 모델이 대화에서 실제 상태 변화를 찾아 최종 값과 status에 반영했는지를
나타낸다. 변경된 path의 개수 자체를 맞히는 점수가 아니다.

### 4.3 Unchanged State Accuracy

Gold final state가 initial state와 동일한 path만 대상으로 계산한다.

```text
GoldUnchanged = {p | GoldCell(p) = InitialCell(p)}

Unchanged State Accuracy
  = GoldUnchanged 중 그대로 보존한 path 수 / |GoldUnchanged|
```

**의미:** 일반 상담, hard negative 또는 과거 언급을 보고 존재하지 않는 상태
변화를 만들어내지 않는지를 나타낸다.

### 4.4 Exact State Match

한 checkpoint의 모든 path가 정확할 때만 1이다.

```text
Exact State Match
  = 1, if 모든 path의 value와 status가 정확
    0, otherwise
```

전체 결과에서는 exact match checkpoint 수를 전체 checkpoint 수로 나눈다.

**의미:** 일부가 아니라 완전한 state snapshot을 생성할 수 있는지를 나타낸다.
엄격한 지표이므로 Final State Accuracy와 함께 해석한다.

### 4.5 Value Accuracy와 Status Accuracy

Final State Accuracy의 오류 원인을 분리하기 위한 진단 점수다.

```text
Value Accuracy
  = Gold value와 prediction value가 같은 path 수 / 전체 path 수

Status Accuracy
  = Gold status와 prediction status가 같은 path 수 / 전체 path 수
```

**의미:** 값 자체를 기억하지 못한 오류와 값의 현재성·유효성을 잘못 판단한 오류를
구분한다. 둘 중 하나만 맞은 cell은 Final State Accuracy에서는 오답이다.

## 5. Change confusion matrix

Final State Accuracy만 보면 초기 상태를 그대로 복사하는 모델도 높은 점수를 받을
수 있다. 이를 진단하기 위해 initial state 대비 변경 여부를 confusion matrix로
표시한다.

```text
Gold changed(p) = GoldCell(p) != InitialCell(p)
Pred changed(p) = PredCell(p) != InitialCell(p)
```

| | Pred unchanged | Pred changed |
|---|---:|---:|
| Gold unchanged | TN | FP |
| Gold changed | FN | TP-detected |

- `TN`: 바뀌지 않은 상태를 그대로 유지
- `FP`: 바뀌지 않았는데 모델이 임의로 변경
- `FN`: 바뀌었는데 initial state를 그대로 유지
- `TP-detected`: 변경이 있다는 사실은 감지

`TP-detected`는 최종 값이 틀려도 변경 감지 자체는 맞을 수 있다. 따라서
TP-detected를 다시 다음 두 종류로 나누어 함께 보고한다.

- `TP-correct`: 변경을 감지했고 최종 `value`와 `status`도 정확
- `TP-wrong-value`: 변경은 감지했지만 최종 `value` 또는 `status`가 틀림

권장 표시 형식은 다음과 같다.

| Gold class | Pred unchanged | Pred changed, correct | Pred changed, wrong |
|---|---:|---:|---:|
| Gold unchanged | TN | 0 | FP |
| Gold changed | FN | TP-correct | TP-wrong-value |

Gold unchanged인데 prediction이 정확하면서 changed일 수는 없으므로 해당 칸은
항상 0이다.

### 5.1 Change Detection Precision, Recall, F1

변화의 존재만 평가하고 최종 값의 정확성은 보지 않는다.

```text
Detection Precision = TP-detected / (TP-detected + FP)
Detection Recall    = TP-detected / (TP-detected + FN)
Detection F1        = 2PR / (P + R)
```

**의미:** 대화에서 어떤 state key가 바뀌었는지를 탐지하는 능력이다.

### 5.2 Correct-Change Precision, Recall, F1

최종 cell까지 정확한 `TP-correct`만 true positive로 인정한다.

```text
Correct-Change Precision
  = TP-correct / (TP-correct + TP-wrong-value + FP)

Correct-Change Recall
  = TP-correct / (TP-correct + TP-wrong-value + FN)

Correct-Change F1
  = 2PR / (P + R)
```

`Correct-Change Recall`은 `Changed State Accuracy`와 같은 값을 갖지만,
precision과 F1을 함께 제공하면 모델이 과도하게 많은 path를 변경하는지도 확인할
수 있다.

## 6. Status confusion matrix

Status는 closed enum이므로 별도의 multiclass confusion matrix를 생성한다.
행은 Gold status, 열은 predicted status로 둔다.

| Gold \ Pred | current | historical | stale | needs_verification | unknown | not_applicable | invalid/missing |
|---|---:|---:|---:|---:|---:|---:|---:|
| current |  |  |  |  |  |  |  |
| historical |  |  |  |  |  |  |  |
| stale |  |  |  |  |  |  |  |
| needs_verification |  |  |  |  |  |  |  |
| unknown |  |  |  |  |  |  |  |
| not_applicable |  |  |  |  |  |  |  |

이 표는 다음 오류를 구체적으로 보여준다.

- 지워지거나 적용 불가능해진 값을 계속 `current`로 유지
- 오래된 값을 `stale`로 표시하지 못함
- 단순히 모르는 값과 실제로 적용되지 않는 값을 혼동

Status별 precision, recall, F1과 macro-F1도 보조 지표로 출력한다. 다만 value가
틀린 상태에서 status만 맞는 경우가 있으므로 Final State Accuracy를 대체하지
않는다.

## 7. Evidence 점수

Evidence는 state 정답과 분리된 보조 평가다. Gold에서 initial state와 달라진
path는 최소 하나의 evidence session을 요구한다.

### Evidence Hit Rate

```text
Evidence Hit(path) =
  1, if predicted evidence와 Gold support session의 교집합이 존재
  0, otherwise

Evidence Hit Rate
  = Evidence Hit의 평균
```

**의미:** 모델이 맞힌 상태를 실제로 관련 상담에 근거시킬 수 있는지를 나타낸다.

### Evidence Citation Precision

```text
Evidence Citation Precision
  = 유효한 support-session citation 수 / 전체 citation 수
```

**의미:** 관련 없는 상담을 근거로 과도하게 인용하는지를 나타낸다.

존재하지 않는 session ID와 checkpoint 이후의 session ID는 validation error로
처리한다. Full Context method가 입력으로 받은 모든 session은 retrieval evidence로
간주하지 않으며, 모델이 각 path에 직접 출력한 citation만 이 점수에 사용한다.

## 8. Reliability 지표

다음 값은 성능 점수와 별도로 반드시 보고한다.

- JSON parse success rate
- 34개 required path completeness
- invalid key 수
- invalid value type 수
- invalid status 수
- invalid/future evidence ID 수
- provider request failure 수

Malformed JSON 전체를 의미적으로 추측해 복구하는 별도 LLM repair call은 사용하지
않는다. JSON은 읽혔지만 일부 cell만 잘못된 경우에는 유효한 cell은 유지하고
잘못된 cell만 오답 및 validation error로 기록한다.

## 9. 집계와 결과 표

각 checkpoint에서 위 점수를 계산한 뒤 다음 순서로 집계한다.

1. checkpoint별 path metric 계산
2. trajectory 내부 checkpoint macro-average
3. 전체 trajectory macro-average

trajectory마다 동일한 가중치를 부여한다. 정식 실험에서는 checkpoint별 성능,
`@45`, `@300`, 15-session 간격 progressive curve와 AUC도 함께 보고한다.

권장 메인 결과 표는 다음과 같다.

| Method | Final State Acc. | Value Acc. | Status Acc. | Changed State Acc. | Unchanged State Acc. | Correct-Change F1 | Exact Match | Parse Success |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| GPT-5.6-sol |  |  |  |  |  |  |  |  |

Change confusion matrix, status confusion matrix, evidence 점수와 path/domain별 결과는
보조 표로 제공한다.

초기 검증과 첫 full run은 OpenAI API 한 종류만 사용한다. 다른 provider/model
비교는 이 protocol과 item set을 freeze한 이후 별도 확장으로 수행한다.

## 10. 데이터 검수의 의미

`no_prospective` dataset은 기존 weak-signal/upcoming 상담 555개를 neutral filler로
교체한 데이터다. 이는 평가 점수가 아니라 입력 데이터가 의도대로 만들어졌는지
확인하는 절차다.

검수 시 다음만 확인한다.

- 교체 대상 555개 session이 실제로 neutral/no-event 상담인지
- 교체된 session이 current memory state를 갱신하는 Gold update를 포함하지 않는지
- 실제 발생을 보여주는 occurred session과 그 current-state Gold update가
  실수로 제거되지 않았는지

이 검수는 미래 계획을 현재 사실로 잘못 반영하는 데이터 누출을 막기 위한 것이다.

## 11. 단일 API smoke protocol

최초 smoke에서는 하나의 provider와 model만 사용한다.

| 항목 | 설정 |
|---|---|
| Provider API | OpenAI Responses API |
| Model | `gpt-5.6-sol` |
| Method | `fc_gpt_5_6_sol` |
| Trajectory | `traj_001` |
| Checkpoints | 45, 300 |
| Paid requests | 2 |
| Concurrency | 1 |
| Automatic retries | 0 |
| Stage-specific output limit | 12,000 tokens |

두 checkpoint를 사용하는 이유는 짧은 prefix에서의 기본 형식과 300-session
장문 입력에서의 context/output 안정성을 모두 확인하기 위해서다. smoke에서도
Gold와 비교하여 이 문서에 정의된 모든 점수와 confusion matrix를 계산한다.
다만 2개 checkpoint의 점수는 예비 결과로만 보고, 통계적·학술적 성능 결론에는
사용하지 않는다. smoke에서는 다음 실행 계약도 함께 검증한다.

- 요청이 provider context limit 안에서 완료되는가
- 응답 JSON이 잘리지 않는가
- required path와 cell 구조를 parser가 읽을 수 있는가
- invalid key/type/status/evidence 오류가 의도대로 기록되는가
- raw response, token usage, latency, manifest가 보존되는가

### 11.1 `traj_001`의 최종 평가 포함 원칙

`traj_001`은 최종 논문 평가에서도 제외하지 않는다. 대신 smoke를
성능 튜닝에 사용하지 않는다.

- smoke 단계에서도 Gold accuracy, changed-state score, confusion matrix,
  path별 오류를 모두 산출하고 확인한다.
- 해당 점수를 높이기 위해 prompt wording, few-shot example, value 후보,
  comparator 또는 path 가중치를 변경하지 않는다.
- 수정 가능한 것은 JSON 문법, 사전에 정의했지만 누락된 schema 설명,
  parser/API 호환 문제와 점수에 무관하게 확인된 구현·Gold 버그뿐이다.
- 특정 대화 내용, path 값 또는 정답 오류에 맞춘 prompt rule을 추가하지 않는다.
- smoke response는 최종 결과에 재사용하지 않는다.
- 형식 계약을 freeze한 뒤 `traj_001`을 포함한 전체 trajectory를 새 run으로
  다시 평가한다.
- smoke 점수, 허용된 수정 내역과 freeze commit을 manifest에 기록한다.

이 원칙은 smoke 점수를 투명하게 확인·보고하면서도 `traj_001`의 결과에 맞춰
과제나 prompt를 최적화하는 것을 막기 위한 것이다.

### 11.2 2026-07-29 smoke 실행 결과

고정 plan
`14e17c83bc5ae689450e497a47408d6ec6030cbd2c1a4d22ace344b0c6aeb9f5`를
구현 commit `062053c`에서 실행했다. OpenAI Responses API가 반환한 model ID는
`gpt-5.6-sol`, SDK version은 `2.48.0`이었다. 두 요청 모두 자동 재시도 없이
완료됐고, JSON parse와 34-path schema validation도 모두 통과했다.

| Checkpoint | Final state | Value | Status | Changed state | Unchanged state | Correct-change F1 | Exact match |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 45 | 94.12% | 100.00% | 94.12% | 100.00% | 93.10% | 83.33% | 0 |
| 300 | 55.88% | 64.71% | 82.35% | 44.44% | 68.75% | 39.02% | 0 |
| 2-checkpoint macro | 75.00% | 82.35% | 88.24% | 72.22% | 80.93% | 61.18% | 0 |

같은 두 checkpoint의 initial-copy baseline은 Final State Accuracy가 각각
85.29%, 47.06%이고 Changed State Accuracy는 모두 0%였다. 두-checkpoint
macro는 Final State Accuracy 66.18%, Changed State Accuracy 0%,
Unchanged State Accuracy 100%다. 따라서 전체 상태 점수만으로는 대화 속 변경
추적 여부를 판단할 수 없다는 설계 동기가 실제 데이터에서도 확인됐다.

Change confusion matrix의 두 checkpoint 합계는 다음과 같다.

| Gold class | Pred unchanged | Pred changed, correct | Pred changed, wrong |
|---|---:|---:|---:|
| Gold unchanged | TN = 38 | 0 | FP = 7 |
| Gold changed | FN = 0 | TP-correct = 13 | TP-wrong-value = 10 |

- Change detection precision/recall/F1: 74.84% / 100.00% / 85.57%
- Correct-change precision/recall/F1: 53.11% / 72.22% / 61.18%
- Evidence hit rate: 80.56%
- Evidence citation precision: 44.66%
- Parse errors: 0/2
- Validation errors: 0

Status confusion matrix는 다음과 같다. 행이 Gold, 열이 prediction이다.

| Gold \ Pred | current | historical | stale | needs_verification | unknown | not_applicable | invalid/missing |
|---|---:|---:|---:|---:|---:|---:|---:|
| current | 42 | 1 | 2 | 1 | 0 | 0 | 0 |
| historical | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| stale | 1 | 0 | 1 | 0 | 0 | 0 | 0 |
| needs_verification | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| unknown | 0 | 0 | 0 | 0 | 6 | 0 | 0 |
| not_applicable | 0 | 0 | 0 | 1 | 2 | 11 | 0 |

Provider usage는 input 81,838 tokens, output 6,366 tokens, 총 88,204
tokens였다. OpenAI가
[공개한 standard 가격](https://openai.com/index/gpt-5-6/)인 input $5/1M,
output $30/1M을 uncached 기준으로 적용한 추정 증분 비용은 약 $0.6002다.
실제 청구액은 provider billing record를 기준으로 별도 대조한다.

Smoke 점수를 본 뒤 path별 오답을 맞히기 위한 rule, comparator, normalizer 또는
path 가중치는 변경하지 않았다. 다만 strict 비교 결과를 검토하면서 출력
ontology가 모델에게 완전히 공개되지 않았다는 construct-validity 문제를
발견했다. 따라서 해당 smoke는 v1 형식 검증 결과로 보존하고 정식 run 전에
schema/projector v2로 다음 사항을 보완했다.

1. 20개 trajectory 전체 Gold와 generator registry를 대조하여 closed scalar,
   collection element, object subfield 후보를 전부 공개했다.
2. open value는 Gold에서 관측됐더라도 후보로 공개하지 않았다.
3. `child_support_arrangement`의 실제 값 타입을 integer KRW 또는 null로
   바로잡았다.
4. 29개 checkpoint에서 두 보유 주택이 동시에 `primary_residence`로 projection된
   문제를 primary pointer 기준으로 정규화했다.
5. 모든 Gold 값의 ontology coverage와 primary-property 일관성을 자동 검증한다.
6. 최초 smoke row에는 token usage와 전체 run 시간만 있고 요청별 latency가 없어,
   이후 실행부터 provider metadata에 `latency_seconds`를 기록하도록 했다.

v2의 parser는 공개 closed ontology 밖의 값을 validation error로 처리한다.
v1 smoke 점수는 v2 점수와 직접 비교하지 않으며 최종 결과에 재사용하지 않는다.
최종 run에서는 v2 protocol로 `traj_001`을 포함한 전 항목을 새로 호출한다.

### 11.3 2026-07-29 v2 five-checkpoint smoke

고정 plan
`04ef9b5d3459a4732fdf859c1e59f692fc9f251228bf26373a93c6035fbfcfac`로
`traj_001`의 60, 120, 180, 240, 300 checkpoint를 새로 실행했다. 실행 commit은
`bab4f75`, schema는 `stage2_2_reconstruct-v2`, projector는
`stage2_2_observable_state-v2`다. 단일 `gpt-5.6-sol` Responses API를 사용했고
동시성 1, 자동 재시도 0으로 5개 요청이 모두 완료됐다.

| Checkpoint | Final | Value | Status | Changed | Unchanged | Correct-change F1 | Exact |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 60 | 97.06% | 100.00% | 97.06% | 100.00% | 96.30% | 93.33% | 0 |
| 120 | 85.29% | 91.18% | 88.24% | 93.33% | 78.95% | 82.35% | 0 |
| 180 | 70.59% | 73.53% | 85.29% | 62.50% | 77.78% | 60.61% | 0 |
| 240 | 85.29% | 91.18% | 91.18% | 88.24% | 82.35% | 81.08% | 0 |
| 300 | 76.47% | 94.12% | 82.35% | 72.22% | 81.25% | 66.67% | 0 |
| 5-checkpoint macro | 82.94% | 90.00% | 88.82% | 83.26% | 83.32% | 76.81% | 0 |

동일 checkpoint의 initial-copy Final State Accuracy는 각각 79.41%, 55.88%,
52.94%, 50.00%, 47.06%였고 Changed State Accuracy는 모두 0%,
Unchanged State Accuracy는 모두 100%였다.

5-checkpoint change confusion 합계는 다음과 같다.

| Gold class | Pred unchanged | Pred changed, correct | Pred changed, wrong |
|---|---:|---:|---:|
| Gold unchanged | TN = 82 | 0 | FP = 15 |
| Gold changed | FN = 3 | TP-correct = 59 | TP-wrong-value = 11 |

- Change detection precision/recall/F1: 82.73% / 96.25% / 88.91%
- Correct-change precision/recall/F1: 71.38% / 83.26% / 76.81%
- Evidence hit rate: 75.67%
- Evidence citation precision: 43.67%
- Parse errors: 0/5
- Validation errors: 0

Status confusion matrix는 다음과 같다.

| Gold \ Pred | current | historical | stale | needs_verification | unknown | not_applicable | invalid/missing |
|---|---:|---:|---:|---:|---:|---:|---:|
| current | 109 | 3 | 2 | 2 | 0 | 0 | 0 |
| historical | 0 | 0 | 0 | 0 | 0 | 1 | 0 |
| stale | 2 | 0 | 2 | 0 | 0 | 2 | 0 |
| needs_verification | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| unknown | 0 | 0 | 0 | 0 | 15 | 0 | 0 |
| not_applicable | 3 | 0 | 0 | 0 | 4 | 25 | 0 |

| Checkpoint | Input tokens | Output tokens | Latency |
|---:|---:|---:|---:|
| 60 | 15,590 | 2,215 | 35.79s |
| 120 | 29,586 | 3,441 | 57.50s |
| 180 | 43,238 | 2,989 | 47.61s |
| 240 | 56,894 | 3,690 | 66.20s |
| 300 | 70,608 | 4,155 | 82.30s |
| 합계 | 215,916 | 16,490 | 289.39s |

공개 standard 단가를 uncached 기준으로 적용한 사용량 상한 비용은
`$1.57428`이며 ledger에는 `$1.575`로 올림 기록했다. checkpoint가 길수록
항상 점수가 단조 감소하지는 않았고 180에서 하락한 뒤 240에서 회복됐다.
하나의 trajectory와 단일 호출 결과이므로 이 변동을 context-length 효과로
단정하지 않는다. 이 결과를 본 뒤 prompt, ontology, parser, projector 또는
scorer를 변경하지 않았다.

### 11.4 2026-07-29 v3 `traj_010` robustness smoke

비중복 후보 9개를 추가한 `stage2_2_reconstruct-v3`를 구현 commit
`c274d05`에서 `traj_010`의 60, 120, 180, 240, 300 checkpoint에 실행했다.
고정 plan은
`d0c01022ed52aa00589f542045b2b6841979e3a650c8f91d46c99493f2a9f1ca`다.
Gold와 projector는 v2와 동일하며 단일 `gpt-5.6-sol` Responses API,
동시성 1, 자동 재시도 0을 사용했다.

| Checkpoint | Final | Value | Status | Changed | Unchanged | Correct-change F1 | Exact |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 60 | 79.41% | 85.29% | 91.18% | 71.43% | 85.00% | 68.97% | 0 |
| 120 | 88.24% | 91.18% | 94.12% | 90.91% | 86.96% | 80.00% | 0 |
| 180 | 82.35% | 85.29% | 94.12% | 91.67% | 77.27% | 75.86% | 0 |
| 240 | 58.82% | 61.76% | 76.47% | 52.94% | 64.71% | 46.15% | 0 |
| 300 | 61.76% | 73.53% | 79.41% | 40.00% | 78.95% | 38.71% | 0 |
| 5-checkpoint macro | 74.12% | 79.41% | 87.06% | 69.39% | 78.58% | 61.94% | 0 |

동일 checkpoint의 initial-copy Final State Accuracy는 각각 58.82%, 67.65%,
64.71%, 50.00%, 55.88%였고 Changed State Accuracy는 모두 0%,
Unchanged State Accuracy는 모두 100%였다.

| Gold class | Pred unchanged | Pred changed, correct | Pred changed, wrong |
|---|---:|---:|---:|
| Gold unchanged | TN = 80 | 0 | FP = 21 |
| Gold changed | FN = 6 | TP-correct = 46 | TP-wrong-value = 17 |

- Change detection precision/recall/F1: 75.38% / 91.97% / 82.60%
- Correct-change precision/recall/F1: 56.24% / 69.39% / 61.94%
- Evidence hit rate: 69.80%
- Evidence citation precision: 46.53%
- Parse errors: 0/5
- Validation errors: 0

Status confusion matrix는 다음과 같다.

| Gold \ Pred | current | historical | stale | needs_verification | unknown | not_applicable | invalid/missing |
|---|---:|---:|---:|---:|---:|---:|---:|
| current | 118 | 2 | 3 | 5 | 1 | 2 | 0 |
| historical | 1 | 0 | 0 | 0 | 0 | 1 | 0 |
| stale | 1 | 0 | 1 | 0 | 0 | 2 | 0 |
| needs_verification | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| unknown | 0 | 0 | 0 | 0 | 10 | 0 | 0 |
| not_applicable | 4 | 0 | 0 | 0 | 0 | 19 | 0 |

| Checkpoint | Input tokens | Output tokens | Latency |
|---:|---:|---:|---:|
| 60 | 15,996 | 3,362 | 64.67s |
| 120 | 29,865 | 3,026 | 57.38s |
| 180 | 43,074 | 3,649 | 67.20s |
| 240 | 56,672 | 3,456 | 66.89s |
| 300 | 70,113 | 3,968 | 70.91s |
| 합계 | 215,720 | 17,461 | 327.06s |

공개 standard 단가의 uncached 상한 비용은 `$1.60243`이며 ledger에는
`$1.603`으로 올림 기록했다. v3 추가 후보는 Gold 정답으로 등장하지 않으므로
이 결과는 새 상태 인식 능력이 아니라 넓어진 ontology에서 기존 Gold를 선택하는
robustness 결과다. 다른 trajectory에서 실행한 v2 smoke와 직접적인 모델 성능
차이로 해석하지 않는다. 결과 확인 후 prompt, ontology, parser, projector,
Gold 또는 scorer를 변경하지 않았다.

Provider별 constrained JSON/structured-output 기능은 사용하지 않는다. 공통
prompt와 strict parser를 사용해 이후 다른 provider를 추가하더라도 output
제약이 달라지지 않게 한다.

## 12. 난이도 검증과 baseline

새 과제는 기존 MCQ보다 구조적으로 어렵다.

- 하나의 A–D 답이 아니라 전체 state JSON을 생성한다.
- 여러 path의 최신 값을 동시에 유지해야 한다.
- 회사명, 주소, 금액 같은 open value를 대화에서 복원한다.
- 여러 사건에 의한 overwrite, archive, stale 상태를 처리한다.
- neutral filler와 hard negative를 상태 변화로 오해하지 않아야 한다.

그러나 구조가 어렵다는 사실만으로 실제 난이도 상승을 주장하지 않는다. 최종
결과에서 기존 MCQ의 ceiling과 새 과제의 다음 지표를 함께 보고 난 뒤에만
empirical difficulty를 판단한다.

- Final State Accuracy
- Exact State Match
- Changed/Unchanged State Accuracy
- checkpoint 45 대비 300 성능
- initial-copy baseline 대비 개선

### 12.1 Initial-copy baseline

모든 대화를 무시하고 `S000`을 그대로 최종 prediction으로 사용하는 deterministic
baseline을 반드시 계산한다.

```text
InitialCopy(path) = InitialCell(path)
```

이 baseline은 unchanged path가 많은 경우 전체 정확도가 얼마나 부풀려지는지
보여준다. 모델이 initial-copy보다 높은 Final State Accuracy를 얻더라도,
Changed State Accuracy와 Correct-Change F1이 낮다면 대화 속 변화를 제대로
추적했다고 해석하지 않는다.

Initial-copy는 다음과 같은 예상 패턴을 갖는다.

- Unchanged State Accuracy: 높음
- Changed State Accuracy: 0에 가까움
- Change confusion matrix: FN이 많음

## 13. Gold 품질과 학술적 타당성

### 13.1 자동 검증

- 전체 dialogue/gold identity와 session 순서를 검증한다.
- changed cell마다 checkpoint 이전의 visible supporting dialogue가 있는지
  검증한다.
- neutral/no-event session이 current-state update를 만들지 않는지 검증한다.
- occurred session의 current-state update가 보존됐는지 검증한다.
- overwrite/archive/stale 적용 후 최종 effective state가 일관적인지 검증한다.
- 공개 projection에 internal-only ID가 남지 않는지 검증한다.

### 13.2 사람 검수

논문 보고 전에는 changed cell, unchanged hard negative, overwrite, stale/archive
사례를 층화 표집하여 두 명이 독립 검수한다.

- change 여부와 status: Cohen's kappa
- open value: exact agreement
- 불일치: 합의 adjudication
- annotation guideline, 표본 수, agreement 결과 공개

Gold가 자동 replay로 생성됐다는 사실만으로 대화에서 실제 복원 가능한 정답이라는
점이 보장되지는 않는다. 사람 검수는 synthetic Gold의 construct validity를
확인하기 위한 절차다.

### 13.3 평가 freeze와 집계

- smoke 점수와 실행 계약을 기록한 뒤 prompt, schema, parser, normalizer를
  freeze한다.
- 최종 run은 20개 trajectory 모두를 처음부터 새로 실행한다.
- checkpoint를 독립 표본으로 취급하지 않고 trajectory 내부에서 먼저 평균한다.
- 전체 결과는 trajectory macro-average와 trajectory bootstrap 95% CI로 보고한다.
- API 실행 날짜, exact model ID, SDK version, prompt hash, dataset revision,
  Gold projection version을 manifest에 기록한다.
- 실패 응답을 결과를 본 뒤 선택적으로 재실행하거나 제외하지 않는다.

## 14. 논문 보고 지표의 역할

많은 지표를 출력하되 사후에 유리한 점수만 선택하지 않도록 역할을 미리 고정한다.

| 역할 | 지표 |
|---|---|
| 대표 전체-state 지표 | Final State Accuracy |
| 엄격한 전체 snapshot 지표 | Exact State Match |
| 실제 dialogue update 반영 | Changed State Accuracy, Correct-Change F1 |
| 과잉 변경 방지 | Unchanged State Accuracy, FP rate |
| 오류 분석 | Value/Status Accuracy, confusion matrices |
| 근거성 | Evidence Hit Rate, Citation Precision |
| 신뢰성 | Parse success, schema validation, failed requests |

Evidence 오류는 state cell의 정답 여부와 분리한다. 이벤트 복원 개수는
`stage2_2_reconstruct`의 핵심 지표로 사용하지 않는다. 이미 덮어써져 current
state에 남지 않은 과거 이벤트를 평가하려면 별도의 event-history reconstruction
과제가 필요하기 때문이다.

## 15. 관련 평가 관행과의 정합성

- Full-state exact match는 Dialogue State Tracking의 Joint Goal Accuracy와 같은
  역할을 한다.
- path별 Final State Accuracy는 slot accuracy에 대응하며 exact match의 엄격함을
  보완한다.
- Changed State Accuracy와 change confusion matrix는 unchanged slot의 다수성으로
  전체 정확도가 부풀려지는 문제를 진단한다.
- 공개 schema와 open value 생성은 schema-guided/open-vocabulary DST 설정과
  일치한다.
- deterministic parser와 schema validation은 state 의미 평가와 출력 형식 실패를
  구분한다.

참고 문헌:

- Dey, Kummara, and Desarkar. 2022. [Towards Fair Evaluation of Dialogue
  State Tracking by Flexible Incorporation of Turn-level
  Performances](https://aclanthology.org/2022.acl-short.35/).
- Aksu and Chen. 2024. [Granular Change Accuracy: A More Accurate
  Performance Metric for Dialogue State
  Tracking](https://aclanthology.org/2024.lrec-main.699/).
- Li et al. 2024. [Large Language Models as Zero-shot Dialogue State Tracker
  through Function Calling](https://aclanthology.org/2024.acl-long.471/).
- Li et al. 2021. [Zero-shot Generalization in Dialog State Tracking through
  Generative Question Answering](https://aclanthology.org/2021.eacl-main.91/).
- Ye, Manotumruksa, and Yilmaz. 2021. [MultiWOZ 2.4: A Multi-Domain
  Task-Oriented Dialogue Dataset with Essential Annotation Corrections to
  Improve State Tracking Evaluation](https://arxiv.org/abs/2104.00773).

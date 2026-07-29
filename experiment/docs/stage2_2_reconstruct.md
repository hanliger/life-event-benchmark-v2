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
| Entity reference | alias 규칙만 제공 | `H001`, `H002` | 내부 simulator ID 노출 방지 |

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
| Claude Opus 5 |  |  |  |  |  |  |  |  |
| Gemini 3.1 Pro |  |  |  |  |  |  |  |  |

Change confusion matrix, status confusion matrix, evidence 점수와 path/domain별 결과는
보조 표로 제공한다.

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

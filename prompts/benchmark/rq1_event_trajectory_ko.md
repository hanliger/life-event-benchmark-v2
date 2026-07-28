<!-- prompt: rq1_event_trajectory_ko v1 -->
다음은 한 고객의 은행 상담 세션 이력입니다. 세션은 시간순으로 정렬되어 있고, 각 세션은 `[세션 D###]` 식별자와 대화 발화로만 구성됩니다.

당신의 과제는 지금까지의 세션 이력에서 확인 가능한 고객의 생애 사건(Life Event) 인스턴스 전체 목록을 복원하는 것입니다.

## 판단 규칙

- 사건 상태는 다음 네 가지 중 하나입니다.
  - `weak_signal`: 약한 단서만 있는 단계
  - `upcoming`: 예정이 구체적으로 확인된 단계
  - `occurred`: 실제로 일어난 것이 확인된 단계
  - `cancelled`: 예정되었던 사건이 취소된 것이 확인된 단계
- 사건의 개수는 알려져 있지 않습니다. 0개, 1개, 여러 개일 수 있습니다.
- 같은 event_id의 사건이 서로 다른 시점에 여러 번 발생할 수 있습니다. 각 발생은 별도의 인스턴스로 보고하세요.
- 일부 세션은 사건과 무관한 일상 금융 업무이고, 일부 세션은 사건을 암시하는 듯하지만 실제로는 아무 사건도 일어나지 않는 오해 유발 세션입니다. 이런 세션을 사건으로 보고하지 마세요.
- 특정 구간(예: 15개 세션)마다 사건이 하나씩 있다고 가정하지 마세요.
- 발생(occurred)이 대화에서 뒷받침되지 않는 사건을 `occurred`로 표시하지 마세요.
- 근거 세션이 없는 사건을 만들어내지 마세요. 근거가 없으면 `events`를 빈 배열로 두세요.

## 근거 세션 규칙

- `core_evidence_session_ids`: 그 사건의 존재와 상태를 직접 뒷받침하는 최소한의 세션 집합만 넣으세요.
- `supporting_session_ids`: 사건 이후의 후속 결과나 과거 상태 회상처럼 간접적으로만 관련된 세션을 넣을 수 있습니다. 이것으로 core 근거를 대체하지 마세요.
- `first_evidence_session_id`: 그 사건의 단서가 처음 나타난 세션.
- `status_anchor_session_id`: 보고한 상태(status)를 확정짓는 세션. `occurred`/`cancelled`는 그것이 처음 확인된 세션, `weak_signal`/`upcoming`은 가장 최근 근거 세션.
- 모든 세션 ID는 위 이력에 실제로 존재하는 `D###` 형식이어야 합니다.

## 가능한 Life Event 목록

{{TAXONOMY}}

## 출력 형식

이유나 설명 없이 아래 JSON만 출력하세요. `events`는 `first_evidence_session_id` 기준 시간순으로 정렬하세요. `confidence`는 0과 1 사이의 숫자입니다.

```json
{
  "events": [
    {
      "prediction_id": "P001",
      "event_id": "career_employment",
      "status": "occurred",
      "first_evidence_session_id": "D010",
      "status_anchor_session_id": "D015",
      "core_evidence_session_ids": ["D010", "D015"],
      "supporting_session_ids": [],
      "confidence": 0.92
    }
  ]
}
```

## 상담 세션 이력

{{SESSIONS}}

# Stage 1 Prompt Leakage Audit

## 결론

Stage 1 `stage1_occurred_event_evidence_pairs` prompt는 checkpoint까지 선택된
상담 발화와 전체 public life-event taxonomy만 공개한다. 모델이 답해야 하는
occurred pair Gold, event instance ID, session type, lifecycle status, canonical
`S###` ID는 공개하지 않는다.

## 실행 시 검사

`stage1.py::audit_rendered_prompt`는 method별 최초·최종 checkpoint prompt에서
다음을 검사한다.

| 검사 | 실패 조건 |
|---|---|
| `future_session_ids` | checkpoint를 넘는 `D###` session이 등장 |
| `canonical_session_ids_in_prompt` | private `S###` ID가 하나라도 등장 |
| `gold_fields_in_prompt` | Gold ledger/pair/session-map field가 등장 |
| `gold_fields_in_retrieval_query` | retrieval query metadata에 같은 field가 등장 |
| `rendered_candidate_events` | 전체 taxonomy 개수와 렌더된 후보 개수가 다름 |

하나라도 실패하면 paid execution을 차단한다. `evaluator.py`는 prompt 생성 전에
`stage1.generation_item`으로 Gold와 Gold-derived metadata를 제거한다.

## 모델 공개 형식

- session: 시간순 `D###` ID와 user/assistant 발화
- taxonomy: 활성 event의 `event_id`와 한국어 이름 전체
- 출력: `{"pairs": [{"event_id", "evidence_session_id"}]}`

설명, lifecycle status, confidence는 받지 않는다. 같은 event가 여러 번
발생했다면 서로 다른 발생 session과 함께 여러 pair로 답한다.

Full Context는 누적 prefix 전체를 받고, BM25/Dense/Mem0는 같은 과업 질문으로
top-10 근거를 선택한다. Letta는 같은 질문으로 archival search를 한 번 수행한다.

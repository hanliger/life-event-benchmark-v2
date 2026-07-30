# Stage 1 Prompt Leakage Audit

## 결론

Stage 1 `stage1_event_identification` prompt에는 Gold `event_id`, `event_label`,
`event_instance_id`, `target_event_status`가 field 형태로 전달되지 않으며 query
checkpoint 이후 session도 포함되지 않는다. 다만 과제 정의상 **활성 life event
template 전체 목록**이 후보로 공개된다. 정답은 이 목록 안에 있으므로 후보 목록
자체는 leakage가 아니라 closed-set 선택 과제의 disclosure이다.

이 문서는 생성 prompt를 바꾸는 specification이 아니라 현재 protocol의 disclosure
및 leakage audit 기록이다. 실행 시 `audit-prompt`가 method별 runtime 검사를
수행하고 `traj_001`의 최초·최종 checkpoint rendering을
`prompts/audit_examples/*.txt.gz`로 보존한다.

## 검사 항목

`stage1.py::audit_rendered_prompt`가 method × (최초 checkpoint, 최종 checkpoint)
마다 다음을 확인하고, 하나라도 실패하면 paid execution을 차단한다.

| 검사 | 실패 조건 |
|---|---|
| `future_session_ids` | prompt에 `[S###` 형태로 checkpoint 초과 session이 등장 |
| `gold_fields_in_prompt` | `"gold"`, `event_instance_id`, `target_event_id`, `target_event_label`, `target_event_status` 중 하나가 prompt에 등장 |
| `gold_fields_in_retrieval_query` | 같은 field 이름이 retrieval query에 등장 |
| `gold_event_instance_id_in_prompt` | 해당 item의 Gold `event_instance_id` 값이 prompt에 등장 |
| `rendered_candidate_events` | 렌더된 후보 개수가 `metadata.candidate_events` 개수와 불일치 |

마지막 검사가 핵심이다. 후보 목록이 정답 쪽으로 좁혀지면 문항 난이도가 조용히
바뀌므로, 모든 checkpoint에서 후보 집합이 동일한 전체 목록임을 확인한다.

`evaluator.py`는 생성 직전 `stage1.generation_item`으로 `gold`와
`metadata.target_event_status`를 제거한다. `build_query`는 item metadata를
렌더하지 않으므로 이는 defence in depth이며, `show-prompt`가 출력하는 문자열이
paid 실행에서 전송되는 문자열과 동일하도록 보장한다.

## System prompt 전문

```text
당신은 금융 상담 이력에서 과거 Life Event와 그 시점의 금융 상태를 판정한다.
계획·희망·검토·신청과 실제 발생을 구분한다.
취소·철회·정정·갱신이 있으면 질문에 지정된 기간을 기준으로 판단한다.
현재 질의 checkpoint 이후의 정보는 사용하지 않는다.
설명 없이 <answer>정답</answer> 형식으로만 답한다.
```

## User prompt 전문

`prompts.py::build_query`가 아래 고정 본문과 method별 evidence를 결합한다.

```text
아래 제공된 상담 이력 또는 검색 근거와 질문만 사용하세요.
질문의 대상 기간과 현재 질의 checkpoint를 혼동하지 마세요.

[제공된 이력/근거]
<method가 제공한 S000 및 S001…Scheckpoint>

[질문]
전체 상담 이력을 참고하여, <대상 기간> 기간에 마지막으로 실제 발생한 Life
Event는 무엇인가? 가능한 목록에서 하나를 선택하시오.

[가능한 event_id]
- <event_id>: <label_ko>
...

설명 없이 <answer>event_id</answer> 형식으로만 답하세요.
```

Letta는 evidence를 prompt에 싣지 않고 archival memory에서 직접 검색한다. 따라서
감사 대상은 아래 pre-search query이며 검색 결과는 paid 실행 시점에 agent가
선택한다.

```text
archival search는 최대 1회, 각 결과는 최대 10개만 사용하라.

<위 user prompt에서 [제공된 이력/근거]가 빈 상태>
```

## Method별 evidence 노출

| Method family | prompt에 실리는 session |
|---|---|
| Full Context | S000과 S001…Scheckpoint 전체 |
| BM25 / Dense | 질문 단일 query의 top-10 (S000이 index에 포함) |
| Mem0 | 질문 단일 query의 top-10 memory |
| Letta | 없음. agent가 archival search 1회, top_k=10으로 직접 조회 |

## 알려진 disclosure와 limitation

- 활성 life event template 전체가 후보로 공개된다. 후보 수가 적은 locale에서는
  무작위 정답률이 올라갈 수 있으므로 후보 수를
  `answer_pairs/*/cp_XXX.json`의 `candidate_event_count`로 함께 보고한다.
- 질문 본문에 대상 기간의 시작·종료 **날짜**가 포함된다. 이는 과제 정의이며
  session ID 범위는 노출하지 않는다.
- Retrieval/Memory arm은 질문 문장을 그대로 query로 사용한다. 질문에 포함된
  날짜 표현이 BM25 token으로 작동할 수 있다는 점은 결과 해석에 명시한다.

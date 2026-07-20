# 은행 상담 세션 수정 프롬프트 (ko_KR)

이전에 생성한 세션이 검증에 실패했습니다. 아래 위반 사항을 고치고,
동일한 조건과 출력 형식으로 JSON만 다시 출력하세요.

## 위반 사항
{violations}

위 목록은 현재 시도뿐 아니라 앞선 시도에서 발견된 위반을 누적한 것입니다. 모두 고쳐야 합니다.

## 반드시 지킬 압축 조건
{repair_constraints}

## 이전 출력
{previous_output}

주의:
- 위반 표현을 제거하되 필수 단서 표현은 유지해야 합니다.
- must_not_include_terms는 명세 전달용입니다. 목록의 문자열을 turns, cue_text, evidence_text에 복사하지 마세요.
- session_memory_updates에 없는 memory_fact 또는 operation/value annotation을 새로 만들지 마세요.
- allowed_concrete_values에 없는 날짜·금액·기간·금리·인원·횟수·계좌 끝자리를 만들지 마세요. 필요한 경우 숫자 없는 일반 표현으로 바꾸세요.
- hard_negative/no_update 세션에서는 memory_fact annotation을 모두 제거하고 event_signal 또는 near_miss만 사용하세요.
- cue_annotations는 반드시 cue_type, linked_memory_path, linked_memory_operation, linked_memory_value 필드를 사용하세요. planned_cues의 cue_id, cue_role, path, operation, value, linked_memory_paths 형식을 출력에 복사하지 마세요.
- 금지 표현은 user와 assistant 양쪽 모두에서 제거합니다. assistant의 선택지, 예시, 확인 질문에도 넣지 않습니다.
- 대화 자연스러움을 유지하세요.
- direct_event_disclosure/forbidden_event_paraphrase: 직설 표현을 제거하고 계약에 있는 간접 state·financial dimension으로 바꾸세요.
- lifecycle_*: 상투적인 상태 표지어를 반복하지 말고 lifecycle semantic strategy의 의미만 다른 문장으로 실현하세요.
- insufficient_event_evidence/required_evidence_not_realized: 빠진 dimension을 지정된 user 턴에 추가하되 사건을 더 직접적으로 말하지 마세요.
- high_risk_*: 누락 슬롯을 만들지 말고 작업을 pending으로 유지하며 완료·접수·적용 문구를 제거하세요.
- unsupported_bank_policy_claim/bank_policy_contradiction: 지원 여부·수수료·자격 단정을 중립적인 앱 확인 절차로 바꾸세요.
- event_label_leakage: 실제 문맥에서 라벨인 경우만 간접 금융 표현으로 바꾸고, 다른 단어 속 우연한 문자열은 바꿀 필요가 없습니다.
- stale_old_current_confusion: old/current annotation과 user 근거를 각각 보존하고 현재 유효값을 명시적으로 구분하세요.
- action_resolution과 cue_annotations도 수정된 visible dialogue에 맞춰 함께 갱신하세요.
- 이전 출력 전체를 수정한 완전한 JSON 객체를 다시 출력하세요. 일부 turn이나 annotation만 출력하지 마세요.
- JSON 외 다른 텍스트를 출력하지 마세요.

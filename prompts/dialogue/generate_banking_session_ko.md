# 은행 상담 세션 생성 프롬프트 (ko_KR)

당신은 한국 모바일/인터넷뱅킹 챗봇 상담 대화 데이터를 생성하는 시뮬레이터입니다.
아래 조건을 만족하는 하나의 상담 세션을 JSON으로만 출력하세요.

## 고객 정보 (숨김 상태 — 대화에 직접 노출 금지)
- 나이: {age}
- 말투: {user_style}
- 페르소나 요약: {persona_summary}
- 현재 고용상태: {employment_status}
- 현재 주거상태: {residence_status}
- 현재 혼인상태: {marital_status}
- 대출 보유 여부: {has_loan}

## 세션 조건
- 세션 유형: {session_type}
- 은행 업무(financial_task): {financial_task}
- 이벤트 상태(내부 정보): {event_status}
- 반드시 자연스럽게 포함할 단서 표현: {must_include_cues}
- 절대 등장하면 안 되는 표현: {must_not_include_terms}
- cue_annotations에서 연결 가능한 memory path: {target_memory_paths}
- 구조화된 사실 컨텍스트: {structured_context}

## 대화 규칙
1. 고객은 은행 업무를 보러 온 것이지 자기 인생을 설명하러 온 것이 아닙니다.
2. 고객은 Life Event 명칭(예: 이사, 결혼, 이직, 퇴사 등)을 직접 말하지 않습니다.
   단서 표현을 통해 간접적으로만 드러납니다.
3. 고객 발화는 짧은 구어체입니다. 초성체(ㅋㅋ, ㅇㅇ 등)와 이모지는 금지합니다.
4. assistant는 모바일/인터넷뱅킹 챗봇입니다. 정중하지만 앱 안에서 응답하는 말투를 사용합니다.
5. 상담원은 고객의 Life Event를 요약하거나 명명하지 않습니다.
   ("이사하셨군요", "결혼하셨군요" 같은 발화 금지)
6. assistant는 한 번에 하나의 실무적인 은행 질문만 합니다.
7. 대화에 이벤트 라벨, FA 코드, 메타데이터, 섹션 제목이 보이면 안 됩니다.
8. 이벤트 상태가 weak_signal이면 확정된 것처럼 말하지 않습니다.
9. 이벤트 상태가 upcoming이면 미래 시점 표현은 되지만 이미 일어난 것처럼 말하지 않습니다.
10. 이벤트 상태가 occurred이면 금전적 결과(입금, 이체, 납부 변경 등) 단서가 포함되어야 합니다.
11. 이벤트 상태가 cancelled이면 이전 신호와 이후 취소 언급이 함께 드러나야 합니다.
12. 출금이 발생하는 변경(정기이체 변경/해지, 송금, 해지 등)은 상담원이
    "고객님 확인 후 진행" 원칙을 안내할 수 있으나 자동으로 실행하지 않습니다.
13. 전체 {turns_min}~{turns_max}턴, 고객 {user_turns_min}~{user_turns_max}턴. user와 assistant가 번갈아 말합니다.
14. 첫 턴은 user, 마지막 턴은 assistant입니다. 즉 전체 턴 수는 짝수여야 합니다.
15. cue_annotations의 turn_index는 0부터 시작하는 인덱스이며, 반드시 user 턴을 가리킵니다.
16. cue_annotations의 linked_memory_path는 위 "연결 가능한 memory path" 중 하나만 사용합니다. 연결 가능한 path가 없으면 null로 둡니다.
17. 현재 고객 상태와 충돌하는 세부사항을 만들지 않습니다. 예를 들어 retired/unemployed/student/homemaker 고객에게 월급일·급여 받는 계좌를 말하게 하지 않고, owner/jeonse/family_home 고객에게 월세·집주인 납부를 말하게 하지 않습니다.
18. 오프라인 지점/창구 상황처럼 쓰지 않습니다. 다음 표현과 장면은 금지합니다: 창구, 영업점, 방문, 안내 창구, 모시겠습니다, 신분증 지참, 실물 신분증, 신청서 작성, 서명, 출력, 우편 발송, 우편 배송, 배송, 방문 수령, 창구 수령, 실물 수령. 필요한 확인은 앱 인증, 본인인증, 확인 버튼, 메뉴 이동, 알림/문자 안내처럼 비대면 흐름으로 표현합니다.
19. 절대 등장하면 안 되는 표현과 금지 표현은 user와 assistant 양쪽 모두에 적용됩니다. assistant의 선택지, 예시, 확인 질문에도 넣지 않습니다.
20. 구조화된 사실 컨텍스트의 event.params, session_memory_updates, event_memory_updates, persona_state, current_memory와 충돌하는 주소·금액·가족관계·고용·주거 정보를 만들지 않습니다. 제공되지 않은 구체값은 새로 만들지 말고 일반 표현을 사용합니다.
21. session_memory_updates의 각 항목은 반드시 user 발화에 명시적으로 근거가 있어야 합니다. update/create/set_pending은 new_value가 자연어로 드러나야 하고, archive/mark_stale/clear_pending/set_not_applicable은 해당 변경·취소·종료가 분명히 드러나야 합니다.
22. 각 session_memory_updates 항목마다 cue_type="memory_fact" annotation을 하나 이상 만들고 linked_memory_path, linked_memory_operation, linked_memory_value를 원본과 정확히 동일하게 복사합니다. evidence_text에는 해당 user 발화에 실제로 포함된 최소 근거 문자열을 넣습니다.

## 출력 형식 (JSON만 출력)
{
  "turns": [
    {"speaker": "user", "text": "..."},
    {"speaker": "assistant", "text": "..."}
  ],
  "cue_annotations": [
    {
      "turn_index": 2,
      "cue_type": "memory_fact",
      "linked_memory_path": "employment.salary_day",
      "linked_memory_operation": "update",
      "linked_memory_value": 25,
      "evidence_text": "급여가 매달 25일에 들어와요"
    }
  ],
  "quality_self_check": {
    "no_direct_life_event_mention": true,
    "no_assistant_label_leakage": true,
    "financial_task_clear": true,
    "turn_count_ok": true
  }
}

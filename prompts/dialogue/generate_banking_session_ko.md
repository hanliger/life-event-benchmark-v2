# 은행 상담 세션 생성 프롬프트 (ko_KR)

당신은 한국 은행 상담 대화 데이터를 생성하는 시뮬레이터입니다.
아래 조건을 만족하는 하나의 상담 세션을 JSON으로만 출력하세요.

## 고객 정보 (숨김 상태 — 대화에 직접 노출 금지)
- 나이: {age}
- 말투: {user_style}
- 페르소나 요약: {persona_summary}

## 세션 조건
- 세션 유형: {session_type}
- 은행 업무(financial_task): {financial_task}
- 이벤트 상태(내부 정보): {event_status}
- 반드시 자연스럽게 포함할 단서 표현: {must_include_cues}
- 절대 등장하면 안 되는 표현: {must_not_include_terms}

## 대화 규칙
1. 고객은 은행 업무를 보러 온 것이지 자기 인생을 설명하러 온 것이 아닙니다.
2. 고객은 Life Event 명칭(예: 이사, 결혼, 이직, 퇴사 등)을 직접 말하지 않습니다.
   단서 표현을 통해 간접적으로만 드러납니다.
3. 고객 발화는 짧은 구어체입니다. 초성체(ㅋㅋ, ㅇㅇ 등)와 이모지는 금지합니다.
4. 상담원은 정중한 은행 상담원 말투를 사용합니다.
5. 상담원은 고객의 Life Event를 요약하거나 명명하지 않습니다.
   ("이사하셨군요", "결혼하셨군요" 같은 발화 금지)
6. 상담원은 한 번에 하나의 실무적인 은행 질문만 합니다.
7. 대화에 이벤트 라벨, FA 코드, 메타데이터, 섹션 제목이 보이면 안 됩니다.
8. 이벤트 상태가 weak_signal이면 확정된 것처럼 말하지 않습니다.
9. 이벤트 상태가 upcoming이면 미래 시점 표현은 되지만 이미 일어난 것처럼 말하지 않습니다.
10. 이벤트 상태가 occurred이면 금전적 결과(입금, 이체, 납부 변경 등) 단서가 포함되어야 합니다.
11. 이벤트 상태가 cancelled이면 이전 신호와 이후 취소 언급이 함께 드러나야 합니다.
12. 출금이 발생하는 변경(정기이체 변경/해지, 송금, 해지 등)은 상담원이
    "고객님 확인 후 진행" 원칙을 안내할 수 있으나 자동으로 실행하지 않습니다.
13. 전체 {turns_min}~{turns_max}턴, 고객 {user_turns_min}~{user_turns_max}턴. user와 assistant가 번갈아 말합니다.

## 출력 형식 (JSON만 출력)
{
  "turns": [
    {"speaker": "user", "text": "..."},
    {"speaker": "assistant", "text": "..."}
  ],
  "cue_annotations": [
    {"turn_index": 2, "cue_type": "...", "linked_memory_path": "..."}
  ],
  "quality_self_check": {
    "no_direct_life_event_mention": true,
    "no_assistant_label_leakage": true,
    "financial_task_clear": true,
    "turn_count_ok": true
  }
}

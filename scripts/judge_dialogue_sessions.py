#!/usr/bin/env python
"""OPTIONAL, ADVISORY LLM judge over generated dialogue sessions.

This tool is deliberately *outside* the required procedural gate chain
(`--require-canary-pass` / `--require-human-review-pass` /
`--require-regression-pass`). It never blocks production on its own. It runs an
LLM evaluator over dialogue sessions and emits advisory artifacts:

- ``judge_report.{json,md}``   — per-dimension pass rates and gate view
- ``judged_sessions.jsonl``    — one packet-shaped record per session
- ``suggested_regeneration.jsonl`` — sessions the judge flagged as likely fails

The judge is an approximation, not ground truth. It shares the same rubric as
the human review packet so its output can be calibrated against human labels,
but its decision is NON-AUTHORITATIVE: use it to widen coverage (100% vs the
canary sample) and to triage which sessions a human should look at, not to
replace the human gate. The most trust-sensitive dimensions (memory grounding,
semantic leakage, high-risk safety) are exactly where an LLM judge — especially
one from the same model family that generated the dialogue — is least reliable.

Example:

  python scripts/judge_dialogue_sessions.py \
    --plans-dir data/runs/v4/dialogues/plans \
    --sessions-dir data/runs/v4/dialogues/sessions \
    --output-dir data/runs/v4/reports/dialogue_judge \
    --provider anthropic --model claude-opus-4-8
"""

from __future__ import annotations

import argparse
import json
import re
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:  # imported as scripts.judge_dialogue_sessions in tests
    from scripts import _bootstrap  # type: ignore # noqa: F401

from fin_life_benchmark.io import read_jsonl, write_jsonl
from fin_life_benchmark.llm.client import LLMClient

try:
    from score_dialogue_review_packet import CRITICAL_FIELDS, RATE_THRESHOLDS, score_records
except ModuleNotFoundError:  # imported as scripts.judge_dialogue_sessions in tests
    from scripts.score_dialogue_review_packet import (  # type: ignore
        CRITICAL_FIELDS,
        RATE_THRESHOLDS,
        score_records,
    )

REVIEWER_BOOL_FIELDS = (
    "natural_korean_dialogue",
    "event_task_alignment",
    "lifecycle_calibration",
    "memory_grounding",
    "assistant_semantic_leakage",
    "high_risk_safety",
    "event_implicit_but_recoverable",
)

SYSTEM_PROMPT = """당신은 한국어 모바일뱅킹 대화 데이터의 전문 평가자다. 제공된 evaluator-only metadata와 benchmark-visible dialogue를 이용해 각 세션의 reviewer field를 `pass` 또는 `fail`로 판정한다.

## 최우선 원칙

1. 제공된 rubric만 평가 기준으로 사용한다.
2. rubric에 없는 별도의 완결성·UI 구현·상품 정확성 기준을 추가하지 않는다.
3. 각 reviewer field는 서로 독립적으로 판정한다.
4. 명시적인 fail 조건에 해당하지 않으면 pass로 판정한다.
5. evaluator-only metadata는 정답 판단에 사용할 수 있지만, comments에 숨은 event label, lifecycle label, memory path 등 evaluator 전용 표현을 노출하지 않는다.
6. 외부 웹 검색이나 실제 은행 앱 검증은 하지 않는다. 이 평가는 대화 내부의 정합성 평가다.
7. `repairs` 수나 자동 flag 존재 여부 자체를 fail 근거로 사용하지 않는다.

## 입력

각 세션에는 다음 정보가 포함될 수 있다.

* session ID
* evaluator event
* lifecycle
* financial task
* expected memory operation 또는 memory fact
* action resolution
* user–assistant dialogue
* repairs 또는 자동 flag

제공되지 않은 metadata를 임의로 추정하지 않는다.

## 전체 판정 절차

각 세션을 다음 순서로 검토한다.

### 1단계: 사용자 근거 추출

먼저 user 발화에서만 다음 내용을 추출한다.

* 사용자가 요청한 금융 업무
* 생활 사건을 암시하는 표현
* 사건이 발생·예정·가능성·취소 중 어느 상태인지
* 과거 상태와 현재 상태
* 사용자 본인이 제시한 날짜, 금액, 주소, 인원, 관계, 회사, 계좌 정보
* 실행을 위해 명시적으로 제공한 값
* 사용자가 최종적으로 승인하거나 거절한 행동

assistant 발화에만 등장한 사실은 사용자 근거로 사용하지 않는다.

### 2단계: assistant의 추가 의미 확인

assistant가 다음 내용을 새로 만들었는지 확인한다.

* 사용자가 아직 말하지 않은 사건 정체
* lifecycle 상태
* subtype
* 날짜, 금액, 주소, 가족관계, 회사 등 구체적 값
* 실행에 필요한 계좌, 금액, 수취인, 날짜
* 처리 완료 또는 조회 결과

### 3단계: 각 field 독립 판정

한 field의 fail을 다른 field에 자동 전파하지 않는다.

예:

* 계산 결과가 불완전해도 event alignment는 별도로 pass일 수 있다.
* 사건 subtype이 복원되지 않아도 lifecycle 시제는 정확할 수 있다.
* 문장이 약간 어색해도 high-risk safety는 pass일 수 있다.
* assistant leakage가 있어도 memory grounding은 user evidence가 충분하면 별도 판단한다.

---

# Reviewer field 판정 기준

## 1. natural_korean_dialogue

8개 안팎의 발화가 하나의 금융 업무에 관한 자연스럽고 간결한 한국어 모바일뱅킹 대화를 구성하는지 판단한다.

### Pass

다음 조건을 대체로 충족하면 pass다.

* user 발화가 실제 한국어 구어체로 이해된다.
* assistant가 정중한 앱 챗봇 말투를 사용한다.
* 각 답변이 직전 발화와 자연스럽게 연결된다.
* 세션이 하나의 주요 금융 업무에 머문다.
* 확인 질문이 해당 업무를 처리하는 데 실무적으로 필요하다.
* 사용자가 보류하거나 나중에 다시 하겠다고 하면 자연스럽게 종료된다.
* 약간 반복적이거나 정형적인 문구가 있어도 흐름을 방해하지 않는다.

### 화면·메뉴 표현 처리

다음 표현 자체는 fail 사유가 아니다.

* “화면에 보여드릴게요.”
* “앱에서 확인하실 수 있어요.”
* “메뉴로 안내해드릴게요.”
* “화면에 띄워드렸어요.”
* “앱 알림으로 안내드릴게요.”

실제 화면이 benchmark dialogue에 첨부되지 않았다는 이유로 fail 처리하지 않는다. 모바일뱅킹 챗봇이 앱 UI와 연결되어 있다는 대화적 관례로 인정한다.

### 계산 업무 처리

계산 결과가 숫자로 직접 출력되지 않았다는 이유만으로 fail 처리하지 않는다.

다만 다음은 fail이 될 수 있다.

* 계산에 반드시 필요한 원금 또는 월 납입액이 제공되지 않았는데 계산이 완료됐다고 단정한다.
* assistant가 사용자가 제공하지 않은 값을 사용해 결과가 산출됐다고 말한다.
* 사용자가 계산을 요청했는데 필요한 값을 묻지도 않고 결과가 나왔다고 주장해 대화 흐름이 실질적으로 성립하지 않는다.

반면 필요한 값이 부족하다고 설명하고 나중에 다시 계산하자고 하면 pass다.

### Fail

다음과 같은 실질적 문제가 있을 때만 fail이다.

* 앞뒤가 맞지 않는 응답
* 동일한 질문이나 이미 확인된 사실을 불필요하게 반복
* 하나의 세션에서 무관한 여러 금융 업무가 혼합됨
* 사용자 요청과 관계없는 절차를 안내함
* user가 제공하지 않은 정보를 전제로 대화가 진행됨
* 계산이나 조회에 필요한 필수 정보가 없는데 결과가 나왔다고 단정함
* 존댓말과 반말이 심하게 뒤섞임
* 단순한 업무를 이유 없이 길게 늘임
* 창구 방문 등 대화의 앱 맥락과 뚜렷하게 충돌하는 안내

경미한 어색함, 조사 생략, 쉼표 부족, 일반적인 챗봇 표현만으로 fail 처리하지 않는다.

---

## 2. event_task_alignment

계획된 금융 업무와 대화에 나타난 life-event evidence가 자연스럽게 연결되는지 판단한다.

### Pass

* 사용자가 해당 금융 업무를 처리하면서 사건 단서를 말할 현실적인 이유가 있다.
* 사건의 subtype 세부사항이 금융 업무와 호환된다.
* 세션 전체가 하나의 financial task에 머문다.
* routine 또는 hard-negative 세션에서는 사건이 없다는 설명이 업무 맥락과 자연스럽게 연결된다.

사건 설명이 다소 장황하거나 금융 업무에 꼭 필요하지 않은 정도라는 이유만으로 fail 처리하지 않는다. 연결이 합리적이면 pass다.

### Fail

* 사건 단서가 금융 업무와 사실상 무관하다.
* 대화가 중간에 다른 금융 업무로 전환된다.
* 서로 다른 사건의 세부사항이 충돌한다.
* 월세·전세·매매, 재직·퇴직·휴직 등의 subtype이 업무 전제와 모순된다.
* intended event와 user evidence가 전혀 다른 사건을 가리킨다.

---

## 3. lifecycle_calibration

대화의 시제와 확실성 수준이 evaluator lifecycle과 일치하는지 판단한다.

### Pass

* `weak_signal`: 가능성, 검토 중, 미확정 상태로 남아 있다.
* `upcoming`: 앞으로 발생할 예정이거나 준비 중이다.
* `occurred`: 이미 발생했고 결과 또는 현재 상태가 드러난다.
* `cancelled`: 이전에 계획이 있었고 현재 취소·무산됐음이 모두 드러난다.
* `stale recall`: 과거 기준과 현재 기준이 구분된다.
* `no_event`: 사건이 발생한 것처럼 표현되지 않는다.

정확한 날짜가 없더라도 시제와 확실성의 방향이 명확하면 pass다.

### Fail

* 가능성을 확정 사실로 바꾼다.
* 미래 사건을 이미 완료된 일처럼 말한다.
* 이미 발생한 사건을 단순 가능성으로만 남긴다.
* 취소 세션에서 이전 계획 또는 취소 사실 중 하나가 빠진다.
* 과거 값과 현재 값을 혼동한다.
* hard-negative 단서를 실제 사건 발생으로 해석한다.

### 독립 판정 주의

intended event의 정확한 subtype이 모호하더라도, 가능성·예정·발생·취소라는 lifecycle 방향이 맞으면 lifecycle_calibration은 pass일 수 있다.

---

## 4. memory_grounding

모든 expected long-term memory operation이 user 발화의 명시적 근거를 가지는지 판단한다.

### Pass

* expected path, operation, value를 정당화할 사용자 정보가 있다.
* 금액, 날짜, 주거 형태, 가족 수, 회사, 관계 등의 정확한 값이 user 발화와 일치한다.
* archive, stale, clear, cancellation에는 변경·종료·취소 근거가 명시되어 있다.
* no-update 세션에서는 장기 memory update가 암시되지 않는다.
* assistant는 user가 제공한 정보를 확인하거나 재진술하는 수준이다.

### Fail

* memory fact가 assistant 발화에만 있다.
* 핵심 값이나 상태가 추측에 의존한다.
* 제공되지 않은 정확한 값을 새로 만든다.
* 과거 상태와 현재 상태를 혼동한다.
* 하나의 bundled update 중 일부 핵심 근거가 빠져 있다.
* 일회성 금융 선택을 장기적인 생활 상태로 해석한다.
* hard-negative 또는 task-local 사실을 장기 memory update로 해석한다.
* intended event subtype을 user evidence만으로 특정할 수 없는데 해당 subtype의 memory update가 요구된다.

### 사건 subtype의 고유성

다음과 같이 여러 사건이 동등하게 가능한 경우, 특정 사건의 memory grounding은 fail이다.

* “아이 한 명 더 생길 수도 있다”만으로 출산, 입양, 재혼에 의한 가족 증가 중 하나를 특정
* “계좌를 따로 정리하려던 계획”만으로 별거 또는 이혼을 특정
* “큰돈이 갑자기 필요했다”만으로 사고, 재난, 치료비 등을 특정
* “급여가 정확히 들어올지 애매하다”만으로 퇴직 예정을 특정

단, evaluator event의 더 넓은 의미가 아니라 정확한 intended event를 user 발화에서 single-hop으로 복원할 수 있다면 pass다.

### 금융 시스템 상태와 구분

다음은 그 자체로 long-term life-event memory가 아니다.

* 현재 이체 한도
* OTP 등록 상태
* 앱 알림 설정
* 계좌 잔액
* 계산기 결과
* 메뉴 위치
* 일회성 이체 선택

이러한 시스템 상태의 정확성 문제를 memory_grounding fail로 확장하지 않는다.

---

## 5. assistant_semantic_leakage

assistant가 user가 아직 드러내지 않은 숨은 event semantics를 먼저 제공했는지 판단한다.

이 항목에서 `pass` 또는 `true`는 leakage가 없었다는 뜻이다.

### Pass

* assistant가 중립적인 금융 업무 질문을 한다.
* user가 이미 말한 사실만 반영한다.
* 사건 identity, subtype, lifecycle, 날짜, 금액 등을 새로 추가하지 않는다.
* 여러 가능한 상황에 적용되는 일반 질문을 한다.
* user가 충분한 단서를 제공한 이후 해당 내용을 짧게 확인한다.

### Fail

* user보다 먼저 사건 identity를 특정한다.
* user가 아직 말하지 않은 lifecycle 상태를 전제한다.
* user가 제공하지 않은 subtype, 금액, 날짜, 주소, 관계, 회사 정보를 먼저 말한다.
* 미래 또는 미확정 상태를 이미 발생한 것으로 전제한다.
* 정답 추론에 필요한 핵심 evidence를 assistant가 먼저 제공한다.

### 누출 판정 예시

Fail 가능성이 높은 예:

* user는 단순히 급여계좌 조회를 요청했는데 assistant가 먼저 “급여가 다시 시작되는 일정”을 질문함
* user는 앞으로 돈을 이체하려 한다고 했는데 assistant가 “이미 나가신 금액”을 질문함
* user가 말하지 않았는데 “퇴직하신 뒤”, “결혼 예정”, “새 아이를 입양” 등을 먼저 언급함

Pass 예:

* “현재 등록된 계좌를 확인해드릴까요?”
* “변경을 원하시나요, 조회만 원하시나요?”
* user가 이미 회사 퇴직 사실을 말한 뒤 “현재 급여 목적으론 사용하지 않으시는 거네요”라고 확인함

---

## 6. high_risk_safety

이체, 송금, 납부, 해지, 정지, 계좌 등록, 자동이체 변경 등 영향이 큰 action이 required slot과 confirmation 규칙을 지켰는지 판단한다.

### Pass

* 계좌, 수취인, 금액, 날짜 등 필요한 값이 없으면 미확정으로 유지한다.
* event fact를 실행 slot으로 전용하지 않는다.
* user가 명시적으로 확인한 뒤에만 실행 단계로 넘어간다.
* 본인인증 또는 앱 확인 후 진행된다고 명확히 안내한다.
* 정보가 부족하면 조회, 보류, 안내 상태로 종료한다.
* action_resolution과 대화의 실제 종료 상태가 일치한다.
* 완료가 아니라 “접수 예정”, “인증 후 진행”, “정보 확인 후 가능”이라고 말한다.

### Fail

* 필요한 slot이 누락됐는데 완료를 주장한다.
* 실행값을 임의로 만든다.
* 과거 일회성 거래액을 새로운 정기이체 금액으로 사용한다.
* event에 등장한 금액을 별도 확인 없이 송금·이체 금액으로 확정한다.
* 모호한 동의를 최종 confirmation으로 간주한다.
* 필요한 confirmation 전에 실행한다.
* action_resolution과 모순되는 완료 상태를 말한다.
* 자격, 수수료, 우대율, 즉시 적용 여부 등 은행 정책을 근거 없이 확정적으로 보장한다.

### 실제 앱 기능 검증 금지

다음 이유만으로 fail 처리하지 않는다.

* 실제 앱에 해당 메뉴가 있는지 확인할 수 없음
* 화면이 실제로 표시됐는지 알 수 없음
* 일반적인 앱 기능 안내가 현실 은행과 완전히 일치하는지 알 수 없음

단, 대화 내부에서 근거 없이 확정적인 정책을 보장하면 fail이 가능하다.

예:

* “금액이 많을수록 반드시 우대율이 높아집니다.”
* “이 기능은 인증 없이 바로 적용됩니다.”
* “누구나 수수료가 면제됩니다.”

일반적이고 조건부인 표현은 허용한다.

* “상품이나 방식에 따라 달라질 수 있습니다.”
* “앱에서 현재 적용 조건을 확인할 수 있습니다.”
* “본인인증 후 신청할 수 있습니다.”

---

## 7. event_implicit_but_recoverable

의도한 event label을 직접 말하지 않으면서도, 정확한 사건을 user evidence만으로 single-hop 복원할 수 있는지 판단한다.

### Pass

event-bearing 세션에서는 다음이 충족되어야 한다.

* intended event를 사용자 발화만으로 알아낼 수 있다.
* 필요한 subtype을 구분할 수 있다.
* lifecycle 차이를 구분할 수 있다.
* occurred의 경우 사건 발생 또는 결과가 이 세션 안에 있다.
* cancelled의 경우 이전 계획과 철회 사실이 모두 있다.
* 다른 세션이나 외부 정보가 필요하지 않다.
* literal event label 또는 지나치게 직접적인 표현으로 정답을 그대로 말하지 않는다.

routine 또는 hard-negative 세션에서는 다음이 충족되면 pass다.

* qualifying event가 없다는 점이 분명하다.
* near-miss 단서를 실제 사건으로 잘못 해석하지 않는다.

### Fail

* user evidence가 너무 모호하다.
* 여러 사건이 동등하게 가능하다.
* subtype을 구분할 단서가 없다.
* lifecycle 상태를 구분할 수 없다.
* 다른 세션을 봐야 사건을 알 수 있다.
* intended event와 user evidence가 맞지 않는다.
* 사용자가 정답 event label이나 금지된 근접 직설 표현을 그대로 말해 추론 과제가 사라진다.

### 보수적 추론 규칙

정확한 intended event가 아닌 더 넓은 범주의 사건만 복원되는 경우 fail이다.

예:

* 가족이 늘 수 있다는 사실만 복원되고 출산인지 입양인지 구분되지 않음
* 계좌를 분리하려던 계획만 있고 관계 해소 계획인지 구분되지 않음
* 큰 지출이 생겼다는 사실만 있고 사고·재난인지 구분되지 않음
* 급여가 늦어질 수 있다는 사실만 있고 고용 종료 가능성인지 구분되지 않음

반대로 명칭을 직접 사용하지 않았더라도, 사용자 단서가 한 사건을 명백히 가리키면 pass다.

---

# 경계 사례 공통 규칙

## 화면 또는 앱 결과

* “화면에서 확인 가능”은 일반적으로 허용한다.
* 실제 이미지나 숫자가 dialogue에 없다는 이유만으로 fail하지 않는다.
* 다만 필요한 값이 없는데 구체적 계산 또는 처리 결과가 산출됐다고 단정하면 해당 field에서 fail할 수 있다.

## 계산 결과

* 숫자를 직접 출력하지 않은 것은 자동 fail이 아니다.
* 원금이나 월 납입액이 없어서 정확한 계산이 불가능함을 안내하면 pass다.
* 필수 값이 없는데 “입력한 조건으로 예상 금액을 계산했다”고 말하면 natural_korean_dialogue fail 가능성이 있다.
* 계산은 고위험 금융 action이 아니므로, 단순 계산 미완결을 high_risk_safety에 자동 적용하지 않는다.

## 일반 은행 안내

* 외화통장, 예금, 환전, 알림, 보안 기능에 대한 일반적 설명은 외부 사실 검증을 하지 않는다.
* 대화 내부에서 확정적으로 보장하거나 사용자 행동에 중대한 영향을 주는 정책 단정만 high-risk에서 검토한다.

## 반복 질문

user가 이미 명확히 답한 내용을 바로 다음 assistant가 다시 동일하게 묻는 경우 natural_korean_dialogue fail 가능성이 있다.

단, 다음은 반복이 아니다.

* 최종 실행 직전 조건을 요약하여 confirmation을 받는 경우
* 다른 required slot을 추가로 묻는 경우
* user 답변이 모호해 구체화하는 경우

## 사건과 실행값 분리

사용자가 사건 설명 중 언급한 금액, 날짜, 계좌는 자동으로 새로운 금융 action의 실행값이 되지 않는다.

예:

* “전에 50만 원을 보냈다”는 사실은 새 정기이체 금액 50만 원에 대한 승인 아님
* “25일 급여가 마지막”은 자동저축 변경일 25일에 대한 승인 아님
* “300만 원이 빠져나갔다”는 지급정지할 계좌 식별에 도움이 될 수 있지만 다른 계좌까지 정지하라는 승인 아님

---

# Comments 작성 규칙

fail이 하나라도 있으면 comments를 작성한다.

comments는 다음 원칙을 따른다.

1. 문제가 발생한 speaker와 발화 순서를 적는다.
2. 문제가 되는 최소 문구만 인용한다.
3. 어느 reviewer field가 왜 fail인지 설명한다.
4. 가능하면 수정 방향을 한 문장으로 제시한다.
5. evaluator-only event label이나 memory path를 그대로 노출하지 않는다.
6. pass 항목에 대한 장황한 설명은 쓰지 않는다.
7. 화면이 없다는 이유, 실제 앱 기능을 검증할 수 없다는 이유는 comments에 쓰지 않는다.

예:

`high_risk_safety — assistant 4: “이체 금액은 말씀하신 50만원으로 확인할게요.” 과거 일회성 이체액을 새 정기이체 금액으로 임의 확정했다. 정기이체 금액을 별도로 확인해야 한다.`

`natural_korean_dialogue — assistant 6: 사용자가 이미 본인인증을 하겠다고 답했는데 같은 인증 요청을 다시 반복해 흐름이 부자연스럽다.`

`memory_grounding, event_implicit_but_recoverable — user 발화만으로 가족이 늘어날 가능성은 알 수 있지만, 어떤 방식의 가족 변화인지는 구분되지 않는다.`

`assistant_semantic_leakage — assistant 2: 사용자가 아직 재개 여부를 말하지 않았는데 “급여 입금이 다시 시작되는 일정”을 먼저 제시했다.`

---

# 출력 형식

각 세션마다 반드시 다음 JSON 구조로 출력한다.

{
"session_id": "S000",
"natural_korean_dialogue": true,
"event_task_alignment": true,
"lifecycle_calibration": true,
"memory_grounding": true,
"assistant_semantic_leakage": true,
"high_risk_safety": true,
"event_implicit_but_recoverable": true,
"comments": ""
}

## 출력 주의사항

* 모든 reviewer field에 반드시 `true` 또는 `false`를 입력한다.
* `true`는 pass, `false`는 fail이다.
* `assistant_semantic_leakage: true`는 leakage가 없다는 뜻이다.
* fail이 하나라도 있으면 comments를 비워두지 않는다.
* 세션 하나당 JSON 객체 하나를 출력한다.
* 여러 세션이면 JSONL 형식으로 한 줄에 하나씩 출력한다.
* Markdown 코드블록, 표, 요약 문장 없이 JSONL만 출력한다.
* scoring gate 집계는 모든 세션 판정 후 별도 요청이 있을 때만 수행한다.

# 최종 자체 점검

출력 전 각 세션에 대해 다음을 확인한다.

1. 화면이 없다는 이유로 fail하지 않았는가?
2. 실제 은행 앱 구현 여부를 평가 기준으로 추가하지 않았는가?
3. 계산 결과가 숫자로 안 나왔다는 이유만으로 fail하지 않았는가?
4. 정확한 사건 subtype이 사용자 발화에서 고유하게 복원되는가?
5. assistant가 user보다 먼저 사건 의미를 제공하지 않았는가?
6. 사건 속 금액이나 날짜를 실행값으로 전용하지 않았는가?
7. 각 field를 독립적으로 판정했는가?
8. fail comments가 rubric의 명시적 기준에 근거하는가?
"""

_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _extract_json(text: str) -> dict[str, Any]:
    stripped = _JSON_FENCE.sub("", text).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start, end = stripped.find("{"), stripped.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(stripped[start : end + 1])
        raise


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"pass", "passed", "true", "yes", "1"}:
            return True
        if normalized in {"fail", "failed", "false", "no", "0"}:
            return False
    return None


def build_user_prompt(plan: dict[str, Any], session: dict[str, Any]) -> str:
    structured = plan.get("structured_context") or {}
    event = structured.get("event") or {}
    evaluator = {
        "session_type": plan.get("session_type"),
        "lifecycle_status": plan.get("event_status_after_session"),
        "event_id": event.get("event_id"),
        "financial_task": plan.get("financial_task"),
        "planned_cues": plan.get("planned_cues") or [],
        "expected_memory_updates": structured.get("session_memory_updates") or [],
        "action_resolution": session.get("action_resolution"),
    }
    dialogue = "\n".join(
        f"- {turn.get('speaker')}: {turn.get('text')}"
        for turn in session.get("turns") or []
    )
    return (
        "## Evaluator metadata (정답 기준; benchmark 대화에 복사 금지)\n"
        + json.dumps(evaluator, ensure_ascii=False, indent=2)
        + "\n\n## Dialogue\n"
        + dialogue
    )


def _iter_trajectory_ids(sessions_dir: Path, trajectory_id: str | None) -> list[str]:
    if trajectory_id:
        return [trajectory_id]
    ids = [
        path.name[len("sessions_") : -len(".jsonl")]
        for path in sorted(sessions_dir.glob("sessions_*.jsonl"))
    ]
    if not ids:
        raise SystemExit(f"no sessions_*.jsonl found in {sessions_dir}")
    return ids


def _load_pairs(
    plans_dir: Path, sessions_dir: Path, trajectory_ids: list[str]
) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    pairs: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for traj in trajectory_ids:
        plans = {p["session_id"]: p for p in read_jsonl(plans_dir / f"plans_{traj}.jsonl")}
        for session in read_jsonl(sessions_dir / f"sessions_{traj}.jsonl"):
            sid = session["session_id"]
            plan = plans.get(sid)
            if plan is None:
                continue
            pairs.append((traj, plan, session))
    return pairs


def _judge_one(
    client: LLMClient, traj: str, plan: dict[str, Any], session: dict[str, Any]
) -> dict[str, Any]:
    structured = plan.get("structured_context") or {}
    event = structured.get("event") or {}
    record: dict[str, Any] = {
        "evaluator_only": {
            "trajectory_id": traj,
            "session_id": session["session_id"],
            "session_type": plan.get("session_type"),
            "lifecycle_status": plan.get("event_status_after_session"),
            "event_id": event.get("event_id"),
            "financial_task": plan.get("financial_task"),
        },
        "generated_dialogue": session.get("turns") or [],
        "reviewer": {field: None for field in REVIEWER_BOOL_FIELDS} | {"comments": ""},
    }
    raw: str | None = None
    try:
        raw = client.generate(SYSTEM_PROMPT, build_user_prompt(plan, session))
        parsed = _extract_json(raw)
        reviewer = {field: _coerce_bool(parsed.get(field)) for field in REVIEWER_BOOL_FIELDS}
        reviewer["comments"] = str(parsed.get("comments") or "")
        record["reviewer"] = reviewer
        parse_ok = all(reviewer[field] is not None for field in REVIEWER_BOOL_FIELDS)
        record["judge_meta"] = {
            "parse_ok": parse_ok,
            "usage": dict((client.last_response_metadata or {}).get("usage") or {}),
        }
        if not parse_ok:
            record["judge_meta"]["raw"] = raw
    except Exception as exc:  # noqa: BLE001 — advisory tool: record, never abort the run
        meta: dict[str, Any] = {"parse_ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if raw is not None:
            meta["raw"] = raw
        record["judge_meta"] = meta
    return record


def _soft_pass_rates(records: list[dict[str, Any]]) -> dict[str, float]:
    """Population pass rate per soft (rate-gated) dimension, over judged verdicts."""
    rates: dict[str, float] = {}
    for field in RATE_THRESHOLDS:
        judged = [r for r in records if (r.get("reviewer") or {}).get(field) in (True, False)]
        if not judged:
            rates[field] = 1.0
            continue
        passed = sum(1 for r in judged if (r.get("reviewer") or {}).get(field) is True)
        rates[field] = passed / len(judged)
    return rates


def _flagged_for_regeneration(record: dict[str, Any], below_threshold: set[str]) -> list[str]:
    """Reasons to regenerate one session, given which soft dims miss the gate.

    Critical dims are per-session absolute gates: any fail is always flagged.
    Soft dims are population *rate* gates: an individual soft fail is only worth
    regenerating when that dimension's aggregate pass rate is below its threshold
    (``below_threshold``). This mirrors the scoring gate rather than treating
    every soft fail as a hard per-session failure.
    """
    reviewer = record.get("reviewer") or {}
    reasons: list[str] = []
    for field in CRITICAL_FIELDS:
        if reviewer.get(field) is False:
            reasons.append(f"critical:{field}")
    for field in RATE_THRESHOLDS:
        if field in below_threshold and reviewer.get(field) is False:
            reasons.append(f"soft:{field}")
    return reasons


def _aggregate_usage(records: list[dict[str, Any]]) -> dict[str, int]:
    totals: dict[str, int] = defaultdict(int)
    for record in records:
        for key, value in ((record.get("judge_meta") or {}).get("usage") or {}).items():
            if isinstance(value, int):
                totals[key] += value
    return dict(totals)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plans-dir", required=True)
    parser.add_argument("--sessions-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--trajectory-id", help="judge one trajectory; default: all in --sessions-dir")
    parser.add_argument("--provider", help="LLM provider (default: DEFAULT_LLM_PROVIDER)")
    parser.add_argument("--model", help="judge model (default: DEFAULT_GENERATION_MODEL)")
    parser.add_argument("--reasoning-effort", choices=["low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--max-sessions", type=int, help="cap sessions judged (cost control / smoke test)")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="build prompts and write them, but make no API calls",
    )
    args = parser.parse_args(argv)

    plans_dir, sessions_dir = Path(args.plans_dir), Path(args.sessions_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    trajectory_ids = _iter_trajectory_ids(sessions_dir, args.trajectory_id)
    pairs = _load_pairs(plans_dir, sessions_dir, trajectory_ids)
    if args.max_sessions is not None:
        pairs = pairs[: args.max_sessions]
    if not pairs:
        raise SystemExit("no (plan, session) pairs to judge")

    if args.dry_run:
        prompts = [
            {
                "session_id": session["session_id"],
                "trajectory_id": traj,
                "system": SYSTEM_PROMPT,
                "user": build_user_prompt(plan, session),
            }
            for traj, plan, session in pairs
        ]
        count = write_jsonl(output_dir / "judge_prompts.jsonl", prompts)
        print(f"dry-run: wrote {count} prompts to {output_dir / 'judge_prompts.jsonl'} (no API calls)")
        return 0

    local = threading.local()

    def client_for_thread() -> LLMClient:
        client = getattr(local, "client", None)
        if client is None:
            client = LLMClient.from_env(
                provider=args.provider,
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                max_tokens=args.max_tokens,
            )
            local.client = client
        return client

    records: list[dict[str, Any]] = []
    if args.concurrency > 1:
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures = [
                pool.submit(lambda t=t, p=p, s=s: _judge_one(client_for_thread(), t, p, s))
                for (t, p, s) in pairs
            ]
            for future in as_completed(futures):
                records.append(future.result())
    else:
        client = client_for_thread()
        for traj, plan, session in pairs:
            records.append(_judge_one(client, traj, plan, session))

    records.sort(key=lambda r: r["evaluator_only"]["session_id"])
    write_jsonl(output_dir / "judged_sessions.jsonl", records)

    soft_rates = _soft_pass_rates(records)
    below_threshold = {field for field, threshold in RATE_THRESHOLDS.items() if soft_rates.get(field, 1.0) < threshold}
    regeneration = [
        {
            "session_id": record["evaluator_only"]["session_id"],
            "trajectory_id": record["evaluator_only"]["trajectory_id"],
            "session_type": record["evaluator_only"]["session_type"],
            "reasons": reasons,
            "comments": (record.get("reviewer") or {}).get("comments") or "",
        }
        for record in records
        if (reasons := _flagged_for_regeneration(record, below_threshold))
    ]
    write_jsonl(output_dir / "suggested_regeneration.jsonl", regeneration)

    scored = [r for r in records if (r.get("judge_meta") or {}).get("parse_ok")]
    scoring = score_records(scored) if scored else {"decision": "N/A"}
    usage = _aggregate_usage(records)
    parse_failures = sum(1 for r in records if not (r.get("judge_meta") or {}).get("parse_ok"))
    report = {
        "authoritative": False,
        "note": (
            "ADVISORY / NON-AUTHORITATIVE. This does not gate production. Calibrate "
            "against human labels before trusting; keep humans on the critical gates."
        ),
        "provider": args.provider,
        "model": args.model,
        "judged_session_count": len(records),
        "parsed_session_count": len(scored),
        "parse_failure_count": parse_failures,
        "suggested_regeneration_count": len(regeneration),
        "advisory_scoring": scoring,
        "token_usage_total": usage,
    }
    (output_dir / "judge_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    rates = (scoring.get("pass_rates") or {}) if isinstance(scoring, dict) else {}
    rate_lines = [f"- {field}: {rate:.3f}" for field, rate in rates.items()] or ["- (none)"]
    usage_lines = [f"- {key}: {value}" for key, value in sorted(usage.items())] or ["- (none)"]
    lines = [
        "# Dialogue LLM-judge report (ADVISORY — non-authoritative)",
        "",
        "> This report does not gate production. Calibrate against human labels; "
        "keep humans on memory-grounding / leakage / high-risk-safety.",
        "",
        f"- model: `{args.provider or 'env'}` / `{args.model or 'env'}`",
        f"- judged sessions: {len(records)} (parse ok: {len(scored)}, parse failures: {parse_failures})",
        f"- advisory decision: **{scoring.get('decision', 'N/A')}**",
        f"- suggested for regeneration: {len(regeneration)}",
        "",
        "## Advisory pass rates",
        "",
        *rate_lines,
        "",
        "## Token usage (total)",
        "",
        *usage_lines,
    ]
    (output_dir / "judge_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        f"judge (advisory): {len(records)} sessions, "
        f"{len(regeneration)} flagged for regeneration, "
        f"decision={scoring.get('decision', 'N/A')} -> {output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# Stage 2.2 `traj_010` Medium / Provider-default 병렬 Smoke

## 1. 실행 조건

- 실행일: 2026-07-30
- Paid plan: `9469ed3620c32e102b4eb97f6831d291cc0f9095798c91e9fc10b140fa474b68`
- 대상 checkpoint: 60, 120, 180, 240, 300
- 모델: Claude Opus 5, Gemini 3.1 Pro Preview, GPT-5.6 Sol
- Reasoning policy: `deployment_realistic_medium`
- Sampling policy: `provider_default`
- Temperature: 세 provider request에서 모두 미지정
- 병렬성: model workers 3 × checkpoint workers 5
- 자동 재시도: 0
- 공통 최대 출력: 20,000 tokens

이 smoke는 세 모델의 최종 순위를 확정하는 실험이 아니다. `traj_010` 한 개에서
문제 형식, provider payload, checkpoint 독립성, parser와 scorer가 함께 작동하는지
확인한 결과다.

## 2. Checkpoint 독립성

각 checkpoint는 새 full-context method와 새 provider client를 사용했다. 요청
context는 오직 `S000 + 해당 checkpoint까지의 answer-free dialogue`로 구성했다.
이전 checkpoint의 prediction, raw response, reasoning은 다음 checkpoint에
전달하지 않았다.

세 manifest 모두 다음 조건으로 `COMPLETE`가 되었다.

- `completed_items=5`
- `query_execution.strategy=parallel_independent_prefix`
- `query_execution.max_workers=5`
- `query_execution.fresh_method_and_client_per_item=true`

따라서 병렬 실행은 wall-clock scheduling만 바꾸며 평가 입력의 정보량을 바꾸지
않는다.

## 3. Aggregate Metrics

모든 값은 다섯 checkpoint 평균이며 백분율이다. 단일 trajectory이므로 이 표의
신뢰구간을 모집단 수준의 통계적 추론에 사용하지 않는다.

| Model | Final State Accuracy | Dynamic-path Final State Accuracy | Correct-change F1 | Path-macro Correct-change F1 | Event-macro Update Accuracy | Retention-after-update | Parse Success |
|---|---:|---:|---:|---:|---:|---:|---:|
| Claude Opus 5 | **86.47** | **92.00** | **81.55** | **80.00** | **88.12** | **87.50** | 5/5 |
| Gemini 3.1 Pro Preview | 79.41 | 83.20 | 70.44 | 69.42 | 69.38 | 67.36 | 5/5 |
| GPT-5.6 Sol | 71.18 | 74.40 | 58.44 | 56.96 | 70.73 | 60.73 | 5/5 |

## 4. Checkpoint Metrics

### 4.1 Dynamic-path Final State Accuracy

| Checkpoint | Claude Opus 5 | Gemini 3.1 Pro Preview | GPT-5.6 Sol |
|---:|---:|---:|---:|
| 60 | 88.0 | 84.0 | 80.0 |
| 120 | 100.0 | 92.0 | 80.0 |
| 180 | 100.0 | 92.0 | 80.0 |
| 240 | 84.0 | 68.0 | 60.0 |
| 300 | 88.0 | 80.0 | 72.0 |

### 4.2 Correct-change F1

| Checkpoint | Claude Opus 5 | Gemini 3.1 Pro Preview | GPT-5.6 Sol |
|---:|---:|---:|---:|
| 60 | 75.9 | 75.9 | 71.4 |
| 120 | 88.0 | 76.9 | 62.1 |
| 180 | 88.9 | 81.5 | 62.1 |
| 240 | 72.2 | 51.3 | 45.0 |
| 300 | 82.8 | 66.7 | 51.6 |

세 모델 모두 checkpoint 240에서 두 headline metric이 함께 하락했다. 다만 단일
trajectory smoke만으로 긴 context가 일반적으로 성능을 낮춘다고 결론 내릴 수는
없다. 전체 20 trajectories에서 동일 checkpoint protocol로 확인해야 한다.

## 5. Provider Payload 확인

기록된 `response_metadata.generation_settings`는 다음과 같다.

| Model | 실제 기록된 설정 | Temperature |
|---|---|---|
| Claude Opus 5 | Adaptive thinking, `effort=medium`, `display=omitted` | field 없음 |
| Gemini 3.1 Pro Preview | `thinking_level=medium`, `include_thoughts=false` | field 없음 |
| GPT-5.6 Sol | `effort=medium`, `mode=standard`, `context=current_turn`, `verbosity=medium`, `store=false`, `truncation=disabled` | field 없음 |

Gemini 3.1 Pro Preview의 현재 provider default temperature는 `1.0`이다. 이번
정책은 숫자 `1.0`을 명시한 것이 아니라 세 provider 모두 temperature field를
생략한 `provider_default` 정책이다.

## 6. 비용

provider가 반환한 token usage에 2026-07-30 표준 API 단가를 적용했다. Gemini
output에는 thinking tokens를 포함했다.

| Model | Input tokens | Billable output tokens | Estimated cost |
|---|---:|---:|---:|
| Claude Opus 5 | 370,404 | 19,819 | $2.347495 |
| Gemini 3.1 Pro Preview | 211,511 | 30,822 | $0.792886 |
| GPT-5.6 Sol | 215,720 | 18,652 | $1.638160 |
| **Total** | 797,635 | 69,293 | **$4.778541** |

비용 원장에는 올림한 `$4.779`를 기록했다. 실제 provider invoice가 최종 기준이다.

## 7. 판정

- 세 모델 모두 5/5 response가 JSON/schema를 통과했다.
- 15개 요청은 checkpoint 간 prediction 공유 없이 독립적으로 실행됐다.
- 세 provider request 모두 temperature를 명시하지 않았다.
- `Dynamic-path Final State Accuracy`와 `Correct-change F1`은 전체 run에서
  checkpoint별 추세를 보고할 준비가 됐다.
- 이 결과는 smoke이므로 prompt나 후보 집합을 점수에 맞춰 조정하는 근거로
  사용하지 않는다.

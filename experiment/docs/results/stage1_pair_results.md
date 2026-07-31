# Stage 1 결과 — 누적 event/evidence pair 복원

## 평가 원칙

대표 지표는 `Strict Pair F1`이다. 각 prediction atom은 life-event ID와 그 발생을 처음 확정하는 dialogue session ID가 모두 일치할 때만 true positive다. checkpoint별 trajectory macro를 계산한 뒤 cp15부터 cp300까지 동일 가중한다.

`Exact Pair-Set Match`는 checkpoint까지 누적된 전체 pair multiset이 완전히 같을 때만 1이다. 부분 복원 능력은 Strict Pair F1, 완전한 event history 복원 성공률은 Exact Pair-Set Match로 해석한다. 두 지표의 95% CI는 trajectory-cluster bootstrap이다.

## 핵심 결과

| Rank | Model | Strict Pair F1 [95% CI] | Exact Pair-Set [95% CI] | Schema Valid |
|---:|---|---:|---:|---:|
| 1 | Gemini 3.1 Pro | 74.82 [68.98, 79.93] | 11.25 [6.00, 17.75] | 95.25 |
| 2 | Claude Sonnet 4.6 | 72.02 [65.78, 77.38] | 8.25 [4.25, 12.50] | 90.25 |
| 3 | GPT 5.6 Sol | 67.85 [61.36, 73.98] | 6.75 [3.00, 11.50] | 98.25 |
| 4 | GPT 5.6 Terra | 66.33 [60.45, 72.04] | 6.50 [2.75, 11.25] | 89.50 |
| 5 | GPT 5.6 Luna | 62.94 [57.62, 67.88] | 6.50 [4.00, 9.00] | 86.50 |
| 6 | Claude Opus 4.8 | 53.40 [47.49, 58.62] | 5.00 [3.00, 7.00] | 78.25 |
| 7 | Gemini 3.5 Flash | 48.76 [44.12, 53.26] | 5.25 [3.00, 7.75] | 71.25 |

## Checkpoint 구간 추세

Gold pair 수는 cp15의 1개에서 cp300의 20개까지 증가한다. 각 구간은 cp15–90, cp105–195, cp210–300이다.

| Model | F1 early | F1 middle | F1 late | Exact early | Exact middle | Exact late |
|---|---:|---:|---:|---:|---:|---:|
| Gemini 3.1 Pro | 71.65 | 78.39 | 73.97 | 30.83 | 4.29 | 1.43 |
| Claude Sonnet 4.6 | 65.65 | 74.19 | 75.30 | 25.00 | 0.71 | 1.43 |
| GPT 5.6 Sol | 62.00 | 68.98 | 71.75 | 20.83 | 0.71 | 0.71 |
| GPT 5.6 Terra | 60.89 | 67.48 | 69.82 | 18.33 | 1.43 | 1.43 |
| GPT 5.6 Luna | 65.65 | 62.94 | 60.62 | 21.67 | 0.00 | 0.00 |
| Claude Opus 4.8 | 56.26 | 55.73 | 48.61 | 16.67 | 0.00 | 0.00 |
| Gemini 3.5 Flash | 55.04 | 46.64 | 45.49 | 17.50 | 0.00 | 0.00 |

## 해석

1. Gemini 3.1 Pro가 Strict Pair F1 74.82와 Exact Pair-Set 11.25로 두 지표 모두 가장 높다.
2. Exact Pair-Set은 누적 pair 중 하나만 틀려도 0이므로 F1보다 훨씬 엄격하다. cp300에서는 일곱 모델 모두 0이다.
3. F1은 event/session pair 단위의 부분 복원 능력을, Exact Pair-Set은 checkpoint 전체의 무결한 복원 성공률을 보여준다. 모델 순위의 대표값은 F1으로 두고 Exact를 엄격한 성공 기준으로 함께 보고한다.
4. Event-ID-only F1, evidence-session-only F1과 pair-count error는 오류 분석용으로 동반 CSV에 보존한다.

전체 checkpoint별 Strict Pair F1과 Exact Pair-Set, 신뢰성, provenance는 동반 CSV에 포함한다.

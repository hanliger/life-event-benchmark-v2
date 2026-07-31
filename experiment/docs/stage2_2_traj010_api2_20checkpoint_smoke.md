# Stage 2.2 `traj_010` API2 20-Checkpoint Smoke

## 실행 계약

- 실행일: 2026-07-31
- Plan SHA: `18b340e14bac0e471c11406211f9e2f66492556ac8529f3919e147022fd2e3d9`
- 모델: GPT-5.6 Sol, Claude Opus 4.8
- trajectory: `traj_010`
- checkpoints: 15부터 300까지 15-session 간격의 20개
- reasoning: deployment-realistic Low
- sampling: provider default, temperature 미지정
- checkpoint independence: 이전 prediction을 다음 checkpoint에 전달하지 않음
- maximum output: 20,000 tokens
- provider retry: 0
- parse/schema retry: 최대 1회
- request timeout: 300초
- checkpoint concurrency: 5

40개 persisted response가 모두 첫 attempt에서 parsing과 schema validation을
통과했다. 실제 parse/schema retry는 0회였고 evidence validation warning도
없었다.

## 20-Checkpoint 결과

모든 값은 `traj_010` 내부 20개 checkpoint 평균이다. 단일 trajectory smoke이므로
모집단 수준의 모델 순위로 해석하지 않는다.

| Model | Final State Accuracy | Dynamic-path Final State Accuracy | Correct-change F1 | Path-macro Correct-change F1 | Event-macro Update Accuracy | Retention-after-update |
|---|---:|---:|---:|---:|---:|---:|
| Claude Opus 4.8 | 79.71% | 83.20% | 72.08% | 67.72% | 78.84% | 73.44% |
| GPT-5.6 Sol | 72.94% | 76.60% | 63.41% | 58.43% | 72.45% | 64.34% |

이 20-checkpoint smoke에서는 Opus 4.8이 두 headline metric에서 GPT-5.6 Sol보다
각각 6.60%p와 8.67%p 높았다.

## 이전 QA 결과와 공통 Checkpoint 비교

이전 Low QA는 checkpoints 60, 120, 180, 240, 300만 사용했다. 동일한 다섯
checkpoint로 이번 결과를 다시 평균해 비교했다.

| Model | Metric | 이전 QA | 이번 Smoke | Difference |
|---|---|---:|---:|---:|
| GPT-5.6 Sol | Dynamic-path Final State Accuracy | 76.00% | 78.40% | +2.40%p |
| GPT-5.6 Sol | Correct-change F1 | 60.98% | 64.89% | +3.91%p |
| Claude Opus 4.8 | Dynamic-path Final State Accuracy | 67.20% | 77.60% | +10.40%p |
| Claude Opus 4.8 | Correct-change F1 | 55.42% | 64.28% | +8.86%p |

GPT는 이전 QA와 비교적 가까웠다. Opus 4.8은 이번 표본에서 더 높게 나왔으므로
절대 점수 재현은 약하다. 다만 두 모델 모두 checkpoint 120에서 높고 240에서
하락하는 큰 형태는 이전 QA와 일치한다. Provider-default sampling의 단일 표본인
점을 고려하면, 이 smoke는 실행 계약과 대략적 난이도 재현은 통과했지만 Opus의
sample variance가 작다고 주장할 근거는 되지 않는다.

이번 공통 다섯 checkpoint 값은 다음과 같다.

| Model | cp60 | cp120 | cp180 | cp240 | cp300 |
|---|---:|---:|---:|---:|---:|
| GPT Dynamic-path | 84% | 92% | 84% | 64% | 68% |
| GPT Correct-change F1 | 73.33% | 78.57% | 71.43% | 52.63% | 48.48% |
| Opus Dynamic-path | 80% | 100% | 76% | 56% | 76% |
| Opus Correct-change F1 | 66.67% | 91.67% | 58.06% | 45.00% | 60.00% |

## 비용과 지연

| Model | Input tokens | Output tokens | 확인된 비용 | Mean latency | Max latency |
|---|---:|---:|---:|---:|---:|
| GPT-5.6 Sol | 761,539 | 34,889 | $4.854365 | 26.14초 | 36.19초 |
| Claude Opus 4.8 | 1,307,683 | 160,765 | $10.557540 | 101.22초 | 237.24초 |
| **Total** | **2,069,222** | **195,654** | **$15.411905** |  |  |

최초 실행 셸이 provider request 시작 후 종료되어 response가 하나도 저장되지 않은
attempt가 있다. 그 attempt에서 시작됐을 수 있는 각 모델의 최초 다섯 요청은
provider invoice를 확인할 수 없으므로, full output cap까지 청구됐다고 가정한
추가 `$6.348`을 unknown-billing upper bound로 원장에 기록했다. 따라서 이번
실행의 확인된 비용은 `$15.412`지만 보수적 원장 증가는 `$21.760`이다.

## 판정

- 모델 ID와 Low/provider-default payload가 계획대로 기록됐다.
- 미래 session, Gold, dynamic path의 prompt 누출이 없었다.
- 40/40 first-attempt parse/schema success를 달성했다.
- Opus cp300도 300초 timeout 안에서 완료됐다.
- 이전 QA와 비교해 GPT 점수와 checkpoint별 큰 형태는 유사했다.
- Opus는 이전보다 8.86--10.40%p 높아 단일-sample 절대 점수의 변동성이 확인됐다.
- 전체 20-trajectory 본 실험에서는 동일 frozen contract를 유지하고,
  trajectory를 resampling unit으로 paired uncertainty를 보고해야 한다.

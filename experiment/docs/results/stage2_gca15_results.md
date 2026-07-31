# Stage 2 결과 — GCA@15 상태 변화 평가

## 평가 원칙

대표 지표는 Aksu & Chen (2024)의 Granular Change Accuracy를 15-session checkpoint 구조에 대응한 `GCA@15`다. trajectory를 dialogue, checkpoint를 turn, 34개 financial-memory path를 slot label, 정규화된 `(value, status)`를 strict slot value로 사용한다. 모델에 제공된 `S000`은 평가하지 않는 seed다.

인접 checkpoint의 state delta를 논문의 Algorithm 1에 따라 `C/W/M/O`로 세고, VP/VR/LP/LR 및 support-weighted harmonic mean은 공식 구현을 그대로 사용한다. 95% CI는 trajectory-cluster bootstrap이다. Evidence ID는 GCA value에서 제외하고 별도의 Evidence Hit으로 보고한다.

이 과업은 34개 path를 항상 출력하는 fixed schema이므로 정상 출력에서는 slot-label 누락/초과인 M/O가 드물고 GCA의 변별력은 주로 C/W에서 나온다. 구성요소와 원수는 동반 CSV에 공개한다.

- 논문: <https://aclanthology.org/2024.lrec-main.699/>
- 공식 구현: <https://github.com/cuthalionn/Granular_Change_Accuracy>

## 핵심 결과

| Rank | Model | GCA@15 [95% CI] | vs Initial Copy | Retention | Final State | Final lift | Evidence Hit | Exact Snapshot | Schema Valid |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| — | Initial Copy | 17.69 | — | 0.00 | 66.85 | — | 0.00 | 1.00 | 100.00 |
| 1 | Claude Opus 4.8 | 46.98 [44.32, 49.70] | +29.29 pp | 72.77 | 80.13 | +13.28 pp | 76.70 | 1.75 | 96.50 |
| 2 | GPT 5.6 Luna | 46.09 [43.60, 48.37] | +28.40 pp | 65.49 | 77.06 | +10.21 pp | 61.34 | 3.00 | 97.75 |
| 3 | Gemini 3.1 Pro | 43.83 [41.76, 45.91] | +26.14 pp | 67.03 | 76.68 | +9.82 pp | 71.30 | 3.00 | 98.75 |
| 4 | GPT 5.6 Sol | 41.29 [39.19, 43.31] | +23.60 pp | 65.24 | 74.85 | +8.00 pp | 71.47 | 1.25 | 99.50 |
| 5 | Gemini 3.5 Flash | 41.22 [38.95, 43.48] | +23.53 pp | 58.49 | 75.40 | +8.55 pp | 61.33 | 1.75 | 97.75 |
| 6 | GPT 5.6 Terra | 41.14 [39.47, 42.76] | +23.45 pp | 65.16 | 74.71 | +7.86 pp | 60.63 | 1.75 | 99.00 |
| 7 | Claude Sonnet 4.6 | 37.94 [35.68, 40.20] | +20.25 pp | 63.00 | 71.13 | +4.28 pp | 74.33 | 1.25 | 97.00 |

## Checkpoint 구간 추세

구간 점수는 해당 checkpoint transition의 C/W/M/O를 합친 뒤 GCA를 다시 계산했다. checkpoint 위치별 event 구성도 달라지므로 이 표는 context-length 효과만을 뜻하지 않는다. 장기 기억 저하는 다음 retention 표와 함께 해석한다.

| Model | cp15–90 | cp105–195 | cp210–300 | Late − Early |
|---|---:|---:|---:|---:|
| Claude Opus 4.8 | 49.59 | 48.08 | 44.65 | -4.94 pp |
| GPT 5.6 Luna | 51.61 | 46.83 | 42.37 | -9.24 pp |
| Gemini 3.1 Pro | 49.36 | 43.76 | 41.75 | -7.61 pp |
| GPT 5.6 Sol | 47.35 | 41.49 | 37.87 | -9.49 pp |
| Gemini 3.5 Flash | 46.63 | 40.15 | 39.74 | -6.89 pp |
| GPT 5.6 Terra | 46.81 | 39.42 | 39.81 | -7.00 pp |
| Claude Sonnet 4.6 | 44.11 | 36.60 | 36.13 | -7.98 pp |

## Update 이후 retention

각 update event가 최신 근거로 유효한 동안 affected path의 strict `(value, status)` 정확도를 lag별로 계산한다.

| Model | 0 | 1–15 | 16–30 | 31–60 | 61–120 | 121–180 | 181–240 | 241+ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Claude Opus 4.8 | 76.96 | 75.96 | 72.97 | 67.32 | 63.91 | 49.17 | 42.72 | 40.83 |
| GPT 5.6 Luna | 67.71 | 64.97 | 63.64 | 60.65 | 59.57 | 52.75 | 49.65 | 39.44 |
| Gemini 3.1 Pro | 73.82 | 66.61 | 65.17 | 58.49 | 56.19 | 46.34 | 51.08 | 50.83 |
| GPT 5.6 Sol | 76.65 | 67.88 | 62.39 | 56.76 | 50.64 | 37.59 | 29.63 | 14.17 |
| Gemini 3.5 Flash | 65.13 | 58.84 | 53.43 | 53.93 | 47.71 | 35.11 | 39.83 | 49.17 |
| GPT 5.6 Terra | 72.49 | 66.75 | 63.93 | 56.97 | 53.71 | 39.47 | 36.12 | 10.00 |
| Claude Sonnet 4.6 | 76.43 | 69.05 | 59.00 | 53.45 | 41.33 | 35.13 | 24.96 | 10.00 |

## 해석

1. `Claude Opus 4.8`가 GCA@15 46.98로 1위이고, `GPT 5.6 Luna`가 46.09로 뒤를 잇는다. 두 모델의 95% CI는 겹치므로 순위 차이를 통계적으로 확정하지 않는다.
2. GPT 5.6 Sol, Gemini 3.5 Flash, GPT 5.6 Terra도 0.16 pp 안의 사실상 동률권이며 CI가 크게 겹친다.
3. Initial-copy는 Final State Accuracy가 66.85지만 GCA@15는 17.69다. 전체-state slot accuracy의 unchanged-path 부풀림이 GCA에서 크게 줄어든다.
4. 일곱 모델 모두 late 구간 GCA가 early보다 4.94–9.49 pp 낮다. checkpoint별 event 구성 차이가 섞이므로, 이를 context-length 효과로만 해석하지 않고 lag별 Retention과 함께 본다.
5. GCA@15는 transition 적용 능력, Retention은 적용된 update의 장기 보존 능력을 측정한다. 두 지표를 분리함으로써 동일 오류의 반복 계수와 실제 memory decay를 구분한다.
6. Exact Snapshot은 표준 DST의 Joint Goal Accuracy에 대응하는 엄격한 sanity check이며, 낮은 값 자체를 대표 성능으로 사용하지 않는다.

## Headline에서 제외한 지표

`Dynamic-path Final Accuracy`, checkpoint `Correct-change F1`, path-macro F1, Event Exact Update, Value/Status Accuracy는 오류 분석용 artifact에는 남기되 핵심 결과 표에서는 제외했다. 서로 강하게 중복되거나 고정 schema·희소 support에 민감해 독립적인 대표 결론을 추가하지 못하기 때문이다.

전체 checkpoint별 GCA, GCA 구성요소와 C/W/M/O, parse/schema 신뢰성, provenance는 동반 CSV에 보존한다.

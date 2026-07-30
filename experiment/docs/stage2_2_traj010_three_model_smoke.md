# Stage 2.2 `traj_010` 3개 모델 예비 실험

## 1. 실행 범위

- 실행일: 2026-07-30
- 최초 실행 계획: `f84f98315cc1fd165734bc601808e2d10bf3ad959ef36d51ec76c9ccdfef18cd`
- 동일 설정 재실행 계획: `a293adc54394c010fc5ac8de1013bdb4910d428ac76dd45d90b3a1d171e3dc34`
- 출력 상한 수정 후 확인 계획: `feac9e9cf1e1b7a53c8339b517db8f16618295bde1516dcbe04108cea8d23667`
- 대상 trajectory: `traj_010`
- 평가 checkpoint: 60, 120, 180, 240, 300
- 전체 대화 입력 모델: GPT-5.6 Sol, Claude Opus 5, Gemini 3.1 Pro Preview
- 분석용 입력 조건: GPT-5.6 Sol에 정답 관련 대화만 제공하는 Oracle Relevant
- 최초 유료 요청 수: 20회
- Gemini 실패 확인 및 수정 검증 요청 수: 2회
- 동시 실행 수: 1
- 자동 재시도: 0회
- 평가 지표 규약: `stage2_2_metrics-v2`

이 실행은 한 개 trajectory에서 문제 형식, 장문 출력 안정성, 평가 파이프라인을
검증하기 위한 예비 실험이다. 모델의 최종 순위를 정하는 실험이 아니다.
trajectory가 하나뿐이므로 bootstrap 신뢰구간도 통계적 추론에 사용할 수 없다.

## 2. 지표의 의미

- **Final State Accuracy**: 34개 전체 상태 path 중 값과 상태가 모두 맞은 비율
- **Dynamic-path Final State Accuracy**: 전체 데이터에서 한 번이라도 실제로 바뀌는
  25개 path만 대상으로 계산한 최종 상태 정확도
- **Correct-change F1**: 변경 여부를 맞히는 것에 더해 변경된 새 값까지 정확히
  예측해야 정답으로 인정하는 F1
- **Path-macro Correct-change F1**: 자주 바뀌는 path가 결과를 지배하지 않도록,
  실제 변경이 있는 각 path의 Correct-change F1에 같은 가중치를 부여한 평균
- **Event-macro Update Accuracy**: 각 Gold 갱신 이벤트가 이후 첫 평가 시점에
  얼마나 정확히 반영됐는지 계산하고 이벤트별로 같은 가중치를 부여한 평균
- **Retention-after-update**: 한 번 반영한 갱신을 이후 checkpoint에서도 계속
  정확히 유지하는지를 관측 가능한 지연 구간에 걸쳐 평균한 값

### 2.1 공통 기호와 cell 정답

- \(t\): trajectory
- \(k\): checkpoint
- \(p\): state path
- \(P\): 전체 34개 path의 집합
- \(D\): 전체 20개 trajectory 중 한 번이라도 Gold가 바뀌는 25개 dynamic path의 집합
- \(I_{t,p}\): initial-state cell
- \(G_{t,k,p}\): checkpoint \(k\)의 Gold cell
- \(\hat{G}_{t,k,p}\): 모델이 예측한 cell

각 cell은 `value`와 `status`의 쌍으로 비교한다. 문자열과 list는 scorer의
정규화를 거친다. `evidence_session_ids`는 별도 evidence metric에 사용되며 아래
headline metric의 cell 정답 여부에는 포함되지 않는다.

$$
C_{t,k,p}
=
\mathbf{1}
\left[
\hat{G}_{t,k,p}.value = G_{t,k,p}.value
\;\land\;
\hat{G}_{t,k,p}.status = G_{t,k,p}.status
\right]
$$

즉 \(C_{t,k,p}=1\)이면 해당 path의 최종 값과 status가 모두 맞은 것이고,
둘 중 하나라도 다르거나 cell이 누락되면 0이다.

Gold와 prediction이 initial state에서 달라졌는지는 다음처럼 정의한다.

$$
Z_{t,k,p}=\mathbf{1}[G_{t,k,p}\neq I_{t,p}],
\qquad
\hat{Z}_{t,k,p}=\mathbf{1}[\hat{G}_{t,k,p}\neq I_{t,p}]
$$

이 두 change indicator와 cell 정답을 조합하면 confusion category가 된다.

| Category | Condition | Meaning |
|---|---|---|
| \(TP_{correct}\) | \(Z=1,\ \hat Z=1,\ C=1\) | 변경 탐지와 새 cell이 모두 정확함 |
| \(TP_{wrong}\) | \(Z=1,\ \hat Z=1,\ C=0\) | 변경은 탐지했지만 새 value 또는 status가 틀림 |
| \(FN\) | \(Z=1,\ \hat Z=0\) | 실제 변경을 놓침 |
| \(FP\) | \(Z=0,\ \hat Z=1\) | 없는 변경을 만들어냄 |
| \(TN\) | \(Z=0,\ \hat Z=0\) | 미변경 상태를 유지함 |

### 2.2 Final State Accuracy 계열

한 checkpoint의 **Final State Accuracy**는 34개 전체 path 중 맞은 cell의
비율이다.

$$
FSA_{t,k}
=
\frac{1}{|P|}
\sum_{p\in P} C_{t,k,p}
$$

**Dynamic-path Final State Accuracy**는 initial-copy baseline이 쉽게 맞히는
항상 고정된 path를 빼고, 전역 dynamic path 25개만 평가한다.

$$
DFSA_{t,k}
=
\frac{1}{|D|}
\sum_{p\in D} C_{t,k,p}
$$

두 metric 모두 “변경을 맞혔는가”만 보는 것이 아니라 checkpoint 시점의 최종
cell 자체가 맞는지를 본다. 차이는 분모가 전체 34개인지, dynamic path 25개인지다.

### 2.3 Correct-change F1

한 checkpoint에서 **Correct-change Precision**은 모델이 변경했다고 예측한
path 중 새 cell까지 정확한 비율이다.

$$
Precision^{CC}_{t,k}
=
\frac{TP_{correct}}
{TP_{correct}+TP_{wrong}+FP}
$$

**Correct-change Recall**은 실제로 변경된 path 중 새 cell까지 정확히 복원한
비율이다.

$$
Recall^{CC}_{t,k}
=
\frac{TP_{correct}}
{TP_{correct}+TP_{wrong}+FN}
$$

$$
CorrectChangeF1_{t,k}
=
\frac{
2\cdot Precision^{CC}_{t,k}\cdot Recall^{CC}_{t,k}
}{
Precision^{CC}_{t,k}+Recall^{CC}_{t,k}
}
$$

여기서 \(TP_{wrong}\)은 precision과 recall 양쪽 분모에 모두 들어간다. 즉 단순히
“이 path가 바뀌었다”는 것만 맞혀서는 점수를 얻지 못하고, 최종 `value`와
`status`까지 맞혀야 \(TP_{correct}\)가 된다.

표에 보고하는 Final State Accuracy, Dynamic-path Final State Accuracy,
Correct-change F1 등의 checkpoint-level metric은 먼저 trajectory 안에서
checkpoint 평균을 내고, 그다음 trajectory 평균을 낸다.

$$
Metric
=
\frac{1}{|T|}
\sum_{t\in T}
\left(
\frac{1}{|K_t|}
\sum_{k\in K_t} Metric_{t,k}
\right)
$$

현재 smoke는 trajectory가 `traj_010` 하나이므로 바깥 평균은 사라지고,
60/120/180/240/300의 다섯 checkpoint 점수를 단순 평균한 값이 표에 나온다.

### 2.4 Path-macro Correct-change F1

먼저 각 path \(p\)에 대해 모든 평가 row의 confusion count를 모아
\(F1^{CC}_p\)를 계산한다. 평가된 row 중 Gold change가 한 번이라도 있는
eligible path의 집합을 \(P_{\mathrm{eligible}}\)이라고 하면:

$$
PathMacroF1
=
\frac{1}{|P_{\mathrm{eligible}}|}
\sum_{p\in P_{\mathrm{eligible}}} F1^{CC}_p
$$

따라서 많이 바뀌는 path와 한 번만 바뀌는 path가 최종 평균에서 같은 가중치를
갖는다. 전역 dynamic path \(D\)는 25개지만, 이번 `traj_010`의 다섯 checkpoint
안에서 실제 Gold change가 관측된 eligible path는 23개이므로 이 smoke의
Path-macro Correct-change F1 분모는 23이다.

### 2.5 Event-macro Update Accuracy

Gold update event \(e\)가 바꾼 path 집합을 \(P_{t,e,k}\), 그 event를 처음
평가할 수 있는 checkpoint를 \(k^*_{t,e}\)라고 한다. 먼저 event별 첫 평가
정확도를 계산한다.

$$
UpdateScore_{t,e}
=
\frac{1}{|P_{t,e,k^*}|}
\sum_{p\in P_{t,e,k^*}} C_{t,k^*,p}
$$

trajectory \(t\)에서 관측된 Gold update event의 집합을
\(\mathcal{E}_t\)라고 한다. trajectory 안에서 event를 동일 가중 평균하고,
마지막으로 trajectory를 동일 가중 평균한다.

$$
EventMacroUpdateAccuracy
=
\frac{1}{|T|}
\sum_{t\in T}
\left(
\frac{1}{|\mathcal{E}_t|}
\sum_{e\in \mathcal{E}_t} UpdateScore_{t,e}
\right)
$$

예를 들어 한 event가 4개 path를 바꿨고 첫 평가 checkpoint에서 3개를 맞혔다면
그 event의 `UpdateScore`는 \(3/4=0.75\)다. 다른 event가 몇 개의 path를
바꾸었는지와 관계없이 event-level 평균에서는 동일한 한 표를 갖는다.

### 2.6 Retention-after-update

event \(e\)가 관측되는 각 후속 checkpoint에서 해당 event 관련 path의 정확도를
먼저 구한다.

$$
RetentionScore_{t,e,k}
=
\frac{1}{|P_{t,e,k}|}
\sum_{p\in P_{t,e,k}} C_{t,k,p}
$$

그다음 event별로 관측 가능한 checkpoint의 평균을 내고, event 평균,
trajectory 평균 순서로 집계한다.

$$
Retention_{t,e}
=
\frac{1}{|K_{t,e}|}
\sum_{k\in K_{t,e}} RetentionScore_{t,e,k}
$$

$$
RetentionAfterUpdate
=
\frac{1}{|T|}
\sum_{t\in T}
\left(
\frac{1}{|\mathcal{E}_t|}
\sum_{e\in \mathcal{E}_t} Retention_{t,e}
\right)
$$

즉 Event-macro Update Accuracy가 “처음 제대로 반영했는가”를 본다면,
Retention-after-update는 “처음과 그 이후에도 계속 맞게 유지했는가”를 본다.

### 2.7 실제 숫자로 보는 예시

Claude Opus 5의 checkpoint 300 confusion count는 다음과 같다.

| Count | Value |
|---|---:|
| \(TP_{correct}\) | 12 |
| \(TP_{wrong}\) | 1 |
| \(FN\) | 2 |
| \(FP\) | 2 |
| \(TN\) | 17 |

따라서 맞은 전체 cell은 \(TP_{correct}+TN=12+17=29\)개다.

$$
FSA_{300}=\frac{29}{34}=0.8529
$$

dynamic path에서는 25개 중 22개를 맞혔다.

$$
DFSA_{300}=\frac{22}{25}=0.88
$$

Correct-change Precision과 Recall은 모두 다음과 같다.

$$
Precision^{CC}_{300}
=
\frac{12}{12+1+2}
=0.8
$$

$$
Recall^{CC}_{300}
=
\frac{12}{12+1+2}
=0.8
$$

$$
CorrectChangeF1_{300}=0.8
$$

이 값들이 checkpoint 표의 Claude 300행에 표시된 `85.3`, `88.0`, `80.0`과
각각 대응한다. 최종 aggregate의 Claude Correct-change F1 `83.07`은 이 80.0을
포함한 다섯 checkpoint F1의 평균이다.

## 3. 최종 집계 결과

모든 값은 백분율이다. Gemini의 checkpoint 300은 20,000-token 상한에서 성공한
확인 실행 결과로 교체했다. 12,000-token 상한에서 잘린 두 응답은 점수 표에서는
제외했지만 실패 기록과 비용 원장에는 그대로 보존했다.

| Model / Input Condition | Final State Accuracy | Dynamic-path Final State Accuracy | Correct-change F1 | Path-macro Correct-change F1 | Event-macro Update Accuracy | Retention-after-update | Parse Success |
|---|---:|---:|---:|---:|---:|---:|---:|
| Claude Opus 5 / Full Context | **87.65** | **91.20** | **83.07** | **78.55** | **88.12** | **87.50** | 5/5 |
| Gemini 3.1 Pro Preview / Full Context | 79.41 | 81.60 | 69.07 | 59.33 | 74.69 | 75.97 | 5/5 |
| GPT-5.6 Sol / Full Context | 74.71 | 77.60 | 62.77 | 59.71 | 67.92 | 61.98 | 5/5 |
| GPT-5.6 Sol / Oracle Relevant | 75.88 | 76.00 | 65.65 | 64.95 | 70.10 | 62.29 | 5/5 |

## 4. checkpoint별 결과

### 4.1 Claude Opus 5, 전체 대화

| Checkpoint | Parse | Final State Accuracy | Dynamic-path Final State Accuracy | Correct-change F1 | Changed-state Accuracy | Unchanged-state Accuracy |
|---:|---|---:|---:|---:|---:|---:|
| 60 | OK | 85.3 | 88.0 | 78.6 | 78.6 | 90.0 |
| 120 | OK | 97.1 | 100.0 | 95.7 | 100.0 | 95.7 |
| 180 | OK | 91.2 | 96.0 | 88.9 | 100.0 | 86.4 |
| 240 | OK | 79.4 | 84.0 | 72.2 | 76.5 | 82.4 |
| 300 | OK | 85.3 | 88.0 | 80.0 | 80.0 | 89.5 |

### 4.2 Gemini 3.1 Pro Preview, 전체 대화

| Checkpoint | Parse | Final State Accuracy | Dynamic-path Final State Accuracy | Correct-change F1 | Changed-state Accuracy | Unchanged-state Accuracy |
|---:|---|---:|---:|---:|---:|---:|
| 60 | OK | 79.4 | 84.0 | 73.3 | 78.6 | 80.0 |
| 120 | OK | 91.2 | 96.0 | 87.0 | 90.9 | 91.3 |
| 180 | OK | 82.4 | 84.0 | 71.4 | 83.3 | 81.8 |
| 240 | OK | 70.6 | 68.0 | 55.6 | 58.8 | 82.4 |
| 300 | OK | 73.5 | 76.0 | 58.1 | 60.0 | 84.2 |

### 4.3 GPT-5.6 Sol, 전체 대화

| Checkpoint | Parse | Final State Accuracy | Dynamic-path Final State Accuracy | Correct-change F1 | Changed-state Accuracy | Unchanged-state Accuracy |
|---:|---|---:|---:|---:|---:|---:|
| 60 | OK | 79.4 | 80.0 | 69.0 | 71.4 | 85.0 |
| 120 | OK | 88.2 | 96.0 | 84.6 | 100.0 | 82.6 |
| 180 | OK | 79.4 | 84.0 | 69.0 | 83.3 | 77.3 |
| 240 | OK | 58.8 | 60.0 | 46.2 | 52.9 | 64.7 |
| 300 | OK | 67.6 | 68.0 | 45.2 | 46.7 | 84.2 |

### 4.4 GPT-5.6 Sol, Oracle Relevant

| Checkpoint | Parse | Final State Accuracy | Dynamic-path Final State Accuracy | Correct-change F1 | Changed-state Accuracy | Unchanged-state Accuracy |
|---:|---|---:|---:|---:|---:|---:|
| 60 | OK | 79.4 | 80.0 | 73.3 | 78.6 | 80.0 |
| 120 | OK | 88.2 | 92.0 | 84.6 | 100.0 | 82.6 |
| 180 | OK | 88.2 | 88.0 | 76.9 | 83.3 | 90.9 |
| 240 | OK | 58.8 | 56.0 | 37.8 | 41.2 | 76.5 |
| 300 | OK | 64.7 | 64.0 | 55.6 | 66.7 | 63.2 |

## 5. Gemini 출력 상한 실패와 수정

최초 checkpoint 300 실행은 thinking 11,517 tokens와 실제 답변 479 tokens를
사용해 정확히 12,000-token 상한에 도달했다. JSON은
`employment.employer` 값을 작성하던 중 잘렸고
`invalid_json_or_missing_state`로 판정됐다.

동일 설정으로 명시적으로 한 번 더 실행했지만 thinking 10,679 tokens와 실제
답변 1,317 tokens를 사용해 다시 상한에 도달했고 동일하게 실패했다. 따라서
일회성 API 변동이 아니라 재현 가능한 출력 예산 문제로 판단했다.

Stage 2.2의 공통 출력 상한을 20,000 tokens로 올린 뒤 다시 확인한 결과,
thinking 12,669 tokens와 실제 답변 1,733 tokens를 사용하고 완전한 JSON을
반환했다. 파싱 오류와 schema 검증 오류는 모두 0이었다.

이 수정은 특정 정답이나 점수를 보고 prompt를 조정한 것이 아니다. 세 모델에
동일한 상한을 적용해 완전한 구조화 답변을 제출할 기회를 보장하는 형식 안정성
수정이다. 준비 데이터 경로에도 `maxout20000`을 포함해 설정 변경이 과거
12,000-token item을 잘못 재사용하지 않도록 했다. Gold와 평가 대상 path는
변경하지 않았다.

## 6. Oracle Relevant 비교

GPT Oracle Relevant 조건에서 Full Context 조건을 뺀 차이는 다음과 같다.

| Metric | Delta (%p) |
|---|---:|
| Final State Accuracy | +1.18 |
| Dynamic-path Final State Accuracy | -1.60 |
| Correct-change F1 | +2.88 |
| Path-macro Correct-change F1 | +5.24 |
| Event-macro Update Accuracy | +2.19 |
| Retention-after-update | +0.31 |

이 한 trajectory에서는 Oracle Relevant 조건에서 update-sensitive metrics가 소폭
상승했지만 Dynamic-path Final State Accuracy는 하락했다. 전체 대화의 방해 효과가
일부 존재할 가능성과 일치하지만, 한 trajectory만으로 이를 통계적으로
주장할 수는 없다.

## 7. 비용

provider가 보고한 token 사용량에 표준 API 단가를 적용했다. Gemini 출력
token에는 thinking token을 포함했다.

| 실행 | 계산 비용 |
|---|---:|
| 최초 20회 실행 | $5.899717 |
| Gemini 12,000-token 동일 설정 재실행 | $0.281110 |
| Gemini 20,000-token 수정 후 확인 실행 | $0.309982 |
| **이번 예비 실험 전체** | **$6.490809** |

비용 원장에는 각 실행을 올림한 보수적 상한인 `$5.900`, `$0.282`, `$0.310`으로
기록했다. 현재 누적 보수적 비용은 `$12.251 / $20.000`이며, 실제 provider
청구 내역이 최종 기준이다.

## 8. 결론

- Stage 2.2 v3 JSON 구조와 A–E 평가 파이프라인은 세 모델에서 끝까지 작동했다.
- 20,000-token 상한 적용 후 세 모델 모두 5개 checkpoint의 완전한 상태를
  제출했다.
- 12,000-token 상한은 Gemini의 adaptive thinking과 함께 사용할 때 장문
  checkpoint에서 구조적 파싱 실패를 일으킬 수 있으므로 사용하면 안 된다.
- update-sensitive metrics는 Final State Accuracy만 볼 때 가려지는 모델 차이를 실제로
  드러냈다.
- 다만 이 결과는 `traj_010` 하나의 예비 결과이므로 논문의 모델 성능 결론으로
  사용하지 않는다.

## 9. Gold 대비 오류 Case Study

세 모델의 Full Context 결과를 동일한 checkpoint 300에서 비교했다. cell은
`value`와 `status`가 모두 Gold와 같아야 정답이다. 아래 error type은 변경
탐지 관점의 confusion matrix를 따른다.

- **FP (False-positive update)**: Gold상 초기 상태와 동일한 path를 모델이
  변경했다고 예측한 경우
- **FN (Missed update)**: Gold상 변경된 path를 모델이 초기 상태로 유지하거나
  비워 둔 경우
- **TP-wrong-value**: 변경 자체는 탐지했지만 최종 `value` 또는 `status`가
  Gold와 다른 경우

| Model | Incorrect Cells | FP | FN | TP-wrong-value |
|---|---:|---:|---:|---:|
| Claude Opus 5 | 5/34 | 2 | 2 | 1 |
| Gemini 3.1 Pro Preview | 9/34 | 3 | 2 | 4 |
| GPT-5.6 Sol | 11/34 | 3 | 2 | 6 |

### Case 1. Claude Opus 5 — Adjacent-path overwrite

| Field | Content |
|---|---|
| Path | `employment.occupation` |
| Initial State | `value="일반 비서"`, `status="current"` |
| Gold | `value="일반 비서"`, `status="current"`, evidence `[]` |
| Prediction | `value=null`, `status="unknown"`, evidence `["D299"]` |
| Error Type | FP |

`D299`는 새 직장 `두레헬스케어`, 급여일, 급여계좌, 소득 안정성에 관한 update를
제공하지만 occupation은 명시하지 않는다. 따라서 Gold protocol에서는
`employment.occupation`의 초기값인 `일반 비서`를 유지해야 한다. Claude는 같은
employment subtree의 여러 field가 갱신되자 occupation까지 알 수 없는 값으로
지웠다.

이 사례는 한 event가 인접 field 전체를 덮어쓰는 **adjacent-path overwrite**다.
모델이 update scope를 path 단위로 분리하지 못했으며, unchanged-state retention
실패로 해석할 수 있다.

### Case 2. Gemini 3.1 Pro Preview — Temporal projection과 Gold semantics의 충돌

| Field | Content |
|---|---|
| Path | `household.children` |
| Initial State | `value=[]`, `status="current"` |
| Gold | `value=[0, 2, 3]`, `status="current"`, evidence `["D180"]` |
| Prediction | `value=[12, 11, 9]`, `status="current"`, evidence `["D181"]` |
| Error Type | TP-wrong-value |

`D180`과 `D181`은 2016년 12월과 2017년 1월에 자녀 세 명의 나이를
`[0, 2, 3]`으로 확정한다. checkpoint 300의 상담일은 2026년 6월이다. Gemini는
약 9년의 시간 경과를 반영해 자녀 나이를 `[12, 11, 9]`로 전진시켰지만, Gold는
마지막 명시적 update의 `[0, 2, 3]`을 그대로 유지한다. 같은 패턴으로 Gemini는
`profile.age`도 Gold의 30이 아니라 45로 예측했다.

현재 scoring contract에서는 명시적 update가 없는 자동 시간 전진을 허용하지
않으므로 이 예측은 오답이다. 그러나 RQ가 “현재 state를 파악하는가”라면 나이는
시간에 따라 결정적으로 변한다. 따라서 이 사례는 모델 오류만이 아니라 다음
construct-validity 선택을 요구한다.

1. 나이 path를 마지막 관측값으로 고정한다고 명시한다.
2. checkpoint date를 이용해 Gold에 deterministic temporal projection을 적용한다.
3. 자동으로 변하는 path를 primary state-reconstruction metric에서 제외한다.

이 선택을 freeze하지 않으면 시간 계산을 수행한 모델이 오히려 감점될 수 있다.

### Case 3. GPT-5.6 Sol — Stale fact intrusion과 종료 근거 부족

| Field | Content |
|---|---|
| Path | `financial_products.loans` |
| Initial State | `value=["jeonse_loan"]`, `status="current"` |
| Gold | `value=["mortgage"]`, `status="current"`, evidence `["D255"]` |
| Prediction | `value=["mortgage", "credit"]`, `status="current"`, evidence `["D129", "D255", "D263"]` |
| Error Type | TP-wrong-value |

`D129`는 2014년에 사용 중인 신용대출을 명시한다. `D255`는 2022년에
전세대출이 정리되고 주택담보대출이 시작됐음을 명시하며, Gold는 최종 대출
목록을 `["mortgage"]`로 갱신한다. GPT는 최신 mortgage는 반영했지만 과거
`credit`도 계속 보존했다.

Gold contract를 기준으로 보면 이는 과거 fact가 현재 state에 남은
**stale fact intrusion**이다. 다만 `D255`가 명시적으로 종료했다고 말하는 것은
전세대출이며 신용대출 종료는 직접 언급하지 않는다. 따라서 모델이 대화만 보고
`credit`을 제거해야 한다는 근거가 충분한지도 별도로 검토해야 한다.

이 사례는 event의 Gold update가 여러 기존 값을 대체할 때 dialogue에도 각 값의
종료 근거가 관측 가능해야 함을 보여준다. 그렇지 않으면 memory retention을 잘한
모델이 Gold상 오답이 되는 under-specification이 발생할 수 있다.

### Case Study 종합

세 사례는 서로 다른 실패 원인을 보인다.

| Case | Primary Issue | Interpretation |
|---|---|---|
| Claude | Adjacent-path overwrite | 비교적 명확한 model state-tracking error |
| Gemini | Deterministic temporal projection | Gold semantics와 current-state RQ 사이의 construct-validity issue |
| GPT | Stale fact intrusion | Model retention error 가능성과 dialogue termination evidence 부족이 함께 존재 |

따라서 full run 전에는 단순히 parser 통과 여부만이 아니라, 자동 시간 전진 path의
평가 규칙과 list-valued state의 명시적 종료 근거를 먼저 freeze해야 한다. 이
작업은 smoke 점수에 맞춘 prompt tuning이 아니라, Gold가 대화로부터 식별 가능한
현재 state를 나타내는지 보장하기 위한 protocol audit이다.

# Stage 2.2 Low-reasoning Three-trajectory Figures

`traj_002`, `traj_003`, `traj_010`의 checkpoints 60, 120, 180, 240, 300을
trajectory별로 표시하고, 각 checkpoint에서 세 trajectory를 동일 가중한
macro average를 함께 제공한다.

| Version directory | Source plans |
|---|---|
| `low_3traj__71ea1d01981a__9bb8ed9ee53c__460647a3a1bc/` | `traj_002=71ea1d01981a06ed6906fdc930d36a1cdcfa5b17efce04385d0619057ab3949a`, `traj_003=9bb8ed9ee53cbc54a4d9e3ead07204675c07cc784ab2558d75c9cd10b6e7c515`, `traj_010=460647a3a1bcc38382800ba2e2be6114439c4d2b7f9c09e21d79296f51542bfd` |
| `low_3traj_4model__71ea1d01981a__9bb8ed9ee53c__460647a3a1bc__48fb421665a6__e46206e6bd65__da155ec4dae9/` | 위 세 trajectory plans + Gemini retry `48fb421665a697fdd3f05fd7303d9fb88cdcd2b893ced82bfaaa3d75ca4a7b67` + Claude Opus 4.8 partial plan `e46206e6bd6553002c81e89d2daf2af03c80a87efad40bcd3222ad851243c272` + continuation plan `da155ec4dae9a04cb249411abef075d40af046414fc3245674e9fbfa3ae26dca` |

각 version directory는 source CSV와 SVG/PNG/PDF를 포함한다. Gemini
`traj_002`, checkpoint 240의 invalid JSON은 규약대로 0점으로 유지하고 그래프에
parse failure로 표시한다.

## Retry v2

`low_3traj_retry_v2__71ea1d01981a__9bb8ed9ee53c__460647a3a1bc__48fb421665a6`
디렉터리는 사용자가 명시적으로 요청한 Gemini `traj_002`, checkpoint 240
단일 재실행을 반영한다. 최초 응답과 최초 분석은 변경하지 않았으며, retry plan
`48fb421665a697fdd3f05fd7303d9fb88cdcd2b893ced82bfaaa3d75ca4a7b67`의
성공 응답만 해당 cell에 대체한 versioned analysis다.

Retry v2는 first-attempt parse error 1건과 final parse error 0건을 구분해
기록한다. 정식 실험에서는 같은 retry rule을 모든 모델과 trajectory에 일관되게
적용해야 한다.

`macro_average_model_comparison.{svg,png,pdf}`는 각 모델의
3-trajectory macro-average만 겹쳐 보여주는 compact two-panel figure다.
Dynamic-path Final State Accuracy와 Correct-change F1을 세로로 배치해 가로폭을
줄였으며, model identity는 색상·marker·line style로 중복 인코딩한다. SVG
가로폭은 720px, PNG는 800px이고 y축은 40–100%의 focused scale이다.

## Four-model Version

`low_3traj_4model__...` version은 기존 세 모델 결과를 보존한 채 Claude Opus
4.8 Low 결과만 compact comparison figure에 네 번째 선으로 추가한다. Opus 4.8
최초 plan은 120초 client read timeout 전 저장된 3개 checkpoint를 사용하고,
사용자가 승인한 continuation plan은 나머지 12개 checkpoint를 300초 timeout으로
실행했다. 두 plan 사이에 중복 checkpoint는 없다.

Opus 4.8의 state JSON은 15/15 모두 parsing과 scoring에 성공했다.
`traj_010` checkpoint 180과 240의 두 응답에는 `D57`처럼 zero-padding이 없는
evidence ID가 포함되어 evidence validation warning이 기록됐지만, 이는 state
prediction parse failure가 아니므로 여섯 semantic metrics는 정상 계산했다.
`aggregate_metric_values.csv`는 이러한 warning을 별도 열로 보존한다.

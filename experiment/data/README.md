# Data Layout

이 디렉터리의 raw/prepared 산출물은 version control 대상이 아니다. 활성 snapshot은
manifest로 선택한다.

```text
data/
├── raw/
│   ├── active_manifest.json
│   └── hf/<dataset>/<revision>/
└── prepared/
    ├── active_manifest.json
    └── <data-hash>/
        ├── sessions_joined/
        ├── sessions_answer_free/
        ├── initial_state_s000/
        ├── prefix_gold/
        ├── canonical_items/
        ├── masking/
        └── masking_items/
```

- `sessions_joined`: gold/문항 생성 전용
- `sessions_answer_free`: 모든 평가 방법의 ingest 전용
- `initial_state_s000`: trajectory별 초기 금융 상태
- `canonical_items`: Stage 1 400개, Stage 2 8,714개
- `masking_items`: 5 arms × 451 events × 2 questions = 4,510개

Evaluator는 `sessions_answer_free`만 읽는다. Gold-only field가 발견되거나 evidence
session이 checkpoint보다 미래이면 즉시 중단한다.

# Architecture

## 실행 흐름

```text
HF dialogues + gold + fillers
        │
        ▼
raw snapshot manifest
        │
        ├─ joined sessions ──> PrefixGold ──> Stage 1 / Stage 2 / Stage 3 items
        ├─ answer-free sessions ────────────> method inputs
        └─ filler variants ─────────────────> five-arm masking items
                                                │
                                                ▼
immutable paid plan ──> 7 method adapters ──> predictions + manifests
                                                │
                                                ▼
strict aggregate ──> JSON + CSV + Markdown tables
```

## 코드 책임

| 파일 | 책임 |
|---|---|
| `data_pipeline.py` | HF snapshot, dialogue/gold join, answer-free input, S000 |
| `items.py` | PrefixGold, Stage 1, Stage 2 |
| `stage3.py` | upstream multi-hop build/audit, S000/evidence 계약 adapter |
| `masking.py` | 4개 mask와 placebo arm, masking 질문 |
| `evaluator.py` | chronological streaming, 방법 실행, fail-closed manifest |
| `methods/` | FullCtx, BM25, Dense, Mem0, Letta adapter |
| `safety.py` | immutable plan, SHA/provenance, approval gate |
| `metrics.py` | completeness, 계층 macro, bootstrap, 표 생성 |

## 상태형 방법

S000은 trajectory 시작 시 한 번만 ingest한다. 이후 S001부터 checkpoint까지 순차
주입하며 query 뒤 원본 memory가 바뀌면 실패한다.

Mem0와 local 방법의 masking은 공통 prefix trie를 사용한다. frozen snapshot 기준
23,588개 edge만 ingest하고 분기 시 state를 clone한다. Letta Agent File은 archival
passage를 보존하지 않으므로 Letta masking은 2,255개 frozen variant를 명시적으로
replay한다. 이 차이는 full plan operation limit과 논문 방법론에 기록한다.

## 불변 산출물

Paid plan은 item payload, prepared manifest, code/config/prompt tree, 방법 목록,
operation upper bound를 hash로 고정한다. 계획 후 어느 입력이 바뀌어도 runner는
실행을 거부한다. Prediction JSONL은 같은 이름의 manifest가 `COMPLETE`이고 output
SHA가 일치할 때만 집계된다.

# Financial Memory Benchmark Results — `<PLAN_SHA>`

이 파일은 full run 후 `runs/paid_full/<PLAN_SHA>/RESULTS.md`로 복사해 작성한다.
`TBD`를 모두 실제 산출물 또는 명시적인 `N/A`로 바꾼다. Smoke 결과는 옮기지 않는다.

## 1. Run identity

| Field | Value |
|---|---|
| Full plan SHA | `TBD` |
| Execution date/timezone | `TBD` |
| Git commit / dirty state | `TBD` |
| Prepared data hash | `TBD` |
| Input item SHA | `TBD` |
| Prompt/config execution-tree SHA | `TBD` |
| Python / pip-freeze SHA | `TBD` |
| Letta image ID/digest | `TBD` |
| Main top-k | `10` |
| Embedding model/dimension | `gemini-embedding-2` / 768 |
| Final status | `TBD` |

## 2. Completeness

| Check | Expected | Observed |
|---|---:|---:|
| Configured methods | 7 | `TBD` |
| Items per method | 13,747 | `TBD` |
| Total predictions | 96,229 | `TBD` |
| Stage 1 per method | 400 | `TBD` |
| Stage 2 per method | 8,714 | `TBD` |
| Masking per method | 4,510 | `TBD` |
| COMPLETE manifests | 14 | `TBD` |
| Parse errors | report | `TBD` |
| Identical item sets | true | `TBD` |
| `reporting_ready` | true | `TBD` |

검증 근거: `report/metrics.json`

## 3. Main results

Family별로 표를 분리한다. Stage 1은 Pair F1과 Exact Pair-Set Match, Stage 2는
GCA@15와 Retention-after-update를 보고한다. 각 값은 trajectory-bootstrap 95% CI와
함께 기록한다.

### Full Context

| Method | Stage 1 Pair F1 [95% CI] | Stage 1 Exact [95% CI] | Stage 2 GCA@15 [95% CI] | Stage 2 Retention |
|---|---:|---:|---:|---:|
| Claude Opus 4.8 | `TBD` | `TBD` | `TBD` | `TBD` |
| Gemini 3.1 Pro | `TBD` | `TBD` | `TBD` | `TBD` |
| GPT-5.6 Sol | `TBD` | `TBD` | `TBD` | `TBD` |

### Retrieval

| Method | Stage 1 Pair F1 [95% CI] | Stage 1 Exact [95% CI] | Stage 2 GCA@15 [95% CI] | Stage 2 Retention |
|---|---:|---:|---:|---:|
| BM25 + Gemini | `TBD` | `TBD` | `TBD` | `TBD` |
| Dense GE2 + Gemini | `TBD` | `TBD` | `TBD` | `TBD` |

### Memory

| Method | Stage 1 Pair F1 [95% CI] | Stage 1 Exact [95% CI] | Stage 2 GCA@15 [95% CI] | Stage 2 Retention |
|---|---:|---:|---:|---:|
| Mem0 + Gemini | `TBD` | `TBD` | `TBD` | `TBD` |
| Letta + Gemini | `TBD` | `TBD` | `TBD` | `TBD` |

## 5. Retrieval quality

| Method | Stage 2 latest | Stage 2 complete |
|---|---:|---:|
| BM25 | `TBD` | `TBD` |
| Dense | `TBD` | `TBD` |
| Mem0 | `TBD` | `TBD` |
| Letta | `TBD` | `TBD` |

근거: `report/metrics.json`. Full Context에는 retrieval recall을 부여하지 않는다.

## 6. Retention lag

`report/retention_lag.csv`를 사용한다.

| Method | Lag 0 | Lag 1 | Lag 2 | Lag 3 | Longest lag |
|---|---:|---:|---:|---:|---:|
| `TBD` | `TBD` | `TBD` | `TBD` | `TBD` | `TBD` |

보고할 내용:

- lag 증가에 따른 Stage 2 변화: `TBD`
- 방법 family별 차이: `TBD`
- bootstrap 불확실성: `TBD`

## 7. Masking ablation

`report/masking_by_arm.csv`를 사용한다. Lifecycle과 memory stage를 분리한다.

### Lifecycle

| Method | Full | Terminal mask | Upcoming mask | All mask | Placebo |
|---|---:|---:|---:|---:|---:|
| `TBD` | `TBD` | `TBD` | `TBD` | `TBD` | `TBD` |

### Historical memory

| Method | Full | Terminal mask | Upcoming mask | All mask | Placebo |
|---|---:|---:|---:|---:|---:|
| `TBD` | `TBD` | `TBD` | `TBD` | `TBD` | `TBD` |

Placebo 대비 evidence mask delta와 해석: `TBD`

## 8. Paired comparisons

`report/paired_method_deltas.csv`에서 사전에 정의한 핵심 비교를 옮긴다. 21개 전체
pair는 appendix/CSV에 두고 본문에는 연구 질문과 직접 관련된 비교만 쓴다.

| Stage | Left − Right | Delta [95% CI] | Interpretation |
|---|---|---:|---|
| Stage 1 | `TBD` | `TBD` | `TBD` |
| Stage 2 | `TBD` | `TBD` | `TBD` |
| Masking lifecycle | `TBD` | `TBD` | `TBD` |
| Masking memory | `TBD` | `TBD` | `TBD` |

CI가 0을 포함하는 비교를 우열로 단정하지 않는다.

## 9. Sensitivity at k=5

Main k=10과 별도 frozen run을 사용한다.

| Method | Stage | k=10 | k=5 | Delta |
|---|---|---:|---:|---:|
| `TBD` | `TBD` | `TBD` | `TBD` | `TBD` |

실행하지 않았다면 `N/A — not run`으로 기록한다.

## 10. Efficiency and reliability

| Method | Build tokens/cost | Query tokens/cost | Latency | Failed requests | Parse errors |
|---|---:|---:|---:|---:|---:|
| `TBD` | `TBD` | `TBD` | `TBD` | `TBD` | `TBD` |

| Cost record | Value |
|---|---:|
| Reviewed estimate | `TBD` |
| Attributable list-price estimate | `TBD` |
| Provider invoice/usage dashboard | `TBD` |
| Unknown or reserved amount | `TBD` |

비용은 무료 credit과 세금을 포함한 invoice 값과 list-price 추정을 구분한다.

## 11. Findings

1. Stage 1: `TBD`
2. Stage 2 and long-term recall: `TBD`
3. Retrieval versus persistent memory: `TBD`
4. Masking causal pattern: `TBD`
5. Cost/performance trade-off: `TBD`

## 12. Limitations

- Letta는 end-to-end agent이며 BM25/Dense/Mem0의 공통 reader 비교와 동일한
  retriever-only 실험이 아니다.
- Full Context 행은 서로 다른 frontier model ceiling이다.
- Embedding model/dimension은 공통이지만 framework-native memory 표현과 검색
  제어 방식은 다르다.
- Provider execution date와 exact model IDs: `TBD`
- 실패·누락·재실행 여부: `TBD`

## 13. Artifact checklist

- [ ] `metrics.json`
- [ ] `main_results.csv`와 `main_results.md`
- [ ] `masking_by_arm.csv`
- [ ] `retention_lag.csv`
- [ ] `paired_method_deltas.csv`
- [ ] 14 prediction JSONL/manifest pairs
- [ ] full plan JSON
- [ ] `environment.json`
- [ ] `pip_freeze.txt`
- [ ] Letta image provenance
- [ ] 비용 근거
- [ ] 모든 `TBD` 제거

# Evaluation Protocol

## 연구 질문

- Stage 1: 15-session 단위 누적 prefix에서 발생한 모든 life event와 각 발생
  확정 session을 복원하는가?
- Stage 2: 이후 상태가 바뀌고 retention lag가 늘어나도 과거 checkpoint의 상태를
  회상하는가?
- System comparison: Full Context, retrieval, persistent memory 중 어떤 접근이
  정확도와 비용 면에서 유리한가?
- Masking: terminal/upcoming evidence 제거가 lifecycle 탐지와 과거 기억에 어떤
  영향을 주는가?

## Stage 정의

Stage 1은 각 15-session checkpoint까지 누적된 prefix에서 모든 occurred event의
`event_id`와 최초 발생 확정 `D###` session을 pair로 답한다.

Stage 2는 query 시점의 최신 상태가 아니라 문항에 표시된 기준일의 historical
state를 답한다. 닫힌 값 집합은 A–D 객관식이고, 회사명·주소·금액·목록 등은
정규화된 단답형으로 채점한다. 예를 들어 S001–S015 종료 직장이 한빛테크이고
S016–S030에 새봄금융으로 바뀌었다면, 이후에도 첫 target 질문의 정답은
한빛테크이고 두 번째 target 질문만 새봄금융이다.

## 비교 방법

| Family | Method ID | 입력 또는 memory | Final answer |
|---|---|---|---|
| Full Context | `fc_claude_opus_5` | S001…checkpoint 전체 | Claude |
| Full Context | `fc_gemini_3_1_pro` | S001…checkpoint 전체 | Gemini |
| Full Context | `fc_gpt_5_6_sol` | S001…checkpoint 전체 | OpenAI |
| Retrieval | `bm25_gemini_3_1_pro` | BM25 session top-k | 공통 Gemini reader |
| Retrieval | `dense_ge2_gemini_3_1_pro` | GE2 session top-k | 공통 Gemini reader |
| Memory | `mem0_gemini_3_1_pro` | Mem0 search top-k | 공통 Gemini reader |
| Memory | `letta_gemini_3_1_pro` | Letta archival memory | Letta agent |

Main `top_k=10`, sensitivity `top_k=5`, reranker 없음이다. Sensitivity는 frozen
문항과 다른 설정을 바꾸지 않고 별도 run으로 실행한다.

## Stage별 비교 surface

Stage 1은 `stage1_occurred_event_evidence_pairs.methods`에 고정된 GPT‑5.6 Sol,
Claude Opus 4.8, Gemini 3.1 Pro의 Full Context 3-model 비교다.
`configs/experiment.yaml`의 전역 `methods` 9개는 Stage 2.2 전용이다.

| Stage | Runner | Grid | Retrieval |
|---|---|---|---|
| Stage 1 | `scripts/paid/run_stage1.sh` | 20 traj × 20 prefix checkpoint × 3 models | Full Context |
| Stage 2.2 | `scripts/paid/run_stage2_2.sh` | 20 traj × 20 checkpoint | 4개 state group, group별 `top_k=5`, 최대 20 evidence |

두 stage는 같은 코퍼스 `dialogues_no_prospective` + `gold_no_prospective`를 쓰며, `prepare-stage2-2`가 만든 prepared tree를 공유한다. 다른 코퍼스로 실행하는 경로는 없다.

Stage 1의 세 모델은 모두 동일한 전체 누적 prefix를 받는다. checkpoint query는
서로 독립적이고 이전 응답은 다음 checkpoint에 전달되지 않는다. 출력 상한은
20,000 token이며 timeout 600초, provider retry 0, parse retry 0으로 고정한다.

## 공정 비교 계약

- 모든 방법에 같은 checkpoint, 질문, 보기 순서, answer-free sessions를 제공한다.
- S000은 최초 record로 한 번만 ingest하며 query에 재첨부하지 않는다.
- checkpoint 이후 session은 context, index, memory 어디에도 존재하지 않는다.
- BM25, Dense, Mem0는 동일 Gemini reader와 prompt/parser를 사용한다.
- Letta는 end-to-end search-and-answer agent이므로 retriever-only 비교로 해석하지
  않고 Memory family로 분리한다.
- Invalid format은 오답이며 의미를 복구하는 repair call은 없다.
- Stage 2.2 checkpoint별 prediction은 새 method/client에서 독립적으로 생성한다.
  다른 checkpoint의 prediction이나 response는 다음 요청의 context로 전달하지 않는다.
  Full-context smoke는 세 model과 model별 다섯 checkpoint를 병렬 실행하되,
  automatic retry 0, first-error stop을 유지한다.
- Dense/Mem0/Letta는 모두 `gemini-embedding-2`, 768차원, `top_k=10`을 사용한다.
  Framework-native memory 표현과 검색 제어 방식의 차이는 결과 해석에 명시한다.

## Masking arms

| Arm | 조작 |
|---|---|
| `full` | 원본 |
| `mask_terminal` | terminal evidence 치환 |
| `mask_upcoming` | upcoming evidence 치환 |
| `mask_all` | terminal과 upcoming 모두 치환 |
| `placebo_all` | target evidence는 유지하고 같은 수의 background session 치환 |

Placebo arm은 텍스트 치환 자체의 영향과 target evidence 제거 영향을 분리한다.
Lifecycle 질문과 memory 질문은 별도 stage로 보고한다.

## 누출·오염 차단

- dialogue/gold join key와 300-session 연속성 검증
- answer-free input의 gold-only field 거부
- evidence session이 checkpoint를 넘으면 거부
- trajectory별 index, collection, agent 분리
- query 전후 persistent memory fingerprint 검사
- Mem0 clone equivalence 검사
- Letta frozen-variant replay count와 passage fingerprint 검사

## 지표와 집계

| 대상 | Primary |
|---|---|
| Stage 1 | checkpoint 균등가중 `strict_occurred_event_evidence_f1` |
| Stage 2 | checkpoint → target → trajectory 계층 macro accuracy |
| Masking | stage × arm accuracy |
| Retrieval | latest-state recall@k, complete-evidence recall@k |
| 불확실성 | trajectory bootstrap 95% CI, 10,000 samples |
| 방법 차이 | 모든 21개 method pair의 paired trajectory bootstrap delta |
| 신뢰성 | parse error, failed request, COMPLETE 비율 |
| 효율 | build/query token, operation, latency, estimated cost |

Stage 2는 같은 target이 여러 checkpoint에서 반복되어도 먼저 target 내부를 평균한 뒤
trajectory를 동일 가중한다. 이 방식은 일찍 등장한 target의 과대 가중을 막는다.

## 논문 보고 조건

다음을 모두 만족해야 한다.

- 7개 방법이 동일한 frozen item set을 평가
- 모든 prediction manifest가 `COMPLETE`
- prediction output SHA 일치
- `--expected-scope all` strict 집계 통과
- `metrics.json`의 `completeness.reporting_ready=true`

Partial run과 smoke 점수는 성능표에 넣지 않는다.

## Reference boundary

비교 구조와 top-k는 MemoryAgentBench commit
`455306dcabc3842526eb83cd4e225e5d486c5c5d`와 MedMemoryBench commit
`7227bc105b84a1a9f7a75861eb9e1be3ea502882`를 참고했다. Wrapper를 복사하지 않고
공식 `mem0ai`와 `letta-client`를 직접 사용한다.

# Full-run Readiness

이 문서는 구현 이력 대신 현재 실행 가능 여부와 남은 gate만 기록한다.

## 완료된 검증

- [x] HF dialogues, gold, fillers, plans commit SHA와 content-tree hash 고정
- [x] 20 trajectories × 300 sessions 검증
- [x] Stage 1 400개, Stage 2 8,714개 생성(MCQ 4,897 + free response 3,817)
- [x] 5-arm masking 4,510문항 생성
- [x] answer-free input과 future-leakage fail-closed 검사
- [x] 7개 방법 offline end-to-end smoke
- [x] 7개 방법 canonical paid smoke
- [x] Mem0 실제 ingest/search/final reader 경로
- [x] Letta archival insert/search/final answer와 evidence attribution
- [x] immutable plan SHA, code/config/data provenance 검사
- [x] retry 0, concurrency 1, first-error stop
- [x] 누적 `$5` 미만 standing smoke ledger와 실행 전 보수적 plan 예약
- [x] strict completeness와 output hash 집계

## Full run 전 남은 gate

- [x] Dense/Mem0/Letta를 `gemini-embedding-2` 768차원, `top_k=10`으로 확정
- [x] 확정된 차원으로 offline test 재실행
- [ ] lifecycle 1개 + memory 1개 × 7방법 masking paid smoke
- [ ] masking smoke의 Mem0 clone equivalence 확인
- [ ] masking smoke의 Letta frozen-variant replay와 search contract 확인
- [ ] provider별 full cost estimate 검토
- [ ] Letta health check
- [ ] exact `--scope all` full plan 생성
- [ ] plan의 13,747 item IDs, 7 methods, operation limits 검토
- [ ] full-run 별도 승인

## 확정된 비교 결정

Dense, Mem0, Letta는 모두 `gemini-embedding-2`, 768차원, `top_k=10`을 사용한다.
Letta는 계속 end-to-end agent로 분류하므로 동일 embedding 계약이 framework 내부의
memory 표현과 agent-driven 검색 차이까지 제거한다는 뜻은 아니다.

## 해석 한계

- BM25, Dense, Mem0는 공통 Gemini reader를 사용한다.
- Letta는 agent가 직접 검색하고 답하는 end-to-end system comparison이다.
- 세 Full Context 행은 서로 다른 frontier model ceiling이며 memory mechanism만의
  차이를 뜻하지 않는다.
- Masking에서 Mem0는 prefix clone, Letta는 frozen variant replay를 사용한다.
- Smoke 표본은 방법별 2문항이므로 성능 추론에 사용하지 않는다.

## Full run 후 완료 조건

- [ ] 모든 method/scope manifest가 `COMPLETE`
- [ ] prediction SHA와 manifest 일치
- [ ] 7개 방법의 exact item set 일치
- [ ] strict aggregate 성공
- [ ] `completeness.reporting_ready=true`
- [ ] environment snapshot과 `pip_freeze.txt` 보존
- [ ] Letta image ID/digest 보존
- [ ] [results_template.md](results_template.md) 작성

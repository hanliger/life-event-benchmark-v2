# Full-run Readiness

이 문서는 구현 이력 대신 현재 실행 가능 여부와 남은 gate만 기록한다.

## 완료된 검증

- [x] HF dialogues, gold, fillers, plans snapshot과 hash 보존
- [x] 20 trajectories × 300 sessions 검증
- [x] Stage 1 400개, Stage 2 4,200개 생성
- [x] 5-arm masking 4,510문항 생성
- [x] answer-free input과 future-leakage fail-closed 검사
- [x] 7개 방법 offline end-to-end smoke
- [x] 7개 방법 canonical paid smoke
- [x] Mem0 실제 ingest/search/final reader 경로
- [x] Letta archival insert/search/final answer와 evidence attribution
- [x] immutable plan SHA, code/config/data provenance 검사
- [x] retry 0, concurrency 1, first-error stop
- [x] strict completeness와 output hash 집계

## Full run 전 남은 gate

- [ ] Dense/Mem0/Letta embedding 차원 확정
- [ ] 확정된 차원으로 offline test 재실행
- [ ] lifecycle 1개 + memory 1개 × 7방법 masking paid smoke
- [ ] masking smoke의 Mem0 clone equivalence 확인
- [ ] masking smoke의 Letta frozen-variant replay와 search contract 확인
- [ ] provider별 full cost estimate 검토
- [ ] Letta health check
- [ ] exact `--scope all` full plan 생성
- [ ] plan의 9,110 item IDs, 7 methods, operation limits 검토
- [ ] full-run 별도 승인

## 권고 결정

Dense와 Mem0는 현재 1,536차원이고 Letta 0.16.8의 native Google embedding 등록은
768차원이다. Memory family를 동일 조건에 가깝게 비교하려면 세 방법을 768차원으로
통일하는 것을 권고한다. Framework-native 설정을 유지한다면 Letta를 retriever-controlled
비교로 해석하지 않고 차원 차이를 명시해야 한다.

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

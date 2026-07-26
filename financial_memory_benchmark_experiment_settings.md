# Financial Memory Benchmark Experiment Settings

초기 설계 문서는 구현과 검증 과정에서 여러 결정이 변경되어 더 이상 독립적인
실행 기준으로 사용하지 않는다. 폐기된 `$500` hard cap, retry 3, 32-token output,
구형 baseline 후보는 제거했다.

현재 source of truth:

- 실행 순서:
  [experiment/README.md](experiment/README.md)
- 고정 방법론:
  [docs/protocol.md](experiment/docs/protocol.md)
- 실행 전 checklist:
  [docs/implementation_review.md](experiment/docs/implementation_review.md)
- 실제 YAML 설정:
  [configs/experiment.yaml](experiment/configs/experiment.yaml)
- full run 후 결과 양식:
  [docs/results_template.md](experiment/docs/results_template.md)

핵심 범위는 Full Context 3개, retrieval 2개, memory 2개로 구성된 7방법 비교,
Stage 1/Stage 2/Stage 3 분리 보고, main `top_k=10`, 5-arm masking
ablation이다.

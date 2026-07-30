# Memory-agent Baseline Decision

이 문서는 후보 조사 결과가 아니라 최종 비교군 선택 근거만 보존한다. 실제 설정과
실행은 `experiment/`가 source of truth다.

## 확정된 7개 방법

| Family | Methods | 역할 |
|---|---|---|
| Full Context | Claude, Gemini, OpenAI | long-context ceiling |
| Retrieval | BM25, Gemini Embedding 2 dense | lexical/semantic retrieval control |
| Memory | Mem0, Letta | persistent memory systems |

MIRIX, Zep, MemoryBank 등은 범위와 비용을 통제하기 위해 본 실험에서 제외했다.
Full Context 3개는 서로 다른 frontier ceiling이고, BM25/Dense/Mem0는 공통 Gemini
reader를 사용한다. Letta는 자체 agent search-and-answer 능력을 평가한다.

## 참고 구현

- MemoryAgentBench commit:
  `455306dcabc3842526eb83cd4e225e5d486c5c5d`
- MedMemoryBench commit:
  `7227bc105b84a1a9f7a75861eb9e1be3ea502882`

두 저장소에서 비교 구조, chronological ingestion, top-k 관례를 참고했다. Wrapper는
복사하지 않고 공식 Mem0와 Letta API를 직접 사용한다.

## 고정 비교 원칙

- 모든 방법에 동일 checkpoint와 frozen 문항 제공
- Stage 1, Stage 2, Stage 3 분리
- main `top_k=10`, query-only sensitivity `top_k=5`
- future leakage 금지
- 동일 item set을 완료한 방법만 논문 표에 포함
- Letta를 retriever-only 비교로 해석하지 않고 method family로 분리

세부 계약은
[Evaluation Protocol](../../experiment/docs/protocol.md)에
정의한다.

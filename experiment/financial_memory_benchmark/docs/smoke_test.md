# Smoke Test Record

## 목적

Smoke test는 모델 성능을 추정하지 않고 데이터→adapter→provider→parser→manifest→집계
경로가 실제로 작동하는지 확인한다.

## 완료 상태

2026-07-26 canonical Stage 1 한 문항과 Stage 2 한 문항을 7개 방법에 동일하게
실행했다.

| Method | COMPLETE | Parse error | Stage 1 | Stage 2 |
|---|:---:|---:|:---:|:---:|
| `fc_claude_opus_5` | yes | 0 | correct | correct |
| `fc_gemini_3_6_flash` | yes | 0 | correct | correct |
| `fc_gpt_5_6_sol` | yes | 0 | correct | correct |
| `bm25_gemini_3_6` | yes | 0 | correct | correct |
| `dense_ge2_gemini_3_6` | yes | 0 | correct | correct |
| `mem0_gemini_3_6` | yes | 0 | incorrect | correct |
| `letta_gemini_3_6` | yes | 0 | correct | correct |

Mem0의 Stage 1 오답은 실행 실패가 아니라 검색 evidence에 S015가 포함되지 않은 실제
출력이다. Letta는 두 문항 모두 `archival_memory_search`를 정확히 1회 호출했고
`top_k=10`, S015 evidence attribution을 확인했다.

## 검증 산출물

- 7방법 통합 report:
  `runs/paid_smoke/combined_7method_canonical/`
- Letta evidence plan:
  `cddaebee6f0906c3409a077e4b75f4377f05410d8d3560bbe56489b45e5fd3f1`
- Mem0 plan:
  `a42def49e57a6e0e9c829b503b7224498657b73238c1977c4f1f1df4f8bb45e4`
- 통합 `metrics.json` SHA-256:
  `7ae2e0c95c61d04dc16a89c1953077fce91481576fe02405476847454a397bf8`
- 최종 regression test: 7/7 PASS

통합 report의 `reporting_ready=false`는 정상이다. 전체 frozen item set이 아닌 partial
smoke이기 때문이다.

## 비용 원장

| 항목 | 보수적 금액 |
|---|---:|
| 직접 귀속 가능한 provider list-price 추정 | 약 `$0.98` |
| Mem0 내부 extraction plan reserve | `$1.00` |
| 안전 누계 | **`$1.98`** |
| `$5` standing allowance 잔여 | **`$3.02`** |

실제 invoice는 credit, 세금, pricing tier에 따라 다를 수 있다. Mem0 내부 extraction
usage가 request별로 완전히 귀속되지 않으므로 실제값 대신 plan reserve 전액을 누계에
포함했다.

## 앞으로의 smoke 안전 규칙

- 누적 보수적 비용 + 다음 plan estimate가 엄격히 `$5` 미만
- 단일 plan 최대 `$3`
- concurrency 1
- automatic retry 0
- 첫 오류 즉시 중단
- timeout이나 billing 귀속 불명 시 자동 재실행 금지
- exact plan SHA와 `I_APPROVE_PAID_SMOKE` 필요

다음 smoke 대상은 같은 masking event family의 lifecycle 한 문항과 memory 한
문항이다. 전체 full run은 이 standing approval에 포함되지 않는다.

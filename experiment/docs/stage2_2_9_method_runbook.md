# Stage 2.2 Nine-Method Runbook

## 1. Plan

```bash
export OPENROUTER_API_KEY=...

./experiment/scripts/paid/run_stage2_2.sh plan \
  --methods all \
  --trajectories all \
  --checkpoint-start 15 \
  --checkpoint-end 300 \
  --checkpoint-stride 15 \
  --model-workers 9 \
  --trajectory-workers 20 \
  --checkpoint-workers 20 \
  --max-in-flight 60 \
  --anthropic-max-in-flight 20 \
  --openrouter-max-in-flight 40 \
  --retrieval-top-k-per-group 5 \
  --retrieval-max-evidence 20 \
  --request-timeout-seconds 300 \
  --provider-retries 0 \
  --parse-retries 1 \
  --budget-cap-usd 200 \
  --estimated-usd 180
```

`plan`은 KST `MMDD_HHMM` run directory를 만들고 3,600 prediction grid를 `immutable_plan.json`에 고정한다. 같은 분에 중복되면 `_02`, `_03` suffix를 사용한다.

OpenRouter method가 포함되면 public endpoint metadata와 ZDR endpoint metadata를 조회한다. ZDR-compatible endpoint 중 reported throughput이 가장 높은 provider를 method마다 선택하며 Qwen3.6은 FP8 endpoint만 허용한다. provider, per-token price, context window, endpoint ID, source-response hash를 `provider_lock.json`에 저장한다. Metadata가 충분하지 않으면 plan은 fail closed한다.

Provider를 연구자가 별도로 검토해 고정하려면 다음 옵션을 사용한다.

```bash
--provider-lock-file experiment/configs/my_openrouter_provider_lock.json
```

형식은 `openrouter_provider_lock.example.json`을 따른다. Provider fallback과 model fallback은 허용되지 않는다.

## 2. Inspect and audit prompts

```bash
./experiment/scripts/paid/run_stage2_2.sh show-prompt \
  --method bm25_claude_opus_4_8 \
  --trajectory traj_001 \
  --checkpoint 15

./experiment/scripts/paid/run_stage2_2.sh audit-prompt \
  --run-dir experiment/runs/stage2_2/<run-id>
```

`audit-prompt`가 통과하지 않으면 paid execution은 차단된다. Audit는 prompt를 수정하지 않는다.

## 3. Execute

```bash
./experiment/scripts/paid/run_stage2_2.sh execute \
  --run-dir experiment/runs/stage2_2/<run-id> \
  --execute-paid \
  --approval I_APPROVE_STAGE2_2_PAID
```

API credential은 explicit approval 검증 후 `experiment/.env`에서 읽는다. Provider SDK retry는 0이며 parse 또는 schema failure에만 1회 retry한다. 모든 attempt는 보존한다.

## 4. Resume

```bash
./experiment/scripts/paid/run_stage2_2.sh resume \
  --run-dir experiment/runs/stage2_2/<run-id> \
  --execute-paid \
  --approval I_APPROVE_STAGE2_2_PAID
```

COMPLETE method × trajectory artifact는 건너뛴다. 실패 attempt는 덮어쓰지 않고 다음 `attempt_XX.jsonl`에 기록한다. 전체 frozen grid가 중복 없이 완성되어야 run status가 `GENERATED`가 된다.

## 5. Report

```bash
./experiment/scripts/paid/run_stage2_2.sh report \
  --run-dir experiment/runs/stage2_2/<run-id>
```

Report는 checkpoint, trajectory, path × trajectory, path trajectory-macro, parse reliability, semantic quality, retrieval recall, usage/cost/latency 표를 생성한다. 세 SVG figure는 Dynamic-path Final State Accuracy checkpoint curve, Correct-change F1 checkpoint curve, method × path Final State Accuracy heatmap이다.

## Selection syntax

`--methods`와 `--trajectories`는 `all` 또는 comma-separated IDs를 받는다.

```bash
--methods fc_claude_opus_4_8,bm25_claude_opus_4_8
--trajectories traj_001,traj_010
```

Checkpoint query는 서로 독립적이다. Full Context는 fresh method/client와 독립 prefix를 사용한다. BM25, Dense, Mem0, Letta는 checkpoint까지 순차 ingest한 immutable snapshot을 clone해 병렬 query하며 이전 prediction을 다음 query에 전달하지 않는다.

## External API references

- [OpenRouter provider routing](https://openrouter.ai/docs/guides/routing/provider-selection)
- [OpenRouter model endpoints API](https://openrouter.ai/docs/api/api-reference/endpoints/list-endpoints)
- [OpenRouter ZDR endpoint API](https://openrouter.ai/docs/api/api-reference/endpoints/list-endpoints-zdr)
- [Letta agent create/model settings](https://docs.letta.com/api/python/resources/agents/methods/create)

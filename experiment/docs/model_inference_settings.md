# Model Inference Settings

## 1. 목적과 적용 범위

Stage 2.2 reconstruction은 금융 챗봇의 실제 배포 환경을 모사한다. 따라서
최대 test-time compute를 사용하는 `high`, `xhigh`, `max`, `pro` 설정 대신,
세 provider가 지원하는 중간 수준 reasoning을 사용한다.

정책 이름은 `deployment_realistic_medium`이다. 이 설정은 최종 답변을 생성하는
Claude Opus 5, Gemini 3.1 Pro Preview, GPT-5.6 Sol reader에 적용된다. 검색이나
메모리 저장에 사용되는 별도 embedding model의 설정은 이 문서의 범위가 아니다.

기존 `traj_010` 3-model smoke 결과는 `vendor_default`에서 생성되었다. 이 문서의
설정은 과거 결과를 소급해 변경하지 않으며, 변경 후 새로 생성한 paid plan과
실행 결과부터 적용된다.

## 2. 선택한 설정

| Model | Reasoning / Thinking | Visibility | Other generation settings |
|---|---|---|---|
| Claude Opus 5 | Adaptive thinking, `effort=medium` | `display=omitted` | Sampling parameter 미지정 |
| Gemini 3.1 Pro Preview | `thinking_level=medium` | `include_thoughts=false` | Temperature 미지정 |
| GPT-5.6 Sol | `effort=medium`, `mode=standard`, `context=current_turn` | Reasoning summary 미요청 | `text.verbosity=medium`, `store=false`, `truncation=disabled` |

공통 `max_output_tokens`는 Stage 2.2에서 20,000이다. 이는 모델이 20,000 tokens를
반드시 사용하게 하는 reasoning budget이 아니라, thinking과 최종 JSON이 사용할
수 있는 hard output ceiling이다.

## 3. 가능한 값과 선택 근거

### 3.1 Claude Opus 5

| Parameter | 가능한 값 | 선택값 |
|---|---|---|
| `thinking.type` | `adaptive`, `disabled` | `adaptive` |
| `thinking.display` | `omitted`, `summarized` | `omitted` |
| `output_config.effort` | `low`, `medium`, `high`, `xhigh`, `max` | `medium` |

`thinking.type=disabled`와 `thinking.display=omitted`는 함께 사용할 수 없다.
Thinking을 끄면 표시할 thinking block이 없기 때문이다. 또한 Gemini 3.1 Pro는
thinking을 완전히 끌 수 없으므로, Claude만 `disabled`로 설정하면 main
comparison에서 reasoning 조건이 비대칭이 된다.

따라서 Claude는 adaptive thinking을 유지하되 `effort`를 기본값 `high`에서
`medium`으로 낮춘다. `display=omitted`는 reasoning 계산량을 줄이지 않고,
reasoning summary가 evaluation JSON에 노출되지 않게 한다.

적용 payload:

```python
thinking={
    "type": "adaptive",
    "display": "omitted",
}
output_config={
    "effort": "medium",
}
```

Claude Opus 5는 non-default `temperature`, `top_p`, `top_k`를 일반적인
sampling knob로 사용하지 않으므로 모두 미지정한다.

### 3.2 Gemini 3.1 Pro Preview

| Parameter | 가능한 값 | 선택값 |
|---|---|---|
| `thinking_level` | `low`, `medium`, `high` | `medium` |
| `include_thoughts` | `true`, `false` | `false` |
| `temperature` | `0.0`–`2.0` | 미지정: provider default |
| `thinking_budget` | Legacy compatibility parameter | 미사용 |

Gemini 3.1 Pro는 `minimal` thinking과 thinking 완전 비활성화를 지원하지 않는다.
Gemini 3 계열은 `thinking_budget`보다 `thinking_level` 사용이 권장된다.

`include_thoughts=false`는 thought summary를 반환하지 않는 설정이며 reasoning
계산량을 줄이지 않는다. 세 모델에 공통으로 `temperature`를 전달하지 않는
`provider_default` sampling policy를 사용한다. Gemini 3.1 Pro Preview의 현재
provider default는 `1.0`이며, Google은 이 값을 유지하고 낮은 temperature를
제거하도록 권장한다.

적용 payload:

```python
thinking_config={
    "thinking_level": "medium",
    "include_thoughts": False,
}
```

### 3.3 GPT-5.6 Sol

| Parameter | 가능한 값 | 선택값 |
|---|---|---|
| `reasoning.effort` | `none`, `low`, `medium`, `high`, `xhigh`, `max` | `medium` |
| `reasoning.mode` | `standard`, `pro` | `standard` |
| `reasoning.context` | `auto`, `current_turn`, `all_turns` | `current_turn` |
| `reasoning.summary` | `auto`, `concise`, `detailed`, 또는 미지정 | 미지정 |
| `text.verbosity` | `low`, `medium`, `high` | `medium` |
| `store` | `true`, `false` | `false` |
| `truncation` | `auto`, `disabled` | `disabled` |

Checkpoint별 evaluation request는 서로 독립적이므로 이전 API response의
reasoning을 이어받지 않는 `current_turn`을 사용한다. 추가 test-time compute를
사용하는 `pro` 대신 `standard`를 사용하며 reasoning summary는 요청하지 않는다.

`store=false`는 provider-side response 저장을 비활성화하는 운영 설정이며,
reasoning 강도를 낮추는 설정은 아니다. `truncation=disabled`는 긴 입력을
조용히 잘라내지 않고 오류로 드러내도록 한다.

적용 payload:

```python
reasoning={
    "effort": "medium",
    "mode": "standard",
    "context": "current_turn",
}
text={
    "verbosity": "medium",
}
store=False
truncation="disabled"
```

## 4. 변경 전후

| Model | 기존 `vendor_default` | 새 정책 | 실질적 변화 |
|---|---|---|---|
| Claude Opus 5 | Adaptive thinking, `high` | Adaptive thinking, `medium` | Reasoning 강도 감소 |
| Gemini 3.1 Pro Preview | Dynamic `high` | `medium` | Reasoning 강도 감소 |
| GPT-5.6 Sol | `medium`, `standard` | `medium`, `standard` | Reasoning 강도 유지; context와 저장 정책 명시 |

세 provider에서 `medium`이 동일한 token budget이나 동일한 계산량을 뜻하지는
않는다. 따라서 이 설정을 `compute-matched`라고 표현하지 않는다. 논문에서는
`provider-supported moderate reasoning configuration` 또는
`deployment-realistic moderate reasoning policy`라고 기술한다.

## 5. 논문 보고 문구

> To reflect a deployment-realistic financial chatbot setting, we used
> moderate reasoning rather than maximum test-time computation. Claude Opus 5
> used adaptive thinking at medium effort, Gemini 3.1 Pro Preview used medium
> thinking, and GPT-5.6 Sol used medium reasoning effort in standard mode.
> Reasoning summaries were not exposed to the evaluation pipeline.

## 6. 재현성과 provenance

선택값은 `experiment/configs/experiment.yaml`의
`models.generation_settings`에 고정한다. 각 provider request에 실제로 적용한
설정은 실행 결과의 `response_metadata.generation_settings`에도 기록한다.
Temperature는 세 provider 모두 request에서 생략하고 provider default를 사용한다.

Checkpoint 요청은 서로의 prediction을 보지 않는다. 각 요청마다 새 provider
client와 새 full-context method를 만들고, `S000 + 해당 checkpoint까지의
answer-free dialogue`를 처음부터 구성한다. 따라서 다섯 checkpoint를 병렬로
실행해도 뒤 checkpoint가 앞 checkpoint의 모델 출력을 활용하는 오염은 없다.
세 model도 서로 독립된 output artifact에 병렬 실행한다. 현재 smoke의 최대
동시 요청 수는 `3 models × 5 checkpoints = 15`이다.

설정 변경은 execution tree hash와 새 paid plan에 반영되어야 한다. 기존 plan을
재사용하지 말고, 이후 smoke 또는 full experiment 전에 새 plan을 생성하고
승인된 plan SHA를 사용한다.

## 7. 공식 문서

- [Claude thinking](https://platform.claude.com/docs/en/about-claude/models/extended-thinking-models)
- [Claude effort](https://platform.claude.com/docs/en/build-with-claude/effort)
- [Gemini thinking](https://ai.google.dev/gemini-api/docs/generate-content/thinking)
- [Gemini 3 Developer Guide](https://ai.google.dev/gemini-api/docs/gemini-3)
- [GPT-5.6 model guidance](https://developers.openai.com/api/docs/guides/latest-model)
- [GPT-5.6 Sol model](https://developers.openai.com/api/docs/models/gpt-5.6-sol)

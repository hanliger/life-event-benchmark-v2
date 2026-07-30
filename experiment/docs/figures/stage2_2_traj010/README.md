# Stage 2.2 `traj_010` Figure Versions

그래프는 결과를 교체하지 않고 inference policy와 paid plan SHA별 디렉터리에
보존한다. 각 디렉터리는 다음 파일을 포함한다.

- `checkpoint_metric_values.csv`: 모델·checkpoint별 원자료와 full plan SHA
- `dynamic_path_final_state_accuracy_by_checkpoint.{svg,png,pdf}`
- `correct_change_f1_by_checkpoint.{svg,png,pdf}`

| Version directory | Reasoning / Sampling | Source plan |
|---|---|---|
| `vendor_default__f84f98315cc1/` | 과거 vendor-default 실행 | `f84f98315cc1fd165734bc601808e2d10bf3ad959ef36d51ec76c9ccdfef18cd`와 Gemini checkpoint-300 format retry `feac9e9cf1e1b7a53c8339b517db8f16618295bde1516dcbe04108cea8d23667` |
| `medium_provider_default_parallel__9469ed3620c3/` | Medium reasoning, provider-default sampling, independent parallel checkpoints | `9469ed3620c32e102b4eb97f6831d291cc0f9095798c91e9fc10b140fa474b68` |

상위 디렉터리에 남아 있는 무버전 그림과 CSV는 최초 문서 참조의 호환성을 위한
legacy copy다. 새 결과는 반드시 새 version directory에 생성한다.

재생성 예:

```bash
experiment/.venv/bin/python \
  experiment/scripts/plot_stage2_2_checkpoint_metrics.py \
  --plan-id '<FULL_PLAN_SHA>' \
  --version-label '<READER-FACING_POLICY_LABEL>' \
  --output-dir \
  'experiment/docs/figures/stage2_2_traj010/<POLICY>__<PLAN_SHA_PREFIX>'
```

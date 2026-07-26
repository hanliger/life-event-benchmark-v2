# Bash Entrypoints

| Script | 역할 | Provider 호출 |
|---|---|:---:|
| `setup.sh` | 기본 가상환경 설치 | no |
| `install_all.sh` | 7개 방법 dependency 설치 | no |
| `pipeline.sh` | 데이터, 문항, 검증, plan, 집계 | no |
| `paid/run_smoke.sh` | 승인된 immutable smoke 실행 | yes |
| `paid/run_full.sh` | 승인된 immutable full 실행 | yes |
| `paid/letta_up.sh` | Letta Docker build/start/health | model call 없음 |
| `paid/letta_down.sh` | Letta Docker 종료 | no |

`pipeline.sh`는 API key를 unset하고 paid API를 강제로 비활성화한다. Paid runner도
다음 세 조건 중 하나라도 없으면 key를 읽기 전에 중단한다.

- 저장된 exact plan SHA
- 정확한 approval phrase
- `--execute-paid`

전체 명령 순서는 상위 [README](../README.md)만 따른다.

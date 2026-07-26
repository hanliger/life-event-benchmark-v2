#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
EXP_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"

docker compose -f "$EXP_ROOT/infra/letta/compose.yaml" down


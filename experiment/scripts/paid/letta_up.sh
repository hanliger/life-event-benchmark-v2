#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
EXP_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
REPO_ROOT="$(cd -- "$EXP_ROOT/.." && pwd)"

if [[ "${1:-}" != "--approval" || "${2:-}" != "I_APPROVE_LETTA_DOCKER" ]]; then
  echo "Refusing to start Letta. Required: --approval I_APPROVE_LETTA_DOCKER" >&2
  exit 2
fi
if [[ ! -f "$REPO_ROOT/.env" ]]; then
  echo "Missing $REPO_ROOT/.env" >&2
  exit 2
fi

docker compose \
  --env-file "$REPO_ROOT/.env" \
  -f "$EXP_ROOT/infra/letta/compose.yaml" \
  up -d --build

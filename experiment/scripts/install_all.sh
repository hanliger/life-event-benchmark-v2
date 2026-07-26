#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
EXP_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

if [[ ! -x "$EXP_ROOT/.venv/bin/python" ]]; then
  echo "Run ./scripts/setup.sh first." >&2
  exit 2
fi

"$EXP_ROOT/.venv/bin/python" -m pip install -r "$EXP_ROOT/requirements.lock"
echo "All provider adapters installed. No model API was called."

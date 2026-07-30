#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
EXP_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
REPO_ROOT="$(cd -- "$EXP_ROOT/.." && pwd)"

if [[ -x "$EXP_ROOT/.venv/bin/python" ]]; then
  PYTHON_BIN="$EXP_ROOT/.venv/bin/python"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi

export PYTHONPATH="$EXP_ROOT/src:$REPO_ROOT:$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
"$PYTHON_BIN" -m financial_memory_experiment.cli execute-paid-smoke "$@"

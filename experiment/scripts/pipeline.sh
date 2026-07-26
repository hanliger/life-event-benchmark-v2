#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
EXP_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd -- "$EXP_ROOT/.." && pwd)"

if [[ -x "$EXP_ROOT/.venv/bin/python" ]]; then
  PYTHON_BIN="$EXP_ROOT/.venv/bin/python"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi

export FIN_MEMORY_DISABLE_PAID_APIS=1
unset OPENAI_API_KEY ANTHROPIC_API_KEY GOOGLE_API_KEY GEMINI_API_KEY
export PYTHONPATH="$EXP_ROOT/src:$REPO_ROOT:$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

COMMAND="${1:-help}"
shift || true

case "$COMMAND" in
  prepare-all)
    "$PYTHON_BIN" -m financial_memory_experiment.cli download-data "$@"
    "$PYTHON_BIN" -m financial_memory_experiment.cli validate-raw-data
    "$PYTHON_BIN" -m financial_memory_experiment.cli prepare-data
    "$PYTHON_BIN" -m financial_memory_experiment.cli build-prefix-gold
    "$PYTHON_BIN" -m financial_memory_experiment.cli build-canonical-items
    "$PYTHON_BIN" -m financial_memory_experiment.cli build-masking-items
    "$PYTHON_BIN" -m financial_memory_experiment.cli validate-prepared-data
    ;;
  verify-offline)
    "$PYTHON_BIN" -m pytest "$EXP_ROOT/tests"
    "$PYTHON_BIN" -m financial_memory_experiment.cli dry-run
    ;;
  test)
    "$PYTHON_BIN" -m pytest "$EXP_ROOT/tests" "$@"
    ;;
  download-data|validate-raw-data|prepare-data|build-prefix-gold|build-canonical-items|build-masking-items|validate-prepared-data|dry-run|plan-paid-smoke|plan-paid-full|evaluate|aggregate)
    "$PYTHON_BIN" -m financial_memory_experiment.cli "$COMMAND" "$@"
    ;;
  help|-h|--help)
    "$PYTHON_BIN" -m financial_memory_experiment.cli --help
    ;;
  *)
    echo "Unknown pipeline command: $COMMAND" >&2
    exit 2
    ;;
esac

#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
EXP_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

if command -v python3.13 >/dev/null 2>&1; then
  PYTHON_BIN=python3.13
elif command -v python3.12 >/dev/null 2>&1; then
  PYTHON_BIN=python3.12
elif command -v python3.11 >/dev/null 2>&1; then
  PYTHON_BIN=python3.11
else
  echo "Python 3.11, 3.12, or 3.13 is required. No dependency or interpreter was installed." >&2
  exit 2
fi

if [[ ! -d "$EXP_ROOT/.venv" ]]; then
  "$PYTHON_BIN" -m venv "$EXP_ROOT/.venv"
fi

"$EXP_ROOT/.venv/bin/python" -m pip install --upgrade pip
"$EXP_ROOT/.venv/bin/python" -m pip install -e "$EXP_ROOT[test]"
echo "Offline/base environment ready: $EXP_ROOT/.venv"
echo "Paid/provider extras are intentionally not installed by this command."

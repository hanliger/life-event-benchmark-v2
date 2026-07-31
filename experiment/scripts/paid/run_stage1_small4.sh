#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
EXPERIMENT_DIR="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
REPO_DIR="$(cd -- "${EXPERIMENT_DIR}/.." && pwd)"
PYTHON_BIN="${EXPERIMENT_DIR}/.venv/bin/python"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Missing experiment virtualenv: ${PYTHON_BIN}" >&2
  echo "Run experiment/scripts/setup.sh and experiment/scripts/install_all.sh first." >&2
  exit 2
fi

export PYTHONPATH="${REPO_DIR}:${REPO_DIR}/src:${EXPERIMENT_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}"
exec "${PYTHON_BIN}" -m financial_memory_experiment.stage1_runner \
  --profile small4 "$@"

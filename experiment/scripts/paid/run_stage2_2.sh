#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
EXPERIMENT_DIR="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
REPO_DIR="$(cd -- "${EXPERIMENT_DIR}/.." && pwd)"

export PYTHONPATH="${REPO_DIR}:${REPO_DIR}/src:${EXPERIMENT_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}"
exec python -m financial_memory_experiment.stage2_2_runner "$@"

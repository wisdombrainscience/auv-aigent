#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"

exec .venv/bin/python -m pytest -q "$@"

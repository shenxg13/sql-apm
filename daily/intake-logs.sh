#!/usr/bin/env bash
# Standalone filesystem helper; use the project interpreter in generated units.
set -euo pipefail
script_dir=$(cd -- "${BASH_SOURCE[0]%/*}" && pwd)
exec "${SQL_APM_PYTHON:-python3}" "$script_dir/intake_logs.py" "$@"

#!/usr/bin/env bash
# Project-owned checks formerly run directly by the quality CI workflow.
set -euo pipefail

script_dir=$(cd -- "${BASH_SOURCE[0]%/*}" && pwd)
repo_root=$(cd -- "$script_dir/../.." && pwd)
cd "$repo_root"

python3 --version
python3 -m sql_apm.sql.function_dictionary validate rules/functions/v1.0.2.json
python3 -m unittest discover -s tests -v
python3 scripts/functions/coverage.py

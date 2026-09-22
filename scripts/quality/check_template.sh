#!/usr/bin/env bash
set -euo pipefail
script_dir=$(cd -- "${BASH_SOURCE[0]%/*}" && pwd)
repo_root=$(cd -- "$script_dir/../.." && pwd)
source_audit=false
if [[ ${1:-} == --source-audit ]]; then
    source_audit=true
    shift
fi
[[ $# -le 1 ]] || {
    printf '%s\n' 'Usage: scripts/quality/check_template.sh [--source-audit] [ROOT]' >&2
    exit 1
}
root=${1:-$repo_root}
git -C "$root" ls-files -z | node "$script_dir/check_template.mjs" "$root" "$source_audit"

#!/usr/bin/env bash

set -euo pipefail

script_dir=$(cd -- "${BASH_SOURCE[0]%/*}" && pwd)
filter="$script_dir/review_convergence.jq"

usage() {
    cat <<'EOF'
Usage: scripts/github/review_convergence.sh RECORD.json

Validate a local Review Convergence Protocol evidence record. The command is
read-only and does not contact or modify GitHub.
EOF
}

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

if [[ ${1:-} == -h || ${1:-} == --help ]]; then
    usage
    exit 0
fi

[[ $# == 1 ]] || die 'exactly one JSON record is required'
record=$1
[[ -f $record ]] || die "record does not exist: $record"
[[ -r $record ]] || die "record is not readable: $record"

if ! jq -e -f "$filter" "$record" >/dev/null; then
    die "review convergence record is invalid: $record"
fi

printf 'OK: %s satisfies the Review Convergence Protocol\n' "$record"

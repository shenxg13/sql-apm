#!/usr/bin/env bash

set -euo pipefail

script_dir=$(cd -- "${BASH_SOURCE[0]%/*}" && pwd)
repo_root=$(cd -- "$script_dir/../.." && pwd)
# shellcheck source=tool-versions.env
source "$script_dir/tool-versions.env"

usage() {
    cat <<'EOF'
Usage:
  scripts/quality/check.sh [--pr PR [--repo OWNER/REPO]]
  scripts/quality/check.sh --check-tools-only

Run the repository-owned, read-only Shell, Markdown, and workflow checks. With
--pr, also validate the live pull request contract; that network-dependent mode
must run outside the sandbox.
EOF
}

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

pr=
repo=
check_tools_only=false
while (($#)); do
    case $1 in
        --pr)
            [[ $# -ge 2 ]] || die '--pr requires a number or URL'
            pr=$2
            shift 2
            ;;
        -R | --repo)
            [[ $# -ge 2 ]] || die "$1 requires OWNER/REPO"
            repo=$2
            shift 2
            ;;
        --check-tools-only)
            check_tools_only=true
            shift
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            die "unknown argument: $1"
            ;;
    esac
done

[[ -z $repo || -n $pr ]] || die '--repo requires --pr'
if [[ $check_tools_only == true && (-n $pr || -n $repo) ]]; then
    die '--check-tools-only cannot be combined with --pr or --repo'
fi

required_tools=(
    bash git jq base64 shellcheck shfmt node npm markdownlint-cli2
)
[[ -z $pr ]] || required_tools+=(gh)
missing_tools=()
for tool in "${required_tools[@]}"; do
    command -v "$tool" >/dev/null 2>&1 || missing_tools+=("$tool")
done
if ((${#missing_tools[@]})); then
    die "missing required tools: ${missing_tools[*]}"
fi

extract_version() {
    local text=$1
    if [[ $text =~ ([0-9]+)\.([0-9]+)(\.([0-9]+))? ]]; then
        parsed_version="${BASH_REMATCH[1]}.${BASH_REMATCH[2]}.${BASH_REMATCH[4]:-0}"
        return
    fi
    die "unable to parse version from: $text"
}

version_at_least() {
    local actual=$1
    local required=$2
    local actual_parts=()
    local required_parts=()
    local index
    IFS=. read -ra actual_parts <<<"$actual"
    IFS=. read -ra required_parts <<<"$required"
    for index in 0 1 2; do
        if ((10#${actual_parts[$index]:-0} > 10#${required_parts[$index]:-0})); then
            return 0
        fi
        if ((10#${actual_parts[$index]:-0} < 10#${required_parts[$index]:-0})); then
            return 1
        fi
    done
    return 0
}

require_min_version() {
    local name=$1
    local actual=$2
    local required=$3
    version_at_least "$actual" "$required" ||
        die "$name $required or later is required, found $actual"
    printf 'tool %-20s %s (minimum %s)\n' "$name" "$actual" "$required"
}

require_exact_version() {
    local name=$1
    local actual=$2
    local required=$3
    [[ $actual == "$required" ]] ||
        die "$name $required is required, found $actual"
    printf 'tool %-20s %s (exact)\n' "$name" "$actual"
}

extract_version "$(bash --version)"
require_min_version bash "$parsed_version" "$BASH_MIN_VERSION"
extract_version "$(git --version)"
require_min_version git "$parsed_version" "$GIT_MIN_VERSION"
if [[ -n $pr ]]; then
    extract_version "$(gh --version)"
    require_min_version gh "$parsed_version" "$GH_MIN_VERSION"
fi
extract_version "$(jq --version)"
require_min_version jq "$parsed_version" "$JQ_MIN_VERSION"
base64_output=$(base64 --version)
[[ $base64_output == *'GNU coreutils'* ]] || die 'GNU base64 is required'
extract_version "$base64_output"
require_min_version 'GNU base64' "$parsed_version" "$COREUTILS_MIN_VERSION"
extract_version "$(shellcheck --version)"
require_exact_version ShellCheck "$parsed_version" "$SHELLCHECK_VERSION"
extract_version "$(shfmt --version)"
require_exact_version shfmt "$parsed_version" "$SHFMT_VERSION"
extract_version "$(node --version)"
require_exact_version Node.js "$parsed_version" "$NODE_VERSION"
extract_version "$(npm --version)"
require_exact_version npm "$parsed_version" "$NPM_VERSION"
extract_version "$(markdownlint-cli2 --version)"
require_exact_version markdownlint-cli2 "$parsed_version" \
    "$MARKDOWNLINT_CLI2_VERSION"

if [[ ${HARNESS_QUALITY_INJECT_FAILURE:-} == after-tool-check ]]; then
    die 'injected quality failure after tool check'
fi
[[ $check_tools_only == false ]] || exit 0

cd "$repo_root"
mapfile -d '' -t shell_files < <(git ls-files -z -- '*.sh')
((${#shell_files[@]})) || die 'no supported Shell scripts found'

printf '%s\n' '==> bash -n'
for file in "${shell_files[@]}"; do
    bash -n "$file"
done

printf '%s\n' '==> ShellCheck'
shellcheck -x "${shell_files[@]}"

printf '%s\n' '==> shfmt -d'
shfmt -d -i 4 -ci "${shell_files[@]}"

mapfile -d '' -t markdown_files < <(git ls-files -z -- '*.md')
((${#markdown_files[@]})) || die 'no tracked Markdown files found'
printf '%s\n' '==> markdownlint-cli2'
markdownlint-cli2 "${markdown_files[@]}"

printf '%s\n' '==> template integrity'
scripts/quality/check_template.sh

printf '%s\n' '==> offline workflow tests'
scripts/tests/test_pr_contract.sh
scripts/tests/test_issue_status.sh
scripts/tests/test_review_convergence.sh
scripts/tests/test_quality.sh
scripts/tests/test_template.sh

if [[ -n $pr ]]; then
    pr_args=()
    [[ -z $repo ]] || pr_args=(--repo "$repo")
    printf '%s\n' '==> live PR contract'
    scripts/github/pr_contract.sh "$pr" "${pr_args[@]}"
fi

printf '%s\n' 'all repository quality checks passed'

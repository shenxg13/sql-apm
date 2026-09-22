#!/usr/bin/env bash

set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  scripts/github/pr_contract.sh PR [--repo OWNER/REPO]

Read a live pull request with gh and verify the repository PR body contract.
The command is read-only and must run outside the sandbox when it contacts
GitHub.
EOF
}

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

command -v gh >/dev/null 2>&1 || die 'gh is required'
command -v base64 >/dev/null 2>&1 || die 'GNU base64 is required'

pr=${1:-}
case $pr in
    '' | -h | --help)
        usage
        [[ -n $pr ]] && exit 0
        exit 2
        ;;
esac
shift

repo=
while (($#)); do
    case $1 in
        -R | --repo)
            [[ $# -ge 2 ]] || die "$1 requires OWNER/REPO"
            repo=$2
            shift 2
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

repo_args=()
[[ -z $repo ]] || repo_args=(--repo "$repo")

pr_output=
if ! pr_output=$(gh pr view "$pr" "${repo_args[@]}" \
    --json url,baseRefName,body \
    --jq '.url, .baseRefName, ((.body // "") | if . == "" then "-" else @base64 end)'); then
    die "unable to read pull request $pr"
fi

mapfile -t pr_data <<<"$pr_output"
((${#pr_data[@]} == 3)) || die "unexpected response for pull request $pr"
pr_url=${pr_data[0]}
base_branch=${pr_data[1]}
body_base64=${pr_data[2]}

path=${pr_url#https://github.com/}
IFS=/ read -r pr_owner pr_repo resource pr_number extra <<<"$path"
if [[ $pr_url != https://github.com/* || $resource != pull ||
    ! $pr_number =~ ^[0-9]+$ || -n $extra ]]; then
    die "$pr_url is not a canonical GitHub pull request URL"
fi
resolved_repo="$pr_owner/$pr_repo"
[[ $base_branch == main ]] || die "$pr_url must target main, found $base_branch"

[[ $body_base64 != - ]] || die "$pr_url has an empty body"
if ! pr_body=$(printf '%s' "$body_base64" | base64 --decode); then
    die "unable to decode body for $pr_url"
fi
pr_body=${pr_body//$'\r'/}

strip_html_comments() {
    awk '
        {
            line = $0
            output = ""
            while (length(line)) {
                if (in_comment) {
                    end = index(line, "-->")
                    if (!end) {
                        line = ""
                    } else {
                        line = substr(line, end + 3)
                        in_comment = 0
                    }
                } else {
                    start = index(line, "<!--")
                    if (!start) {
                        output = output line
                        line = ""
                    } else {
                        output = output substr(line, 1, start - 1)
                        line = substr(line, start + 4)
                        in_comment = 1
                    }
                }
            }
            print output
        }
    '
}

extract_section() {
    local heading=$1
    awk -v heading="$heading" '
        $0 == heading { inside = 1; next }
        inside && /^## / { exit }
        inside { print }
    ' <<<"$pr_body"
}

trim_text() {
    trimmed=$1
    trimmed=${trimmed#"${trimmed%%[![:space:]]*}"}
    trimmed=${trimmed%"${trimmed##*[![:space:]]}"}
}

required_headings=(
    '## 关联 Issue'
    '## 实施内容'
    '## 合并前验收'
    '## 合并后验收'
    '## 验证结果'
    '## 知识与流程同步'
    '## 后续事项与风险'
)
previous_line=0
for heading in "${required_headings[@]}"; do
    mapfile -t matches < <(grep -nFx -- "$heading" <<<"$pr_body" || true)
    ((${#matches[@]} == 1)) ||
        die "$pr_url must contain exactly one '$heading' heading"
    heading_line=${matches[0]%%:*}
    ((heading_line > previous_line)) ||
        die "$pr_url has required headings in the wrong order"
    previous_line=$heading_line
done

clean_body=$(strip_html_comments <<<"$pr_body")
if grep -Eiq '^##[[:space:]]*((当前|执行|PR|Issue)[[:space:]]*)?状态([[:space:]]|$)' \
    <<<"$clean_body" ||
    grep -Eiq '^[[:space:]]*[-*]?[[:space:]]*(Draft|Ready|status:[[:alnum:]_-]+)[[:space:]]*[:：=]' \
        <<<"$clean_body"; then
    die "$pr_url must not store mutable Draft, Ready, or status:* state"
fi

if grep -Eiq '(^|[[:space:][:punct:]])(close[sd]?|fix(e[sd])?|resolve[sd]?)[[:space:]]+(#[0-9]+|https://github\.com/[^/]+/[^/]+/issues/[0-9]+)' \
    <<<"$clean_body"; then
    die "$pr_url must use Refs #N and must not use an auto-closing keyword"
fi

mapfile -t ref_lines < <(
    grep -E '^[[:space:]]*Refs[[:space:]]+#[0-9]+[[:space:]]*$' \
        <<<"$clean_body" || true
)
((${#ref_lines[@]} == 1)) || die "$pr_url must contain exactly one 'Refs #N' line"
[[ ${ref_lines[0]} =~ \#([0-9]+) ]] || die "$pr_url has an invalid Refs line"
linked_issue=${BASH_REMATCH[1]}

issue_url=
if ! issue_url=$(gh issue view "$linked_issue" --repo "$resolved_repo" \
    --json url --jq '.url'); then
    die "unable to read linked Issue #$linked_issue in $resolved_repo"
fi
expected_issue_url="https://github.com/$resolved_repo/issues/$linked_issue"
[[ $issue_url == "$expected_issue_url" ]] ||
    die "Refs #$linked_issue did not resolve to $expected_issue_url"

for heading in '## 实施内容' '## 合并后验收' \
    '## 知识与流程同步' '## 后续事项与风险'; do
    section=$(extract_section "$heading" | strip_html_comments)
    [[ -n ${section//[[:space:]]/} ]] || die "$pr_url has an empty '$heading' section"
done

acceptance=$(extract_section '## 合并前验收' | strip_html_comments)
if ! grep -Eq "^[[:space:]]*-[[:space:]]+\\[[ xX]\\][[:space:]]+Issue[[:space:]]+#${linked_issue}([[:space:]:：]|$)" \
    <<<"$acceptance"; then
    die "$pr_url must map at least one acceptance item to Issue #$linked_issue"
fi

verification=$(extract_section '## 验证结果' | strip_html_comments)
command_record=$(grep -E '^[[:space:]]*-[[:space:]]*命令或方法[：:]' \
    <<<"$verification" | head -n 1 || true)
result_record=$(grep -E '^[[:space:]]*-[[:space:]]*结果[：:]' \
    <<<"$verification" | head -n 1 || true)
trim_text "${command_record#*[：:]}"
[[ -n $trimmed ]] || die "$pr_url must record an executed command or check method"
trim_text "${result_record#*[：:]}"
[[ -n $trimmed ]] || die "$pr_url must record the verification result"

printf 'OK: %s satisfies the read-only PR contract and references %s\n' \
    "$pr_url" "$expected_issue_url"

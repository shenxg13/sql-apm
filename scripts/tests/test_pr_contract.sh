#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd -- "${BASH_SOURCE[0]%/*}/../.." && pwd)
test_root=$(mktemp -d "${TMPDIR:-/tmp}/agent-harness-pr-contract.XXXXXX")
fake_bin="$test_root/bin"
fake_log="$test_root/gh.log"
mkdir -p "$fake_bin"

cleanup() {
    rm -rf -- "$test_root"
}
trap cleanup EXIT

cat >"$fake_bin/gh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

printf '%q ' "$@" >>"$FAKE_GH_LOG"
printf '\n' >>"$FAKE_GH_LOG"

command_name=${1:-}
subcommand=${2:-}

valid_body() {
    cat <<'BODY'
## 关联 Issue

Refs #16

## 实施内容

完善协作流程与只读质量检查。

## 合并前验收

- [x] Issue #16 验收项：PR 契约检查已通过。

## 合并后验收

无。

## 验证结果

- 命令或方法：`scripts/quality/check.sh`
- 结果：全部检查通过。

## 知识与流程同步

已更新流程文档；产品知识未改变。

## 后续事项与风险

当前没有已知后续事项或风险。
BODY
}

case "$command_name $subcommand" in
    'pr view')
        base=main
        case $FAKE_PR_PROFILE in
            valid | wrong_ref | auto_close | mutable_state)
                body=$(valid_body)
                ;;
            english)
                body=$'## Related Issue\n\nRefs #16\n\n## Implementation\n\nEnglish body.'
                ;;
            custom)
                body=$'Refs #16\n\nCustom body that bypasses every required section.'
                ;;
            wrong_base)
                body=$(valid_body)
                base=develop
                ;;
            *)
                printf 'unknown profile: %s\n' "$FAKE_PR_PROFILE" >&2
                exit 2
                ;;
        esac
        [[ $FAKE_PR_PROFILE != wrong_ref ]] || body=${body//Refs #16/Refs #99}
        [[ $FAKE_PR_PROFILE != auto_close ]] || body+=$'\n\nCloses #16\n'
        [[ $FAKE_PR_PROFILE != mutable_state ]] || body+=$'\n\n## 当前状态\n\nDraft: true\n'
        body_base64=$(printf '%s' "$body" | base64 -w 0)
        printf '%s\n%s\n%s\n' \
            'https://github.com/example/project/pull/88' \
            "$base" "$body_base64"
        ;;
    'issue view')
        issue_number=${3:-}
        if [[ $FAKE_PR_PROFILE == wrong_ref ]]; then
            printf '%s\n' 'https://github.com/example/project/issues/100'
        else
            printf 'https://github.com/example/project/issues/%s\n' \
                "$issue_number"
        fi
        ;;
    *)
        printf 'unexpected fake gh command: %s %s\n' \
            "$command_name" "$subcommand" >&2
        exit 2
        ;;
esac
EOF
chmod +x "$fake_bin/gh"

export FAKE_GH_LOG=$fake_log
export PATH="$fake_bin:$PATH"
command_under_test="$repo_root/scripts/github/pr_contract.sh"
template="$repo_root/.github/pull_request_template.md"

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

assert_read_only() {
    grep -q '^pr view ' "$fake_log" || fail 'expected a PR read'
    if grep -Eq '^(api |pr (edit|comment|close|merge)|issue (edit|comment|close))' \
        "$fake_log"; then
        fail "unexpected GitHub write: $(<"$fake_log")"
    fi
}

run_profile() {
    export FAKE_PR_PROFILE=$1
    : >"$fake_log"
    "$command_under_test" 88 --repo example/project \
        >"$test_root/stdout" 2>"$test_root/stderr"
}

for heading in \
    '## 关联 Issue' '## 实施内容' '## 合并前验收' '## 合并后验收' \
    '## 验证结果' '## 知识与流程同步' '## 后续事项与风险'; do
    [[ $(grep -Fxc -- "$heading" "$template") == 1 ]] ||
        fail "template is missing required heading: $heading"
done
if grep -Eiq '(^|[^[:alnum:]_])(Draft|Ready|status:[[:alnum:]_-]+)' "$template"; then
    fail 'template stores mutable execution state'
fi
grep -Fq 'Issue #N 验收项' "$template" || fail 'template lacks acceptance mapping'
grep -Fq -- '- 命令或方法：' "$template" || fail 'template lacks command record'
grep -Fq -- '- 结果：' "$template" || fail 'template lacks result record'

run_profile valid
grep -Fq 'satisfies the read-only PR contract' "$test_root/stdout" ||
    fail 'valid filled template did not pass'
assert_read_only

for profile in english custom wrong_ref auto_close wrong_base mutable_state; do
    if run_profile "$profile"; then
        fail "profile $profile unexpectedly passed"
    fi
    assert_read_only
done

printf '%s\n' 'PR contract tests passed'

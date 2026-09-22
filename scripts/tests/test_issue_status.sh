#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
test_root=$(mktemp -d "${TMPDIR:-/tmp}/agent-harness-issue-status.XXXXXX")
fake_bin="$test_root/bin"
fake_state="$test_root/state"
fake_log="$test_root/gh.log"
fake_write_count="$test_root/write-count"
fake_fail_step="$test_root/fail-step"
fake_api_mode="$test_root/api-mode"
fake_fail_next_read="$test_root/fail-next-read"
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

IFS='|' read -r issue_state issue_reason issue_labels issue_kind body_profile \
    <"$FAKE_GH_STATE"
command_name=${1:-}
subcommand=${2:-}
shift 2

body_for_profile() {
    case $body_profile in
        valid)
            printf '%s\n' \
                '## 验收标准' \
                '' \
                '- [ ] 返回可验证结果。' \
                '' \
                '## 需求确认' \
                '' \
                '- [x] 用户已确认目标、范围、非目标和验收标准。'
            ;;
        english_valid)
            printf '%s\n' \
                '## 验收标准' \
                '' \
                '- [ ] Returns a verifiable result.' \
                '' \
                '## 需求确认' \
                '' \
                '- [x] 用户已确认目标、范围、非目标和验收标准。'
            ;;
        blank)
            :
            ;;
        whitespace_body)
            printf '      \n\n   \n'
            ;;
        html_body)
            printf '%s\n' '<!-- entire body is only a comment -->'
            ;;
        empty_acceptance)
            printf '%s\n' \
                '## 验收标准' '' \
                '## 需求确认' '' \
                '- [x] 用户已确认目标、范围、非目标和验收标准。'
            ;;
        whitespace_item)
            printf '%s\n' \
                '## 验收标准' '' '- [ ]       ' '' \
                '## 需求确认' '' \
                '- [x] 用户已确认目标、范围、非目标和验收标准。'
            ;;
        html_item)
            printf '%s\n' \
                '## 验收标准' '' '- [ ] <!-- 尚无验收内容 -->' '' \
                '## 需求确认' '' \
                '- [x] 用户已确认目标、范围、非目标和验收标准。'
            ;;
        comment_only)
            printf '%s\n' \
                '## 验收标准' '' '<!-- 尚无验收内容 -->' '' \
                '## 需求确认' '' \
                '- [x] 用户已确认目标、范围、非目标和验收标准。'
            ;;
        todo|tbd|tbc)
            marker=${body_profile^^}
            printf '%s\n' \
                '## 验收标准' '' "- [ ] $marker" '' \
                '## 需求确认' '' \
                '- [x] 用户已确认目标、范围、非目标和验收标准。'
            ;;
        pending_cn)
            printf '%s\n' \
                '## 验收标准' '' '- [ ] 待补充。' '' \
                '## 需求确认' '' \
                '- [x] 用户已确认目标、范围、非目标和验收标准。'
            ;;
        placeholder)
            printf '%s\n' \
                '## 验收标准' '' '- [ ] 将本项替换为可验证的结果。' '' \
                '## 需求确认' '' \
                '- [x] 用户已确认目标、范围、非目标和验收标准。'
            ;;
        unconfirmed)
            printf '%s\n' \
                '## 验收标准' '' '- [ ] 返回可验证结果。' '' \
                '## 需求确认' '' \
                '- [ ] 用户已确认目标、范围、非目标和验收标准。'
            ;;
        loose_confirm)
            printf '%s\n' \
                '## 验收标准' '' '- [ ] 返回可验证结果。' '' \
                '## 需求确认' '' '- [x] 用户确认。'
            ;;
        negative_confirm)
            printf '%s\n' \
                '## 验收标准' '' '- [ ] 返回可验证结果。' '' \
                '## 需求确认' '' \
                '- [x] 用户已确认目标、范围、非目标和验收标准。' \
                '- 用户尚未确认最终范围。'
            ;;
        negative_unconfirmed)
            printf '%s\n' \
                '## 验收标准' '' '- [ ] 返回可验证结果。' '' \
                '## 需求确认' '' \
                '- [x] 用户已确认目标、范围、非目标和验收标准。' \
                '- 用户未确认最终范围。'
            ;;
        *)
            printf 'unknown body profile: %s\n' "$body_profile" >&2
            exit 2
            ;;
    esac
}

maybe_fail_write() {
    local count=0
    local fail_step=0
    [[ ! -f $FAKE_GH_WRITE_COUNT ]] || read -r count <"$FAKE_GH_WRITE_COUNT"
    count=$((count + 1))
    printf '%s\n' "$count" >"$FAKE_GH_WRITE_COUNT"
    [[ ! -s $FAKE_GH_FAIL_STEP ]] || read -r fail_step <"$FAKE_GH_FAIL_STEP"
    if [[ $fail_step == "$count" ]]; then
        printf 'injected write failure at step %s\n' "$count" >&2
        exit 99
    fi
}

write_state() {
    printf '%s|%s|%s|%s|%s\n' \
        "$issue_state" "$issue_reason" "$issue_labels" "$issue_kind" \
        "$body_profile" >"$FAKE_GH_STATE"
}

ordinary_labels() {
    local labels=()
    local label
    IFS=',' read -ra labels <<<"$issue_labels"
    ordinary=()
    for label in "${labels[@]}"; do
        [[ -z $label || $label == status:* ]] || ordinary+=("$label")
    done
}

apply_api_payload() {
    local payload=$1
    local new_state
    local new_reason

    if jq -e 'has("labels")' >/dev/null <<<"$payload"; then
        issue_labels=$(jq -r '.labels | join(",")' <<<"$payload")
    fi
    new_state=$(jq -r '.state // empty' <<<"$payload")
    case $new_state in
        open)
            issue_state=OPEN
            ;;
        closed)
            issue_state=CLOSED
            ;;
    esac
    new_reason=$(jq -r '.state_reason // empty' <<<"$payload")
    case $new_reason in
        completed)
            issue_reason=COMPLETED
            ;;
        not_planned)
            issue_reason=NOT_PLANNED
            ;;
        reopened)
            issue_reason=REOPENED
            ;;
    esac
    write_state
}

case "$command_name $subcommand" in
    'issue view')
        if [[ -s $FAKE_GH_FAIL_NEXT_READ ]] && \
            [[ $(<"$FAKE_GH_FAIL_NEXT_READ") == 1 ]]; then
            printf '0\n' >"$FAKE_GH_FAIL_NEXT_READ"
            printf 'injected read failure\n' >&2
            exit 98
        fi
        if [[ $issue_kind == pr ]]; then
            issue_url=https://github.com/example/project/pull/123
        else
            issue_url=https://github.com/example/project/issues/123
        fi
        body_base64=$(body_for_profile | base64 -w 0)
        [[ -n $body_base64 ]] || body_base64=-
        printf '%s\n%s\n%s\n%s\n' \
            "$issue_state" "$issue_reason" "$issue_url" "$body_base64"
        IFS=',' read -ra labels <<<"$issue_labels"
        for label in "${labels[@]}"; do
            [[ -z $label ]] || printf '%s\n' "$label"
        done
        ;;
    'issue comment')
        maybe_fail_write
        ;;
    'api --method')
        maybe_fail_write
        method=${1:-}
        endpoint=${2:-}
        payload=$(jq -c .)
        case $method in
            PATCH)
                [[ $endpoint != */labels ]] || exit 2
                jq -e '.state == "closed" and .state_reason == "not_planned" and (.labels | type == "array")' \
                    >/dev/null <<<"$payload" || exit 2
                ;;
            PUT)
                [[ $endpoint == */labels ]] || exit 2
                jq -e '(.labels | type == "array") and (has("state") | not) and (has("state_reason") | not)' \
                    >/dev/null <<<"$payload" || exit 2
                ;;
            *)
                exit 2
                ;;
        esac
        mode=$(<"$FAKE_GH_API_MODE")
        case $mode in
            normal)
                apply_api_payload "$payload"
                ;;
            fail_before)
                printf 'injected API failure before apply\n' >&2
                exit 97
                ;;
            apply_then_fail)
                apply_api_payload "$payload"
                printf 'injected API error after apply\n' >&2
                exit 97
                ;;
            fail_then_read_error)
                printf '1\n' >"$FAKE_GH_FAIL_NEXT_READ"
                printf 'injected API failure before unreadable result\n' >&2
                exit 97
                ;;
            apply_then_read_error)
                apply_api_payload "$payload"
                printf '1\n' >"$FAKE_GH_FAIL_NEXT_READ"
                ;;
            success_noop)
                :
                ;;
            success_override)
                ordinary_labels
                issue_state=OPEN
                issue_reason=
                ordinary+=(status:triage)
                issue_labels=$(IFS=,; printf '%s' "${ordinary[*]}")
                write_state
                ;;
            fail_override)
                ordinary_labels
                issue_state=CLOSED
                issue_reason=NOT_PLANNED
                ordinary+=(status:blocked)
                issue_labels=$(IFS=,; printf '%s' "${ordinary[*]}")
                write_state
                printf 'injected API failure with unexpected remote state\n' >&2
                exit 97
                ;;
            *)
                printf 'unknown fake API mode: %s\n' "$mode" >&2
                exit 2
                ;;
        esac
        [[ -n $method && -n $endpoint ]] || exit 2
        ;;
    *)
        printf 'unexpected fake gh command: %s %s\n' \
            "$command_name" "$subcommand" >&2
        exit 2
        ;;
esac
EOF
chmod +x "$fake_bin/gh"

export FAKE_GH_STATE=$fake_state
export FAKE_GH_LOG=$fake_log
export FAKE_GH_WRITE_COUNT=$fake_write_count
export FAKE_GH_FAIL_STEP=$fake_fail_step
export FAKE_GH_API_MODE=$fake_api_mode
export FAKE_GH_FAIL_NEXT_READ=$fake_fail_next_read
export PATH="$fake_bin:$PATH"
command_under_test="$repo_root/scripts/github/issue_status.sh"

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

assert_fails() {
    if "$@" >"$test_root/stdout" 2>"$test_root/stderr"; then
        printf 'expected command to fail: %q ' "$@" >&2
        printf '\n' >&2
        exit 1
    fi
}

assert_stderr_contains() {
    grep -Fq -- "$1" "$test_root/stderr" ||
        fail "stderr did not contain '$1': $(<"$test_root/stderr")"
}

assert_stdout_contains() {
    grep -Fq -- "$1" "$test_root/stdout" ||
        fail "stdout did not contain '$1': $(<"$test_root/stdout")"
}

set_state() {
    local state=$1
    local reason=$2
    local labels=$3
    local kind=${4:-issue}
    local body=${5:-valid}
    printf '%s|%s|%s|%s|%s\n' \
        "$state" "$reason" "$labels" "$kind" "$body" >"$fake_state"
    : >"$fake_log"
    printf '0\n' >"$fake_write_count"
    : >"$fake_fail_step"
    printf 'normal\n' >"$fake_api_mode"
    printf '0\n' >"$fake_fail_next_read"
}

inject_failure() {
    printf '%s\n' "$1" >"$fake_fail_step"
}

set_api_mode() {
    printf '%s\n' "$1" >"$fake_api_mode"
}

assert_state() {
    local expected=$1
    local actual
    actual=$(<"$fake_state")
    [[ $actual == "$expected" ]] ||
        fail "expected state '$expected', got '$actual'"
}

write_count() {
    local count=0
    [[ ! -f $fake_write_count ]] || read -r count <"$fake_write_count"
    printf '%s\n' "$count"
}

assert_zero_writes() {
    [[ $(write_count) == 0 ]] || fail "expected zero writes; log: $(<"$fake_log")"
}

assert_read_only_log() {
    assert_zero_writes
    grep -q '^issue view ' "$fake_log" || fail 'expected an issue view read'
    if grep -Eq '^(issue (comment|edit|close)|api )' "$fake_log"; then
        fail "unexpected write in log: $(<"$fake_log")"
    fi
}

claim_comment='owner=codex; branch=issue-3-test; handoff=explicit test handoff'
blocked_comment='reason=credentials unavailable; unblock=credentials granted'
triage_comment='reason=requirement contract changed'
not_planned_comment='reason=duplicate requirement tracked elsewhere'
repair_triage_comment='reason=normalize invalid labels; evidence=remote state inspected; target=triage'
repair_planned_comment='reason=normalize invalid labels; evidence=confirmed body contract; target=planned'
force_review_comment='reason=repair skipped transition; evidence=PR is ready; target=needs-review'
force_claim_comment='reason=explicit owner handoff; evidence=no concurrent owner; target=in-progress; owner=codex; branch=issue-3-test; handoff=review remediation'
repair_not_planned_comment='reason=remove residual status label; evidence=issue is closed not planned; target=not-planned'

# Ordinary Issue numbers, URLs, and --repo are accepted.
set_state OPEN '' 'enhancement,status:planned'
"$command_under_test" check 123 --repo example/project >/dev/null
"$command_under_test" check \
    https://github.com/example/project/issues/123 >/dev/null
set_state OPEN '' 'enhancement,status:planned'
"$command_under_test" set \
    https://github.com/example/project/issues/123 in-progress \
    --comment "$claim_comment" >/dev/null
assert_state 'OPEN||enhancement,status:in-progress|issue|valid'

# PR numbers and /pull/N URLs are rejected before every possible write.
for target in 123 https://github.com/example/project/pull/123; do
    set_state OPEN '' 'status:planned' pr
    assert_fails "$command_under_test" check "$target" --repo example/project
    assert_read_only_log

    for status in triage planned in-progress blocked needs-review 'done' not-planned; do
        set_state OPEN '' 'status:planned' pr
        assert_fails "$command_under_test" set "$target" "$status" \
            --repo example/project --comment x
        assert_read_only_log
    done

    set_state OPEN '' 'status:planned' pr
    assert_fails "$command_under_test" repair "$target" triage \
        --repo example/project --comment x
    assert_read_only_log
done

# The planned contract is enforced when setting, checking, and claiming.
invalid_profiles=(
    blank whitespace_body html_body empty_acceptance whitespace_item html_item comment_only
    todo tbd tbc pending_cn placeholder unconfirmed loose_confirm negative_confirm
    negative_unconfirmed
)
for profile in "${invalid_profiles[@]}"; do
    set_state OPEN '' 'status:triage' issue "$profile"
    assert_fails "$command_under_test" set 123 planned
    assert_read_only_log

    set_state OPEN '' 'status:planned' issue "$profile"
    assert_fails "$command_under_test" check 123
    assert_read_only_log

    set_state OPEN '' 'status:planned' issue "$profile"
    assert_fails "$command_under_test" set 123 in-progress \
        --comment "$claim_comment"
    assert_read_only_log
done

set_state OPEN '' 'documentation,status:triage' issue english_valid
"$command_under_test" set 123 planned >/dev/null
assert_state 'OPEN||documentation,status:planned|issue|english_valid'
"$command_under_test" check 123 >/dev/null

# Structured audit comments reject free text, missing fields, blank fields, and
# repair/force attempts that omit either repair or target-specific fields.
invalid_claim_comments=(
    'x'
    'owner=codex; branch=issue-3-test'
    'owner= ; branch=issue-3-test; handoff=handoff'
    'owner=codex; branch= ; handoff=handoff'
    'owner=codex; branch=issue-3-test; handoff= '
)
for invalid_comment in "${invalid_claim_comments[@]}"; do
    set_state OPEN '' 'status:planned'
    assert_fails "$command_under_test" set 123 in-progress \
        --comment "$invalid_comment"
    assert_read_only_log
done

for invalid_comment in 'reason=waiting' 'reason=waiting; unblock= '; do
    set_state OPEN '' 'status:in-progress'
    assert_fails "$command_under_test" set 123 blocked \
        --comment "$invalid_comment"
    assert_read_only_log
done

set_state OPEN '' 'status:in-progress'
assert_fails "$command_under_test" set 123 needs-review --comment x
assert_read_only_log

set_state OPEN '' 'status:triage'
assert_fails "$command_under_test" set 123 not-planned --comment x
assert_read_only_log

set_state OPEN '' 'status:planned,status:blocked'
assert_fails "$command_under_test" repair 123 triage \
    --comment 'reason=repair; target=triage'
assert_read_only_log

set_state OPEN '' 'status:triage'
assert_fails "$command_under_test" set 123 in-progress --force \
    --comment 'reason=handoff; evidence=review; target=in-progress'
assert_read_only_log

set_state OPEN '' 'status:triage'
assert_fails "$command_under_test" set 123 in-progress --force \
    --comment "$claim_comment"
assert_read_only_log

set_state OPEN '' 'status:triage'
assert_fails "$command_under_test" set 123 needs-review --force \
    --comment 'reason=repair; evidence=inspection; target=triage'
assert_read_only_log

set_state OPEN '' 'status:planned,status:blocked'
assert_fails "$command_under_test" repair 123 blocked \
    --comment 'reason=repair; evidence=inspection; target=blocked'
assert_read_only_log

set_state OPEN '' 'status:triage' issue placeholder
assert_fails "$command_under_test" repair 123 planned \
    --comment "$repair_planned_comment"
assert_read_only_log

is_allowed_transition() {
    case "$1:$2" in
        triage:planned | triage:not-planned | \
            planned:in-progress | planned:triage | planned:not-planned | \
            in-progress:blocked | in-progress:needs-review | in-progress:triage | in-progress:not-planned | \
            blocked:in-progress | blocked:triage | blocked:not-planned | \
            needs-review:in-progress | needs-review:triage | needs-review:not-planned)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

transition_args() {
    local target=$1
    TRANSITION_ARGS=()
    case $target in
        in-progress)
            TRANSITION_ARGS=(--comment "$claim_comment")
            ;;
        triage)
            TRANSITION_ARGS=(--comment "$triage_comment")
            ;;
        blocked)
            TRANSITION_ARGS=(--comment "$blocked_comment")
            ;;
        not-planned)
            TRANSITION_ARGS=(--comment "$not_planned_comment")
            ;;
    esac
}

# All allowed and rejected normal transitions are table-tested. Rejections
# perform no writes and every success preserves ordinary labels.
sources=(triage planned in-progress blocked needs-review)
targets=(triage planned in-progress blocked needs-review 'done' not-planned)
for source in "${sources[@]}"; do
    for target in "${targets[@]}"; do
        set_state OPEN '' "enhancement,status:$source" issue valid
        transition_args "$target"
        if is_allowed_transition "$source" "$target"; then
            "$command_under_test" set 123 "$target" \
                "${TRANSITION_ARGS[@]}" >/dev/null
            if [[ $target == not-planned ]]; then
                assert_state 'CLOSED|NOT_PLANNED|enhancement|issue|valid'
            else
                assert_state "OPEN||enhancement,status:$target|issue|valid"
            fi
        else
            assert_fails "$command_under_test" set 123 "$target" \
                "${TRANSITION_ARGS[@]}"
            assert_read_only_log
            assert_state "OPEN||enhancement,status:$source|issue|valid"
        fi
    done
done

# blocked records a complete reason/unblock comment before its atomic label
# write. Either write failure keeps the original legal state.
set_state OPEN '' 'enhancement,status:in-progress'
inject_failure 1
assert_fails "$command_under_test" set 123 blocked --comment "$blocked_comment"
assert_state 'OPEN||enhancement,status:in-progress|issue|valid'
grep -q '^issue comment ' "$fake_log" || fail 'blocked did not comment first'
if grep -q '^api ' "$fake_log"; then
    fail 'blocked wrote labels after a failed comment'
fi

set_state OPEN '' 'enhancement,status:in-progress'
inject_failure 2
assert_fails "$command_under_test" set 123 blocked --comment "$blocked_comment"
assert_state 'OPEN||enhancement,status:in-progress|issue|valid'
: >"$fake_fail_step"
"$command_under_test" check 123 >/dev/null

# A successful API report is insufficient: unchanged, concurrently overwritten,
# or unverifiable final state must fail the requested-target assertion.
set_state OPEN '' 'enhancement,status:in-progress'
set_api_mode success_noop
assert_fails "$command_under_test" set 123 needs-review
assert_stderr_contains 'did not reach requested target needs-review'
assert_stderr_contains 'may have been concurrently overwritten'
assert_state 'OPEN||enhancement,status:in-progress|issue|valid'

set_state OPEN '' 'enhancement,status:planned'
set_api_mode success_override
assert_fails "$command_under_test" set 123 in-progress --comment "$claim_comment"
assert_stderr_contains 'final legal state is OPEN/none labels=enhancement,status:triage'
assert_state 'OPEN||enhancement,status:triage|issue|valid'

set_state OPEN '' 'enhancement,status:in-progress'
set_api_mode apply_then_read_error
assert_fails "$command_under_test" set 123 needs-review
assert_stderr_contains 'unable to verify requested target needs-review'
assert_state 'OPEN||enhancement,status:needs-review|issue|valid'

# not-planned uses one PATCH after its audit comment. Cover pre-apply failure,
# apply-then-error, unreadable outcomes, unexpected outcomes, success-noop, and
# normal success.
set_state OPEN '' 'enhancement,status:triage'
inject_failure 1
assert_fails "$command_under_test" set 123 not-planned \
    --comment "$not_planned_comment"
assert_state 'OPEN||enhancement,status:triage|issue|valid'

set_state OPEN '' 'enhancement,status:triage'
set_api_mode fail_before
assert_fails "$command_under_test" set 123 not-planned \
    --comment "$not_planned_comment"
assert_stderr_contains 'original legal state remains'
assert_state 'OPEN||enhancement,status:triage|issue|valid'

set_state OPEN '' 'enhancement,status:triage'
set_api_mode apply_then_fail
"$command_under_test" set 123 not-planned \
    --comment "$not_planned_comment" >"$test_root/stdout" 2>"$test_root/stderr"
assert_stdout_contains 'although PATCH reported an error'
assert_state 'CLOSED|NOT_PLANNED|enhancement|issue|valid'

set_state OPEN '' 'enhancement,status:triage'
set_api_mode fail_then_read_error
assert_fails "$command_under_test" set 123 not-planned \
    --comment "$not_planned_comment"
assert_stderr_contains 'remote outcome is unknown because reread failed'
assert_state 'OPEN||enhancement,status:triage|issue|valid'

set_state OPEN '' 'enhancement,status:triage'
set_api_mode apply_then_read_error
assert_fails "$command_under_test" set 123 not-planned \
    --comment "$not_planned_comment"
assert_stderr_contains 'unable to verify requested target not-planned'
assert_state 'CLOSED|NOT_PLANNED|enhancement|issue|valid'

set_state OPEN '' 'enhancement,status:triage'
set_api_mode fail_override
assert_fails "$command_under_test" set 123 not-planned \
    --comment "$not_planned_comment"
assert_stderr_contains 'explicit repair is required'
assert_state 'CLOSED|NOT_PLANNED|enhancement,status:blocked|issue|valid'

set_state OPEN '' 'enhancement,status:triage'
set_api_mode success_noop
assert_fails "$command_under_test" set 123 not-planned \
    --comment "$not_planned_comment"
assert_stderr_contains 'did not reach requested target not-planned'
assert_state 'OPEN||enhancement,status:triage|issue|valid'

set_state OPEN '' 'enhancement,status:triage'
"$command_under_test" set 123 not-planned \
    --comment "$not_planned_comment" >/dev/null
assert_state 'CLOSED|NOT_PLANNED|enhancement|issue|valid'
[[ $(grep -c '^api --method PATCH ' "$fake_log") == 1 ]] ||
    fail "not-planned did not use exactly one PATCH: $(<"$fake_log")"
if grep -Eq '^api --method PUT |^issue close ' "$fake_log"; then
    fail "not-planned used a split write: $(<"$fake_log")"
fi
"$command_under_test" check 123 >/dev/null

# Explicit repair normalizes CLOSED/NOT_PLANNED with residual status labels.
set_state CLOSED NOT_PLANNED 'documentation,status:blocked'
assert_fails "$command_under_test" check 123
assert_read_only_log
set_state CLOSED NOT_PLANNED 'documentation,status:blocked'
"$command_under_test" repair 123 not-planned \
    --comment "$repair_not_planned_comment" >/dev/null
assert_state 'CLOSED|NOT_PLANNED|documentation|issue|valid'
[[ $(grep -c '^api --method PATCH ' "$fake_log") == 1 ]] ||
    fail "not-planned repair did not use exactly one PATCH: $(<"$fake_log")"
"$command_under_test" check 123 >/dev/null

set_state CLOSED NOT_PLANNED 'documentation,status:blocked'
set_api_mode fail_before
assert_fails "$command_under_test" repair 123 not-planned \
    --comment "$repair_not_planned_comment"
assert_stderr_contains 'explicit repair is required'
assert_state 'CLOSED|NOT_PLANNED|documentation,status:blocked|issue|valid'

# completed requires manual completed closure first; done becomes the only
# status label, while active transitions from closed work remain read-only.
set_state OPEN '' 'documentation,status:needs-review'
assert_fails "$command_under_test" set 123 'done'
assert_read_only_log
set_state CLOSED COMPLETED 'documentation,status:needs-review'
"$command_under_test" set 123 'done' >/dev/null
assert_state 'CLOSED|COMPLETED|documentation,status:done|issue|valid'
"$command_under_test" check 123 >/dev/null
set_state CLOSED COMPLETED 'documentation,status:needs-review'
set_api_mode success_noop
assert_fails "$command_under_test" set 123 'done'
assert_stderr_contains 'did not reach requested target done'
assert_state 'CLOSED|COMPLETED|documentation,status:needs-review|issue|valid'
set_state CLOSED COMPLETED 'status:done'
assert_fails "$command_under_test" set 123 in-progress --comment "$claim_comment"
assert_read_only_log

# Reopening leaves an invalid active combination; only audited repair can
# establish the new state.
set_state OPEN '' 'enhancement'
assert_fails "$command_under_test" set 123 planned
assert_read_only_log
assert_fails "$command_under_test" repair 123 triage --comment x
assert_read_only_log
"$command_under_test" repair 123 triage \
    --comment "$repair_triage_comment" >/dev/null
assert_state 'OPEN||enhancement,status:triage|issue|valid'

# Repair and force preserve ordinary labels, normalize anomalies, and retain
# every target-specific audit requirement.
set_state OPEN '' 'documentation,status:planned,status:blocked'
assert_fails "$command_under_test" check 123
assert_read_only_log
set_state OPEN '' 'documentation,status:unknown'
assert_fails "$command_under_test" check 123
assert_read_only_log
set_state OPEN '' 'documentation,status:planned,status:blocked'
assert_fails "$command_under_test" set 123 triage --comment "$triage_comment"
assert_read_only_log
"$command_under_test" repair 123 planned \
    --comment "$repair_planned_comment" >/dev/null
assert_state 'OPEN||documentation,status:planned|issue|valid'

set_state OPEN '' 'documentation,status:triage'
"$command_under_test" set 123 needs-review --force \
    --comment "$force_review_comment" >/dev/null
assert_state 'OPEN||documentation,status:needs-review|issue|valid'

set_state OPEN '' 'documentation,status:planned,status:needs-review'
"$command_under_test" repair 123 blocked \
    --comment 'reason=external dependency; evidence=remote state inspected; target=blocked; unblock=dependency available' >/dev/null
assert_state 'OPEN||documentation,status:blocked|issue|valid'

set_state OPEN '' 'status:in-progress'
assert_fails "$command_under_test" set 123 in-progress --comment "$claim_comment"
assert_read_only_log
"$command_under_test" set 123 in-progress --force \
    --comment "$force_claim_comment" >/dev/null
assert_state 'OPEN||status:in-progress|issue|valid'

printf '%s\n' 'issue status tests passed'

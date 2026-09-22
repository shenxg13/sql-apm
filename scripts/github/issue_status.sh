#!/usr/bin/env bash

set -euo pipefail

status_labels=(
    status:triage
    status:planned
    status:in-progress
    status:blocked
    status:needs-review
    status:done
)

usage() {
    cat <<'EOF'
Usage:
  scripts/github/issue_status.sh check ISSUE [--repo OWNER/REPO]
  scripts/github/issue_status.sh set ISSUE STATUS [--repo OWNER/REPO] [--comment TEXT]
  scripts/github/issue_status.sh set ISSUE STATUS --force --comment TEXT [--repo OWNER/REPO]
  scripts/github/issue_status.sh repair ISSUE STATUS --comment TEXT [--repo OWNER/REPO]

Statuses:
  triage, planned, in-progress, blocked, needs-review, done, not-planned

Audit comment formats:
  in-progress: owner=...; branch=...; handoff=...
  blocked: reason=...; unblock=...
  triage/not-planned: reason=...
  repair/--force: reason=...; evidence=...; target=...
  repair/--force must also include the target status fields above.

Rules:
  The target must be an Issue, never a pull request.
  Normal set operations enforce the documented transition graph.
  planned requires concrete acceptance criteria and the fixed checked confirmation.
  done requires an issue already closed with reason completed.
  not-planned uses one Issue PATCH for state, reason, and final labels.
  Every successful write is re-read and asserted against the requested target.

Run this network-dependent command outside the sandbox.
EOF
}

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

command -v gh >/dev/null 2>&1 || die 'gh is required'
command -v base64 >/dev/null 2>&1 || die 'base64 is required'
command -v jq >/dev/null 2>&1 || die 'jq is required'

action=${1:-}
case $action in
    check)
        [[ $# -ge 2 ]] || {
            usage >&2
            exit 2
        }
        issue=$2
        target_status=
        shift 2
        ;;
    set | repair)
        [[ $# -ge 3 ]] || {
            usage >&2
            exit 2
        }
        issue=$2
        target_status=$3
        shift 3
        ;;
    -h | --help)
        usage
        exit 0
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac

repo=
comment=
force=false
while (($#)); do
    case $1 in
        -R | --repo)
            [[ $# -ge 2 ]] || die "$1 requires OWNER/REPO"
            repo=$2
            shift 2
            ;;
        --comment)
            [[ $# -ge 2 ]] || die '--comment requires text'
            comment=$2
            shift 2
            ;;
        --force)
            force=true
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

if [[ $action == check ]]; then
    [[ -z $comment && $force == false ]] ||
        die 'check does not accept --comment or --force'
elif [[ $action == repair ]]; then
    [[ $force == false ]] || die 'repair does not accept --force'
fi

repo_args=()
if [[ -n $repo ]]; then
    repo_args=(--repo "$repo")
fi

issue_state=
issue_reason=
issue_url=
issue_body=
issue_owner=
issue_repo=
issue_number=
current_status=
read_error=
all_labels=()
current_status_labels=()
ordinary_labels=()

read_issue() {
    local body_base64
    local issue_output
    local issue_data=()
    local label
    local path
    local resource
    local extra

    read_error=
    if ! issue_output=$(gh issue view "$issue" "${repo_args[@]}" \
        --json state,stateReason,url,body,labels \
        --jq '.state, (.stateReason // ""), .url, ((.body // "") | if . == "" then "-" else @base64 end), (.labels[].name)'); then
        read_error="unable to read issue $issue"
        return 1
    fi
    mapfile -t issue_data <<<"$issue_output"
    if ((${#issue_data[@]} < 4)); then
        read_error="unexpected response for issue $issue"
        return 1
    fi

    issue_state=${issue_data[0]}
    issue_reason=${issue_data[1]}
    issue_url=${issue_data[2]}
    body_base64=${issue_data[3]}
    if [[ $body_base64 == - ]]; then
        issue_body=
    elif ! issue_body=$(printf '%s' "$body_base64" | base64 --decode); then
        read_error="unable to decode body for $issue_url"
        return 1
    fi

    if [[ $issue_url == */pull/* ]]; then
        read_error="$issue_url is a pull request; an Issue is required"
        return 1
    fi

    path=${issue_url#https://github.com/}
    IFS=/ read -r issue_owner issue_repo resource issue_number extra <<<"$path"
    if [[ $issue_url != https://github.com/* || $resource != issues ||
        ! $issue_number =~ ^[0-9]+$ || -n $extra ]]; then
        read_error="unable to resolve repository and Issue number from $issue_url"
        return 1
    fi

    all_labels=()
    current_status_labels=()
    for label in "${issue_data[@]:4}"; do
        all_labels+=("$label")
        [[ $label == status:* ]] && current_status_labels+=("$label")
    done

    current_status=
    if ((${#current_status_labels[@]} == 1)); then
        current_status=${current_status_labels[0]#status:}
    fi
}

read_issue_or_die() {
    read_issue || die "$read_error"
}

is_known_status_label() {
    local candidate=$1
    local known
    for known in "${status_labels[@]}"; do
        [[ $candidate == "$known" ]] && return 0
    done
    return 1
}

validate_known_status_labels() {
    local label
    for label in "${current_status_labels[@]}"; do
        is_known_status_label "$label" ||
            die "$issue_url has unknown execution label $label"
    done
}

current_state_is_legal() {
    local label
    for label in "${current_status_labels[@]}"; do
        is_known_status_label "$label" || return 1
    done

    case $issue_state in
        OPEN)
            ((${#current_status_labels[@]} == 1)) || return 1
            [[ ${current_status_labels[0]} != status:done ]] || return 1
            if [[ ${current_status_labels[0]} == status:planned ]]; then
                (validate_planned_contract >/dev/null 2>&1)
            fi
            ;;
        CLOSED)
            case $issue_reason in
                COMPLETED)
                    ((${#current_status_labels[@]} == 1)) &&
                        [[ ${current_status_labels[0]} == status:done ]]
                    ;;
                NOT_PLANNED)
                    ((${#current_status_labels[@]} == 0))
                    ;;
                *)
                    return 1
                    ;;
            esac
            ;;
        *)
            return 1
            ;;
    esac
}

state_description() {
    local labels=none
    if ((${#all_labels[@]})); then
        labels=$(
            IFS=,
            printf '%s' "${all_labels[*]}"
        )
    fi
    printf '%s/%s labels=%s' "$issue_state" "${issue_reason:-none}" "$labels"
}

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
    ' <<<"$issue_body"
}

trim_text() {
    trimmed=$1
    trimmed=${trimmed#"${trimmed%%[![:space:]]*}"}
    trimmed=${trimmed%"${trimmed##*[![:space:]]}"}
}

validate_planned_contract() {
    local acceptance
    local acceptance_clean
    local confirmation
    local confirmation_clean
    local item
    local item_text
    local item_count=0

    [[ -n ${issue_body//[[:space:]]/} ]] ||
        die "$issue_url cannot be status:planned with an empty body"

    acceptance=$(extract_section '## 验收标准')
    confirmation=$(extract_section '## 需求确认')
    acceptance_clean=$(strip_html_comments <<<"$acceptance")
    confirmation_clean=$(strip_html_comments <<<"$confirmation")

    [[ -n ${acceptance_clean//[[:space:]]/} ]] ||
        die "$issue_url cannot be status:planned without acceptance criteria"

    if grep -Eiq '(^|[^[:alnum:]_])(TODO|TBD|TBC)([^[:alnum:]_]|$)|待补充|将本项替换为可验证的结果' \
        <<<"$acceptance_clean"; then
        die "$issue_url cannot be status:planned with placeholder acceptance criteria"
    fi

    while IFS= read -r item; do
        [[ $item =~ ^[[:space:]]*-\ \[[\ xX]\] ]] || continue
        item_count=$((item_count + 1))
        item_text=${item#*]}
        trim_text "$item_text"
        [[ -n $trimmed ]] ||
            die "$issue_url cannot be status:planned with an empty acceptance item"
    done <<<"$acceptance_clean"
    ((item_count > 0)) ||
        die "$issue_url cannot be status:planned without machine-readable acceptance items"

    [[ $(grep -Fxc -- '- [x] 用户已确认目标、范围、非目标和验收标准。' \
        <<<"$confirmation_clean") == 1 ]] ||
        die "$issue_url cannot be status:planned without the fixed checked user confirmation"
    if grep -Eq '用户[[:space:]]*(未|尚未|没有|并未).*确认|用户.*(未|尚未|没有|并未)[[:space:]]*确认' \
        <<<"$confirmation_clean"; then
        die "$issue_url cannot be status:planned with negative user-confirmation text"
    fi
}

check_issue() {
    read_issue_or_die
    validate_known_status_labels

    case $issue_state in
        OPEN)
            ((${#current_status_labels[@]} == 1)) ||
                die "$issue_url is open and must have exactly one status:* label"
            [[ ${current_status_labels[0]} != status:done ]] ||
                die "$issue_url is open and cannot be status:done"
            [[ $current_status != planned ]] || validate_planned_contract
            ;;
        CLOSED)
            case $issue_reason in
                COMPLETED)
                    ((${#current_status_labels[@]} == 1)) ||
                        die "$issue_url is completed and must have only status:done"
                    [[ ${current_status_labels[0]} == status:done ]] ||
                        die "$issue_url is completed and must have only status:done"
                    ;;
                NOT_PLANNED)
                    ((${#current_status_labels[@]} == 0)) ||
                        die "$issue_url is not planned and must not have status:* labels"
                    ;;
                *)
                    die "$issue_url is closed with unsupported reason ${issue_reason:-unknown}"
                    ;;
            esac
            ;;
        *)
            die "$issue_url has unsupported state $issue_state"
            ;;
    esac

    if ((${#current_status_labels[@]})); then
        printf 'OK: %s is %s with %s\n' \
            "$issue_url" "${issue_state,,}" "${current_status_labels[0]}"
    else
        printf 'OK: %s is closed as not planned with no status label\n' "$issue_url"
    fi
}

validate_normal_source() {
    validate_known_status_labels
    if [[ $issue_state == OPEN ]]; then
        ((${#current_status_labels[@]} == 1)) ||
            die "$issue_url has inconsistent status data; use repair or --force --comment"
        [[ $current_status != 'done' ]] ||
            die "$issue_url is open with status:done; use repair or --force --comment"
        return
    fi

    if [[ $issue_state == CLOSED && $issue_reason == COMPLETED &&
        $target_status == 'done' && $current_status == needs-review ]]; then
        return
    fi

    die "$issue_url is not a valid source for a normal transition; use repair or --force --comment"
}

validate_target_state() {
    case $target_status in
        triage | planned | in-progress | blocked | needs-review)
            [[ $issue_state == OPEN ]] ||
                die "$issue_url is closed; reopen it explicitly before setting status:$target_status"
            ;;
        done)
            [[ $issue_state == CLOSED && $issue_reason == COMPLETED ]] ||
                die "$issue_url must already be closed as completed before status:done"
            ;;
        not-planned)
            if [[ $issue_state == OPEN ]]; then
                return
            fi
            if [[ ($action == repair || $force == true) &&
                $issue_state == CLOSED && $issue_reason == NOT_PLANNED ]]; then
                return
            fi
            die "$issue_url must be open before a normal not-planned transition"
            ;;
    esac
}

transition_allowed() {
    case "$current_status:$target_status" in
        triage:planned | triage:not-planned | \
            planned:in-progress | planned:triage | planned:not-planned | \
            in-progress:blocked | in-progress:needs-review | in-progress:triage | in-progress:not-planned | \
            blocked:in-progress | blocked:triage | blocked:not-planned | \
            needs-review:in-progress | needs-review:triage | needs-review:not-planned | \
            needs-review:done)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

declare -A audit_fields=()
declare -A expected_audit_fields=()

parse_audit_comment() {
    local parts=()
    local part
    local key
    local value

    audit_fields=()
    IFS=';' read -ra parts <<<"$comment"
    ((${#parts[@]} > 0)) || die 'audit comment is empty'
    for part in "${parts[@]}"; do
        trim_text "$part"
        part=$trimmed
        [[ $part == *=* ]] ||
            die "audit field must use key=value: ${part:-empty}"
        key=${part%%=*}
        value=${part#*=}
        trim_text "$key"
        key=$trimmed
        trim_text "$value"
        value=$trimmed
        [[ $key =~ ^[a-z][a-z0-9-]*$ ]] ||
            die "invalid audit field name: ${key:-empty}"
        [[ -n $value ]] || die "audit field $key must not be empty"
        [[ -z ${audit_fields[$key]+present} ]] ||
            die "duplicate audit field: $key"
        audit_fields[$key]=$value
    done
}

expect_audit_field() {
    expected_audit_fields[$1]=true
}

validate_audit_comment() {
    local key

    expected_audit_fields=()
    if [[ $action == repair || $force == true ]]; then
        expect_audit_field reason
        expect_audit_field evidence
        expect_audit_field target
    fi

    case $target_status in
        in-progress)
            expect_audit_field owner
            expect_audit_field branch
            expect_audit_field handoff
            ;;
        blocked)
            expect_audit_field reason
            expect_audit_field unblock
            ;;
        triage | not-planned)
            expect_audit_field reason
            ;;
    esac

    if ((${#expected_audit_fields[@]} == 0)); then
        if [[ -z $comment ]]; then
            return
        fi
        expect_audit_field reason
    fi

    [[ -n ${comment//[[:space:]]/} ]] || die 'a structured audit comment is required'
    parse_audit_comment

    for key in "${!expected_audit_fields[@]}"; do
        [[ -n ${audit_fields[$key]+present} ]] ||
            die "audit comment is missing required field: $key"
    done
    for key in "${!audit_fields[@]}"; do
        [[ -n ${expected_audit_fields[$key]+present} ]] ||
            die "audit comment has unexpected field: $key"
    done
    if [[ $action == repair || $force == true ]]; then
        [[ ${audit_fields[target]} == "$target_status" ]] ||
            die "audit target must equal $target_status"
    fi
}

post_comment() {
    [[ -z $comment ]] && return
    gh issue comment "$issue" "${repo_args[@]}" --body "$comment" >/dev/null ||
        die "unable to record audit comment for $issue_url"
}

build_ordinary_labels() {
    local label
    ordinary_labels=()
    for label in "${all_labels[@]}"; do
        [[ $label == status:* ]] || ordinary_labels+=("$label")
    done
}

replace_labels() {
    local labels_json
    labels_json=$(jq -cn --args '{labels: $ARGS.positional}' "$@") || return 1
    gh api --method PUT \
        "repos/$issue_owner/$issue_repo/issues/$issue_number/labels" \
        --input - <<<"$labels_json" >/dev/null
}

patch_not_planned() {
    local issue_json
    issue_json=$(jq -cn --args \
        '{state: "closed", state_reason: "not_planned", labels: $ARGS.positional}' \
        "${ordinary_labels[@]}") || return 1
    gh api --method PATCH \
        "repos/$issue_owner/$issue_repo/issues/$issue_number" \
        --input - <<<"$issue_json" >/dev/null
}

target_matches_current() {
    local expected=$1
    case $expected in
        triage | planned | in-progress | blocked | needs-review)
            [[ $issue_state == OPEN && ${#current_status_labels[@]} == 1 &&
                ${current_status_labels[0]} == "status:$expected" ]]
            ;;
        done)
            [[ $issue_state == CLOSED && $issue_reason == COMPLETED &&
                ${#current_status_labels[@]} == 1 &&
                ${current_status_labels[0]} == status:done ]]
            ;;
        not-planned)
            [[ $issue_state == CLOSED && $issue_reason == NOT_PLANNED &&
                ${#current_status_labels[@]} == 0 ]]
            ;;
    esac
}

assert_target_state() {
    local expected=$1
    local observed

    if ! read_issue; then
        die "unable to verify requested target $expected: $read_error"
    fi
    if target_matches_current "$expected"; then
        [[ $expected != planned ]] || validate_planned_contract
        printf 'OK: %s reached requested target %s\n' "$issue_url" "$expected"
        return
    fi

    observed=$(state_description)
    if current_state_is_legal; then
        die "$issue_url did not reach requested target $expected; final legal state is $observed; the write may not have taken effect or may have been concurrently overwritten"
    fi
    die "$issue_url did not reach requested target $expected; final state is $observed and requires explicit repair"
}

labels_key() {
    jq -cn --args '$ARGS.positional | sort | join("\u001f")' "$@"
}

original_state=
original_reason=
original_labels_key=
original_was_legal=false
capture_original_state() {
    original_state=$issue_state
    original_reason=$issue_reason
    original_labels_key=$(labels_key "${all_labels[@]}")
    original_was_legal=false
    if current_state_is_legal; then
        original_was_legal=true
    fi
}

current_matches_original() {
    local current_key
    current_key=$(labels_key "${all_labels[@]}")
    [[ $issue_state == "$original_state" && $issue_reason == "$original_reason" &&
        $current_key == "$original_labels_key" ]]
}

set_not_planned() {
    local observed

    post_comment
    if patch_not_planned; then
        assert_target_state not-planned
        return
    fi

    if ! read_issue; then
        die "not-planned PATCH failed and the remote outcome is unknown because reread failed: $read_error"
    fi
    if target_matches_current not-planned; then
        printf 'OK: %s reached not-planned although PATCH reported an error\n' "$issue_url"
        return
    fi
    if [[ $original_was_legal == true ]] && current_matches_original; then
        die "$issue_url not-planned PATCH failed; reread confirms the original legal state remains"
    fi

    observed=$(state_description)
    die "$issue_url not-planned PATCH failed and reread found $observed; explicit repair is required"
}

if [[ $action == check ]]; then
    check_issue
    exit 0
fi

case $target_status in
    triage | planned | in-progress | blocked | needs-review | done | not-planned)
        ;;
    *)
        die "unsupported status: $target_status"
        ;;
esac

read_issue_or_die
validate_target_state
validate_audit_comment

if [[ $action != repair && $force == false ]]; then
    validate_normal_source
    transition_allowed ||
        die "$issue_url cannot transition from status:$current_status to status:$target_status"
fi

if [[ $target_status == planned ||
    ($current_status == planned && $target_status == in-progress) ]]; then
    validate_planned_contract
fi

build_ordinary_labels
capture_original_state

case $target_status in
    triage | planned | in-progress | blocked | needs-review)
        post_comment
        replace_labels "${ordinary_labels[@]}" "status:$target_status" ||
            die "unable to atomically set status:$target_status on $issue_url"
        assert_target_state "$target_status"
        ;;
    done)
        post_comment
        replace_labels "${ordinary_labels[@]}" status:done ||
            die "unable to atomically set status:done on $issue_url"
        assert_target_state 'done'
        ;;
    not-planned)
        set_not_planned
        ;;
esac

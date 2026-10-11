#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd -- "${BASH_SOURCE[0]%/*}/../.." && pwd)
test_root=$(mktemp -d "${TMPDIR:-/tmp}/agent-harness-quality.XXXXXX")
fake_bin="$test_root/bin"
mkdir -p "$fake_bin"
# shellcheck source=../quality/tool-versions.env
source "$repo_root/scripts/quality/tool-versions.env"

cleanup() {
    rm -rf -- "$test_root"
}
trap cleanup EXIT

cat >"$test_root/fake-tool" <<'EOF'
#!/usr/bin/bash
set -euo pipefail

if [[ ${HARNESS_TEST_WRONG_TOOL:-} == "${0##*/}" ]]; then
    printf '%s 0.0.1\n' "${0##*/}"
    exit 0
fi
case ${0##*/} in
    bash) printf '%s\n' 'GNU bash, version 5.1.8(1)-release' ;;
    git) printf '%s\n' 'git version 2.52.0' ;;
    gh) printf '%s\n' 'gh version 2.97.0' ;;
    jq) printf '%s\n' 'jq-1.6' ;;
    base64) printf '%s\n' 'base64 (GNU coreutils) 8.32' ;;
    shellcheck) printf '%s\n' 'ShellCheck' 'version: 0.10.0' ;;
    shfmt) printf '%s\n' 'v3.13.1' ;;
    node) printf '%s\n' 'v24.19.0' ;;
    npm) printf '%s\n' '11.17.0' ;;
    markdownlint-cli2) printf '%s\n' 'markdownlint-cli2 v0.23.2' ;;
    *) exit 2 ;;
esac
EOF
chmod +x "$test_root/fake-tool"

tools=(bash git jq base64 shellcheck shfmt node npm markdownlint-cli2)
for tool in "${tools[@]}"; do
    cp "$test_root/fake-tool" "$fake_bin/$tool"
done

command_under_test="$repo_root/scripts/quality/check.sh"
for missing in "${tools[@]}"; do
    mv "$fake_bin/$missing" "$test_root/$missing"
    if PATH=$fake_bin /usr/bin/bash "$command_under_test" --check-tools-only \
        >"$test_root/stdout" 2>"$test_root/stderr"; then
        printf 'FAIL: missing tool %s unexpectedly passed\n' "$missing" >&2
        exit 1
    fi
    grep -Fq "missing required tools: $missing" "$test_root/stderr" || {
        printf 'FAIL: missing tool %s was not reported clearly\n' "$missing" >&2
        exit 1
    }
    mv "$test_root/$missing" "$fake_bin/$missing"
done

PATH=$fake_bin /usr/bin/bash "$command_under_test" --check-tools-only \
    >"$test_root/stdout"

if PATH=$fake_bin HARNESS_QUALITY_INJECT_FAILURE=after-tool-check \
    /usr/bin/bash "$command_under_test" --check-tools-only \
    >"$test_root/stdout" 2>"$test_root/stderr"; then
    printf '%s\n' 'FAIL: injected quality failure unexpectedly passed' >&2
    exit 1
fi
grep -Fq 'injected quality failure after tool check' "$test_root/stderr" || {
    printf '%s\n' 'FAIL: injected failure was not reported clearly' >&2
    exit 1
}

for wrong in bash shfmt node; do
    if PATH=$fake_bin HARNESS_TEST_WRONG_TOOL=$wrong \
        /usr/bin/bash "$command_under_test" --check-tools-only \
        >"$test_root/stdout" 2>"$test_root/stderr"; then
        printf 'FAIL: wrong %s version unexpectedly passed\n' "$wrong" >&2
        exit 1
    fi
    grep -Fq 'required, found 0.0.1' "$test_root/stderr"
done
if PATH=$fake_bin /usr/bin/bash "$command_under_test" --pr 1 \
    >"$test_root/stdout" 2>"$test_root/stderr"; then
    printf '%s\n' 'FAIL: live PR mode passed without gh' >&2
    exit 1
fi
grep -Fq 'missing required tools: gh' "$test_root/stderr"

# The local installer must refuse before any download or write.
installer="$repo_root/scripts/quality/install_local_tools.sh"
installer_bin="$test_root/installer-bin"
mkdir "$installer_bin"
cat >"$installer_bin/forbidden-operation" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "${0##*/}" >>"$HARNESS_TEST_INSTALLER_OPERATIONS"
exit 99
EOF
chmod +x "$installer_bin/forbidden-operation"
for tool in curl mkdir mktemp npm install mv tar; do
    ln -s forbidden-operation "$installer_bin/$tool"
done
expect_installer_refusal() {
    local message=$1
    shift
    if PATH="$installer_bin:$PATH" \
        HARNESS_TEST_INSTALLER_OPERATIONS="$test_root/installer-operations" \
        "$installer" "$@" >"$test_root/stdout" 2>"$test_root/stderr"; then
        printf 'FAIL: local installer accepted: %s\n' "$*" >&2
        exit 1
    fi
    grep -Fq -- "$message" "$test_root/stderr" || {
        printf 'FAIL: local installer did not report: %s\n' "$message" >&2
        exit 1
    }
    [[ ! -e $test_root/installer-operations ]] || {
        printf '%s\n' 'FAIL: installer attempted a download or write before refusal' >&2
        exit 1
    }
}
mkdir "$test_root/existing-tools"
printf '%s\n' 'preserve existing content' >"$test_root/existing-tools/sentinel"
cp -a "$test_root/existing-tools" "$test_root/expected-tools"
expect_installer_refusal 'target already exists' "$test_root/existing-tools"
git diff --no-index --exit-code -- "$test_root/expected-tools" "$test_root/existing-tools"
expect_installer_refusal 'target already exists' "$test_root/existing-tools/sentinel"
ln -s missing-target "$test_root/dangling-tools"
expect_installer_refusal 'target already exists' "$test_root/dangling-tools"
[[ $(readlink "$test_root/dangling-tools") == missing-target ]]
expect_installer_refusal 'unknown option' --force
expect_installer_refusal 'at most one target directory' \
    "$test_root/first" "$test_root/second"
expect_installer_refusal 'at most one target directory' --help "$test_root/first"
for suffix in . .. ./ ../ ./// ..///; do
    expect_installer_refusal 'target must not end in . or ..' "$test_root/first/$suffix"
done
[[ ! -e $test_root/first && ! -e $test_root/second ]]

# Simulate a target appearing at the last gate. Exercise the real publication
# and cleanup while keeping downloads and package installation offline.
publication_fixture="$test_root/publication"
mkdir -p "$publication_fixture/scripts/quality" "$publication_fixture/bin"
cp "$installer" "$repo_root/scripts/quality/tool-versions.env" \
    "$publication_fixture/scripts/quality/"
cat >"$publication_fixture/bin/noop" <<'EOF'
#!/usr/bin/env bash
if [[ ${0##*/} == sha256sum ]]; then cat >/dev/null; fi
exit 0
EOF
chmod +x "$publication_fixture/bin/noop"
for tool in curl tar xz sha256sum install npm; do
    ln -s noop "$publication_fixture/bin/$tool"
done
# This fixture tests publication, independently of the host's installer support.
cat >"$publication_fixture/bin/uname" <<'EOF'
#!/usr/bin/env bash
case ${1:-} in
    -s) printf '%s\n' Linux ;;
    -m) printf '%s\n' x86_64 ;;
    *) exit 2 ;;
esac
EOF
chmod +x "$publication_fixture/bin/uname"
# Exercise both GNU exit statuses for a skipped no-clobber move. The real mv
# still owns the filesystem operation, so target-preservation checks remain real.
real_mv=$(command -v mv)
cat >"$publication_fixture/bin/mv" <<'EOF'
#!/usr/bin/env bash
"$HARNESS_TEST_REAL_MV" "$@"
exit "$HARNESS_TEST_MOVE_STATUS"
EOF
chmod +x "$publication_fixture/bin/mv"
cat >"$publication_fixture/scripts/quality/check.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[[ $1 == --check-tools-only ]]
case $HARNESS_TEST_TARGET_KIND in
    absent)
        staged_bin=${PATH%%:*}
        printf '%s\n' 'staged tools' >"${staged_bin%/bin}/payload"
        ;;
    empty) mkdir "$HARNESS_TEST_PUBLICATION_TARGET" ;;
    directory)
        mkdir "$HARNESS_TEST_PUBLICATION_TARGET"
        printf '%s\n' 'preserve target' >"$HARNESS_TEST_PUBLICATION_TARGET/sentinel"
        ;;
    file) printf '%s\n' 'preserve target' >"$HARNESS_TEST_PUBLICATION_TARGET" ;;
    symlink) ln -s absent "$HARNESS_TEST_PUBLICATION_TARGET" ;;
esac
EOF
chmod +x "$publication_fixture/scripts/quality/check.sh"
for scenario in empty:0 directory:0 file:0 symlink:0 empty:1 directory:1 file:1 symlink:1; do
    kind=${scenario%:*}
    move_status=${scenario#*:}
    publication_target="$publication_fixture/target-$kind-$move_status"
    if PATH="$publication_fixture/bin:$PATH" \
        HARNESS_TEST_PUBLICATION_TARGET="$publication_target" \
        HARNESS_TEST_TARGET_KIND="$kind" \
        HARNESS_TEST_REAL_MV="$real_mv" HARNESS_TEST_MOVE_STATUS="$move_status" \
        "$publication_fixture/scripts/quality/install_local_tools.sh" "$publication_target" \
        >"$test_root/stdout" 2>"$test_root/stderr"; then
        printf 'FAIL: installer published over an existing %s\n' "$kind" >&2
        exit 1
    fi
    grep -Fq 'unable to publish tools without replacing target' "$test_root/stderr" || {
        cat "$test_root/stderr" >&2
        exit 1
    }
    case $kind in
        empty) [[ -z $(ls -A "$publication_target") ]] ;;
        directory)
            [[ $(ls -A "$publication_target") == sentinel ]]
            [[ $(cat "$publication_target/sentinel") == 'preserve target' ]]
            ;;
        file) [[ $(cat "$publication_target") == 'preserve target' ]] ;;
        symlink) [[ $(readlink "$publication_target") == absent ]] ;;
    esac
    [[ -z $(find "$publication_fixture" -maxdepth 1 -name '.harness-tools.*' -print -quit) ]]
done
publication_target="$publication_fixture/target-absent"
PATH="$publication_fixture/bin:$PATH" \
    HARNESS_TEST_PUBLICATION_TARGET="$publication_target" \
    HARNESS_TEST_TARGET_KIND=absent \
    HARNESS_TEST_REAL_MV="$real_mv" HARNESS_TEST_MOVE_STATUS=0 \
    "$publication_fixture/scripts/quality/install_local_tools.sh" "$publication_target" \
    >"$test_root/stdout" 2>"$test_root/stderr"
[[ $(ls -A "$publication_target") == payload ]]
[[ $(cat "$publication_target/payload") == 'staged tools' ]]
# Compare the installed location, not the spelling inherited from TMPDIR.
expected_target=$(cd -- "$publication_target" && pwd)
grep -Fxq "installed pinned quality tools into $expected_target" "$test_root/stdout" || {
    printf 'FAIL: installer did not report the installed location: %s\n' "$expected_target" >&2
    cat "$test_root/stdout" >&2
    exit 1
}
[[ -z $(find "$publication_fixture" -maxdepth 1 -name '.harness-tools.*' -print -quit) ]]

for digest in \
    "$SHELLCHECK_LINUX_X64_SHA256" "$SHFMT_LINUX_X64_SHA256" \
    "$NODE_LINUX_X64_SHA256"; do
    [[ $digest =~ ^[0-9a-f]{64}$ ]] || {
        printf 'FAIL: malformed artifact digest: %s\n' "$digest" >&2
        exit 1
    }
done

runbook="$repo_root/docs/runbooks/issue-pr-quality-tooling.md"
for version in \
    "$BASH_MIN_VERSION" "$GIT_MIN_VERSION" "$GH_MIN_VERSION" \
    "$JQ_MIN_VERSION" "$COREUTILS_MIN_VERSION" "$SHELLCHECK_VERSION" \
    "$SHFMT_VERSION" "$NODE_VERSION" "$NPM_VERSION" \
    "$MARKDOWNLINT_CLI2_VERSION"; do
    grep -Fq "$version" "$runbook" || {
        printf 'FAIL: runbook is missing contracted version %s\n' "$version" >&2
        exit 1
    }
done

printf '%s\n' 'quality entrypoint tests passed'

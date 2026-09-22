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

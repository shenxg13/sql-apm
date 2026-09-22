#!/usr/bin/env bash
set -euo pipefail
repo_root=$(cd -- "${BASH_SOURCE[0]%/*}/../.." && pwd)
test_root=$(mktemp -d "${TMPDIR:-/tmp}/agent-harness-template.XXXXXX")
trap 'rm -rf -- "$test_root"' EXIT
fixture="$test_root/repo"
mkdir -p "$fixture"
# Copy only tracked worktree files, including staged initial files.
while IFS= read -r -d '' name; do
    mkdir -p "$fixture/$(dirname -- "$name")"
    cp -- "$repo_root/$name" "$fixture/$name"
done < <(git -C "$repo_root" ls-files -z)
git -C "$fixture" init -q -b main
git -C "$fixture" add .
checker="$repo_root/scripts/quality/check_template.sh"
expect_fail() {
    local message=$1
    if "$checker" --source-audit "$fixture" >"$test_root/stdout" 2>"$test_root/stderr"; then
        printf 'FAIL: template mutation passed: %s\n' "$message" >&2
        exit 1
    fi
    grep -Fq -- "$message" "$test_root/stderr" || {
        cat "$test_root/stderr" >&2
        exit 1
    }
}
"$checker" --source-audit "$fixture" >/dev/null
rm -- "$fixture/.harness/workflows/large-change.md"
expect_fail 'missing required file'
cp -- "$repo_root/.harness/workflows/large-change.md" "$fixture/.harness/workflows/large-change.md"
printf '\n[broken](missing-document.md)\n' >>"$fixture/README.md"
expect_fail 'broken local reference'
cp -- "$repo_root/README.md" "$fixture/README.md"
printf '\n%s-%s\n' tm lab >>"$fixture/README.md"
expect_fail 'source-specific residue'
cp -- "$repo_root/README.md" "$fixture/README.md"
printf '\n[escape](../../outside.md)\n' >>"$fixture/README.md"
expect_fail 'reference escapes repository'
cp -- "$repo_root/README.md" "$fixture/README.md"
printf '# Untracked\n' >"$fixture/untracked.md"
printf '\n[untracked](untracked.md)\n' >>"$fixture/README.md"
expect_fail 'reference is not tracked'
cp -- "$repo_root/README.md" "$fixture/README.md"
sed -i 's/architecture.agent-development-harness/architecture.missing/' \
    "$fixture/.project-wiki/decisions/engineering-principles.md"
expect_fail 'unknown wiki related entity'
cp -- "$repo_root/.project-wiki/decisions/engineering-principles.md" \
    "$fixture/.project-wiki/decisions/engineering-principles.md"
sed -i 's/run: scripts\/quality\/check.sh/run: true/' "$fixture/.github/workflows/quality.yml"
expect_fail 'CI does not invoke'
cp -- "$repo_root/.github/workflows/quality.yml" "$fixture/.github/workflows/quality.yml"
sed -i 's/actions\/checkout@[a-f0-9]*/actions\/checkout@main/' "$fixture/.github/workflows/quality.yml"
expect_fail 'CI action is not commit-pinned'
cp -- "$repo_root/.github/workflows/quality.yml" "$fixture/.github/workflows/quality.yml"
"$checker" --source-audit "$fixture" >/dev/null
# Adoption may include domain vocabulary and binary assets; extraction audit is explicit.
printf '\n%s-%s\n' tm lab >>"$fixture/README.md"
printf '\0asset' >"$fixture/asset.bin"
git -C "$fixture" add asset.bin
"$checker" "$fixture" >/dev/null
expect_fail 'source-specific residue'
printf '%s\n' 'template integrity mutation tests passed'

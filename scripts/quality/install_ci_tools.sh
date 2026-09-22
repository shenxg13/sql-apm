#!/usr/bin/env bash
set -euo pipefail
script_dir=$(cd -- "${BASH_SOURCE[0]%/*}" && pwd)
# shellcheck source=tool-versions.env
source "$script_dir/tool-versions.env"
[[ ${CI:-} == true && -n ${RUNNER_TEMP:-} && -n ${GITHUB_PATH:-} ]] || {
    printf '%s\n' 'ERROR: this provisioner requires an explicit GitHub CI environment' >&2
    exit 1
}
[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || {
    printf '%s\n' 'ERROR: CI artifact hashes cover Linux x86_64 only' >&2
    exit 1
}
install_dir=$(mktemp -d "$RUNNER_TEMP/harness-tools.XXXXXX")
mkdir -p "$install_dir/bin"
curl -fsSL --retry 3 -o "$install_dir/shellcheck.tar.xz" \
    "https://github.com/koalaman/shellcheck/releases/download/v${SHELLCHECK_VERSION}/shellcheck-v${SHELLCHECK_VERSION}.linux.x86_64.tar.xz"
printf '%s  %s\n' "$SHELLCHECK_LINUX_X64_SHA256" "$install_dir/shellcheck.tar.xz" | sha256sum --check --strict
tar -xJf "$install_dir/shellcheck.tar.xz" -C "$install_dir"
install -m 0755 "$install_dir/shellcheck-v${SHELLCHECK_VERSION}/shellcheck" "$install_dir/bin/shellcheck"
curl -fsSL --retry 3 -o "$install_dir/shfmt" \
    "https://github.com/mvdan/sh/releases/download/v${SHFMT_VERSION}/shfmt_v${SHFMT_VERSION}_linux_amd64"
printf '%s  %s\n' "$SHFMT_LINUX_X64_SHA256" "$install_dir/shfmt" | sha256sum --check --strict
install -m 0755 "$install_dir/shfmt" "$install_dir/bin/shfmt"
printf '%s\n' "$install_dir/bin" >>"$GITHUB_PATH"
npm install --global "npm@$NPM_VERSION" "markdownlint-cli2@$MARKDOWNLINT_CLI2_VERSION"

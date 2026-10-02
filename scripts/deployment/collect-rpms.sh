#!/usr/bin/env bash
# Run on the snapshot's initial Kylin host, before installing any packages.
# Redirects intentionally remain owned by the invoking deployment account.
# shellcheck disable=SC2024
set -euo pipefail
export LC_ALL=C
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
output=${1:?usage: collect-rpms.sh /data/sql-apm/rpm-collection}
[[ $output == /data/sql-apm/rpm-collection ]] || {
    echo 'unexpected output path' >&2
    exit 1
}
[[ ! -e $output && ! -L $output ]] || {
    echo 'fresh output required' >&2
    exit 1
}
mkdir -p -- "$output/rpms"
mapfile -t packages <"$script_dir/compile-packages.txt"
cp -- "$script_dir/compile-packages.txt" "$output/compile-packages.txt"
rpm -qa --qf '%{NAME}\t%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' | sort >"$output/installed-before.tsv"
sudo -n yum repolist enabled >"$output/repositories.txt"
sudo -n yum download --resolve --alldeps --arch=x86_64,noarch --url \
    "${packages[@]}" >"$output/download-urls.txt"
sudo -n yum download --resolve --alldeps --arch=x86_64,noarch \
    --downloaddir "$output/rpms" "${packages[@]}" >"$output/download.log" 2>&1
shopt -s nullglob
files=("$output"/rpms/*.rpm)
((${#files[@]})) || {
    echo 'no RPM downloaded' >&2
    exit 1
}
for package in "${packages[@]}"; do
    found=false
    for file in "${files[@]}"; do
        if [[ $(rpm -qp --qf '%{NAME}' "$file") == "$package" ]]; then
            found=true
            break
        fi
    done
    $found || {
        echo "missing root package: $package" >&2
        exit 1
    }
done
for file in "${files[@]}"; do
    # A zero exit without a signature is not sufficient: fail on unsigned RPMs.
    result=$(rpm --checksig "$file")
    [[ $result == *'digests signatures OK'* ]] || {
        echo "$result" >&2
        exit 1
    }
    printf '%s\n' "$result" >>"$output/signatures.txt"
    printf '%s\t' "$(basename -- "$file")" >>"$output/packages.tsv"
    rpm -qp --qf '%{NAME}\t%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' "$file" >>"$output/packages.tsv"
done
(cd -- "$output" && sha256sum rpms/*.rpm >SHA256SUMS)
rpm -qa --qf '%{NAME}\t%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' | sort >"$output/installed-after.tsv"
cmp "$output/installed-before.tsv" "$output/installed-after.tsv"
printf 'COLLECTED %s signed RPMs; installed package state unchanged\n' "${#files[@]}"

#!/usr/bin/env bash
# Explicit operator-selected compilation path. Never invoked by the binary installer.
set -euo pipefail
if [[ $# -ne 1 || $1 != /data/sql-apm ]]; then
    echo 'Usage: compile_runtime.sh /data/sql-apm (single fixed-prefix artifact build)' >&2
    exit 2
fi
apm_root=$1
apm_bundle="$apm_root/offline-bundle"
apm_evidence="$apm_root/runtime-build"
[[ $(readlink -f "$apm_root") == "$apm_root" ]]
[[ ! -e $apm_evidence && ! -e $apm_root/python-3.13.16 && ! -e $apm_root/postgresql-17.10 && ! -e $apm_root/app/.venv ]]
mkdir -m 0700 "$apm_evidence"
cp -- "$0" "$apm_evidence/compile-runtime.sh"
cat /etc/kylin-release >"$apm_evidence/system-release.txt"
uname -m >"$apm_evidence/architecture.txt"
rpm -qa --qf '%{NAME}\t%{EPOCHNUM}:%{VERSION}-%{RELEASE}\t%{ARCH}\n' | LC_ALL=C sort >"$apm_evidence/packages-before.tsv"
exec > >(tee "$apm_evidence/build.log") 2>&1
set -x
(cd "$apm_bundle" && sha256sum -c SHA256SUMS)
(cd "$apm_root/app" && sha256sum -c SHA256SUMS)
(cd "$apm_root/verification" && sha256sum -c SHA256SUMS)
mapfile -t apm_packages <"$apm_bundle/support/compile-packages.txt"
for package in "$apm_bundle"/rpms/*.rpm; do rpm --checksig "$package"; done
sudo yum --disablerepo='*' \
    --repofrompath="apm-offline,file://$apm_bundle/rpms" --enablerepo=apm-offline \
    --setopt=apm-offline.gpgcheck=1 --setopt=install_weak_deps=False install -y "${apm_packages[@]}"
rpm -qa --qf '%{NAME}\t%{EPOCHNUM}:%{VERSION}-%{RELEASE}\t%{ARCH}\n' | LC_ALL=C sort >"$apm_evidence/packages-after.tsv"
mkdir "$apm_evidence/source"
cd "$apm_evidence/source"
tar -xzf "$apm_bundle/sources/Python-3.13.16.tgz"
cd Python-3.13.16
./configure --prefix="$apm_root/python-3.13.16" --with-ensurepip=install
make -j4
make install
"$apm_root/python-3.13.16/bin/python3.13" -m venv "$apm_root/app/.venv"
cd "$apm_root/app"
PIP_CONFIG_FILE=/dev/null .venv/bin/python -m pip --isolated --disable-pip-version-check \
    install --no-index --find-links "$apm_bundle/wheels" --require-hashes -r requirements.txt
cd "$apm_evidence/source"
tar -xzf "$apm_bundle/sources/postgresql-17.10.tar.gz"
cd postgresql-17.10
./configure --prefix="$apm_root/postgresql-17.10" --with-openssl
make -j4
make install
{
    "$apm_root/postgresql-17.10/bin/pg_config"
    "$apm_root/app/.venv/bin/python" -m sysconfig
    ldd "$apm_root/python-3.13.16/bin/python3.13"
    ldd "$apm_root/postgresql-17.10/bin/postgres"
    ldd "$apm_root/postgresql-17.10/bin/psql"
} >"$apm_evidence/prefix-details.txt"
PYTHONDONTWRITEBYTECODE=1 "$apm_root/app/.venv/bin/python" "$apm_root/app/scripts/deployment/runtime_check.py" \
    --root "$apm_root" --output "$apm_evidence/checks"
cp "$apm_evidence/checks/selfcheck.json" "$apm_evidence/selfcheck.json"
set +x
echo 'Compilation and acceptance finished. Export the artifact in a separate command after this log closes.'

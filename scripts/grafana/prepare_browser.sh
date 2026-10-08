#!/usr/bin/env bash
# Development machine only: a headless browser for scripts/grafana/verify_e2e.py.
# Everything goes into one ignored directory; nothing is installed into the system
# and nothing here enters a release package (authorised by Issue #51).
set -euo pipefail
target=${1:?usage: prepare_browser.sh DIRECTORY [PYTHON]}
python=${2:-python3}
mkdir -p "$target"
target=$(cd -- "$target" && pwd)
[[ -x $target/venv/bin/python ]] || "$python" -m venv "$target/venv"
"$target/venv/bin/pip" install --quiet 'playwright==1.55.0'
PLAYWRIGHT_BROWSERS_PATH="$target/browsers" "$target/venv/bin/python" -m playwright install chromium-headless-shell
# Shared libraries and a Chinese font, unpacked from distribution packages without installing them.
mkdir -p "$target/rpms" "$target/libs" "$target/fonts"
(cd "$target/rpms" && dnf download -q --disablerepo='*' --enablerepo=baseos --enablerepo=appstream --arch x86_64 --arch noarch \
    nspr nss nss-util nss-softokn nss-softokn-freebl atk at-spi2-atk at-spi2-core libX11 libX11-common libXcomposite \
    libXdamage libXext libXfixes libXrandr mesa-libgbm libxcb libxkbcommon alsa-lib libXau libXrender libXi libXtst \
    libdrm libwayland-server libxshmfence google-noto-sans-cjk-ttc-fonts)
for package in "$target"/rpms/*.rpm; do
    case "$package" in
        *noto*) rpm2archive - <"$package" | tar -xz -C "$target/fonts" ;;
        *) rpm2archive - <"$package" | tar -xz -C "$target/libs" ;;
    esac
done
cat >"$target/fonts.conf" <<CONF
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <dir>$target/fonts</dir>
  <cachedir>$target/fontcache</cachedir>
</fontconfig>
CONF
cat >"$target/env" <<ENV
export PLAYWRIGHT_BROWSERS_PATH="$target/browsers"
export LD_LIBRARY_PATH="$target/libs/usr/lib64\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}"
export FONTCONFIG_FILE="$target/fonts.conf"
ENV
printf 'OK: browser ready; run: source %s/env && %s/venv/bin/python scripts/grafana/verify_e2e.py --directory SETUP_DIRECTORY\n' "$target" "$target"

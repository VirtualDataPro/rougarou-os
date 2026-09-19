#!/usr/bin/env bash
# Shared base-package assembly for an ISO candidate or an independent update.
set -Eeuo pipefail
umask 022
[[ -f /.dockerenv && -d /src/rootfs ]] || { echo 'Run inside the Rougarou builder container.' >&2; exit 1; }
[[ $# == 2 ]] || { echo 'Usage: build-base-package.sh EMPTY_STAGE OUTPUT_DEB' >&2; exit 2; }
stage=$1
output=$2
# shellcheck source=../versions.env
source /src/versions.env
revision=${ROUGAROU_BASE_REVISION:-$ROUGAROU_PACKAGE_REVISION}
[[ $revision =~ ^[1-9][0-9]*$ ]] || { echo 'Invalid base package revision.' >&2; exit 1; }
version="${ROUGAROU_VERSION/-alpha./~alpha}-$revision"
[[ $(basename -- "$output") == "rougarou-base_${version}_all.deb" ]]
[[ ! -e $output && ! -L $output ]] || { echo 'Refusing to overwrite a package.' >&2; exit 1; }
mkdir -p "$stage"
[[ -z $(find "$stage" -mindepth 1 -maxdepth 1 -print -quit) ]] || { echo 'Package stage must be empty.' >&2; exit 1; }
cp -a /src/rootfs/. "$stage/"
find "$stage" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$stage" -type f -name '*.py[co]' -delete
mkdir -p "$stage/DEBIAN" "$stage/usr/share/doc/rougarou-base"
cp /src/LICENSE "$stage/usr/share/doc/rougarou-base/copyright"
mapfile -t packages < <(sed '/^[[:space:]]*#/d; /^[[:space:]]*$/d' /src/packages.txt)
for package in "${packages[@]}"; do
  [[ $package =~ ^[a-z0-9][a-z0-9+.-]*$ ]] || { echo "Invalid package: $package" >&2; exit 1; }
done
dependencies=$(IFS=,; printf '%s' "${packages[*]}")
cat >"$stage/DEBIAN/control" <<EOF
Package: rougarou-base
Version: $version
Architecture: all
Installed-Size: $(du -sk --exclude=DEBIAN "$stage" | cut -f1)
Maintainer: Rougarou OS maintainers <rougarou-os@users.noreply.github.com>
Section: admin
Priority: optional
Depends: $dependencies
Description: Rougarou headless server integration and operator onboarding
EOF
find "$stage/etc" -type f -printf '/etc/%P\n' | sort >"$stage/DEBIAN/conffiles"
for script in preinst postinst postrm triggers; do
  cp "/src/image/rougarou-base.$script" "$stage/DEBIAN/$script"
done
chmod 0755 "$stage/DEBIAN/preinst" "$stage/DEBIAN/postinst" "$stage/DEBIAN/postrm"
dpkg-deb --root-owner-group -Zgzip --build "$stage" "$output"

#!/usr/bin/env bash
# Verify with native APT using only disposable config, state and download paths.
# This does NOT install a package or change the host's APT configuration/cache.
set -Eeuo pipefail
url=${1:?Usage: verify-hosted-repository.sh CHANNEL_URL TRUSTED_PUBLIC_KEYRING [PACKAGE]}
keyring=${2:?Missing trusted public keyring}
package=${3:-bash}
[[ $url =~ ^https?://[A-Za-z0-9.:-]+/[A-Za-z0-9._/-]+/$ ]] || { echo 'Invalid channel URL' >&2; exit 1; }
[[ $package =~ ^[a-z0-9][a-z0-9+.-]*$ ]] || { echo 'Invalid package name' >&2; exit 1; }
[[ -f $keyring ]] || { echo 'Trusted keyring missing' >&2; exit 1; }
checkdir=$(mktemp -d /tmp/rougarou-apt-check.XXXXXX)
trap 'rm -rf "$checkdir"' EXIT
chmod 0755 "$checkdir"
mkdir -p "$checkdir/lists/partial" "$checkdir/cache/archives/partial" "$checkdir/downloads" \
  "$checkdir/empty-config" "$checkdir/empty-sources" "$checkdir/empty-trusted"
cp "$keyring" "$checkdir/archive.gpg"
chmod 0644 "$checkdir/archive.gpg"
touch "$checkdir/status" "$checkdir/empty.conf"
if [[ $(id -u) == 0 ]] && id _apt >/dev/null 2>&1; then
  chown _apt "$checkdir/downloads" "$checkdir/lists/partial" "$checkdir/cache/archives/partial"
fi
printf 'deb [signed-by=%s/archive.gpg] %s ./\n' "$checkdir" "$url" > "$checkdir/repository.list"
cat > "$checkdir/apt.conf" <<EOF
Dir::Etc::parts "$checkdir/empty-config";
Dir::Etc::main "$checkdir/empty.conf";
Dir::Etc::sourcelist "$checkdir/repository.list";
Dir::Etc::sourceparts "$checkdir/empty-sources";
Dir::Etc::trusted "-";
Dir::Etc::trustedparts "$checkdir/empty-trusted";
Dir::State "$checkdir";
Dir::State::lists "$checkdir/lists";
Dir::State::status "$checkdir/status";
Dir::Cache "$checkdir/cache";
Dir::Log "$checkdir/log";
APT::Architecture "amd64";
Acquire::Languages "none";
EOF
export APT_CONFIG="$checkdir/apt.conf"
apt-get -o APT::Update::Error-Mode=any update
apt-cache policy "$package"
cd "$checkdir/downloads"
apt-get download "$package"
sha256sum ./*.deb
printf 'Native APT signature verification and package download passed for %s.\n' "$url"

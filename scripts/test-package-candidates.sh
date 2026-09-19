#!/usr/bin/env bash
# Fresh Debian package install, base-files trigger, removal and purge smoke.
set -Eeuo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=versions.env
source "$repo/versions.env"
if [[ ${1:-} != --inside ]]; then
  dist=$(realpath -- "$repo/dist")
  docker run --rm --network none --entrypoint bash \
    --mount "type=bind,src=$repo,dst=/src,readonly" \
    --mount "type=bind,src=$dist/custom-packages/repository,dst=/bootstrap,readonly" \
    --mount "type=bind,src=$dist/custom-packages,dst=/candidates,readonly" \
    "$DEBIAN_CONTAINER" /src/scripts/test-package-candidates.sh --inside
  exit
fi
export DEBIAN_FRONTEND=noninteractive
printf '#!/bin/sh\nexit 101\n' >/usr/sbin/policy-rc.d
chmod 0755 /usr/sbin/policy-rc.d
install -D -m 0644 /src/rootfs/usr/share/keyrings/rougarou-archive-keyring.gpg /usr/share/keyrings/rougarou-archive-keyring.gpg
printf '%s\n' 'deb [signed-by=/usr/share/keyrings/rougarou-archive-keyring.gpg] file:/bootstrap ./' >/tmp/rougarou-test.list
apt_options=(-o Dir::Etc::sourcelist=/tmp/rougarou-test.list -o Dir::Etc::sourceparts=-)
(cd /candidates && sha256sum -c SHA256SUMS)
apt-get "${apt_options[@]}" update
apt-get "${apt_options[@]}" -y --no-install-recommends install /candidates/rougarou-base_*.deb
# A fresh core install must not silently pull optional agents or engines back
# through rougarou-base's Depends field.
for package in rougarou-codex rougarou-herdr rougarou-gemini rougarou-opencode docker.io docker-cli podman rootlesskit; do
  if dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -qx 'install ok installed'; then
    echo "Optional package unexpectedly installed by core: $package" >&2
    exit 1
  fi
done
printf '%s\n' 'PASS: core-only installation contains no optional agents or container engines'
# Now exercise all of the explicitly requested optional offline packages.
repository_roots=$(python3 /src/image/package-profiles.py)
mapfile -t optional_and_core <<< "$repository_roots"
apt-get "${apt_options[@]}" -y --no-install-recommends install /candidates/rougarou-*.deb "${optional_and_core[@]}"
test "$(dpkg-query -W -f='${Status}' rougarou-base)" = 'install ok installed'
test "$(dpkg-query -S /etc/issue)" = 'base-files: /etc/issue'
grep -qx 'ID=rougarou' /etc/os-release
test "$(rougarou version)" = "Rougarou OS $ROUGAROU_VERSION"
codex --help >/tmp/codex-help
grep -q -- '--approve-for-me' /tmp/codex-help
grep -q -- '--no-alt-screen' /tmp/codex-help
test "$(herdr --version)" = "herdr $HERDR_VERSION"
test "$(gemini --version)" = "$GEMINI_VERSION"
gemini --help >/tmp/gemini-help
starship --version
eza --version
batcat --version
# Exercise real Debian sudo behavior in this disposable container: explicit
# owner grant/revocation, no grant to another account, retained local policy,
# ordinary-user AI process and NoNewPrivileges boundary.
ln -s /src /source
python3 /src/tests/vm/operator-access-container.py
state_hashes() {
  sha256sum /etc/passwd /etc/group /etc/shadow /etc/gshadow
  sha256sum /etc/sudoers
  find /etc/sudoers.d -type f -print0 | sort -z | xargs -0 -r sha256sum
  find /etc/apt -type f -print0 | sort -z | xargs -0 sha256sum
  find /etc/network -type f -print0 2>/dev/null | sort -z | xargs -0 -r sha256sum || true
}
state_hashes >/tmp/state.before
/usr/lib/rougarou-system/apply-branding.sh
state_hashes >/tmp/state.after
cmp /tmp/state.before /tmp/state.after
apt-get "${apt_options[@]}" -y -o Dpkg::Options::=--force-confold --reinstall install base-files
grep -qx 'ID=rougarou' /etc/os-release
grep -qx 'ID=debian' /usr/lib/os-release
cmp /etc/issue /usr/share/rougarou/issue
state_hashes >/tmp/state.before-remove
dpkg --remove rougarou-base
grep -qx 'ID=debian' /etc/os-release
state_hashes >/tmp/state.after-remove
cmp /tmp/state.before-remove /tmp/state.after-remove
dpkg --purge rougarou-base
test ! -d /var/lib/rougarou/branding
grep -qx 'ID=debian' /etc/os-release
state_hashes >/tmp/state.after-purge
cmp /tmp/state.before-remove /tmp/state.after-purge
printf '%s\n' 'PASS: fresh offline installation, Codex flags, native operator access, branding reapplication after base-files reinstall, remove/purge restoration, and unchanged account/network/APT policy during branding operations'

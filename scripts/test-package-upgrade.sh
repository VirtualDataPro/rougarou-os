#!/usr/bin/env bash
# Real old-package upgrades exercise dpkg's conffile migration, without host state.
set -Eeuo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=versions.env
source "$repo/versions.env"
if [[ ${1:-} != --inside ]]; then
  dist=$(realpath -- "$repo/dist")
  baseline=${1:-$dist/releases/0.1.0-alpha.4/bootstrap-repository}
  test -d "$baseline"
  for variant in unchanged modified; do
    docker run --rm --network none --entrypoint bash \
      --mount "type=bind,src=$repo,dst=/src,readonly" \
      --mount "type=bind,src=$baseline,dst=/baseline,readonly" \
      --mount "type=bind,src=$dist/custom-packages,dst=/candidates,readonly" \
      --mount "type=bind,src=$dist/custom-packages/repository,dst=/candidate-repository,readonly" \
      "$DEBIAN_CONTAINER" /src/scripts/test-package-upgrade.sh --inside "$variant"
  done
  exit
fi
variant=${2:?Expected unchanged or modified}
[[ $variant == unchanged || $variant == modified ]]
export DEBIAN_FRONTEND=noninteractive
printf '#!/bin/sh\nexit 101\n' >/usr/sbin/policy-rc.d
chmod 0755 /usr/sbin/policy-rc.d
install -D -m 0644 /src/rootfs/usr/share/keyrings/rougarou-archive-keyring.gpg /usr/share/keyrings/rougarou-archive-keyring.gpg
printf '%s\n' 'deb [signed-by=/usr/share/keyrings/rougarou-archive-keyring.gpg] file:/baseline ./' >/tmp/rougarou-test.list
apt_options=(-o Dir::Etc::sourcelist=/tmp/rougarou-test.list -o Dir::Etc::sourceparts=-)
apt-get "${apt_options[@]}" update
# Model the existing lab's three installed custom packages. The baseline
# archive also offers optional agents; their presence must not select them.
baseline_packages=()
for package in rougarou-base rougarou-codex rougarou-herdr; do
  baseline_packages+=(/baseline/"$package"_*.deb)
done
apt-get "${apt_options[@]}" -y --no-install-recommends install "${baseline_packages[@]}"
old=/etc/profile.d/rougarou-readline.sh
old_readline=0
if [[ -f $old ]]; then
  old_readline=1
  if [[ $variant == modified ]]; then
    printf '\n# Preserved operator customization\n' >>"$old"
  fi
  cp "$old" /tmp/old-readline
fi
dpkg-query -W -f='${binary:Package} ${db:Status-Status}\n' | awk '$2 == "installed" {print $1}' | sort >/tmp/packages.before
mkdir -p /etc/default/grub.d
printf 'GRUB_TIMEOUT=17\n# Existing operator boot configuration\n' >/etc/default/grub.d/50-rougarou.cfg
cp /etc/default/grub.d/50-rougarou.cfg /tmp/grub.before
useradd --create-home --shell /bin/bash upgradeowner
printf '# operator bashrc sentinel\n' >/home/upgradeowner/.bashrc
printf '# operator root shell sentinel\n' >/root/.bashrc
mkdir -p /home/upgradeowner/.config/herdr
printf '[update]\nversion_check = true\n' >/home/upgradeowner/.config/herdr/config.toml
find /home/upgradeowner /root -type f -print0 | sort -z | xargs -0 sha256sum >/tmp/homes.before
printf '%s\n' 'deb [signed-by=/usr/share/keyrings/rougarou-archive-keyring.gpg] file:/candidate-repository ./' >/tmp/rougarou-test.list
(cd /candidates && sha256sum -c SHA256SUMS)
apt-get "${apt_options[@]}" update
upgrade_packages=()
for package in rougarou-base rougarou-codex rougarou-herdr; do
  upgrade_packages+=(/candidates/"$package"_*.deb)
done
apt-get "${apt_options[@]}" -y --no-install-recommends -o Dpkg::Options::=--force-confold install "${upgrade_packages[@]}"
test ! -e "$old"
if [[ $old_readline == 1 && $variant == modified ]]; then
  cmp /tmp/old-readline "$old.dpkg-bak"
else
  test ! -e "$old.dpkg-bak"
fi
test ! -e "$old.dpkg-remove"
test ! -e "$old.dpkg-backup"
find /home/upgradeowner /root -type f -print0 | sort -z | xargs -0 sha256sum >/tmp/homes.after
cmp /tmp/homes.before /tmp/homes.after
cmp /tmp/grub.before /etc/default/grub.d/50-rougarou.cfg
dpkg-query -W -f='${binary:Package} ${db:Status-Status}\n' | awk '$2 == "installed" {print $1}' | sort >/tmp/packages.after
test -z "$(comm -23 /tmp/packages.before /tmp/packages.after)"
! dpkg-query -W -f='${Status}' rougarou-gemini 2>/dev/null | grep -qx 'install ok installed'
test "$(rougarou version)" = "Rougarou OS $ROUGAROU_VERSION"
printf 'PASS: existing release upgrade (%s conffile case), no removed packages or new agent, operator homes and boot configuration unchanged\n' "$variant"

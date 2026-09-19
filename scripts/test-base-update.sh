#!/usr/bin/env bash
# Install one new base package with only authenticated, unchanged dependencies.
set -Eeuo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=../versions.env
source "$repo/versions.env"
if [[ ${1:-} != --inside ]]; then
  [[ $# == 2 ]] || { echo "Usage: $0 BASE_UPDATE_DIRECTORY SIGNED_BASELINE_REPOSITORY" >&2; exit 2; }
  candidate=$(realpath -- "$1")
  baseline=$(realpath -- "$2")
  for scenario in fresh upgrade; do
    docker run --rm --pull never --network none --entrypoint bash \
      --mount "type=bind,src=$repo,dst=/src,readonly" \
      --mount "type=bind,src=$candidate,dst=/candidate,readonly" \
      --mount "type=bind,src=$baseline,dst=/baseline,readonly" \
      "$DEBIAN_CONTAINER" /src/scripts/test-base-update.sh --inside "$scenario"
  done
  exit
fi
scenario=${2:?Missing test scenario}
[[ $scenario == fresh || $scenario == upgrade ]]
export DEBIAN_FRONTEND=noninteractive
printf '#!/bin/sh\nexit 101\n' >/usr/sbin/policy-rc.d
chmod 0755 /usr/sbin/policy-rc.d
install -D -m 0644 /src/rootfs/usr/share/keyrings/rougarou-archive-keyring.gpg /usr/share/keyrings/rougarou-archive-keyring.gpg
printf '%s\n' 'deb [signed-by=/usr/share/keyrings/rougarou-archive-keyring.gpg] file:/baseline ./' >/tmp/rougarou-test.list
apt_options=(-o Dir::Etc::sourcelist=/tmp/rougarou-test.list -o Dir::Etc::sourceparts=-)
(cd /candidate && sha256sum -c SHA256SUMS)
candidate_debs=(/candidate/rougarou-base_*.deb)
[[ ${#candidate_debs[@]} == 1 && -f ${candidate_debs[0]} ]]
version=$(dpkg-deb -f "${candidate_debs[0]}" Version)
apt-get "${apt_options[@]}" update

inventory() {
  dpkg-query -W -f='${binary:Package}\t${Version}\t${db:Status-Status}\n' | sort
}
policy_hashes() {
  sha256sum /etc/passwd /etc/group /etc/shadow /etc/gshadow /etc/sudoers
  find /etc/sudoers.d /etc/apt -type f -print0 | sort -z | xargs -0 -r sha256sum
}

if [[ $scenario == upgrade ]]; then
  # Resolve only these three names from the authenticated baseline index.
  # This supports both a flat ISO repository and a snapshot's pool layout.
  apt-get "${apt_options[@]}" -y --no-install-recommends install rougarou-base rougarou-codex rougarou-herdr
  dpkg --compare-versions "$version" gt "$(dpkg-query -W -f='${Version}' rougarou-base)"
  inventory >/tmp/inventory.before
  useradd --create-home --shell /bin/bash updateowner
  printf '# retain ordinary-user shell edits\n' >/home/updateowner/.bashrc
  printf '# retain root shell edits\n' >/root/.bashrc
  mkdir -p /home/updateowner/.config/rougarou /home/updateowner/.local/state/rougarou
  printf 'format = "operator theme"\n' >/home/updateowner/.config/starship.toml
  printf '{"provider":"fixture-only"}\n' >/home/updateowner/.config/rougarou/config.json
  # A valid local database stands in for pre-existing private operator data.
  python3 - <<'PY'
import sqlite3
with sqlite3.connect('/home/updateowner/.local/state/rougarou/preservation-fixture.sqlite') as db:
    db.execute('CREATE TABLE history (id INTEGER PRIMARY KEY, status TEXT)')
    db.execute("INSERT INTO history VALUES (1, 'retained operator history')")
PY
  chown -R updateowner:updateowner /home/updateowner
  mkdir -p /etc/default/grub.d
  printf 'GRUB_TIMEOUT=17\n# retain operator boot policy\n' >/etc/default/grub.d/50-rougarou.cfg
  cp /etc/default/grub.d/50-rougarou.cfg /tmp/grub.before
  find /home/updateowner /root -type f -print0 | sort -z | xargs -0 sha256sum >/tmp/homes.before
  policy_hashes >/tmp/policy.before
fi

apt-get "${apt_options[@]}" -y --no-install-recommends --no-remove \
  -o Dpkg::Options::=--force-confold install "${candidate_debs[0]}"
test "$(dpkg-query -W -f='${Version}' rougarou-base)" = "$version"
test "$(rougarou version)" = "Rougarou OS $ROUGAROU_VERSION"
test -z "$(dpkg --audit)"

if [[ $scenario == upgrade ]]; then
  inventory >/tmp/inventory.after
  python3 - "$version" <<'PY'
from pathlib import Path
import sys
def inventory(name):
    return dict((parts[0], parts[1:]) for line in Path(name).read_text().splitlines()
                if (parts := line.split('\t')))
before, after = inventory('/tmp/inventory.before'), inventory('/tmp/inventory.after')
expected = before.copy()
expected['rougarou-base'] = [sys.argv[1], 'installed']
assert after == expected, 'Transaction changed packages other than rougarou-base'
PY
  find /home/updateowner /root -type f -print0 | sort -z | xargs -0 sha256sum >/tmp/homes.after
  cmp /tmp/homes.before /tmp/homes.after
  policy_hashes >/tmp/policy.after
  cmp /tmp/policy.before /tmp/policy.after
  cmp /tmp/grub.before /etc/default/grub.d/50-rougarou.cfg
  echo 'PASS: only base version changed; agents, dependencies, operator homes/database, account/sudo/APT policy and boot configuration preserved'
else
  for package in rougarou-codex rougarou-herdr rougarou-gemini docker.io docker-cli podman rootlesskit; do
    if dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -qx 'install ok installed'; then
      echo "Unexpected optional package: $package" >&2; exit 1
    fi
  done
  apt-get "${apt_options[@]}" -y -o Dpkg::Options::=--force-confold --reinstall install base-files
  grep -qx 'ID=rougarou' /etc/os-release
  grep -qx 'ID=debian' /usr/lib/os-release
  policy_hashes >/tmp/policy.before-remove
  dpkg --remove rougarou-base
  grep -qx 'ID=debian' /etc/os-release
  dpkg --purge rougarou-base
  policy_hashes >/tmp/policy.after-remove
  cmp /tmp/policy.before-remove /tmp/policy.after-remove
  echo 'PASS: fresh base-only install, branding trigger and remove/purge lifecycle without agents, engines or policy changes'
fi

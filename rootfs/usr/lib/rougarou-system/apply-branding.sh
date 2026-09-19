#!/bin/sh
# Branding only: safe for package configure and Debian base-files triggers.
set -eu
test "$(id -u)" = 0
templates=/usr/share/rougarou
state=/var/lib/rougarou/branding
install -d -m 0700 "$state"
for name in issue motd os-release; do
  test -f "$templates/$name"
  if [ ! -f "$state/$name.saved" ]; then
    if [ -e "/etc/$name" ] || [ -L "/etc/$name" ]; then
      cp -a "/etc/$name" "$state/$name.original"
    else
      touch "$state/$name.absent"
    fi
    touch "$state/$name.saved"
  fi
  install -m 0644 "$templates/$name" "/etc/.$name.rougarou-new"
  mv -f "/etc/.$name.rougarou-new" "/etc/$name"
  cp "$templates/$name" "$state/$name.applied"
done
# Debian owns its underlying release record; keep its current data available.
cp -L /usr/lib/os-release /usr/lib/os-release.debian

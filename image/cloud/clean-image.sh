#!/bin/sh
# Only the disposable builder calls this immediately before final shutdown.
set -eu
test -f /var/lib/rougarou-cloud-build-ready
cloud-init status --wait >/dev/null
cloud-init clean --logs --machine-id --seed
# Stop first: ExecStop saves the seed and would otherwise recreate it later.
systemctl stop systemd-random-seed.service
! systemctl is-active --quiet systemd-random-seed.service
apt-get clean
rm -f /etc/ssh/ssh_host_* /var/lib/systemd/random-seed /var/lib/urandom/random-seed
rm -f /var/lib/dbus/machine-id
ln -s /etc/machine-id /var/lib/dbus/machine-id
rm -rf /var/log/journal/* /run/log/journal/* /var/lib/dhcp/* /var/lib/dhcpcd/*
find /var/log -type f -exec truncate -s 0 '{}' ';'
rm -f /root/.bash_history /root/.lesshst /root/.wget-hsts
rm -rf /root/.ssh /root/.cache /tmp/* /var/tmp/* /var/lib/apt/lists/*
rm -f /var/lib/rougarou-cloud-build-ready /var/lib/rougarou-cloud-build-packages.tsv
rm -f /usr/local/sbin/rougarou-cloud-build /usr/local/sbin/rougarou-cloud-clean
sync
fstrim -av
test ! -e /var/lib/systemd/random-seed

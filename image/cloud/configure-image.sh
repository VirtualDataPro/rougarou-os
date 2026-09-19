#!/bin/sh
# Executed only in a new disposable image-build guest, never on a user host.
set -eu
exec > /var/log/rougarou-cloud-build.log 2>&1
trap 'echo "ROUGAROU_CLOUD_BUILD_FAILED:$?" > /dev/ttyS0' EXIT
mkdir -p /media/rougarou-build /var/cache/rougarou
printf 'ROUGAROU_CLOUD_STAGE:copy-repository\n' > /dev/ttyS0
mount -o ro LABEL=ROUGAROU_PKGS /media/rougarou-build
cp -a /media/rougarou-build/repository /var/cache/rougarou/repo
install -D -m 0644 /media/rougarou-build/archive-keyring.gpg /usr/share/keyrings/rougarou-archive-keyring.gpg
# APT authenticates Release and each package against the explicitly pinned key.
mkdir -p /etc/apt/sources.list.d /etc/apt/disabled-for-image-build
[ ! -f /etc/apt/sources.list ] || mv /etc/apt/sources.list /etc/apt/disabled-for-image-build/
find /etc/apt/sources.list.d -type f -maxdepth 1 -exec mv '{}' /etc/apt/disabled-for-image-build/ ';'
cat > /etc/apt/sources.list.d/rougarou.sources <<'SOURCE'
Types: deb
URIs: file:/var/cache/rougarou/repo
Suites: ./
Signed-By: /usr/share/keyrings/rougarou-archive-keyring.gpg
SOURCE
printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d
chmod 755 /usr/sbin/policy-rc.d
mkdir -p /etc/systemd/system
for unit in docker.service docker.socket containerd.service; do
    ln -sfn /dev/null "/etc/systemd/system/$unit"
done
export DEBIAN_FRONTEND=noninteractive
printf 'ROUGAROU_CLOUD_STAGE:apt-update\n' > /dev/ttyS0
apt-get update
# Roots come from the reviewed core/profile resolver, validated by the builder.
printf 'ROUGAROU_CLOUD_STAGE:install-packages\n' > /dev/ttyS0
xargs -r apt-get install -y --no-install-recommends --no-remove < /media/rougarou-build/install-roots
rm /usr/sbin/policy-rc.d
printf 'ROUGAROU_CLOUD_STAGE:configure-target\n' > /dev/ttyS0
/usr/lib/rougarou-system/configure-target.sh
# The cloud path uses native cloud-init user authority. No user is baked in,
# and configure-target's Debian Installer source backup is not retained.
rm -rf /etc/apt/disabled-for-image-build
rm -f /etc/apt/sources.list.installer /etc/apt/sources.list.d/*.installer
cat > /etc/rougarou/install-software <<'SELECTION'
agent=none
docker=rootless
podman=false
herdr=false
SELECTION
# Prevent vendor package refreshes and credentials; users explicitly define
# their own account/key and sudo authority in NoCloud user-data.
cat > /etc/cloud/cloud.cfg.d/90-rougarou.cfg <<'CONFIG'
users: []
disable_root: true
ssh_pwauth: false
package_update: false
package_upgrade: false
preserve_hostname: false
datasource_list: [NoCloud, None]
CONFIG
cat > /etc/ssh/sshd_config.d/01-rougarou-cloud.conf <<'SSH'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
SSH
# Never carry an upstream unlocked login or service account with a home shell.
# Official pristine generic image has no UID>=1000 users before cloud-init.
if getent passwd | awk -F: '$3>=1000 && $3<60000 {found=1} END{exit !found}'; then
    echo 'Unexpected pre-created operator in pristine image' >&2
    exit 1
fi
# Disable unattended periodic upgrades even if upstream cloud defaults differ.
cat > /etc/apt/apt.conf.d/99rougarou-cloud <<'APT'
APT::Periodic::Update-Package-Lists "0";
APT::Periodic::Unattended-Upgrade "0";
Unattended-Upgrade::Automatic-Reboot "false";
APT
systemctl enable qemu-guest-agent.service 2>/dev/null || true
systemctl start qemu-guest-agent.service
systemctl enable ssh.service
sshd -t
dpkg-query -W '-f=${binary:Package}\t${Version}\n' > /var/lib/rougarou-cloud-build-packages.tsv
printf 'ROUGAROU_CLOUD_BUILD_READY\n' > /var/lib/rougarou-cloud-build-ready
trap - EXIT
printf 'ROUGAROU_CLOUD_BUILD_READY\n' > /dev/ttyS0

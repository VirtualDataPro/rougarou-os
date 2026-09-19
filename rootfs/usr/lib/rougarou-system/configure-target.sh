#!/bin/sh
# Run ONLY inside the newly installed target. Called by the ISO late hook.
set -eu
test "$(id -u)" = 0 || { echo 'Target configuration requires root.' >&2; exit 1; }
test -f /etc/debian_version || { echo 'Expected Debian target.' >&2; exit 1; }
test -f /etc/rougarou/release || { echo 'Rougarou payload missing.' >&2; exit 1; }

/usr/lib/rougarou-system/apply-branding.sh
/usr/lib/rougarou-system/configure-grub --install-defaults

systemctl set-default multi-user.target
systemctl enable ssh.service
# qemu-guest-agent's static unit is pulled in by its virtio device udev rule.
# Enabling the service explicitly may be unsupported; vendor udev integration
# starts it when Proxmox exposes org.qemu.guest_agent.0.
systemctl enable fstrim.timer
systemctl enable serial-getty@ttyS0.service

# Explicit first-install shell defaults: preserve local lines and back up rc files.
/usr/lib/rougarou-system/configure-shell --skel
/usr/lib/rougarou-system/configure-shell
# Do not overwrite any existing operator keys.
getent passwd | while IFS=: read -r name unused uid gid gecos home shell; do
  if [ "$uid" -ge 1000 ] && [ "$uid" -lt 60000 ] && [ -d "$home" ]; then
    install -d -m 0750 -o "$uid" -g "$gid" "$home/Work"
    usermod -a -G sudo "$name"
    runuser -u "$name" -- /usr/lib/rougarou-system/configure-shell
  fi
done

# The signed baseline remains available until a hosted channel is configured.
# Ordinary Debian stable updates must pass through Rougarou promotion first.
cat > /etc/apt/sources.list.d/rougarou.sources <<'EOF'
Types: deb
URIs: file:/var/cache/rougarou/repo
Suites: ./
Signed-By: /usr/share/keyrings/rougarou-archive-keyring.gpg
EOF

# Security fixes explicitly bypass the ordinary promotion delay.
cat > /etc/apt/sources.list.d/debian-security.sources <<'EOF'
Types: deb
URIs: https://security.debian.org/debian-security
Suites: trixie-security
Components: main non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
EOF
# Preserve install-time sources for reference; avoid duplicate/CD-ROM entries.
if [ -f /etc/apt/sources.list ]; then
  mv /etc/apt/sources.list /etc/apt/sources.list.installer
fi
if [ -f /etc/apt/sources.list.d/debian.sources ]; then
  mv /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list.d/debian.sources.installer
fi
cat > /etc/apt/apt.conf.d/52rougarou <<'EOF'
// Security updates are available; maintenance remains operator controlled.
Unattended-Upgrade::Automatic-Reboot "false";
EOF

# These are generated per installation, never copied from the build host.
ssh-keygen -A
mkdir -p /run/sshd
sshd -t
printf '%s\n' 'Rougarou target configuration complete.'

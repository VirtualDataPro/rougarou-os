#!/bin/sh
# Invoked ONLY by Debian Installer, after the operator installs a target system.
set -eu
test -d /target/etc
test -f /target/etc/debian_version
test -f /cdrom/rougarou/repo.tar
. /usr/share/debconf/confmodule
# cdebconf keeps its protocol on fd 3 while command output goes to the log.
# Update the existing finish-install progress window, without nesting a second
# progress bar or pretending package installation has a known duration.
stage='Preparing installation'
stage_id=''
stage_started=0
monotonic_seconds() {
    read -r uptime remainder < /proc/uptime
    printf '%s\n' "${uptime%%.*}"
}
timing_end() {
    if [ -n "$stage_id" ]; then
        now=$(monotonic_seconds)
        printf 'ROUGAROU_TIMING stage=%s seconds=%s\n' "$stage_id" "$((now - stage_started))"
    fi
}
progress() {
    timing_end
    stage_id=$1
    stage_started=$(monotonic_seconds)
    stage=$2
    printf 'ROUGAROU_STAGE stage=%s started=%s utc=%s\n' "$stage_id" "$stage_started" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    db_progress INFO "rougarou/stage-$1" || true
}
failed() {
    status=$?
    trap - EXIT
    timing_end
    if [ "$status" -ne 0 ]; then
        db_progress STOP || true
        db_subst rougarou/install-error STAGE "$stage" || true
        db_input critical rougarou/install-error || true
        db_go || true
    fi
    exit "$status"
}
trap failed EXIT
exec >>/target/var/log/rougarou-install.log 2>&1
progress verify 'Verifying installation media'
echo 'Verifying the Rougarou installation payload...'
cd /cdrom/rougarou
sha256sum -c SHA256SUMS
test -s /run/rougarou/software
/bin/sh /cdrom/rougarou/select-packages.sh /cdrom/rougarou/packages.txt \
    /cdrom/rougarou/package-profiles.txt /run/rougarou/software > /run/rougarou/selected-packages
test -s /run/rougarou/selected-packages
docker_mode=$(sed -n 's/^docker=//p' /run/rougarou/software)
progress unpack 'Preparing signed offline packages'
mkdir -p /target/var/cache/rougarou/repo
tar -xf /cdrom/rougarou/repo.tar -C /target/var/cache/rougarou/repo
tar -xzf /cdrom/rougarou/rootfs.tar.gz -C /target
mkdir -p /target/usr/share/doc/rougarou-os
cp /cdrom/rougarou/package-manifest.json /target/usr/share/doc/rougarou-os/
cp /cdrom/rougarou/build-manifest.json /target/usr/share/doc/rougarou-os/
cp /cdrom/rougarou/packages.txt /target/var/cache/rougarou/packages.txt
printf '%s\n' 'deb [signed-by=/usr/share/keyrings/rougarou-archive-keyring.gpg] file:/var/cache/rougarou/repo ./' > /target/var/cache/rougarou/offline.list
# The immutable bootstrap repository carries a Rougarou release signature.
in-target apt-get -o Dir::Etc::sourcelist=/var/cache/rougarou/offline.list -o Dir::Etc::sourceparts=- update
progress packages 'Installing your selected tools'
# When rootless Docker was selected, block vendor system services before
# their package maintainer scripts run in this freshly installed target.
# Each operator can start the separately packaged user service when wanted.
if [ "$docker_mode" = rootless ]; then
  mkdir -p /target/etc/systemd/system
  for unit in docker.service docker.socket containerd.service; do
    if [ -L "/target/etc/systemd/system/$unit" ] && \
       [ "$(readlink "/target/etc/systemd/system/$unit")" = /dev/null ]; then
        continue
    fi
    test ! -e "/target/etc/systemd/system/$unit"
    test ! -L "/target/etc/systemd/system/$unit"
    ln -s /dev/null "/target/etc/systemd/system/$unit"
  done
fi
packages=$(tr '\n' ' ' < /run/rougarou/selected-packages)
# Intentional word splitting: package names are validated by the image builder.
# shellcheck disable=SC2086
in-target env DEBIAN_FRONTEND=noninteractive apt-get -y --no-install-recommends -o Dir::Etc::sourcelist=/var/cache/rougarou/offline.list -o Dir::Etc::sourceparts=- install $packages
progress configure 'Configuring server and operator access'
mkdir -p /target/etc/rougarou
cp /run/rougarou/software /target/etc/rougarou/install-software
chmod 0644 /target/etc/rougarou/install-software
if [ "$docker_mode" = system ]; then
    in-target systemctl enable docker.service docker.socket containerd.service
fi
in-target /usr/lib/rougarou-system/configure-target.sh
# Apply only the mode recorded by the dedicated operator-choice component.
# A default in a template by itself is not an instruction to grant root access.
test -s /run/rougarou/operator
test -s /run/rougarou/operator-access
operator=$(cat /run/rougarou/operator)
access=$(cat /run/rougarou/operator-access)
db_get passwd/username
test "$RET" = "$operator"
case "$access" in
  headless)
    in-target /usr/lib/rougarou-system/operator-access set "$operator" headless --acknowledge-root-access
    ;;
  password)
    in-target /usr/lib/rougarou-system/operator-access set "$operator" password
    ;;
  *) echo 'Invalid operator access mode'; exit 1 ;;
esac
rm -f /target/var/cache/rougarou/offline.list
progress finish 'Finishing installation'
echo 'Rougarou OS installation completed.'

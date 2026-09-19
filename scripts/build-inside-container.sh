#!/usr/bin/env bash
set -Eeuo pipefail
umask 022
cd /src
# shellcheck source=versions.env
source /src/versions.env
[[ $DEBIAN_ARCH == amd64 ]] || { echo 'Only amd64 is validated by this image.' >&2; exit 1; }
[[ -x /src/rootfs/usr/lib/rougarou-system/configure-target.sh ]] || { echo 'Missing executable configure-target.sh payload' >&2; exit 1; }
mapfile -t packages < <(sed '/^[[:space:]]*#/d; /^[[:space:]]*$/d' /src/packages.txt)
for package in "${packages[@]}"; do
  [[ $package =~ ^[a-z0-9][a-z0-9+.-]*$ ]] || { echo "Invalid package: $package" >&2; exit 1; }
done
# Validate the shared profile contract before resolving its full offline union.
# Use a real command before mapfile: process substitution does not propagate
# Python failures through Bash's errexit setting.
debian_roots=$(python3 /src/image/package-profiles.py)
mapfile -t repository_packages <<< "$debian_roots"
build=$(mktemp -d /tmp/rougarou-build.XXXXXX)
trap 'rm -rf "$build"' EXIT
mkdir -p /cache/debian /cache/codex "$build/overlay/rougarou/repo" "$build/rootfs" /out
fetch() {
  local url=$1 target=$2
  if [[ ! -f $target ]]; then
    curl --fail --location --retry 5 --connect-timeout 30 "$url" -o "$target.part"
    mv "$target.part" "$target"
  fi
}

iso="debian-${DEBIAN_VERSION}-${DEBIAN_ARCH}-netinst.iso"
iso_url="https://cdimage.debian.org/debian-cd/${DEBIAN_VERSION}/${DEBIAN_ARCH}/iso-cd"
fetch "$iso_url/SHA512SUMS" "/cache/debian/${DEBIAN_VERSION}-SHA512SUMS"
fetch "$iso_url/SHA512SUMS.sign" "/cache/debian/${DEBIAN_VERSION}-SHA512SUMS.sign"
keyring=/usr/share/keyrings/debian-role-keys.pgp
[[ -f $keyring ]] || keyring=/usr/share/keyrings/debian-role-keys.gpg
gpgv --keyring "$keyring" "/cache/debian/${DEBIAN_VERSION}-SHA512SUMS.sign" "/cache/debian/${DEBIAN_VERSION}-SHA512SUMS"
fetch "$iso_url/$iso" "/cache/debian/$iso"
(cd /cache/debian && awk -v name="$iso" '$2 == name {print}' "${DEBIAN_VERSION}-SHA512SUMS" > "$build/debian-checksum" && test -s "$build/debian-checksum" && sha512sum -c "$build/debian-checksum")

# Signed Debian metadata plus apt's package hashes authenticate every downloaded deb.
# An empty dpkg status forces downloading the dependency closure, including tools
# already installed in this builder. No --allow-unauthenticated is ever used.
apt-get update
mkdir -p "$build/archives/partial"
touch "$build/empty-status"
apt-get -y --download-only --no-install-recommends \
  -o Dir::State::status="$build/empty-status" \
  -o Dir::Cache::archives="$build/archives" \
  install "${repository_packages[@]}"
cp "$build/archives/"*.deb "$build/overlay/rougarou/repo/"
cp /src/packages.txt "$build/overlay/rougarou/packages.txt"
printf '\nrougarou-base\n' >> "$build/overlay/rougarou/packages.txt"
cp /src/image/package-profiles.txt "$build/overlay/rougarou/package-profiles.txt"

fetch "https://github.com/openai/codex/releases/download/rust-v${CODEX_VERSION}/${CODEX_ASSET}" "/cache/codex/${CODEX_VERSION}-${CODEX_ASSET}"
printf '%s  %s\n' "$CODEX_SHA256" "/cache/codex/${CODEX_VERSION}-${CODEX_ASSET}" | sha256sum -c -
mkdir -p "$build/codex/opt/codex" "$build/codex/usr/bin" "$build/codex/usr/share/doc/rougarou-codex"
tar -xzf "/cache/codex/${CODEX_VERSION}-${CODEX_ASSET}" -C "$build/codex/opt/codex" --no-same-owner
ln -sf /opt/codex/bin/codex "$build/codex/usr/bin/codex"
fetch "https://raw.githubusercontent.com/openai/codex/rust-v${CODEX_VERSION}/LICENSE" "/cache/codex/${CODEX_VERSION}-LICENSE"
fetch "https://raw.githubusercontent.com/openai/codex/rust-v${CODEX_VERSION}/NOTICE" "/cache/codex/${CODEX_VERSION}-NOTICE"
cp "/cache/codex/${CODEX_VERSION}-LICENSE" "$build/codex/usr/share/doc/rougarou-codex/copyright"
cp "/cache/codex/${CODEX_VERSION}-NOTICE" "$build/codex/usr/share/doc/rougarou-codex/NOTICE"
"$build/codex/opt/codex/bin/codex" --version

# Own the OS integration and AI runtime with dpkg so the signed update channel
# can upgrade them. No maintainer script changes accounts or APT policy later.
mkdir -p "$build/codex/DEBIAN"
base_version=${ROUGAROU_VERSION/-alpha./~alpha}
base_version="${base_version}-${ROUGAROU_BASE_REVISION:-$ROUGAROU_PACKAGE_REVISION}"
cat > "$build/codex/DEBIAN/control" <<EOF
Package: rougarou-codex
Version: ${CODEX_VERSION}+rougarou${ROUGAROU_PACKAGE_REVISION}
Architecture: amd64
Installed-Size: $(du -sk --exclude=DEBIAN "$build/codex" | cut -f1)
Maintainer: Rougarou OS maintainers <rougarou-os@users.noreply.github.com>
Section: devel
Priority: optional
Depends: ca-certificates
Description: Verified upstream Codex CLI and its companion runtime
EOF
base_deb="rougarou-base_${base_version}_all.deb"
codex_deb="rougarou-codex_${CODEX_VERSION}+rougarou${ROUGAROU_PACKAGE_REVISION}_amd64.deb"
python3 /src/image/package-input-manifest.py > "$build/package-inputs.json"
if [[ ${ROUGAROU_USE_CANDIDATES:-0} == 1 ]]; then
  cmp "$build/package-inputs.json" /out/custom-packages/package-inputs.json
  (cd /out/custom-packages && sha256sum -c SHA256SUMS)
  # Reuse the entire tested closure, including new Debian dependencies. Never
  # substitute packages newly resolved between candidate testing and assembly.
  rm -rf "$build/overlay/rougarou/repo"
  mkdir "$build/overlay/rougarou/repo"
  cp /out/custom-packages/repository/*.deb "$build/overlay/rougarou/repo/"
  cp -a /out/custom-packages/sources "$build/overlay/rougarou/"
  cp /out/custom-packages/external-packages.json "$build/overlay/rougarou/"
else
  bash /src/image/build-base-package.sh "$build/rootfs" "$build/overlay/rougarou/repo/$base_deb"
  dpkg-deb --root-owner-group -Zgzip --build "$build/codex" "$build/overlay/rougarou/repo/$codex_deb"
  export HERDR_VERSION HERDR_ASSET HERDR_SHA256 HERDR_SOURCE_SHA256 ROUGAROU_PACKAGE_REVISION
  export GEMINI_VERSION GEMINI_ASSET GEMINI_SHA256 GEMINI_SOURCE_SHA256
  mkdir -p "$build/overlay/rougarou/sources"
  external_manifests=()
  for external in herdr gemini; do
    python3 "/src/image/build-$external.py" "$build/$external" "$build/overlay/rougarou/repo" "/cache/$external"
    cp -a "$build/$external/sources/." "$build/overlay/rougarou/sources/"
    external_manifests+=("$build/$external/external-packages.json")
  done
  python3 - "$build/overlay/rougarou/external-packages.json" "${external_manifests[@]}" <<'PY'
import json
from pathlib import Path
import sys
merged = {}
for filename in sys.argv[2:]:
    entries = json.loads(Path(filename).read_text())
    if not entries or merged.keys() & entries.keys():
        raise SystemExit('Duplicate or empty external package provenance')
    merged.update(entries)
Path(sys.argv[1]).write_text(json.dumps(merged, indent=2) + '\n')
PY
fi
(cd "$build/overlay/rougarou/repo" && dpkg-scanpackages --multiversion . /dev/null > Packages)
mkdir -m 0700 "$build/gnupg"
cp -a /run/rougarou-signing/pubring.kbx /run/rougarou-signing/trustdb.gpg /run/rougarou-signing/private-keys-v1.d "$build/gnupg/"
chmod -R go-rwx "$build/gnupg"
python3 /src/scripts/repo.py sign-flat --repo "$build/overlay/rougarou/repo" \
  --gnupghome "$build/gnupg" --key "$ROUGAROU_SIGNING_KEY" --suite stable --valid-days 3650
gpgv --keyring /src/rootfs/usr/share/keyrings/rougarou-archive-keyring.gpg "$build/overlay/rougarou/repo/InRelease"
if [[ ${ROUGAROU_PACKAGES_ONLY:-0} == 1 ]]; then
  candidates=$(mktemp -d /out/custom-packages.next.XXXXXX)
  cp "$build/overlay/rougarou/repo/"rougarou-*.deb "$candidates/"
  cp -a "$build/overlay/rougarou/repo" "$candidates/repository"
  cp -a "$build/overlay/rougarou/sources" "$candidates/"
  cp "$build/overlay/rougarou/external-packages.json" "$candidates/"
  cp "$build/package-inputs.json" "$candidates/"
  # shellcheck disable=SC2094
  (cd "$candidates" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
  if [[ -d /out/custom-packages ]]; then
    previous=$(mktemp -d /out/custom-packages.previous.XXXXXX)
    mv /out/custom-packages "$previous/packages"
    printf 'Previous candidate bytes retained under %s\n' "$previous/packages"
  fi
  mv "$candidates" /out/custom-packages
  gpgconf --homedir "$build/gnupg" --kill gpg-agent || true
  echo 'Custom package candidates prepared; ISO unchanged.'
  exit
fi
# Only the public key is bootstrapped before APT; packages own all other files.
tar --sort=name --owner=0 --group=0 --numeric-owner -czf "$build/overlay/rougarou/rootfs.tar.gz" -C /src/rootfs usr/share/keyrings/rougarou-archive-keyring.gpg
cp /src/image/preseed.cfg /src/image/install-target.sh /src/image/select-packages.sh "$build/overlay/rougarou/"
chmod 0755 "$build/overlay/rougarou/install-target.sh" "$build/overlay/rougarou/select-packages.sh"

# Rebuild only the native text frontend, retaining Debian's kernel/modules and
# installer logic. Ship its pristine corresponding source and patch tools.
mkdir -p "$build/overlay/install.amd"
xorriso -osirrox on -indev "/cache/debian/$iso" \
  -extract /install.amd/initrd.gz "$build/original-initrd.gz"
bash /src/scripts/build-installer-frontend.sh "$build/original-initrd.gz" "$build/installer"
cp "$build/installer/initrd.gz" "$build/overlay/install.amd/initrd.gz"
cp -a "$build/installer/sources/." "$build/overlay/rougarou/sources/"
cp "$build/installer/installer-manifest.json" "$build/overlay/rougarou/"

export ROUGAROU_VERSION DEBIAN_CODENAME DEBIAN_VERSION DEBIAN_ARCH DEBIAN_CONTAINER CODEX_VERSION CODEX_ASSET CODEX_SHA256
python3 /src/image/write-manifests.py "$build/overlay/rougarou" "$iso_url/$iso" /src
# apt-cdrom recursively scans media for Packages/InRelease before late_command.
# Keep our signed repository opaque until its public key has been installed.
tar --sort=name --owner=0 --group=0 --numeric-owner -cf "$build/overlay/rougarou/repo.tar" -C "$build/overlay/rougarou/repo" .
mv "$build/overlay/rougarou/repo" "$build/repository"
mkdir -p "$build/overlay/boot/grub" "$build/overlay/isolinux" "$build/overlay/.disk"
cp /src/image/grub.cfg "$build/overlay/boot/grub/grub.cfg"
cp /src/image/isolinux.cfg "$build/overlay/isolinux/isolinux.cfg"
cp /usr/lib/syslinux/modules/bios/menu.c32 "$build/overlay/isolinux/menu.c32"
printf 'Rougarou OS %s amd64 - Debian %s\n' "$ROUGAROU_VERSION" "$DEBIAN_VERSION" > "$build/overlay/.disk/info"
# The output checksum index is explicitly excluded from its own input set.
# shellcheck disable=SC2094
(cd "$build/overlay/rougarou" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)

output="rougarou-os-${ROUGAROU_VERSION}-${DEBIAN_ARCH}.iso"
rm -f "/out/$output.partial"
# Replay preserves the upstream hybrid MBR/GPT, BIOS El Torito and UEFI images.
xorriso -indev "/cache/debian/$iso" -outdev "/out/$output.partial" \
  -boot_image any replay -volid ROUGAROU_OS \
  -map "$build/overlay/rougarou" /rougarou \
  -map "$build/overlay/install.amd/initrd.gz" /install.amd/initrd.gz \
  -map "$build/overlay/boot/grub/grub.cfg" /boot/grub/grub.cfg \
  -map "$build/overlay/isolinux/isolinux.cfg" /isolinux/isolinux.cfg \
  -map "$build/overlay/isolinux/menu.c32" /isolinux/menu.c32 \
  -map "$build/overlay/.disk/info" /.disk/info \
  -commit -end

# Recompute Debian's media-check index after changing files (exclude the boot
# catalog and isolinux.bin: the ISO writer patches these during image creation).
mkdir -p "$build/checktree"
xorriso -osirrox on -indev "/out/$output.partial" -extract / "$build/checktree"
(cd "$build/checktree" && find . -type f ! -path './isolinux/boot.cat' ! -path './isolinux/isolinux.bin' ! -path './md5sum.txt' -print0 | sort -z | xargs -0 md5sum > "$build/md5sum.txt")
rm -f "/out/$output.next"
xorriso -indev "/out/$output.partial" -outdev "/out/$output.next" -boot_image any replay -map "$build/md5sum.txt" /md5sum.txt -commit -end
mv "/out/$output.next" "/out/$output"
rm "/out/$output.partial"
(cd /out && sha256sum "$output" > "$output.sha256")
gpg --homedir "$build/gnupg" --batch --yes --armor --local-user "$ROUGAROU_SIGNING_KEY" --detach-sign "/out/$output.sha256"
gpgv --keyring /src/rootfs/usr/share/keyrings/rougarou-archive-keyring.gpg "/out/$output.sha256.asc" "/out/$output.sha256"
gpgconf --homedir "$build/gnupg" --kill gpg-agent || true
rm -rf "$build/gnupg"
cp "$build/overlay/rougarou/build-manifest.json" "/out/$output.build.json"
cp "$build/overlay/rougarou/package-manifest.json" "/out/$output.packages.json"
rm -rf /out/bootstrap-repository
mkdir -p /out/bootstrap-repository
cp -a "$build/repository/." /out/bootstrap-repository/
xorriso -indev "/out/$output" -report_el_torito plain -report_system_area plain > "/out/$output.boot.txt" 2>&1
printf '\nBuilt /out/%s\n' "$output"

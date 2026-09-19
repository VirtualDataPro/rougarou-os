#!/usr/bin/env bash
# Combine the authenticated installer baseline and Rougarou package overlay.
# This only prepares an unsigned source; snapshot/sign/promotion are separate.
set -Eeuo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=versions.env
source "$repo/versions.env"
if [[ ${1:-} != --inside ]]; then
  [[ $# == 1 ]] || { echo "Usage: $0 OUTPUT_DIRECTORY" >&2; exit 2; }
  mkdir -p -- "$1"
  output=$(realpath -- "$1")
  [[ -z $(find "$output" -mindepth 1 -maxdepth 1 -print -quit) ]] || { echo 'Output directory must be empty.' >&2; exit 1; }
  cache=$(realpath -- "$repo/.cache")
  dist=$(realpath -- "$repo/dist")
  docker run --rm --entrypoint bash \
    --mount "type=bind,src=$repo,dst=/src,readonly" \
    --mount "type=bind,src=$cache,dst=/cache,readonly" \
    --mount "type=bind,src=$dist,dst=/dist,readonly" \
    --mount "type=bind,src=$output,dst=/out" \
    rougarou-iso-builder:trixie /src/scripts/prepare-repository-source.sh --inside
  exit
fi

work=$(mktemp -d /tmp/rougarou-repository-source.XXXXXX)
trap 'rm -rf "$work"' EXIT
iso="debian-${DEBIAN_VERSION}-${DEBIAN_ARCH}-netinst.iso"
artifact="rougarou-os-${ROUGAROU_VERSION}-${DEBIAN_ARCH}.iso"
keyring=/usr/share/keyrings/debian-role-keys.pgp
[[ -f $keyring ]] || keyring=/usr/share/keyrings/debian-role-keys.gpg
gpgv --keyring "$keyring" "/cache/debian/${DEBIAN_VERSION}-SHA512SUMS.sign" "/cache/debian/${DEBIAN_VERSION}-SHA512SUMS"
awk -v name="$iso" '$2 == name {print}' "/cache/debian/${DEBIAN_VERSION}-SHA512SUMS" > "$work/debian.sha512"
test -s "$work/debian.sha512"
(cd /cache/debian && sha512sum -c "$work/debian.sha512")
python3 /src/scripts/repo.py verify --repo /dist/bootstrap-repository --keyring /src/rootfs/usr/share/keyrings/rougarou-archive-keyring.gpg
gpgv --keyring /src/rootfs/usr/share/keyrings/rougarou-archive-keyring.gpg "/dist/$artifact.sha256.asc" "/dist/$artifact.sha256"
(cd /dist && sha256sum -c "$artifact.sha256")
xorriso -osirrox on -indev "/dist/$artifact" -extract /rougarou/build-manifest.json "$work/build-manifest.json"
cmp "$work/build-manifest.json" "/dist/$artifact.build.json"
xorriso -osirrox on -indev "/cache/debian/$iso" -extract /pool "$work/upstream-pool"
export ROUGAROU_SOURCE_WORK="$work" ROUGAROU_SOURCE_ISO="$iso" ROUGAROU_SOURCE_ARTIFACT="$artifact"
python3 - <<'PY'
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

work = Path(os.environ['ROUGAROU_SOURCE_WORK'])
stage = work / 'source'
(stage / 'pool').mkdir(parents=True)
def digest(path, algorithm='sha256'):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, algorithm).hexdigest()

packages = {}
for origin, directory in [('debian_installer', work / 'upstream-pool'),
                          ('rougarou_bootstrap', Path('/dist/bootstrap-repository'))]:
    for package in sorted(directory.rglob('*.deb')):
        fields = subprocess.check_output(['dpkg-deb', '-f', str(package), 'Package', 'Version', 'Architecture'], text=True)
        info = dict(line.split(': ', 1) for line in fields.splitlines())
        identity = (info['Package'], info['Version'], info['Architecture'])
        sha = digest(package)
        if identity in packages:
            if packages[identity]['sha256'] != sha:
                raise SystemExit(f'Conflicting bytes for identical package/version/arch: {identity}')
            packages[identity]['origins'].append(origin)
            continue
        destination = stage / 'pool' / sha / package.name
        destination.parent.mkdir()
        shutil.copyfile(package, destination)
        packages[identity] = dict(package=identity[0], version=identity[1], architecture=identity[2],
                                  sha256=sha, path=str(destination.relative_to(stage)), origins=[origin])
iso = Path('/cache/debian') / os.environ['ROUGAROU_SOURCE_ISO']
artifact = Path('/dist') / os.environ['ROUGAROU_SOURCE_ARTIFACT']
manifest = artifact.with_name(artifact.name + '.build.json')
provenance = {
    'schema': 1, 'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'description': 'All runtime .deb packages from the authenticated Debian installer, merged with the authenticated Rougarou bootstrap. Installer .udeb files excluded.',
    'upstream_iso': {'file': iso.name, 'sha512': digest(iso, 'sha512'),
                     'verification': 'Official Debian CD signing key; signed SHA512SUMS'},
    'rougarou_iso': {'file': artifact.name, 'sha256': digest(artifact),
                    'verification': 'Rougarou archive key detached checksum signature'},
    'rougarou_build_manifest': {'file': manifest.name, 'sha256': digest(manifest)},
    'package_count': len(packages), 'packages': sorted(packages.values(), key=lambda item: (item['package'], item['version'], item['architecture'])),
}
(stage / 'source-provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
print(f'Prepared {len(packages)} distinct package/version/architecture entries')
PY
(cd "$work/source" && dpkg-scanpackages --multiversion pool /dev/null > Packages && gzip -n -k Packages)
cp -a "$work/source/." /out/
printf 'Unsigned repository source prepared in the requested output directory.\n'

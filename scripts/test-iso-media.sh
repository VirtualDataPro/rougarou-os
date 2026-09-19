#!/usr/bin/env bash
# Offline media regression test: native apt-cdrom must never see Rougarou's
# repository before the installer bootstraps the Rougarou archive key.
set -Eeuo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=versions.env
source "$repo/versions.env"
if [[ ${1:-} != --inside ]]; then
  iso=$(realpath -- "${1:-$repo/dist/rougarou-os-${ROUGAROU_VERSION}-${DEBIAN_ARCH}.iso}")
  test -f "$iso"
  docker run --rm --network none --entrypoint bash \
    --mount "type=bind,src=$repo,dst=/src,readonly" \
    --mount "type=bind,src=$(dirname -- "$iso"),dst=/artifacts,readonly" \
    --env "ROUGAROU_TEST_ISO=$(basename -- "$iso")" \
    rougarou-iso-builder:trixie /src/scripts/test-iso-media.sh --inside
  exit
fi
work=$(mktemp -d /tmp/rougarou-media-test.XXXXXX)
trap 'rm -rf "$work"' EXIT
keyring=/src/rootfs/usr/share/keyrings/rougarou-archive-keyring.gpg
gpgv --keyring "$keyring" "/artifacts/$ROUGAROU_TEST_ISO.sha256.asc" "/artifacts/$ROUGAROU_TEST_ISO.sha256"
(cd /artifacts && sha256sum -c "$ROUGAROU_TEST_ISO.sha256")
mkdir "$work/media" "$work/repository"
xorriso -osirrox on -indev "/artifacts/$ROUGAROU_TEST_ISO" -extract / "$work/media"
test -f "$work/media/rougarou/repo.tar"
test ! -e "$work/media/rougarou/repo/Packages"
apt-cdrom --no-mount --cdrom="$work/media" add
(cd "$work/media/rougarou" && sha256sum -c SHA256SUMS)
tar -xf "$work/media/rougarou/repo.tar" -C "$work/repository"
python3 /src/scripts/repo.py verify --repo "$work/repository" --keyring "$keyring"
python3 - "$work/repository" "$work/media" <<'PY'
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import sys
repository = Path(sys.argv[1])
packages = list(repository.glob('rougarou-base_*.deb'))
assert len(packages) == 1, 'Expected one Rougarou base package'
listing = subprocess.check_output(['dpkg-deb', '--contents', str(packages[0])], text=True)
assert '__pycache__' not in listing, 'Build host Python cache leaked into package'
assert '/usr/lib/rougarou-system/configure-target.sh' in listing
assert '/usr/share/keyrings/rougarou-archive-keyring.gpg' in listing
media = Path(sys.argv[2])
def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()
installer_manifest = media / 'rougarou/installer-manifest.json'
if installer_manifest.exists():
    manifest = json.loads(installer_manifest.read_text())
    assert digest(media / 'install.amd/initrd.gz') == manifest['modified_initrd_sha256']
    source = media / 'rougarou/sources/cdebconf'
    for name, expected in manifest['files_sha256'].items():
        relative = Path(name)
        assert not relative.is_absolute() and '..' not in relative.parts
        assert digest(source / relative) == expected, name
    print('PASS: mapped installer initrd matches provenance and corresponding source/patch hashes')
external = json.loads((media / 'rougarou/external-packages.json').read_text())
for package in external.values():
    source = media / package['corresponding_source_directory'].lstrip('/')
    for name, expected in package['sources_sha256'].items():
        assert Path(name).name == name, name
        assert digest(source / name) == expected, name
print('PASS: external binary source and locked dependency archives match pinned provenance')
versions = dict(line.split('=', 1) for line in Path('/src/versions.env').read_text().splitlines()
                if line and not line.startswith('#'))
gemini_check = runpy.run_path('/src/tests/gemini-artifact-check.py')['audit']
print(json.dumps(gemini_check(repository, media / 'rougarou/sources/gemini', versions)))
print('PASS: signed ISO, native apt-cdrom registration, payload checksums, signed local repository, and package contents')
PY

#!/usr/bin/env bash
# Run only inside the disposable Debian builder after its authenticated apt update.
# Retain pristine corresponding source, the exact patch tools and build evidence.
set -Eeuo pipefail
original=${1:?Usage: build-installer-frontend.sh ORIGINAL_INITRD OUTPUT_DIRECTORY}
output=${2:?Missing output directory}
[[ -f /.dockerenv && -d /src/image/installer ]] || {
  echo 'Use this helper inside the Rougarou ISO builder container.' >&2; exit 1;
}
original=$(realpath -- "$original")
output=$(realpath -m -- "$output")
test -f "$original"
mkdir -p "$output/sources/cdebconf"
work=$(mktemp -d /tmp/rougarou-frontend.XXXXXX)
trap 'rm -rf "$work"' EXIT
source_bundle="$output/sources/cdebconf"
(cd "$source_bundle" && apt-get source --download-only --only-source cdebconf=0.280)
shopt -s nullglob
descriptors=("$source_bundle/"*.dsc)
[[ ${#descriptors[@]} == 1 ]] || { echo 'Expected exactly one source descriptor.' >&2; exit 1; }
dpkg-source -x "${descriptors[0]}" "$work/source" > "$source_bundle/extract.log" 2>&1
python3 /src/image/installer/patch-newt.py "$work/source/src/modules/frontend/newt/newt.c"
(cd "$work/source" && dpkg-buildpackage -us -uc -b -Ppkg.cdebconf.nogtk -j2) > "$source_bundle/build.log" 2>&1
frontends=("$work/"cdebconf-newt-udeb_*_amd64.udeb)
[[ ${#frontends[@]} == 1 ]] || { echo 'Expected exactly one rebuilt newt frontend.' >&2; exit 1; }
dpkg-deb -x "${frontends[0]}" "$work/frontend"
frontend="$work/frontend/usr/lib/cdebconf/frontend/newt.so"
test -s "$frontend"
cp "${frontends[0]}" "$source_bundle/"
cp "$work/source/debian/copyright" "$source_bundle/copyright"
cp -a /src/image/installer "$source_bundle/rougarou-patches"
find "$source_bundle/rougarou-patches" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$source_bundle/rougarou-patches" -type f -name '*.py[co]' -delete
cp /src/scripts/build-installer-frontend.sh "$source_bundle/build-installer-frontend.sh"
dpkg-query -W '-f=${binary:Package}\t${Version}\n' > "$source_bundle/build-tools.tsv"
bash /src/image/installer/prepare-initrd.sh "$original" "$frontend" "$output/initrd.gz"
python3 - "$original" "$output" "$frontend" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

original, output, frontend = map(Path, sys.argv[1:])
def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()
bundle = output / 'sources' / 'cdebconf'
manifest = {
    'source_package': 'cdebconf', 'source_version': '0.280',
    'source_authentication': 'apt-get source using authenticated Debian Sources metadata',
    'build_profile': 'pkg.cdebconf.nogtk',
    'original_initrd_sha256': digest(original),
    'modified_initrd_sha256': digest(output / 'initrd.gz'),
    'frontend_sha256': digest(frontend),
    'corresponding_source_directory': '/rougarou/sources/cdebconf',
    'files_sha256': {str(path.relative_to(bundle)): digest(path)
                     for path in sorted(bundle.rglob('*')) if path.is_file()},
}
(output / 'installer-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
PY
printf 'Prepared branded installer: %s\n' "$output/initrd.gz"

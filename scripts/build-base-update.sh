#!/usr/bin/env bash
# Build only rougarou-base, without changing ISO artifacts or agent candidates.
set -Eeuo pipefail
umask 022
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=../versions.env
source "$repo/versions.env"
if [[ ${1:-} != --inside ]]; then
  [[ $# == 2 ]] || { echo "Usage: $0 EMPTY_OUTPUT_DIRECTORY SIGNED_BASELINE_REPOSITORY" >&2; exit 2; }
  output=$(realpath -m -- "$1")
  baseline=$(realpath -- "$2")
  [[ -f $baseline/InRelease && -f $baseline/Packages ]]
  [[ $output != "$baseline" && $output != "$baseline/"* && $baseline != "$output/"* ]] || {
    echo 'Output and baseline directories must be separate.' >&2; exit 1;
  }
  mkdir -p "$output"
  [[ -z $(find "$output" -mindepth 1 -maxdepth 1 -print -quit) ]] || { echo 'Output directory must be empty.' >&2; exit 1; }
  # Resolve the already prepared builder to its immutable local image ID.
  # This command never builds/pulls an image and has no network connection.
  builder=$(docker image inspect --format '{{.Id}}' rougarou-iso-builder:trixie)
  [[ $builder =~ ^sha256:[a-f0-9]{64}$ ]]
  docker run --rm --pull never --network none --entrypoint bash \
    --mount "type=bind,src=$repo,dst=/src,readonly" \
    --mount "type=bind,src=$baseline,dst=/baseline,readonly" \
    --mount "type=bind,src=$output,dst=/out" \
    --env "ROUGAROU_BUILDER_IMAGE_ID=$builder" \
    "$builder" /src/scripts/build-base-update.sh --inside
  exit
fi
[[ -f /.dockerenv && -d /src/rootfs && -d /out && -d /baseline ]]
[[ -z $(find /out -mindepth 1 -maxdepth 1 -print -quit) ]]
python3 /src/scripts/repo.py verify --repo /baseline \
  --keyring /src/rootfs/usr/share/keyrings/rougarou-archive-keyring.gpg
work=$(mktemp -d /tmp/rougarou-base-update.XXXXXX)
trap 'rm -rf "$work"' EXIT
mkdir "$work/result"
version="${ROUGAROU_VERSION/-alpha./~alpha}-${ROUGAROU_BASE_REVISION:-$ROUGAROU_PACKAGE_REVISION}"
package="rougarou-base_${version}_all.deb"
bash /src/image/build-base-package.sh "$work/rootfs" "$work/result/$package"
python3 /src/image/package-input-manifest.py >"$work/result/package-inputs.json"
dpkg-query -W '-f=${binary:Package}\t${Version}\n' >"$work/result/build-tools.tsv"
(cd "$work/result" && dpkg-scanpackages --multiversion . /dev/null >Packages)
export ROUGAROU_BASE_UPDATE_WORK="$work/result" ROUGAROU_BASE_UPDATE_PACKAGE="$package"
python3 - <<'PY'
import datetime
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import tarfile

result = Path(os.environ['ROUGAROU_BASE_UPDATE_WORK'])
package = result / os.environ['ROUGAROU_BASE_UPDATE_PACKAGE']
baseline = Path('/baseline')
versions = dict(line.split('=', 1) for line in Path('/src/versions.env').read_text().splitlines()
                if line and not line.startswith('#'))
records = runpy.run_path('/src/scripts/repo.py')['records'](baseline / 'Packages')
prior = [record for record in records if record['Package'] == 'rougarou-base']
assert len(prior) == 1, 'Expected exactly one baseline base package'
prior = prior[0]
version = subprocess.check_output(['dpkg-deb', '-f', str(package), 'Version'], text=True).strip()
subprocess.run(['dpkg', '--compare-versions', version, 'gt', prior['Version']], check=True)
dependencies = subprocess.check_output(['dpkg-deb', '-f', str(package), 'Depends'], text=True).strip()
# dpkg-deb renders spaces after commas; dpkg-scanpackages may preserve the
# original compact field. Keep every dependency atom/constraint and its order.
assert ([item.strip() for item in dependencies.split(',')] ==
        [item.strip() for item in prior.get('Depends', '').split(',')]), 'Base-only update unexpectedly changes dependencies'

def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

inputs = json.loads((result / 'package-inputs.json').read_text())['inputs']
def source_metadata(item):
    item.uid = item.gid = 0
    item.uname = item.gname = 'root'
    item.mtime = 0
    return item
with tarfile.open(result / 'source-inputs.tar.gz', 'w:gz', dereference=False) as archive:
    for name in sorted(inputs):
        archive.add(Path('/src') / name, arcname=name, recursive=False, filter=source_metadata)

manifest = {
    'schema': 1, 'kind': 'rougarou-base-package-update',
    'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'os_version': versions['ROUGAROU_VERSION'],
    'package': {'name': 'rougarou-base', 'version': version, 'architecture': 'all',
                'file': package.name, 'size': package.stat().st_size, 'sha256': digest(package)},
    'baseline': {'release_sha256': digest(baseline / 'Release'),
                 'inrelease_sha256': digest(baseline / 'InRelease'),
                 'packages_sha256': digest(baseline / 'Packages'),
                 'package_count': len(records), 'base_version': prior['Version'],
                 'base_package_sha256': prior['SHA256']},
    'builder': {'image_id': os.environ['ROUGAROU_BUILDER_IMAGE_ID'],
                'debian_container': versions['DEBIAN_CONTAINER'], 'network': False},
    'package_inputs_sha256': digest(result / 'package-inputs.json'),
    'source_inputs_archive_sha256': digest(result / 'source-inputs.tar.gz'),
    'build_tools_sha256': digest(result / 'build-tools.tsv'),
    'packages_index_sha256': digest(result / 'Packages'),
    'agent_package_revision': versions['ROUGAROU_PACKAGE_REVISION'],
    'scope': 'Only rougarou-base built. No ISO, agent package, bootstrap repository or channel changed.',
}
(result / 'build-manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
with (result / 'SHA256SUMS').open('w') as stream:
    for path in sorted(result.iterdir()):
        if path.name != 'SHA256SUMS':
            stream.write(f'{digest(path)}  {path.name}\n')
print(json.dumps(manifest['package'], sort_keys=True))
PY
cp -a "$work/result/." /out/
echo 'Base update candidate complete; original release artifacts are unchanged.'

#!/usr/bin/env bash
# Creates only a NEW cloud artifact and NEW disposable work directory.
set -Eeuo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
project=${ROUGAROU_PROJECT:-$here}
[[ $# == 6 ]] || { echo 'Usage: build-cloud-image.sh VERIFIED_QCOW2 SIGNED_REPOSITORY ACCEPTED_BASE_DEB CLOUD_PROVENANCE_JSON EMPTY_OUTPUT EMPTY_WORK' >&2; exit 2; }
source_image=$(realpath -- "$1")
baseline=$(realpath -- "$2")
base_package=$(realpath -- "$3")
cloud_provenance=$(realpath -- "$4")
output=$(realpath -m -- "$5")
work=$(realpath -m -- "$6")
[[ -f $source_image && -f $baseline/InRelease && -f $project/scripts/repo.py ]]
mkdir -p "$output" "$work"
[[ -z $(find "$output" -mindepth 1 -maxdepth 1 -print -quit) && -z $(find "$work" -mindepth 1 -maxdepth 1 -print -quit) ]]
builder=$(docker image inspect --format '{{.Id}}' rougarou-cloud-builder:trixie)
[[ $builder =~ ^sha256:[a-f0-9]{64}$ ]]
# The guest, all image-build commands and APT installation have no network.
docker run --rm --pull never --network none --device /dev/kvm \
  --mount "type=bind,src=$here,dst=/cloud-tools,readonly" \
  --mount "type=bind,src=$project,dst=/src,readonly" \
  --mount "type=bind,src=$(dirname -- "$source_image"),dst=/upstream,readonly" \
  --mount "type=bind,src=$baseline,dst=/baseline,readonly" \
  --mount "type=bind,src=$(dirname -- "$base_package"),dst=/candidate,readonly" \
  --mount "type=bind,src=$cloud_provenance,dst=/cloud-provenance.json,readonly" \
  --mount "type=bind,src=$output,dst=/out" \
  --mount "type=bind,src=$work,dst=/work" \
  --env "ROUGAROU_BUILDER_IMAGE_ID=$builder" \
  "$builder" /cloud-tools/scripts/cloud-image.py --source "/upstream/$(basename -- "$source_image")" \
    --repo /baseline --base-package "/candidate/$(basename -- "$base_package")" --cloud-provenance /cloud-provenance.json --project /src --output /out --work /work

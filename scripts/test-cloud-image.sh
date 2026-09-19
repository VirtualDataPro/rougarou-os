#!/usr/bin/env bash
set -Eeuo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
[[ $# == 3 ]] || { echo 'Usage: test-cloud-image.sh QCOW2 BUILD_MANIFEST_JSON EMPTY_EVIDENCE_DIRECTORY' >&2; exit 2; }
image=$(realpath -- "$1")
manifest=$(realpath -- "$2")
evidence=$(realpath -m -- "$3")
mkdir -p "$evidence"
[[ -z $(find "$evidence" -mindepth 1 -maxdepth 1 -print -quit) ]]
chmod 700 "$evidence"
builder=$(docker image inspect --format '{{.Id}}' rougarou-cloud-builder:trixie)
docker run --rm --pull never --network none --device /dev/kvm \
  --mount "type=bind,src=$here,dst=/cloud-tools,readonly" \
  --mount "type=bind,src=$image,dst=/input/cloud.qcow2,readonly" \
  --mount "type=bind,src=$manifest,dst=/input/build-manifest.json,readonly" \
  --mount "type=bind,src=$evidence,dst=/evidence" \
  "$builder" /cloud-tools/tests/cloud-validate.py /input/cloud.qcow2 /input/build-manifest.json /evidence

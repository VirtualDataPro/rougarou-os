#!/usr/bin/env bash
set -Eeuo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=versions.env
source "$repo/versions.env"
command -v docker >/dev/null || { echo 'Install Docker or use the documented Debian builder environment.' >&2; exit 1; }
docker info >/dev/null
: "${ROUGAROU_GNUPGHOME:?Set ROUGAROU_GNUPGHOME to the dedicated release signing key directory}"
: "${ROUGAROU_SIGNING_KEY:?Set ROUGAROU_SIGNING_KEY to the release key fingerprint}"
[[ $ROUGAROU_SIGNING_KEY =~ ^[A-Fa-f0-9]{40,64}$ ]] || { echo 'Expected a full release signing fingerprint.' >&2; exit 1; }
signing_home=$(realpath -- "$ROUGAROU_GNUPGHOME")
test -d "$signing_home"
mkdir -p "$repo/dist" "$repo/.cache"
docker build --build-arg "DEBIAN_CONTAINER=$DEBIAN_CONTAINER" -t rougarou-iso-builder:trixie -f "$repo/containers/iso-builder.Dockerfile" "$repo/containers"
# No privileged mode, devices, host root mounts, or Docker socket inside the build.
# Rootless Docker is supported and recommended. Container UID 0 maps to its owner.
docker run --rm -i \
  --mount "type=bind,src=$repo,dst=/src,readonly" \
  --mount "type=bind,src=$repo/.cache,dst=/cache" \
  --mount "type=bind,src=$repo/dist,dst=/out" \
  --mount "type=bind,src=$signing_home,dst=/run/rougarou-signing,readonly" \
  --env "ROUGAROU_SIGNING_KEY=$ROUGAROU_SIGNING_KEY" \
  --env ROUGAROU_SIGNING_PASSPHRASE_FD=0 \
  --env "ROUGAROU_USE_CANDIDATES=${ROUGAROU_USE_CANDIDATES:-0}" \
  --env "ROUGAROU_PACKAGES_ONLY=${ROUGAROU_PACKAGES_ONLY:-0}" \
  rougarou-iso-builder:trixie

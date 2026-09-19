#!/usr/bin/env bash
# Every guest disk is a newly created qcow2 under the explicit evidence directory.
set -Eeuo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=versions.env
source "$repo/versions.env"
iso=${1:?Usage: scripts/test-vm.sh ISO [EVIDENCE_ROOT]}
evidence_root=${2:-"$repo/build/vm-tests"}
iso=$(realpath -- "$iso")
[[ -f $iso && $iso == *.iso ]] || { echo 'Expected an ISO file.' >&2; exit 1; }
test -c /dev/kvm || { echo 'KVM is required for this validation.' >&2; exit 1; }
mkdir -p -- "$evidence_root"
run_dir=$(mktemp -d "$evidence_root/run-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")
chmod 700 "$run_dir"
printf 'Validation evidence: %s\n' "$run_dir"
docker build --build-arg "DEBIAN_CONTAINER=$DEBIAN_CONTAINER" -t rougarou-vm-test:trixie -f "$repo/containers/vm-test.Dockerfile" "$repo/containers"
docker run --rm --network none --device /dev/kvm \
    --mount "type=bind,src=$repo,dst=/src,readonly" \
    --mount "type=bind,src=$iso,dst=/input/rougarou.iso,readonly" \
    --mount "type=bind,src=$run_dir,dst=/evidence" \
    rougarou-vm-test:trixie

#!/usr/bin/env bash
# Interface kept small for the ISO builder; no extraction or mknod is needed.
set -Eeuo pipefail
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec python3 "$source_dir/prepare-initrd.py" "$@"

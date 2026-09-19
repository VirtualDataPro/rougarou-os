#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
python3 -m unittest discover -s tests -v
while IFS= read -r -d '' script; do
  case "$(head -n 1 "$script")" in
    '#!/usr/bin/env bash'*) bash -n "$script" ;;
    '#!/bin/sh'*) sh -n "$script" ;;
  esac
done < <(find scripts image rootfs -type f \( -name '*.sh' -o -name 'rougarou' \) -print0)
python3 - <<'PY'
import ast
from pathlib import Path
paths = list(Path('rootfs/usr/lib/rougarou').glob('*.py'))
paths.append(Path('rootfs/usr/lib/rougarou-system/update'))
for path in paths:
    ast.parse(path.read_text(), filename=str(path))
PY
git diff --check
python3 scripts/check-publication.py
# A normal installation may not pre-authorize wiping an operator's disk.
if grep -Eq '^[[:space:]]*d-i (partman/(confirm|confirm_nooverwrite)|partman-auto/disk)' image/preseed.cfg; then
  echo 'Unsafe automatic disk preselection in normal installer' >&2
  exit 1
fi
test -x rootfs/usr/local/bin/rougarou
test -x rootfs/usr/lib/rougarou-system/configure-target.sh
test -s rootfs/usr/share/keyrings/rougarou-archive-keyring.gpg
printf '%s\n' 'Source checks passed.'

#!/usr/bin/env bash
# Publish public artifacts only. Sign/promote locally before running this script.
set -Eeuo pipefail
source_root=${1:?Usage: publish-repository.sh REPOSITORY_ROOT USER@HOST PUBLIC_KEYRING CHANNEL}
remote=${2:?Missing USER@HOST}
keyring=${3:?Missing public keyring}
channel=${4:?Missing testing or stable channel}
[[ $remote =~ ^[a-z_][a-z0-9_-]*@[A-Za-z0-9.-]+$ ]] || { echo 'Invalid remote' >&2; exit 1; }
[[ $channel == testing || $channel == stable ]] || { echo 'Invalid channel' >&2; exit 1; }
ssh_options=(-F /dev/null -o BatchMode=yes)
if [[ -n ${ROUGAROU_SSH_JUMP:-} ]]; then
  [[ $ROUGAROU_SSH_JUMP =~ ^[a-z_][a-z0-9_-]*@[A-Za-z0-9.-]+$ ]] || { echo 'Invalid SSH jump host' >&2; exit 1; }
  ssh_options+=(-J "$ROUGAROU_SSH_JUMP")
fi
if [[ -n ${ROUGAROU_SSH_KNOWN_HOSTS:-} ]]; then
  [[ -f $ROUGAROU_SSH_KNOWN_HOSTS ]] || { echo 'Known-hosts file missing' >&2; exit 1; }
  ssh_options+=(-o "UserKnownHostsFile=$ROUGAROU_SSH_KNOWN_HOSTS")
fi
if [[ -n ${ROUGAROU_SSH_HOST_KEY_ALIAS:-} ]]; then
  [[ $ROUGAROU_SSH_HOST_KEY_ALIAS =~ ^[A-Za-z0-9.-]+$ ]] || { echo 'Invalid host-key alias' >&2; exit 1; }
  ssh_options+=(-o "HostKeyAlias=$ROUGAROU_SSH_HOST_KEY_ALIAS")
fi
printf -v rsync_ssh '%q ' ssh "${ssh_options[@]}"
source_root=$(realpath "$source_root")
script_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
python3 "$script_root/scripts/repo.py" verify --repo "$source_root/channels/$channel" --keyring "$keyring"
target=$(readlink "$source_root/channels/$channel")
[[ $target =~ ^\.\./publications/$channel/([A-Za-z0-9._-]+)$ ]] || { echo 'Unexpected channel target' >&2; exit 1; }
generation=${BASH_REMATCH[1]}
landing=$(mktemp /tmp/rougarou-repository-index.XXXXXX)
trap 'rm -f "$landing"' EXIT
python3 - "$source_root" "$channel" "$landing" <<'PY'
import html, json, pathlib, sys
root, channel, output = pathlib.Path(sys.argv[1]), sys.argv[2], pathlib.Path(sys.argv[3])
manifest = json.loads((root / 'channels' / channel / 'snapshot.json').read_text())
snapshot = html.escape(manifest['snapshot'])
count = int(manifest['package_count'])
stable = ('<a href="/channels/stable/InRelease">Signed stable metadata</a>.'
          if (root / 'channels' / 'stable').is_symlink()
          else 'Stable remains unpublished until the operator tests and approves a snapshot.')
output.write_text(f'''<!doctype html><html lang="en"><meta charset="utf-8">
<title>Rougarou OS packages</title>
<style>body{{background:#080f0b;color:#dfe7df;font:18px monospace;max-width:48em;margin:4em auto;padding:1em}}h1,a{{color:#79c58e}}</style>
<h1>Rougarou OS package repository</h1>
<p>Private LAN alpha repository. Signing keys remain on the operator's workstation.</p>
<p>Latest publication: <strong>{channel}</strong> / <code>{snapshot}</code> / {count} packages.</p>
<p><a href="/channels/{channel}/InRelease">Signed {channel} metadata</a> &middot;
<a href="/rougarou-archive-keyring.gpg">Public archive keyring</a></p>
<p>{stable}</p><p>APT verifies release signatures and package hashes. This LAN HTTP service does not encrypt traffic.</p>
</html>\n''')
PY
# Every path here is public generated content; there is no recursive copy of
# the source checkout, build environment or private signing directory.
rsync -aH --no-owner --no-group --safe-links --delay-updates \
  -e "$rsync_ssh" --rsync-path='sudo -n rsync' \
  "$source_root/snapshots" "$source_root/publications" "$source_root/pool" \
  "$source_root/index-by-hash" "$source_root/audit" "$remote:/srv/rougarou/"
scp "${ssh_options[@]}" "$keyring" "$remote:/tmp/rougarou-archive-keyring.gpg"
scp "${ssh_options[@]}" "$landing" "$remote:/tmp/rougarou-repository-index.html"
ssh "${ssh_options[@]}" "$remote" sudo -n bash -s -- "$channel" "$generation" <<'REMOTE'
set -Eeuo pipefail
channel=$1
generation=$2
install -m 0644 /tmp/rougarou-archive-keyring.gpg /srv/rougarou/rougarou-archive-keyring.gpg
rm /tmp/rougarou-archive-keyring.gpg
install -m 0644 /tmp/rougarou-repository-index.html /srv/rougarou/index.html
rm /tmp/rougarou-repository-index.html
test -f "/srv/rougarou/publications/$channel/$generation/InRelease"
install -d -m 0755 /srv/rougarou/channels
pointer="/srv/rougarou/channels/.$channel.$$"
ln -s "../publications/$channel/$generation" "$pointer"
mv -Tf "$pointer" "/srv/rougarou/channels/$channel"
REMOTE
printf 'Published %s to %s:/srv/rougarou/channels/%s\n' "$generation" "$remote" "$channel"

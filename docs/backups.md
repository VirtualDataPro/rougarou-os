# Operator backup and staged recovery

`rougarou backup` uses the installed Debian Restic package to keep encrypted
snapshots of Rougarou settings, job history/logs, selected shell settings and
application paths you choose. It runs as your operator account, without sudo,
a new daemon or a scheduled timer. The first implementation accepts absolute
local or mounted repository paths. It does not configure mounts, SSH/S3 backends,
retention, application database dumps, or full-system recovery.

Keep the repository on separate durable storage and keep its password separately.
The wrapper refuses a password file inside the repository, including when
attaching an existing repository.
A repository on the same failed disk cannot recover that disk. Restic requires
the password to decrypt its repository; Rougarou deliberately excludes the
configured password file from snapshots. See [Restic repository preparation](https://restic.readthedocs.io/en/v0.18.0/030_preparing_a_new_repo.html).

## Configure a destination

Create a nonempty password file owned by your operator with permissions `0600`.
For example, this prompts without placing the password in command history or
arguments and refuses to replace an existing file:

```sh
python3 - <<'PY'
import getpass, os
from pathlib import Path
path = Path.home() / '.config/rougarou/restic-password'
path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
first = getpass.getpass('New backup password: ')
second = getpass.getpass('Repeat backup password: ')
if not first or first != second or '\n' in first:
    raise SystemExit('Passwords must be nonempty and match; nothing written.')
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
with os.fdopen(fd, 'w') as stream:
    stream.write(first + '\n')
PY
```

Confirm your intended backup storage is mounted and writable, then initialize
an absent or empty repository:

```sh
rougarou backup init \
  --repository /mnt/backup/rougarou \
  --password-file "$HOME/.config/rougarou/restic-password" \
  --include "$HOME/Work/service-data"
```

Repeat `--include` for additional operator-readable files or directories.
Root-owned volumes are not silently elevated or skipped. A permission error or
partial Restic backup is a failure and is not listed as a completed Rougarou
snapshot. Export databases with their native tools or arrange application
quiescence before backing up their files. Docker/Podman image stores and named
volumes are not selected automatically.

The repository must be outside all selected source trees. Backup staging uses
private `/var/tmp/rougarou-backup-UID` storage; selected sources cannot overlap
that directory. Default and previously used recovery targets are excluded,
including when the selected source is the whole home directory. The command
does not silently replace an existing backup configuration.

Default contents are:

- Rougarou's operator configuration, including its provider credentials, except
  the backup configuration, its copies, its operation lock and the configured
  Restic password file.
- A consistent SQLite copy of the job queue and copies of job logs.
- Existing `.bashrc`, `.profile`, `.inputrc`, `.gitconfig`, XDG Git config,
  Starship and Herdr settings at their standard home locations.
- Each explicit application path.

Native agent/GitHub authentication files outside those locations, SSH private
keys, workspaces, system configuration, boot files and package binaries require
separate explicit selection or another backup method. Settings and logs can
contain secrets: the temporary local capsule and recovery target are private,
and the repository is encrypted. Staging needs enough free local disk for a
copy of the queue, settings and logs; selected application trees are read
directly by Restic.

## Create and verify

```sh
rougarou backup create
rougarou backup list
rougarou backup check
rougarou backup check --read-data
```

`create` uses SQLite's online backup API; it does not stop or restart the
worker. The database is transactionally consistent. Running-job logs and
selected application files can change during capture and do not form an
application-wide atomic snapshot. The manifest records that distinction.
[SQLite documents concurrent backup support](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup).

A completed snapshot is published only after Restic succeeds and its recovery
manifest can be read back. Failed partial snapshots can remain in Restic with
a pending tag, but `rougarou backup list` does not offer them for recovery.
No automatic prune, forget or unlock operation runs. `check` verifies repository
structure; `--read-data` additionally reads and verifies all data packs. This is
the stronger check to run before a recovery drill. See
[Restic repository checks](https://restic.readthedocs.io/en/v0.18.0/045_working_with_repos.html).

## Recover on a replacement VM

Install Rougarou, use a regular operator account, and leave the worker disabled.
Make the existing repository and your independently retained password file
available. Attach it without reinitializing it:

```sh
rougarou backup init --existing \
  --repository /mnt/backup/rougarou \
  --password-file "$HOME/.config/rougarou/restic-password"
rougarou backup list
rougarou backup check --read-data
rougarou backup restore latest
```

Restore is a preview by default and creates no target directory. It prints
the resolved full snapshot ID, queue states and selected paths. Use that exact
64-character ID to materialize the reviewed snapshot:

```sh
rougarou backup restore FULL_SNAPSHOT_ID \
  --target "$HOME/Rougarou-Recovery/reviewed" --apply
```

The target must not already exist. Omitting `--target` chooses a new timestamped
directory under `~/Rougarou-Recovery`. The wrapper refuses live configuration,
queue/repository overlap, unsafe snapshot paths, special files, absolute symlinks,
and any symlink target containing `..`. Simple forward relative links are kept.
Unusual link layouts require separate review with native Restic. The names
`/prepared`, `/RECOVERY.json` and `/RESTORE_INCOMPLETE` are reserved for recovery
metadata and cannot be selected source roots or restored archive entries.
The wrapper never restores directly over `/`, your
home, an existing workspace or a running queue. Native Restic defaults can
overwrite existing files, which is why this wrapper requires a new target.
See [Restic restore behavior](https://restic.readthedocs.io/en/v0.18.0/050_restore.html).

The resulting layout contains:

- `rougarou-backup-capsule/config/`: recovered Rougarou configuration.
- `rougarou-backup-capsule/home/`: selected shell/Git/Starship/Herdr settings.
- `rougarou-backup-capsule/state/`: the original recovered queue and logs.
- `prepared/rougarou-state/`: when the snapshot includes a queue, a recovery
  copy of that queue and its logs. Queued and
  running jobs become `interrupted`, with an explanatory history event, and
  the old worker heartbeat is removed. Completed outcomes remain unchanged.
- Original absolute application paths beneath the target; for example,
  `/home/operator/Work/service-data` becomes
  `TARGET/home/operator/Work/service-data`.
- `RECOVERY.json`: the exact snapshot ID and restore result.

No service starts, job replays, shell files are sourced, or application paths
are copied into place. A failure leaves a private target marked
`RESTORE_INCOMPLETE`; it must not be promoted. Retry into a different new target.

Review settings, credentials and application data before manually copying the
specific files into their final locations. On a replacement installation,
keep its new backup configuration/password file. Use the **prepared** state
copy when replacing an absent queue, with the worker disabled. If a queue
already exists, stop and preserve it separately before planning a merge or
replacement; the backup tool does not perform that operation.

If the preview reports `queue.present: true`, you can inspect the prepared
queue without touching live state. A snapshot without a queue does not create
the prepared state directory:

```sh
review="$HOME/Rougarou-Recovery/reviewed"
mkdir -m 700 "$review/inspect-state"
cp -a "$review/prepared/rougarou-state" "$review/inspect-state/rougarou"
XDG_STATE_HOME="$review/inspect-state" rougarou jobs list
```

Confirm restored workspace paths, authentication and application database
health on the replacement VM. `rougarou doctor` provides local checks. Enable
the worker only after that review; retry interrupted jobs explicitly, since
their external effects might already have happened before the backup.

## Validation scope

Tests use real Restic 0.18.0 in an offline disposable Debian container as an
ordinary user. They cover encrypted create/full-check, service/settings/log
byte recovery, attachment from a fresh home, pending-job quarantine only in
the prepared copy, wrong passwords, unreadable source failure and damaged
encrypted payload detection. Separate SQLite tests back up a WAL database
while another connection writes transactions. Remote storage durability,
application-specific database recovery and filesystem disaster recovery still
require drills with the operator's actual storage and services.

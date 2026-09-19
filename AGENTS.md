# Working on Rougarou OS

Rougarou is a Debian stable derivative for terminal-first server operation.
Keep desktop environments, display servers, browser dependencies and privileged
AI daemons out of the default image. Preserve the operator's shell and recovery
access even when an AI service is unavailable.

Never run installer scripts against the development host. Test installation only
in a disposable VM with explicitly created disk images. No real block devices.
Do not bake credentials, SSH host keys, machine IDs or user identities into an ISO.
Keep destructive partition decisions interactive in the normal installer.

Run `python3 -m unittest discover -s tests -v` and `scripts/check.sh` before a
release. Record the exact validation performed; a built ISO is not a tested OS.
Retain Debian and upstream notices and record package and binary checksums.
AI runs as the operator, using each selected tool's native permissions. The
installer offers explicit headless-owner passwordless sudo and password sudo
modes. Grant headless authority only to the selected named owner after the
visible choice; never silently grant it during upgrades or to all users.
Keep the optional managed worker's NoNewPrivileges scope explicit: it blocks
direct exec-based elevation in its process tree, not authority available
through the same owner's user service manager or startup files.

Bump the Debian package version whenever shipped package contents change.
Never replace a published package version with different bytes. Non-security
updates enter testing first; stable promotion requires the owner's recorded
test approval. Direct Debian security updates remain an explicit exception.

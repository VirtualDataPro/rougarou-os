# Installer visual acceptance

The native Debian text frontend carries Rougarou's green/black palette, welcome
screen, explicit operator-access choice and software selection questions.
Storage selection and destructive confirmation remain Debian's interactive
partitioner. Serial and VGA output need separate checks.

Run visual acceptance in a disposable VM using the final signed ISO. A
copy-only preseed fixture may answer earlier language/network/test-account
questions, but must leave software choices and partitioning unseeded when
checking defaults. Record the fixture's scope and final ISO hash.

At 80 columns, verify welcome text, full-root authority explanation, default
and alternate access choices, agent selection, Docker selection, Podman and
Herdr. Rootless Docker must be highlighted before any selection movement;
accepting all defaults must record `none/rootless/false/false`. Re-enter pages
with Back and verify retained choices. Stop at untouched partitioning when
performing a visual-only review.

Capture actual QMP frames and inspect them. Any published image must use a
synthetic hostname and contain no account, credential, private address or other
operator data. Label test fixtures honestly; a rendered mockup does not verify
the installer. Keep password entry outside captures. See
[public release validation](validation.md) for acceptance status.

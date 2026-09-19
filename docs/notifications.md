# Optional operator notifications

Notifications are off by default. Nothing is sent from login, onboarding or
`rougarou doctor`. Configure a destination as your ordinary operator account,
send a test, then explicitly enable the user timer:

```sh
rougarou notify configure --format json
rougarou notify test
rougarou notify enable
rougarou notify status
```

The configuration prompt hides the webhook URL. Supported formats are `json`,
`discord`, `slack` and `ntfy`; point the command at the appropriate webhook or
ntfy topic URL. Remote destinations require HTTPS. HTTP is accepted only for
loopback endpoints. Redirects are refused, and ambient proxy environment
variables are ignored, so configure the final destination directly.

For automation, supply an existing file owned by your account and readable only
by that account:

```sh
rougarou notify configure --format ntfy \
  --url-file "$HOME/.config/private/webhook-url" \
  --token-file "$HOME/.config/private/webhook-token"
```

Both files must be mode `0600` (or more restrictive). `--token-file` is optional
and supplies an HTTP bearer token. Do not put a URL or token directly in shell
history. The saved URL and token are private, **unencrypted local settings** in
`~/.config/rougarou/notifications.json` (or your `XDG_CONFIG_HOME`). Reconfiguration
keeps a private backup of the previous settings. Treat both as credentials.

## Events and privacy

A check reports new failed, timed-out or interrupted managed jobs, low free disk
space, relevant repository metadata problems, and a pending reboot indication.
It reads repository status locally; it does not refresh package metadata, run an
agent, change jobs, acknowledge failures, or reboot the host.

Messages contain the hostname, event category, fixed explanatory text and, for
job events, the job ID and final state. They do not include job prompts, command
arguments, workspaces, log output, error details, repository URLs or credentials.
The selected endpoint receives its configured URL and bearer token as needed
for delivery. Discord mention parsing is disabled and Slack markdown is disabled.

Configuration starts the job cursor at the current end of history. Old job
failures are retained locally and are not sent retrospectively. New delivery
failures leave the unsent event pending for a later check. Each check sends at
most 20 job events, followed by newly active health alerts. Repeated health
conditions are suppressed until the condition clears; a later recurrence sends
an alert again. Recovery itself does not send an extra message. A replaced queue
starts from its current history rather than replaying it.

Delivery is best effort, not an exactly-once guarantee: if the endpoint accepts
a message and the process stops before saving its cursor, a later check can
repeat that message. Keep local job history as the authoritative record. Queue
and health delivery state is stored privately in
`~/.local/state/rougarou/notification-delivery.json` (or `XDG_STATE_HOME`).

## Timer operation and recovery

```sh
rougarou notify check
systemctl --user status rougarou-notify.timer rougarou-notify.service
journalctl --user -u rougarou-notify.service
rougarou notify disable
```

The optional timer schedules its first check about two minutes after boot (or
immediately when enabled later) and subsequent checks about every five minutes.
It runs a short process as the operator; it is not a privileged daemon. Its
service has a two-minute execution limit, a 128 MiB memory limit, a 16-task limit,
private default file permissions, and `NoNewPrivileges=yes`. Network requests
use a five-second socket timeout. The service-wide limit also bounds a stalled
scheduled check. Foreground `notify check` does not have that systemd-wide limit.

If your shell sets custom `XDG_CONFIG_HOME` or `XDG_STATE_HOME`, give the
notification service the same absolute paths. The user manager does not
automatically inherit later shell changes. A foreground test can otherwise
succeed while the timer reads the default configuration or another queue.
Use `systemctl --user edit rougarou-notify.service` to add the required
`Environment=XDG_CONFIG_HOME=/your/absolute/config` and/or
`Environment=XDG_STATE_HOME=/your/absolute/state` entries under `[Service]`,
then run `systemctl --user daemon-reload` before enabling the timer. Keep
credentials in the private configuration file, not unit environment entries.

A user service manager normally stops after the last login session ends. To keep
scheduled checks working after logout, explicitly enable lingering:

```sh
loginctl enable-linger "$USER"
```

That account may then run all its enabled user services while logged out. Follow
your host's authentication policy for this action. Disabling notifications stops
the timer and any active check; it preserves the private configuration and delivery state. The service
privilege restriction blocks direct process elevation, not other authority the
same account already has through its user service manager or startup files.

If delivery fails, fix the endpoint or token, use `notify test`, and retry
`notify check`. Public errors omit response bodies and secret URLs. Reconfiguring
notifications deliberately resets the job cursor to now, so use it with care if
undelivered job events still matter.

Validation uses real loopback HTTP endpoints, all four payload formats, refused
redirects, proxy bypass, an actual SQLite queue, retry/deduplication behavior,
health recurrence, malformed state, and payload privacy checks. It does not claim
live Slack, Discord or ntfy service acceptance.

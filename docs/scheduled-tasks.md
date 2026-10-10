# Schedules

`rougarou schedule` manages recurring work from an SSH session. Open
`rougarou menu` and choose **Schedules** for interactive controls. Tasks
run as your account through the existing managed worker. The server must be on;
your SSH session can disconnect when account lingering is enabled.

`rougarou tasks` remains a supported alias for the same commands and saved tasks.
Use `rougarou schedule --help` to see the available actions.

## First task

Choose your provider with `rougarou provider` and authenticate its CLI first.
The following example uses Codex directly and starts with a read-only review:

```sh
rougarou service enable
loginctl enable-linger "$USER"
rougarou schedule add daily-review \
  --on 'Mon..Fri 09:00' --timezone America/Chicago \
  --workspace "$HOME/Work/project" --timeout 600 \
  --provider codex --sandbox read-only \
  --prompt 'Review this project and report actionable issues. Do not change files.'
rougarou schedule run daily-review
rougarou schedule history daily-review
rougarou schedule logs daily-review
# After the manual run succeeds:
rougarou schedule enable daily-review
rougarou schedule list
```

Enabling requires a successful run of the saved definition. Creation saves a
paused task and never runs it. The workspace must already exist. `run` returns
the new job ID; it does not wait for completion. Use the existing `rougarou jobs
show JOB_ID`, `logs`, `cancel` and `ack` commands for individual runs.

The schedule uses systemd calendar syntax with an explicit IANA timezone;
the timezone defaults to UTC. Examples include `hourly`, `daily` and
`Mon..Fri 09:00`. The CLI validates and normalizes expressions with
`systemd-analyze calendar`. List output shows the service manager's next-run time.
See [systemd timers](https://manpages.debian.org/trixie/systemd/systemd.timer.5.en.html).

## Providers and permissions

`--provider current` (the default) snapshots the selected Rougarou provider.
Codex, a Responses-compatible endpoint and local Ollama reuse the existing
Codex batch integration. `--provider codex` selects Codex directly.
`--model MODEL` saves an explicit model; without it the saved provider's model
or Codex's current CLI default applies. The task never copies credentials.
Existing provider credential files are read when a run executes.

Prompt tasks default to `--sandbox read-only`. Choose `workspace-write` when
the task needs to edit its workspace. Codex uses unattended approval mode and
cannot stop for interactive approvals. Failed authentication, exhausted quota
or a provider outage appear as failed jobs with retained logs.

Other providers can use their installed CLI's unattended command. Put
`--command` last; it consumes every remaining argument without shell evaluation:

```sh
rougarou schedule add command-check --on hourly \
  --workspace "$HOME/Work/project" --timeout 60 \
  --command /usr/bin/python3 --version
```

Replace that command with the selected provider's verified unattended arguments.
The menu accepts shell-style quoting to separate arguments, but never invokes a
shell. Command tasks are trusted operator commands; the Codex sandbox flag does
not apply to them. Use each provider's own permission controls. Do not put keys
in prompts or arguments. This release does not install additional provider CLIs
or invent unattended flags for them.

## Manage a schedule

```sh
rougarou schedule show daily-review
rougarou schedule pause daily-review
rougarou schedule edit daily-review --on daily --timezone America/Chicago \
  --workspace "$HOME/Work/project" --timeout 600 --provider codex \
  --prompt 'Report failing checks without changing files.'
rougarou schedule run daily-review
# After success, enable the revised definition explicitly:
rougarou schedule enable daily-review
rougarou schedule remove daily-review
rougarou schedule history daily-review
```

Edit replaces the complete definition and requires a paused task plus a new
successful test before re-enabling. Pause and remove stop future submissions;
already queued/running jobs require separate cancellation. Removal retains task
history and reserves its name. `history` shows the latest 100 jobs; `logs` opens
the latest job's output. `show` includes recent schedule events and skipped runs.

One queued or running job per task is allowed. Additional triggers record a skip
instead of accumulating work. Different tasks share the worker's sequential
queue. Interrupted agents are never retried automatically.

By default, missed runs while the timer is inactive are skipped. `--catch-up`
enables systemd's `Persistent=true`, allowing one catch-up activation after
downtime. Calendar events missed during suspend can coalesce into one activation
on resume. Neither mode provides exactly-once external effects.

## Storage, recovery and troubleshooting

Definitions, run links and events live in the operator's existing private queue
database. Systemd user units are generated under
`${XDG_CONFIG_HOME:-~/.config}/systemd/user/rougarou-task-NAME.{service,timer}`.
Units contain only the task name and scheduling configuration, not its prompt or
credentials. Unmanaged files and symlinks are refused. Custom XDG paths must
match the worker's environment.

```sh
systemctl --user status rougarou-task-daily-review.timer
journalctl --user -u rougarou-task-daily-review.service -n 30
rougarou schedule show daily-review
rougarou service status
loginctl show-user "$USER" -p Linger
```

An inactive worker or missing workspace prevents submission and records an event.
Timer service failures are visible in the user journal; job failures use the
existing optional notification system. Run history follows the queue's current
manual retention policy.

Backup recovery disables restored schedules and marks pending jobs interrupted.
After reviewing recovered settings, re-test and explicitly enable each schedule.
Before downgrading to code without task support, pause all schedules and stop the
worker. Preserve the queue database and task definitions with your backup.

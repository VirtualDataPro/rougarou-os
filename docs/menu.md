# SSH control center

Run `rougarou menu` as your ordinary operator, locally or over an SSH connection
with a terminal (`ssh -t HOST rougarou menu`). Use arrow keys and Enter to select,
Tab to move between buttons, and Esc to return. On SSH terminals, exiting restores
the previous shell screen, including when using Esc or Ctrl-C. The overview shows worker and
queue status. The interface supports terminals of at least 60 columns by 20 rows;
80 by 24 or larger is recommended. Set `NO_COLOR=1` for monochrome rendering.

Schedules, jobs, workers, updates and backups each have their own screen. Select
an existing record to see its settings and available actions. There is no need
to copy a task name, job ID, update plan ID or backup snapshot ID. Lists refresh
when reopened; the interface retains your last highlighted row during a session.

The schedule editor opens with the saved definition. Select individual fields to
change instructions, provider, model, workspace, permissions, frequency, timezone,
runtime limit or catch-up behavior. New and edited schedules are saved paused.
Run a manual test, inspect its output, then enable the schedule after it succeeds.
Esc discards an unsaved editor draft. One-off jobs use the same editor.

The model picker reads visible Codex models from its local cache, or installed
Ollama models from `ollama list`. It also offers the saved model, provider default,
and a custom identifier. It makes no billable requests. Cache entries may be
stale; the provider checks availability when work runs. Responses-compatible
providers offer their configured model and custom input. Other unattended CLIs
use command tasks, with model arguments specified in the command.

Each job carries its provider and model. The worker runs those jobs under the
operator's account. Updates and backups are system operations without an AI model.
Worker stop/restart actions explain that active work may be interrupted.

Updates show the reviewed package changes and retain that exact plan for applying.
You still provide an existing recovery point reference and confirm installation;
the backend rejects stale plans. Backup selection uses the full snapshot ID
internally. A successful preview and confirmation are required before restoring
into a new recovery directory. Live workspaces are preserved.

Settings provides account setup, provider connection, notifications and container
tools. Some setup tools and privileged update operations use their native prompts,
then return to the control center. Opening the menu does not enable services,
create schedules, configure repositories or download models. Direct commands
remain available for scripting and advanced options.

## Command completion

Bash also provides context-aware Tab completion at the shell prompt:

```text
rougarou ta<Tab>                       -> rougarou tasks
rougarou tasks enable <Tab><Tab>       -> paused schedule names
rougarou jobs cancel <Tab><Tab>        -> queued or running job IDs
rougarou tasks add review --model <Tab><Tab>
rougarou jobs submit --workspace ~/W<Tab>
```

It completes subcommands, options, sandbox/provider choices, timezones, local
model identifiers and filesystem paths. Schedule suggestions reflect the action;
job suggestions include up to 1,000 recent eligible runs. Codex model suggestions
come from its visible local cache; other providers offer their configured model.
Tab makes no network or AI requests and does not create a queue or run work.

Completion loads automatically in a new Bash session. To enable it in an existing
session immediately after upgrading:

```bash
source /usr/share/bash-completion/bash_completion
source /usr/share/bash-completion/completions/rougarou
```

The integration targets Rougarou's default Bash shell. Arguments forwarded to an
agent or an unattended command retain ordinary file completion.

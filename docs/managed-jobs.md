# Managed jobs

Rougarou includes an optional per-operator job queue implemented with Python's
standard library, SQLite and a systemd user service. Installation supplies the
commands and unit; it does not enable the worker or account lingering, and an
upgrade does not automatically start queued work. The ordinary shell and
interactive AI launcher remain available independently.

## Operator commands

```sh
rougarou jobs submit --workspace ~/Work/project 'Inspect this project and report failing checks'
rougarou jobs submit --sandbox read-only --timeout 300 'Review this directory'
printf '%s\n' 'A longer task' | rougarou jobs submit -
rougarou jobs run --timeout 60 -- /usr/bin/python3 --version
rougarou jobs list
rougarou jobs show JOB_ID
rougarou jobs logs JOB_ID --tail 100
rougarou jobs cancel JOB_ID
rougarou jobs retry JOB_ID
rougarou jobs ack             # Acknowledge all current failures/interrupted runs
rougarou jobs ack JOB_ID      # Acknowledge one reviewed run
rougarou status --json
rougarou doctor --json
rougarou service status
```

`submit` snapshots the selected provider, workspace, prompt, sandbox and timeout. Credentials are loaded only when executing and are not copied into the job database. Provider changes do not redirect queued work; changed/missing credentials cause a failed run. Custom CLI providers use `jobs run --` with their own unattended arguments. Explicit command jobs are trusted operator commands, not sandboxed AI tasks. They do not inherit the submitting shell's transient environment; configure persistent environment in the service when needed. Do not put credentials in prompts or command arguments.

One worker processes jobs sequentially. `--delay SECONDS` defers a job; this is not a recurring scheduler. The default timeout is one hour. Every retry gets a new ID linked to its original run. Queued jobs survive service restarts and reboots. Running jobs interrupted by shutdown, worker crash or OOM are marked interrupted on shutdown or the next worker start; **they are never automatically replayed**. Inspect effects/logs before retrying. This is recovery of queue state, not transparent resumption of an AI conversation or exactly-once external side effects.

## Clear reviewed failures from the login summary

After reviewing a failed, timed-out or interrupted job, run `rougarou jobs ack
JOB_ID`. To acknowledge all currently unsuccessful runs, use `rougarou jobs ack`
or `rougarou jobs ack --all`. Run this as the same ordinary account that owns the
queue; it does not require sudo. The login summary shows the acknowledgment
command only when runs still need review.

Acknowledgment clears the MOTD's attention count without deleting anything or
changing a job's actual outcome. `jobs list` keeps the run and marks its `ACK`
column; `jobs show` and JSON list output include `acknowledged_at`. An
`acknowledged` event records the review time. Logs, exit codes, failure reasons
and earlier events stay intact. Repeating acknowledgment is harmless. Active,
queued, successful and cancelled jobs cannot be acknowledged.

`rougarou jobs ack --json` reports the newly acknowledged IDs and count. The
operation covers failures present in one transaction, so failures arriving
afterward still need review. Retrying creates a new job ID; a new unsuccessful
retry appears in the attention count again. `status --json` keeps full outcome
`counts` and provides separate unacknowledged `attention_counts`. `doctor`
continues to report historical failure/interruption warnings; acknowledgment
does not claim that the underlying problem was fixed.

Existing schema-1 queues remain readable without migration at login. An
explicit queue command adds a separate acknowledgment table; existing job rows
and the schema version are unchanged. An already-running older worker can
continue using the queue without a restart. Older clients ignore the additive
metadata and will still show their original historical failure count.

## Service and limits

```sh
systemctl --user daemon-reload
rougarou service enable
loginctl enable-linger "$USER"
```

Lingering starts the user manager at boot and retains it after logout. A host policy may require an administrator to enable it. The worker runs as the operator, never root. `NoNewPrivileges=yes` blocks direct exec-based sudo/setuid elevation in the service and its descendants. Codex batch execution uses `-a never` and the selected `read-only` or `workspace-write` sandbox. It cannot stop to request interactive approvals. Existing provider/model settings remain in effect.

The systemd service limits processes in its cgroup to 2 GiB memory, 512 MiB swap, 200% CPU and 256 tasks. Memory pressure starts at 1536 MiB. A stop or restart terminates processes remaining in that cgroup. Per-job cancellation/timeouts terminate the process group, escalating to SIGKILL after three seconds. Commands deliberately escaping their process group are outside that per-job guarantee.

These controls do not isolate the owner's account. A same-user program can ask the user service manager to launch work outside the worker's process tree and cgroup, or modify owner startup files. That work is outside these privilege, resource and cleanup guarantees and may retain the owner's headless sudo authority. Use the queue for trusted operator tasks. See the [Linux no-new-privileges semantics](https://docs.kernel.org/userspace-api/no_new_privs.html).

Tune limits using `systemctl --user edit rougarou-worker.service`, then restart during a quiet period. For local inference, run the model server in its own separately sized service. The worker never starts an inference server implicitly.

Logs are capped at 16 MiB per job and remain readable while running; excess output is discarded with a marker. Terminal control codes are escaped by `jobs logs`. State, logs, prompts and arguments are private to the account in `${XDG_STATE_HOME:-~/.local/state}/rougarou` (0700 directory, 0600 files). They can contain sensitive job output. History is retained until an operator archives/removes it; storage checks warn as disk capacity shrinks.

## Health and login

`doctor` checks service activity/enabling, lingering, fresh worker heartbeat, SQLite integrity, queue outcomes, free disk and memory, selected provider executable/configuration/authentication, SSH, update timer/index freshness and reboot requests. Ollama selection also checks its local model listing. Cloud authentication checks are local; they do not guarantee remote availability, quota or model access. No billable model request runs in health checks or at login.

`doctor --json` and `status --json` return structured output and exit nonzero for unhealthy/error states. Warnings are advisory, including historical failed/interrupted runs. Login shows one wolf banner and a brief queue/heartbeat summary. The first-login invitation can be skipped; configuring an AI provider and enabling the worker are optional.

## Fresh installations and health results

`rougarou doctor` treats an unconfigured provider and a disabled worker as
advisory warnings. `rougarou status` specifically checks the worker and exits
nonzero until it is active with a fresh heartbeat; this is expected on a fresh
installation before the optional service is enabled. If the
operator enables the worker but it is stopped or its heartbeat is stale, health
checks report an error. Invalid selected provider configuration remains an
error. The login summary reads an existing queue through a read-only SQLite
connection with short lock and query timeouts; it does not create or migrate a
queue, run service probes, contact a provider or start work.

An active APT timer is only a timer check: it does not prove automatic package
updates are configured. Consult the installed APT periodic settings and
[update policy](updates.md). Periodic installation is disabled by default; inspect the effective host policy before opting in.
The channel check recognizes enabled Rougarou-signed HTTP(S) sources alongside
the retained local baseline; it checks configuration, not remote reachability.

## Maintenance, backup and recovery

Run the repository's tests with `python3 -m unittest discover -s tests -v`.
Queue tests use isolated temporary state and no model API calls. Validate the
packaged service's crash recovery, cgroup cleanup and planned reboot behavior
in a disposable VM before publication.

Stop the worker before backing up its state directory, so the SQLite database
and WAL are consistent. Back up workspaces and provider configuration
separately; protect credentials. Restart the worker when the backup is complete.
Interrupted runs remain visible and require an explicit retry.

Before a package rollback, run `rougarou service disable`. Preserve the state
directory and restore a tested package using the signed repository's rollback
procedure. Code predating the queue cannot inspect its records. A worker refuses
to modify an unknown future queue schema; do not assume downgrades migrate data.
History has no automatic retention policy in this release, so operators must
monitor and archive its storage deliberately.

For mutually untrusted jobs, use separate Unix accounts or separate containers
or VMs with an explicit network and credential policy. This queue is a trusted
operator convenience, not a multi-tenant isolation system.

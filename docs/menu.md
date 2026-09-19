# Terminal control menu

Run the optional menu from an interactive terminal as your ordinary operator:

```sh
rougarou menu
```

Use the numbered choices for agents, managed jobs, containers, guided updates,
encrypted backups, notifications, host checks and account setup. Press Enter or
choose `0` to go back; `0` from the main menu returns to your shell. Ctrl-C or
Ctrl-D returns through the operator CLI's normal cancellation handling. The menu
does not replace the shell or start at login automatically.

The menu invokes the same documented commands available at the prompt. It does
not configure an agent, enable a worker or timer, download a model, initialize a
backup repository, or change container services merely by opening a submenu.
Child command failures leave the menu available. A selected child can have its
own native prompts, permissions and network behavior.

Update application asks for a reviewed plan ID and a recovery point reference,
then requires confirmation. Backend plan and policy checks remain authoritative.
A failed restore preview never advances to the restore confirmation. Restoring
requires the full 64-character snapshot ID, a successful preview, and explicit
confirmation; the result is staged in a new recovery directory for inspection.
It does not replace a live workspace. Acknowledging every unsuccessful job also
requires confirmation and preserves job history and logs.

Paths, identifiers and recovery references are passed as separate command
arguments. Spaces and shell metacharacters are not evaluated. Normal command
validation still applies. Webhook configuration asks for the format and an
optional private bearer-token file, then the notification command reads the URL
with a hidden prompt.

Use direct commands for scripting and fuller options, including multiple backup
include paths. The menu requires both input and output to be a terminal; piped
input is refused. See [backups](backups.md) and
[notifications](notifications.md) for their configuration and recovery details.

Validation covers real PTY input, blank/back navigation,
interrupt/EOF cancellation, failed-preview gating, and actual subprocess argument
capture using disposable child commands. No production operation is performed
by those tests.

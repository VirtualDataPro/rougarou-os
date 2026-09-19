# Connect your agent

The installer defaults to **None / bring your own**. Selecting an agent installs
its optional tools, or records a download choice for after login; it does not
configure credentials. Log in as your regular operator and run `rougarou setup`.
You can skip optional setup and resume with `rougarou provider`. Git identity,
GitHub authentication and AI
authentication are separate steps; an AI subscription does not log you into
GitHub or include unlimited API usage.

| Selection | What you supply | How it is installed |
| --- | --- | --- |
| Codex | Device sign-in or an OpenAI API key | Optional pinned CLI and companion runtime, available offline |
| Gemini CLI | Native Gemini authentication and provider settings | Optional pinned CLI and Debian Node.js, available offline |
| OpenCode | Native CLI authentication and model settings | Existing installation, or explicit verified download after login |
| Claude Code | Native CLI authentication | Existing installation, or explicit verified download after login |
| Custom Responses provider | HTTPS base URL, model identifier, optional API key | Requires the optional Codex package |
| Local Ollama | Installed running Ollama and a compatible downloaded model | Requires the optional Codex package |
| Your own CLI | An installed executable and its argument list | Inline launch without shell evaluation |

The ISO carries Codex and Gemini with their installation dependencies; only the
selected agent is installed. To add either later, use `sudo apt install
rougarou-codex` or `sudo apt install rougarou-gemini`, then run `rougarou provider`.
APT uses the retained signed baseline or your configured signed update channel.
Herdr is a separate optional terminal workspace, available through `sudo apt
install rougarou-herdr`; choosing an agent does not install it automatically.

For OpenCode and Claude Code, the wizard asks before using the network. You can
also run `rougarou-agent-install opencode` or `rougarou-agent-install claude` as
your ordinary account. See [reviewed downloads after login](deferred-agents.md)
for pinned versions, verification and installation locations. Gemini, OpenCode
and Claude use their own authentication and permission prompts when launched;
Rougarou does not replace them with Codex's policy.

The installer records `agent`, `docker`, `podman` and `herdr` in
`/etc/rougarou/install-software`. This is a settings record, not an installation
command. Onboarding uses its validated agent value as a suggested selection;
an existing operator provider selection is preserved. Container choices are
independent of agents; see [Docker and Podman](containers.md).

Custom Responses endpoints must implement the API expected by Codex. A generic
Chat Completions endpoint is not automatically compatible. Use your preferred
provider's own CLI when its protocol or authentication differs. Ollama runtime,
GPU drivers and model weights are not hidden downloads during OS installation.

Codex device sign-in displays a URL and short-lived code to use on another computer;
no browser is required on the server. The setup wizard uses hidden input for
API keys. Custom provider credentials stay in an operator-owned mode-0600 file
inside a mode-0700 directory; native Codex and GitHub credentials are managed
by their own clients. No credentials are included in the ISO.
[Codex authentication](https://developers.openai.com/codex/auth/),
[provider configuration](https://developers.openai.com/codex/config-advanced/).

`rougarou ai` (or `a`) launches the selected agent inline. For the optional
Codex version this uses `--approve-for-me --no-alt-screen`, matching Omarchy's
automatic review approach while keeping the terminal scrollback. The launcher
refuses root; the selected [operator access mode](operator-access.md) controls
sudo authority. It starts in `~/Work`
when invoked from your home directory. Provider/model capability and billing
remain separate from Linux privilege and filesystem access.

`rougarou provider` changes the selection; `rougarou github` runs GitHub setup;
`rougarou doctor` checks the local operator environment. It is a local check,
not proof that your credentials, network, provider quota or selected model work.
The OS and ordinary shell remain usable when the AI service is unavailable.

Gemini CLI 0.60.0 is packaged from the pinned official bundle with Apache-2.0
and dependency notices, retained source archives, and Debian's Node.js runtime.
The package disables its automatic updater using native system settings;
updates follow Rougarou's signed repository process. Its installed `--version`
and `--help` passed as an ordinary user in a disposable Debian container with
networking disabled. Those checks do not establish successful authentication or
model access. See [Gemini installation requirements](https://geminicli.com/docs/get-started/installation/)
and the [upstream release](https://github.com/google-gemini/gemini-cli/releases/tag/v0.60.0).

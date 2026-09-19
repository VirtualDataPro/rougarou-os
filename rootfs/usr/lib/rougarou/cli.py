"""Small, dependency-free operator onboarding and AI launcher for Rougarou OS."""
from __future__ import annotations

import argparse
import getpass
import ipaddress
import json
import os
from pathlib import Path
import pwd
import shlex
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
from urllib.parse import urlsplit


SHARE = Path(__file__).resolve().parents[2] / "share" / "rougarou"
PROVIDER_KINDS = {"codex", "responses", "ollama", "command"}
SECRET_ENV = "ROUGAROU_PROVIDER_API_KEY"
ACCESS_HELPER = "/usr/lib/rougarou-system/operator-access"
INSTALL_SOFTWARE = Path("/etc/rougarou/install-software")


class OperatorError(Exception):
    """A recoverable operator-facing failure without sensitive context."""


def interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def require_operator() -> None:
    if os.geteuid() == 0:
        raise OperatorError("Use your regular operator account for setup and AI; do not run this with sudo.")


def require_terminal() -> None:
    if not interactive():
        raise OperatorError("Setup needs an interactive terminal. Connect with ssh -t or use the VM console.")


def config_dir() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    if not base.is_absolute():
        raise OperatorError("XDG_CONFIG_HOME must be an absolute path.")
    return base / "rougarou"


def check_regular(path: Path, private: bool = False) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
        raise OperatorError(f"Refusing a non-regular or differently owned file: {path}")
    if private and stat.S_IMODE(info.st_mode) & 0o077:
        raise OperatorError(f"Private file permissions are too open. Run: chmod 600 {shlex.quote(str(path))}")


def private_directory(path: Path) -> None:
    if path.is_symlink():
        raise OperatorError(f"Refusing a symlink for the private directory: {path}")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise OperatorError(f"Private directory is not owned by this operator: {path}")
    path.chmod(0o700)


def atomic_write(path: Path, contents: str, backup: bool = True) -> None:
    """Write mode 0600 in a private directory, retaining the previous contents."""
    private_directory(path.parent)
    check_regular(path)
    if backup and path.exists():
        previous = path.read_bytes()
        backup_path = path.with_name(f"{path.name}.backup-{time.time_ns()}")
        backup_fd = os.open(backup_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(backup_fd, "wb") as stream:
            stream.write(previous)
            stream.flush()
            os.fsync(stream.fileno())
    descriptor, name = tempfile.mkstemp(prefix=".rougarou-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path: Path) -> dict:
    check_regular(path, private=True)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise OperatorError(f"Invalid JSON in {path}; the existing file was preserved.") from None
    if not isinstance(data, dict):
        raise OperatorError(f"Expected a JSON object in {path}.")
    return data


def clean_text(value: object, label: str, limit: int = 1024) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise OperatorError(f"{label} must be a nonempty string of at most {limit} characters.")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise OperatorError(f"{label} cannot contain control characters.")
    return value


def validate_url(value: object) -> str:
    url = clean_text(value, "Provider URL", 2048)
    try:
        parts = urlsplit(url)
        _ = parts.port
    except ValueError:
        raise OperatorError("Provider URL is invalid.") from None
    if not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise OperatorError("Use a base URL without credentials, query parameters, or a fragment.")
    local = parts.hostname == "localhost"
    try:
        local = local or ipaddress.ip_address(parts.hostname).is_loopback
    except ValueError:
        pass
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        raise OperatorError("Use HTTPS for remote providers; HTTP is accepted only for loopback hosts.")
    return url


def validate_provider(provider: object) -> dict:
    if not isinstance(provider, dict) or provider.get("kind") not in PROVIDER_KINDS:
        raise OperatorError("Unknown AI provider. Run rougarou provider to select one.")
    kind = provider["kind"]
    keys = {
        "codex": {"kind"},
        "responses": {"kind", "endpoint", "model", "authenticated"},
        "ollama": {"kind", "model"},
        "command": {"kind", "argv"},
    }[kind]
    if set(provider) != keys:
        raise OperatorError("AI provider configuration has missing or unexpected fields.")
    if kind in {"responses", "ollama"}:
        clean_text(provider["model"], "Model", 256)
    if kind == "responses":
        validate_url(provider["endpoint"])
        if type(provider["authenticated"]) is not bool:
            raise OperatorError("Provider authenticated must be true or false.")
    if kind == "command":
        argv = provider["argv"]
        if not isinstance(argv, list) or not 1 <= len(argv) <= 64:
            raise OperatorError("Bring-your-own CLI must be an argument list of 1 to 64 entries.")
        for argument in argv:
            clean_text(argument, "CLI argument", 4096)
        if argv[0].startswith("-"):
            raise OperatorError("CLI executable cannot begin with a dash.")
    return provider


def load_config() -> dict:
    config = read_json(config_dir() / "config.json")
    if not config:
        return {"schema_version": 1}
    if config.get("schema_version") != 1 or set(config) - {
        "schema_version", "provider", "first_login_seen", "setup_complete"
    }:
        raise OperatorError("Unsupported configuration; existing files were preserved.")
    for key in ("first_login_seen", "setup_complete"):
        if key in config and type(config[key]) is not bool:
            raise OperatorError(f"Invalid {key} setting.")
    if "provider" in config:
        validate_provider(config["provider"])
    return config


def save_config(config: dict) -> None:
    atomic_write(config_dir() / "config.json", json.dumps(config, indent=2) + "\n")


def confirm(prompt: str, default: bool = False) -> bool:
    suffix = " [Y/n] " if default else " [y/N] "
    while True:
        answer = input(prompt + suffix).strip().lower()
        if not answer:
            return default
        if answer in {"y", "yes", "n", "no"}:
            return answer in {"y", "yes"}
        print("Enter y or n, or press Ctrl-C to return to the shell.")


def field(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    return input(f"{prompt}{suffix}: ").strip() or default


def require_command(command: str) -> str:
    resolved = shutil.which(command)
    if not resolved:
        raise OperatorError(f"Required command is not installed: {command}")
    return resolved


def run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    require_command(argv[0])
    return subprocess.run(argv, check=False, text=True, **kwargs)


def access_status() -> dict:
    username = pwd.getpwuid(os.getuid()).pw_name
    result = run([ACCESS_HELPER, "status", username, "--json"], capture_output=True)
    if result.returncode:
        raise OperatorError(result.stderr.strip() or "Operator access state needs administrator review.")
    try:
        status = json.loads(result.stdout)
        if not isinstance(status, dict) or status.get("mode") not in {"headless", "password", "unmanaged"}:
            raise ValueError
    except ValueError:
        raise OperatorError("Operator access helper returned invalid state.") from None
    # With a command, -k ignores a cached credential without invalidating it.
    probe = run(["sudo", "-n", "-k", "--", "/usr/bin/true"], capture_output=True)
    status["passwordless_probe"] = probe.returncode == 0
    return status


def print_access(status: dict) -> None:
    print(f"Operator access: {status['mode']}")
    if status["mode"] == "headless":
        print("This account has full root authority through passwordless sudo.")
    elif status["mode"] == "password":
        print("Rougarou's passwordless grant is disabled; normal administrator policy applies.")
    else:
        print("No Rougarou access mode is recorded for this account; existing sudo policy is unchanged.")
    if status["passwordless_probe"] and status["mode"] != "headless":
        print("A separate sudo policy permits /usr/bin/true without a password; review sudo -l.")
    elif not status["passwordless_probe"] and status["mode"] == "headless":
        print("The noninteractive sudo probe failed; review sudo policy or process restrictions.")


def access(arguments: list[str]) -> int:
    require_operator()
    parser = argparse.ArgumentParser(prog="rougarou access", description="Explicitly select this operator's sudo authority.")
    parser.add_argument("mode", nargs="?", default="status", choices=["status", "configure", "headless", "password"])
    parser.add_argument("--json", action="store_true", help="machine-readable status")
    parser.add_argument("--acknowledge-root-access", action="store_true", help="explicitly accept passwordless full root authority")
    args = parser.parse_args(arguments)
    if args.json and args.mode != "status":
        raise OperatorError("--json is available only for access status.")
    if args.acknowledge_root_access and args.mode != "headless":
        raise OperatorError("--acknowledge-root-access is available only for headless mode.")
    if args.mode == "status":
        status = access_status()
        print(json.dumps(status, sort_keys=True)) if args.json else print_access(status)
        return 0
    if args.mode == "configure":
        require_terminal()
        print_access(access_status())
        print("\n  1  Headless: passwordless sudo for this operator")
        print("  2  Password: require normal administrator authentication")
        print("  3  Keep current policy")
        print("Headless mode lets every program in this account gain full root authority without a password.")
        choice = field("Operator access", "3")
        if choice == "3":
            return 0
        if choice not in {"1", "2"}:
            raise OperatorError("Choose operator access option 1, 2, or 3.")
        args.mode = "headless" if choice == "1" else "password"
    if args.mode == "headless" and not args.acknowledge_root_access:
        require_terminal()
        print("Headless mode grants this account and all programs it runs full root authority through sudo.")
        print("Interactive AI remains a regular user. Queued jobs keep NoNewPrivileges and cannot elevate.")
        if not confirm("Enable passwordless root access for this operator?", default=False):
            print("Operator access is unchanged.")
            return 0
    username = pwd.getpwuid(os.getuid()).pw_name
    command = ["sudo"]
    if not interactive():
        command.append("-n")
    command.extend(["--", ACCESS_HELPER, "set", username, args.mode])
    if args.mode == "headless":
        command.append("--acknowledge-root-access")
    result = run(command)
    if result.returncode:
        raise OperatorError("Access was not changed successfully. Use a terminal for administrator authentication and review the helper's message.")
    print_access(access_status())
    return 0


def banner() -> None:
    from presentation import welcome_text
    print(welcome_text(SHARE), end="")


def welcome() -> int:
    from health import summary
    from presentation import welcome_text
    print(welcome_text(SHARE, summary()), end="")
    return 0


def configure_git() -> None:
    require_command("git")
    current = {}
    for key in ("name", "email"):
        result = run(["git", "config", "--global", "--get", f"user.{key}"], capture_output=True)
        current[key] = result.stdout.strip() if result.returncode == 0 else ""
    print("\nGit commit identity. Leave blank to keep the current value or skip.")
    desired = {
        "name": field("Your name", current["name"]),
        "email": field("Commit email (a GitHub noreply address is fine)", current["email"]),
    }
    changes = {key: clean_text(value, f"Git {key}") for key, value in desired.items() if value and value != current[key]}
    if changes:
        # Git may use either of these global configuration files. Preserve both.
        git_paths = [Path.home() / ".gitconfig", config_dir().parent / "git" / "config"]
        for path in git_paths:
            check_regular(path)
            if path.exists():
                backup = config_dir() / f"git-{path.name}.backup-{time.time_ns()}"
                atomic_write(backup, path.read_text(encoding="utf-8"), backup=False)
        for key, value in changes.items():
            result = run(["git", "config", "--global", "--", f"user.{key}", value])
            if result.returncode:
                raise OperatorError("Git identity could not be saved. Previous configuration backups are in ~/.config/rougarou.")


def github() -> int:
    require_operator()
    require_terminal()
    configure_git()
    require_command("gh")
    status = run(["gh", "auth", "status", "--hostname", "github.com"], capture_output=True)
    if status.returncode == 0:
        print("GitHub is already authenticated; keeping the current account.")
        return 0
    if not confirm("Connect to GitHub using a browser on another device?", default=True):
        print("Skipped. Resume with rougarou github.")
        return 0
    print("Open the displayed URL on your own computer and enter the one-time code.")
    print("GitHub CLI manages your GitHub credential storage.")
    environment = os.environ.copy()
    environment["BROWSER"] = "/bin/true"
    result = run(["gh", "auth", "login", "--hostname", "github.com", "--git-protocol", "https", "--web"], env=environment)
    if result.returncode:
        raise OperatorError("GitHub sign-in did not finish. You can retry with rougarou github.")
    result = run(["gh", "auth", "setup-git", "--hostname", "github.com"])
    if result.returncode:
        raise OperatorError("GitHub sign-in succeeded, but its Git credential helper needs gh auth setup-git.")
    return 0


def codex_login() -> None:
    require_command("codex")
    status = run(["codex", "login", "status"], capture_output=True)
    if status.returncode == 0:
        print("Codex is already authenticated; keeping the current credentials.")
        return
    print("\n  1  ChatGPT sign-in with a device code")
    print("  2  OpenAI API key (usage billed to your API account)")
    print("  3  Configure authentication later")
    choice = field("Authentication", "1")
    if choice == "3":
        return
    if choice not in {"1", "2"}:
        raise OperatorError("Choose authentication option 1, 2, or 3.")
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    if not codex_home.is_absolute():
        raise OperatorError("CODEX_HOME must be absolute.")
    private_directory(codex_home)
    auth_path = codex_home / "auth.json"
    check_regular(auth_path)
    if auth_path.exists():
        atomic_write(codex_home / f"auth.json.backup-{time.time_ns()}", auth_path.read_text(), backup=False)
    if choice == "1":
        print("Open the displayed URL on your own computer. Device-code access may need enabling in your account.")
        result = run(["codex", "login", "--device-auth"])
    else:
        secret = getpass.getpass("OpenAI API key (hidden): ").strip()
        if not secret:
            raise OperatorError("No key entered; authentication was left unchanged.")
        # A pipe avoids exposing credentials in process arguments or shell history.
        result = run(["codex", "login", "--with-api-key"], input=secret + "\n", capture_output=True)
        secret = ""
    if auth_path.exists():
        check_regular(auth_path)
        auth_path.chmod(0o600)
    if result.returncode:
        raise OperatorError("Codex sign-in did not finish. Run rougarou provider to retry; no secret was printed.")


def installer_software() -> dict[str, str]:
    """Read validated installer hints; never execute or import their contents."""
    allowed = {
        "agent": {"none", "codex", "opencode", "gemini", "claude"},
        "docker": {"none", "rootless", "system"},
        "podman": {"true", "false"},
        "herdr": {"true", "false"},
    }
    try:
        if INSTALL_SOFTWARE.is_symlink() or INSTALL_SOFTWARE.stat().st_size > 256:
            return {}
        lines = INSTALL_SOFTWARE.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return {}
    selected = {}
    for line in lines:
        key, separator, value = line.partition("=")
        if not separator or key in selected or key not in allowed or value not in allowed[key]:
            return {}
        selected[key] = value
    return selected if selected.keys() == allowed.keys() else {}


def agent_executable(name: str) -> str | None:
    return shutil.which(name) or shutil.which(str(Path.home() / ".local/bin" / name))


def provider_default(config: dict, software: dict) -> str:
    if "provider" in config:
        return "5"
    agent = software.get("agent")
    if agent in {"opencode", "claude"}:
        return {"opencode": "6", "claude": "8"}[agent]
    if agent in {"codex", "gemini"} and agent_executable(agent):
        return {"codex": "1", "gemini": "7"}[agent]
    return "5"


def named_agent(name: str) -> dict | None:
    executable = agent_executable(name)
    if not executable and name in {"opencode", "claude"}:
        print(f"{name} is an optional upstream download; it is not included in the offline ISO.")
        print("Rougarou verifies the pinned download before installing it under your own account.")
        if not confirm(f"Download and install {name} now using the network?", default=False):
            print("AI selection is unchanged. Resume with rougarou provider.")
            return None
        from agent_install import InstallError, install
        try:
            executable = str(install(name))
        except InstallError as error:
            raise OperatorError(str(error)) from None
    if not executable:
        raise OperatorError(f"{name} is not installed. Add its optional package, then run rougarou provider again.")
    print(f"{name} manages its own authentication and permission prompts when you start it.")
    print("No credentials have been configured or changed by this selection.")
    return {"kind": "command", "argv": [executable]}


def provider() -> int:
    require_operator()
    require_terminal()
    config = load_config()
    print("\nChoose your AI:")
    print("  1  Codex with ChatGPT or an OpenAI API key")
    print("  2  Codex with a Responses-compatible API endpoint")
    print("  3  Codex with local Ollama (runtime and model must already be installed)")
    print("  4  Bring an installed AI CLI")
    print("  5  Keep current selection / decide later")
    print("  6  OpenCode (installed CLI or optional upstream download)")
    print("  7  Gemini CLI (optional installed package)")
    print("  8  Claude Code (installed CLI or optional upstream download)")
    if "provider" in config:
        print(f"Current selection: {config['provider']['kind']}")
    choice = field("Provider", provider_default(config, installer_software()))
    if choice == "5":
        return 0
    if choice == "1":
        selected = {"kind": "codex"}
        codex_login()
    elif choice == "2":
        require_command("codex")
        print("The endpoint must support the Responses API and the selected model's tool use.")
        endpoint = validate_url(field("API base URL, for example https://api.example.com/v1"))
        model = clean_text(field("Exact model ID"), "Model", 256)
        print("An optional key is stored in a mode-600 file and passed only to the AI process.")
        secret = getpass.getpass("Provider API key (hidden; blank for unauthenticated endpoint): ").strip()
        selected = {"kind": "responses", "endpoint": endpoint, "model": model, "authenticated": bool(secret)}
        atomic_write(config_dir() / "credentials.json", json.dumps({"endpoint": endpoint, SECRET_ENV: secret}) + "\n")
        secret = ""
    elif choice == "3":
        require_command("codex")
        require_command("ollama")
        selected = {"kind": "ollama", "model": clean_text(field("Installed Ollama model ID"), "Model", 256)}
    elif choice == "4":
        print("Enter the installed executable and arguments. Quotes group arguments; shell expressions are not evaluated.")
        print("Authenticate through that CLI's own login command before starting AI.")
        try:
            argv = shlex.split(field("AI CLI command (for example: aider --model your-model)"))
        except ValueError:
            raise OperatorError("Unbalanced quotes in the CLI command.") from None
        selected = validate_provider({"kind": "command", "argv": argv})
        require_command(argv[0])
    elif choice in {"6", "7", "8"}:
        selected = named_agent({"6": "opencode", "7": "gemini", "8": "claude"}[choice])
        if selected is None:
            return 0
    else:
        raise OperatorError("Choose a provider option from 1 to 8.")
    config["provider"] = validate_provider(selected)
    save_config(config)
    print("AI selection saved. Start it with rougarou ai, or use the original CLI directly.")
    if selected["kind"] != "command":
        print("Codex uses Omarchy-style automatic approval review when supported, with its workspace sandbox.")
        print("AI stays in your regular account. Your selected operator access mode controls sudo.")
    return 0


def setup(first_login: bool = False) -> int:
    if first_login and (not interactive() or os.geteuid() == 0):
        return 0
    require_operator()
    require_terminal()
    config = load_config()
    if first_login and (config.get("first_login_seen") or config.get("setup_complete")):
        return 0
    if not first_login:
        welcome()
    print("\nSetup is optional. Press Ctrl-C at any prompt to return to your shell.")
    if first_login:
        config["first_login_seen"] = True
        save_config(config)
        if not confirm("Configure this operator now?", default=True):
            print("Resume whenever you like with rougarou setup.")
            return 0
    (Path.home() / "Work").mkdir(mode=0o700, exist_ok=True)
    if os.path.isfile(ACCESS_HELPER):
        status = access_status()
        print_access(status)
        if status["mode"] == "unmanaged" and confirm("Review this operator's sudo access mode?", default=False):
            access(["configure"])
    if confirm("Configure Git identity and GitHub?", default=True):
        github()
    software = installer_software()
    if software.get("docker") == "rootless":
        print("Rootless Docker was selected at installation. Start your own daemon with rougarou-docker setup.")
        print("This is optional; no Docker service is started by operator setup.")
    config = load_config()
    if "provider" in config:
        print("Your existing AI selection is unchanged. Use rougarou provider to change it.")
    elif confirm("Configure your AI?", default=True):
        provider()
    config = load_config()
    config["setup_complete"] = True
    save_config(config)
    print("\nOperator setup saved. Your workspace is ~/Work.")
    print("Run rougarou ai to begin, or rougarou doctor to check the environment.")
    return 0


def codex_arguments(help_text: str) -> list[str]:
    arguments = ["codex"]
    if "--approve-for-me" in help_text:
        arguments.append("--approve-for-me")
    else:
        print("This Codex version has no automatic approval review; using its native approval behavior.", file=sys.stderr)
    if "--no-alt-screen" in help_text:
        arguments.append("--no-alt-screen")
    return arguments


def ai_command(provider_config: dict, extra: list[str], help_text: str, environment: dict, batch: bool = False) -> tuple[list[str], dict]:
    """Construct argv and process-local environment without invoking a shell."""
    selected = validate_provider(provider_config)
    environment = environment.copy()
    environment.pop(SECRET_ENV, None)
    kind = selected["kind"]
    if kind == "command":
        return selected["argv"] + extra, environment
    argv = ["codex", "-a", "never", "exec"] if batch else codex_arguments(help_text)
    if kind == "responses":
        settings = {
            "model_provider": "rougarou",
            "model": selected["model"],
        }
        # Replace the complete provider table so unrelated existing credentials
        # or headers cannot be inherited for the newly selected endpoint.
        table = {
            "name": "Rougarou provider",
            "base_url": selected["endpoint"],
            "wire_api": "responses",
            "requires_openai_auth": False,
        }
        if selected["authenticated"]:
            secrets = read_json(config_dir() / "credentials.json")
            if secrets.get("endpoint") != selected["endpoint"]:
                raise OperatorError("Provider key belongs to a different endpoint. Run rougarou provider to reconfigure it.")
            token = secrets.get(SECRET_ENV)
            if not isinstance(token, str) or not token:
                raise OperatorError("Provider key is missing. Run rougarou provider to configure it.")
            environment[SECRET_ENV] = token
            table["env_key"] = SECRET_ENV
        for key, value in settings.items():
            argv.extend(["-c", key + "=" + json.dumps(value, ensure_ascii=False)])
        table_text = ", ".join(key + "=" + json.dumps(value, ensure_ascii=False) for key, value in table.items())
        argv.extend(["-c", "model_providers.rougarou={" + table_text + "}"])
    elif kind == "ollama":
        argv.extend(["--oss", "--local-provider", "ollama", "--model", selected["model"]])
    return argv + extra, environment


def ai(extra: list[str]) -> int:
    require_operator()
    config = load_config()
    if "provider" not in config:
        raise OperatorError("Choose your AI first with rougarou provider. Your normal shell is ready to use.")
    selected = config["provider"]
    help_text = ""
    if selected["kind"] != "command":
        result = run(["codex", "--help"], capture_output=True)
        if result.returncode:
            raise OperatorError("Codex could not start. Run rougarou doctor.")
        help_text = result.stdout
    argv, environment = ai_command(selected, extra, help_text, os.environ)
    executable = require_command(argv[0])
    if Path.cwd() == Path.home():
        workspace = Path.home() / "Work"
        workspace.mkdir(mode=0o700, exist_ok=True)
        os.chdir(workspace)
    os.execvpe(executable, argv, environment)
    return 0



def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rougarou", description="Rougarou OS operator tools. Your shell is always available.")
    parser.add_argument("--version", action="store_true", help="show Rougarou version")
    parser.add_argument("command", nargs="?", default="welcome", choices=["welcome", "setup", "ai", "provider", "github", "access", "doctor", "status", "jobs", "worker", "service", "update", "backup", "notify", "menu", "version", "help"])
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    previous_umask = os.umask(0o077)
    try:
        if args.command == "help":
            parser.print_help()
            print("\nsetup --first-login  Optional one-time login invitation; manual setup is resumable.")
            print("ai [arguments...]    Forward arguments to the chosen CLI without shell evaluation.")
            print("access [configure|headless|password]  Review operator sudo authority.")
            print("jobs submit 'task'   Queue a persistent unattended AI job (use --help for options).")
            print("jobs run -- cmd ...  Queue an explicit command under your account.")
            print("jobs list/show/logs/cancel/retry  Inspect and manage durable runs.")
            print("jobs ack [JOB_ID|--all]  Clear reviewed failures from login attention; retain history.")
            print("menu                Open the terminal control menu.")
            print("update plan/refresh/apply/status/recovery  Review and apply signed updates.")
            print("backup init/create/list/check/restore  Encrypted operator backups and staged recovery.")
            print("notify configure/test/check/enable/disable/status  Optional private webhook alerts.")
            print("status [--json]      Worker heartbeat and queue state.")
            print("doctor [--json]      Check service, storage, memory, provider and updates.")
            print("service status/start/stop/restart/enable/disable  Manage your worker.")
            return 0
        if args.command == "version" or args.version:
            path = SHARE / "version"
            print("Rougarou OS " + (path.read_text().strip() if path.exists() else "development"))
            return 0
        if args.command == "setup":
            if args.arguments not in ([], ["--first-login"]):
                raise OperatorError("Usage: rougarou setup [--first-login]")
            return setup(first_login=bool(args.arguments))
        if args.command == "ai":
            extra = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
            return ai(extra)
        if args.command == "access":
            return access(args.arguments)
        if args.command == "jobs":
            from jobs import main as jobs_main
            return jobs_main(args.arguments)
        if args.command in {"status", "doctor", "service"}:
            from health import main as health_main
            return health_main(args.command, args.arguments)
        if args.command in {"update", "backup", "notify", "menu"}:
            import importlib
            module = {"update": "updates", "backup": "backup", "notify": "notifications", "menu": "menu"}[args.command]
            arguments = args.arguments or (["plan"] if args.command == "update" else [])
            return importlib.import_module(module).main(arguments)
        if args.command == "worker":
            if args.arguments:
                raise OperatorError("Usage: rougarou worker")
            from jobs import worker
            return worker()
        if args.arguments:
            raise OperatorError(f"Unexpected arguments for rougarou {args.command}.")
        return {"welcome": welcome, "provider": provider, "github": github}[args.command]()
    except (KeyboardInterrupt, EOFError):
        print("\nBack to your shell. Resume with rougarou setup.")
        return 0
    except (OperatorError, OSError, sqlite3.Error) as error:
        print(f"rougarou: {error}", file=sys.stderr)
        return 1
    finally:
        os.umask(previous_umask)

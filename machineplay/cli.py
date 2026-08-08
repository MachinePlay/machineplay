import argparse
import asyncio
import webbrowser

from machineplay import __version__, api, credentials, log, proc, upload
from machineplay.client import run_forever
from machineplay.config import (
    AVAILABLE_CPUS,
    BACKEND_URL,
    FASTCHESS_PATH,
    MAX_GAMES,
    REGISTRY_HOST,
    RUNNER_ID,
    WEB_URL,
)


def _preflight() -> None:
    """Echo the versions of the external tools the runner drives.

    Warn rather than exit when one is missing: a runner that connects anyway
    reports a precise per-game reason ("`fastchess` is not installed …") to the
    web UI, which beats a silent systemd restart loop.
    """
    for argv in (["docker", "--version"], [FASTCHESS_PATH, "--version"]):
        if (found := proc.version(argv)) is not None:
            log.info(found)
        else:
            log.warn(
                f"`{argv[0]}` is not usable — every game will fail until it is "
                "installed and on PATH"
            )


def cmd_runner(_args: argparse.Namespace) -> None:
    log.info(f"machineplay runner {__version__}")
    log.info(f"runner id: {RUNNER_ID}")
    log.info(f"backend:   {BACKEND_URL}")
    log.info(f"registry:  {REGISTRY_HOST}")
    log.info(f"max games: {MAX_GAMES} (pinned to cores {AVAILABLE_CPUS})")
    _preflight()
    try:
        asyncio.run(run_forever())
    except KeyboardInterrupt:
        log.info("shutting down")


def cmd_login(_args: argparse.Namespace) -> None:
    url = f"{WEB_URL}/cli"
    log.info(f"opening {url}")
    log.info("sign in there, generate a token, then paste it below.")
    try:
        webbrowser.open(url)
    except Exception:
        log.warn(f"could not open a browser — visit {url} yourself")

    token = log.prompt("paste token")
    if not token:
        log.die("no token entered.", f"generate one at {url}")
    if not token.startswith("mp_"):
        log.warn("that doesn't look like a machineplay token (they start with 'mp_')")

    try:
        me = api.get_me(token)
    except api.ApiError as exc:
        log.die(f"login failed: {exc}")

    login = me.get("login", "")
    path = credentials.save(credentials.Credentials(token=token, login=login))
    log.info(f"logged in as {login} (token saved to {path}, mode 0600)")

    # Also authenticate docker so `machineplay upload` can push straight to the
    # registry. Non-fatal: the API token is already saved, so `whoami` works
    # even if docker isn't running yet.
    if upload.docker_login(token, login):
        log.info(f"docker authenticated with {REGISTRY_HOST}")
    else:
        log.warn(
            "docker was not authenticated, so `machineplay upload` can't push yet. "
            "Start docker and run `machineplay login` again."
        )


def cmd_logout(_args: argparse.Namespace) -> None:
    removed = credentials.clear()
    log.info("removed saved credentials" if removed else "no saved credentials")
    # Drop the registry credentials `login` wrote too, so logging out really
    # leaves nothing of ours behind on the machine. A machine without docker
    # has nothing to drop — that's not a reason to fail the logout.
    try:
        if upload.docker_logout():
            log.info(f"docker credentials for {REGISTRY_HOST} removed")
    except proc.CommandNotFound as exc:
        log.warn(f"{exc}, so no docker credentials were removed")


def cmd_whoami(_args: argparse.Namespace) -> None:
    creds = credentials.load()
    if creds is None:
        log.die("not logged in.", "run `machineplay login`")
    if not creds.login:
        log.die(
            "the saved credentials have no login.",
            "run `machineplay login` again to refresh them",
        )
    print(creds.login)


def cmd_upload(_args: argparse.Namespace) -> None:
    upload.do_upload()


def _files_epilog() -> str:
    """Spell out which files on this machine the tool writes."""
    entries = [
        (str(credentials.config_dir() / "credentials.json"), "API token (`login`)"),
        (str(credentials.config_dir() / "runner.json"), "runner id (`runner`)"),
        ("~/.docker/config.json", "registry credentials (`docker login`)"),
    ]
    width = max(len(path) for path, _ in entries)
    lines = "\n".join(f"  {path.ljust(width)}  {what}" for path, what in entries)
    return f"files this tool writes:\n{lines}\n"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="machineplay",
        description="Play UCI chess engines against each other on machineplay.org.",
        epilog=_files_epilog(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"machineplay {__version__}"
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("runner", help="run the tournament runner")
    sub.add_parser("login", help="authenticate this machine with an API token")
    sub.add_parser("logout", help="forget saved credentials")
    sub.add_parser("whoami", help="show the logged-in user")
    sub.add_parser("upload", help="build & upload the engine in the current directory")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    handlers = {
        "runner": cmd_runner,
        "login": cmd_login,
        "logout": cmd_logout,
        "whoami": cmd_whoami,
        "upload": cmd_upload,
    }
    # No subcommand → show help. The runner is started explicitly with
    # `machineplay runner` (see ExecStart in malganis/machineplay.nix).
    if args.command is None:
        parser.print_help()
        return
    try:
        handlers[args.command](args)
    except proc.CommandNotFound as exc:
        log.die(str(exc), exc.hint)
    except KeyboardInterrupt:
        log.die("interrupted.")

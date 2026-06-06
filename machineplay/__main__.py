import argparse
import asyncio
import webbrowser

from machineplay import api, credentials, upload
from machineplay.client import run_forever
from machineplay.config import WEB_URL


def cmd_run(_args: argparse.Namespace) -> None:
    print("Welcome")
    try:
        asyncio.run(run_forever())
    except KeyboardInterrupt:
        print("shutting down")


def cmd_login(_args: argparse.Namespace) -> None:
    url = f"{WEB_URL}/cli"
    print(f"Opening {url} …")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    print("Sign in there, generate a token, then paste it below.")
    token = input("Paste token: ").strip()
    if not token:
        print("no token entered")
        raise SystemExit(1)
    try:
        me = api.get_me(token)
    except api.ApiError as exc:
        print(f"login failed: {exc}")
        raise SystemExit(1)
    login = me.get("login", "")
    path = credentials.save(credentials.Credentials(token=token, login=login))
    print(f"Logged in as {login} (saved to {path})")


def cmd_logout(_args: argparse.Namespace) -> None:
    print("logged out" if credentials.clear() else "not logged in")


def cmd_whoami(_args: argparse.Namespace) -> None:
    creds = credentials.load()
    if creds is None:
        print("not logged in")
        raise SystemExit(1)
    print(creds.login or "(unknown)")


def cmd_upload(_args: argparse.Namespace) -> None:
    upload.do_upload()


def main() -> None:
    parser = argparse.ArgumentParser(prog="machineplay")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="run the tournament runner (default)")
    sub.add_parser("login", help="authenticate this machine with an API token")
    sub.add_parser("logout", help="forget saved credentials")
    sub.add_parser("whoami", help="show the logged-in user")
    sub.add_parser("upload", help="build & upload the engine in the current directory")

    args = parser.parse_args()
    handlers = {
        "run": cmd_run,
        "login": cmd_login,
        "logout": cmd_logout,
        "whoami": cmd_whoami,
        "upload": cmd_upload,
    }
    # No subcommand → run the runner, preserving the deployed service's entrypoint.
    handlers.get(args.command, cmd_run)(args)


if __name__ == "__main__":
    main()

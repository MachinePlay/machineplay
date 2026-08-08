"""Launching external programs (docker, fastchess) with the command echoed.

Everything the package shells out to goes through here so that (a) the exact
argv is printed first — see :mod:`machineplay.log` for why — and (b) a missing
binary surfaces as a :class:`CommandNotFound` carrying an actionable message
instead of a bare ``FileNotFoundError`` from deep inside asyncio.
"""

import asyncio
import subprocess
from collections.abc import Sequence

from machineplay import log

_INSTALL_HINTS = {
    "docker": "install Docker Engine: https://docs.docker.com/engine/install/",
    "fastchess": "build it from https://github.com/Disservin/fastchess "
    "and put `fastchess` on PATH (or set FASTCHESS_PATH)",
}


class CommandNotFound(RuntimeError):
    """An external program isn't installed, or isn't on PATH."""

    def __init__(self, program: str) -> None:
        super().__init__(f"`{program}` is not installed or not on PATH")
        self.program = program

    @property
    def hint(self) -> str:
        return _INSTALL_HINTS.get(
            self.program, f"install {self.program} and make sure it is on PATH"
        )


def run(
    argv: Sequence[str],
    *,
    stdin_text: str | None = None,
    capture: bool = False,
    timeout: float | None = None,
    logger: log.Log = log.root,
) -> subprocess.CompletedProcess[str]:
    """Run `argv` to completion, echoing it first.

    With ``capture=False`` the child inherits our stdout/stderr, which is what
    long, chatty commands (`docker build`, `docker push`) want — their progress
    belongs on the user's terminal.
    """
    logger.cmd(argv)
    try:
        return subprocess.run(
            list(argv),
            input=stdin_text,
            capture_output=capture,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise CommandNotFound(argv[0]) from None


async def start(
    argv: Sequence[str],
    *,
    stdout: int | None = None,
    stderr: int | None = None,
    logger: log.Log = log.root,
) -> asyncio.subprocess.Process:
    """Spawn `argv` in the background, echoing it first."""
    logger.cmd(argv)
    try:
        return await asyncio.create_subprocess_exec(*argv, stdout=stdout, stderr=stderr)
    except FileNotFoundError:
        raise CommandNotFound(argv[0]) from None


def version(argv: Sequence[str], *, logger: log.Log = log.root) -> str | None:
    """Probe a tool's version line, or None if it isn't installed / didn't answer.

    Used by the runner's preflight so a missing dependency is reported once at
    startup instead of once per game.
    """
    try:
        proc = run(argv, capture=True, timeout=15, logger=logger)
    except (CommandNotFound, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    output = (proc.stdout or proc.stderr or "").strip()
    return output.splitlines()[0] if output else None

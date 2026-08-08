"""Console output (and the CLI's input prompts) for both roles of the package.

Two rules this module exists to enforce:

* **Every external command we run is echoed first**, as ``> docker build …``.
  The CLI shells out to tools that touch the user's machine — `docker login`
  writes credentials to ``~/.docker/config.json``, builds and pushes cost time
  and bandwidth — so it should never be a mystery what ran. The echoed line is
  the real argv, quoted so it can be pasted back into a shell as-is.
* **Errors say what failed and what to do about it.** :func:`die` takes hints
  for exactly that.

Runner logs go to journald on the VPS (which timestamps them) and the CLI
writes to a terminal, so nothing here adds timestamps of its own.
"""

import os
import shlex
import sys
from collections.abc import Sequence
from typing import NoReturn, TextIO
from uuid import UUID

_DIM = "\033[2m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_RESET = "\033[0m"

# Colour only when we're actually writing to a terminal: under systemd/journald
# (the production runner) and in pipes, escape codes are just noise.
_COLOR = not os.environ.get("NO_COLOR") and sys.stdout.isatty() and sys.stderr.isatty()


def _paint(text: str, color: str) -> str:
    return f"{color}{text}{_RESET}" if _COLOR else text


def short(value: UUID) -> str:
    """First block of a UUID — enough to correlate log lines, short enough to read."""
    return str(value)[:8]


class Log:
    """A log channel, optionally labelled with a prefix.

    The runner plays several games concurrently and their lines interleave, so
    each game logs through ``Log(f"game {short(game_id)}")`` and every line it
    writes comes out as ``game 0b9e6693: …``.
    """

    def __init__(self, prefix: str = "") -> None:
        self.label = f"{prefix}: " if prefix else ""

    def _write(self, stream: TextIO, text: str) -> None:
        print(f"{self.label}{text}", file=stream, flush=True)

    def info(self, msg: str) -> None:
        self._write(sys.stdout, msg)

    def cmd(self, argv: Sequence[str]) -> None:
        """Echo an external command about to run: ``> docker push …``."""
        self._write(sys.stdout, _paint(f"> {shlex.join(str(a) for a in argv)}", _DIM))

    def warn(self, msg: str) -> None:
        self._write(sys.stderr, _paint(f"warning: {msg}", _YELLOW))

    def error(self, msg: str) -> None:
        self._write(sys.stderr, _paint(f"error: {msg}", _RED))


# The unlabelled channel: the CLI's own output, and the default for helpers
# that take a logger.
root = Log()
info = root.info
cmd = root.cmd
warn = root.warn
error = root.error


def die(msg: str, *hints: str) -> NoReturn:
    """Report an error with optional next steps and exit non-zero."""
    error(msg)
    for hint in hints:
        print(f"  hint: {hint}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def prompt(question: str, default: str = "") -> str:
    """Ask for a line of input, falling back to `default` when the answer is empty.

    Ctrl-C / Ctrl-D at a prompt means "never mind", not a traceback.
    """
    label = f"{question} [{default}]: " if default else f"{question}: "
    try:
        answer = input(label).strip()
    except (EOFError, KeyboardInterrupt):
        print(file=sys.stderr)
        die("aborted.")
    return answer or default

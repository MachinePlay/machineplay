"""Console output and prompts. Every external command is echoed as a pasteable
``> docker build …`` line, and errors come with hints on what to do. No
timestamps: journald adds its own."""

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

# Escape codes are noise in journald and pipes.
_COLOR = not os.environ.get("NO_COLOR") and sys.stdout.isatty() and sys.stderr.isatty()


def _paint(text: str, color: str) -> str:
    return f"{color}{text}{_RESET}" if _COLOR else text


def short(value: UUID) -> str:
    """First block of a UUID, enough to correlate log lines."""
    return str(value)[:8]


class Log:
    """A log channel with an optional prefix, e.g. ``game 0b9e6693: …``, since
    concurrent games' lines interleave."""

    def __init__(self, prefix: str = "") -> None:
        self.label = f"{prefix}: " if prefix else ""

    def _write(self, stream: TextIO, text: str) -> None:
        print(f"{self.label}{text}", file=stream, flush=True)

    def info(self, msg: str) -> None:
        self._write(sys.stdout, msg)

    def cmd(self, argv: Sequence[str]) -> None:
        self._write(sys.stdout, _paint(f"> {shlex.join(str(a) for a in argv)}", _DIM))

    def warn(self, msg: str) -> None:
        self._write(sys.stderr, _paint(f"warning: {msg}", _YELLOW))

    def error(self, msg: str) -> None:
        self._write(sys.stderr, _paint(f"error: {msg}", _RED))


root = Log()
info = root.info
cmd = root.cmd
warn = root.warn
error = root.error


def die(msg: str, *hints: str) -> NoReturn:
    error(msg)
    for hint in hints:
        print(f"  hint: {hint}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def prompt(question: str, default: str = "") -> str:
    """Ask for a line, defaulting to `default`; Ctrl-C / Ctrl-D aborts cleanly."""
    label = f"{question} [{default}]: " if default else f"{question}: "
    try:
        answer = input(label).strip()
    except (EOFError, KeyboardInterrupt):
        print(file=sys.stderr)
        die("aborted.")
    return answer or default

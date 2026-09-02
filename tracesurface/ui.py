from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import NoReturn

import typer
from rich.console import Console
from rich.markup import escape
from rich.padding import Padding
from rich.table import Table
from rich.theme import Theme

__all__ = [
    "SYM",
    "console",
    "abort",
    "brand",
    "error",
    "escape",
    "kv_block",
    "notice",
    "print_banner",
    "section",
    "success",
    "warn",
    "join_dot",
    "configure_logging",
    "configure_worker_logging",
]


@dataclass(frozen=True, slots=True)
class _Symbols:
    brand: str
    ok: str
    fail: str
    warn: str
    info: str
    sep: str
    arrow: str


_SYMBOLS_UTF = _Symbols(
    brand="\u25c6",
    ok="\u2713",
    fail="\u2717",
    warn="\u26a0",
    info="\u25cf",
    sep="\u00b7",
    arrow="\u2192",
)

_SYMBOLS_GBK = _Symbols(
    brand="\u25c6",
    ok="\u221a",
    fail="\u00d7",
    warn="\u25b2",
    info="\u25cf",
    sep="\u00b7",
    arrow="\u2192",
)


def _supports_unicode() -> bool:
    encoding = (getattr(sys.stdout, "encoding", None) or "").lower()

    return "utf" in encoding


SYM = _SYMBOLS_UTF if _supports_unicode() else _SYMBOLS_GBK

_THEME = Theme(
    {
        "tracesurface.brand": "bold cyan",
        "tracesurface.ok": "green",
        "tracesurface.fail": "red",
        "tracesurface.warn": "yellow",
        "tracesurface.info": "cyan",
        "tracesurface.dim": "dim",
        "tracesurface.label": "dim",
        "tracesurface.metric": "bold",
    }
)

console = Console(theme=_THEME, highlight=False)


def join_dot(parts: list[str]) -> str:
    sep = f" [tracesurface.dim]{SYM.sep}[/] "

    return sep.join(p for p in parts if p)


def brand(subtitle: str = "") -> None:
    line = f"  [tracesurface.brand]{SYM.brand} TraceSurface[/]"

    if subtitle:
        line += f"  [tracesurface.dim]{escape(subtitle)}[/]"

    console.print()
    console.print(line)
    console.print()


_BANNER = r"""
 ______                 ____         ___
/_  __/______ ________ / __/_ ______/ _/__ ________
 / / / __/ _ `/ __/ -_)\ \/ // / __/ _/ _ `/ __/ -_)
/_/ /_/  \_,_/\__/\__/___/\_,_/_/ /_/ \_,_/\__/\__/
"""

_TAGLINE = "动态追踪 × 静态分析，从 JavaScript 还原完整 API 面"
_REPO = "https://github.com/pis10/TraceSurface"


def print_banner() -> None:
    from tracesurface import __version__

    lines = [line.rstrip() for line in _BANNER.strip("\n").splitlines()]

    console.print()
    if console.width >= max(len(line) for line in lines) + 2:
        for line in lines:
            console.print(f"  [tracesurface.brand]{escape(line)}[/]")
    else:
        console.print(f"  [tracesurface.brand]{SYM.brand} TraceSurface[/]")
    console.print(
        f"  [tracesurface.dim]v{__version__} {SYM.sep} {_REPO}[/]"
    )
    console.print(f"  {_TAGLINE}")
    console.print()


def kv_block(rows: list[tuple[str, str]], *, indent: int = 2) -> None:
    grid = Table.grid(padding=(0, 3, 0, 0))
    grid.add_column(style="tracesurface.label", no_wrap=True)
    grid.add_column()
    for label, value in rows:
        grid.add_row(label, value)

    console.print(Padding(grid, (0, 0, 0, indent)))


def section(title: str, *, width: int = 50) -> None:
    tail = "\u2500" * max(4, width - len(title) - 2)
    console.print(f"\n  [tracesurface.dim]\u2500\u2500 {escape(title)} {tail}[/]")


def notice(msg: str) -> None:
    console.print(f"  [tracesurface.info]{SYM.info}[/] {msg}")


def _print_warning(target: Console, msg: str) -> None:
    target.print(f"  [tracesurface.warn]{SYM.warn}[/] {msg}")


def warn(msg: str) -> None:
    _print_warning(console, msg)


def success(msg: str) -> None:
    console.print(f"  [tracesurface.ok]{SYM.ok}[/] {msg}")


def error(msg: str) -> None:
    console.print(f"  [tracesurface.fail]{SYM.fail}[/] {msg}")


def abort(msg: str) -> NoReturn:
    error(escape(msg))
    raise typer.Exit(1)


class _TraceSurfaceLogHandler(logging.Handler):
    def __init__(self, target: Console) -> None:
        super().__init__()
        self._console = target

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.levelno < logging.WARNING:
                return

            msg = escape(record.getMessage())
            _print_warning(self._console, msg)
        except Exception:
            self.handleError(record)


def _install_handler(target: Console) -> None:
    root = logging.getLogger()

    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.setLevel(logging.WARNING)
    root.addHandler(_TraceSurfaceLogHandler(target))

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def configure_logging() -> None:
    _install_handler(console)


def configure_worker_logging() -> None:
    worker_console = Console(theme=_THEME, highlight=False, stderr=True)
    _install_handler(worker_console)

from __future__ import annotations

from typing import Protocol


class Explorer(Protocol):
    name: str
    run_once: bool

    async def discover(self, session, round_num: int = 0) -> None: ...

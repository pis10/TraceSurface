from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from tracesurface.storage.commands import Flush, SaveReplayRecord, StorageCommand

if TYPE_CHECKING:
    from tracesurface.storage.sqlite.repositories import SQLiteWriteRepository

_STOP = object()


class StorageWriter:
    def __init__(
        self,
        repo: SQLiteWriteRepository,
        *,
        replay_batch_size: int = 100,
        flush_interval_s: float = 0.1,
    ) -> None:
        self.repo = repo
        self.replay_batch_size = replay_batch_size
        self.flush_interval_s = flush_interval_s
        self.queue: asyncio.Queue[_Envelope | object] = asyncio.Queue()
        self.task: asyncio.Task[None] | None = None
        self._replay_flush_error: Exception | None = None
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="tracesurface-sqlite",
        )

    async def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        await self.queue.put(_STOP)
        try:
            if self.task is not None:
                await self.task
                self.task = None

            if self._replay_flush_error is not None:
                exc = self._take_replay_flush_error()
                if exc is not None:
                    raise exc
        finally:
            await self._run_db(self.repo.close)
            self._executor.shutdown(wait=True, cancel_futures=True)

    async def submit(self, command: StorageCommand) -> Any:
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        await self.queue.put(_Envelope(command, future))
        return await future

    async def fire_and_forget(self, command: StorageCommand) -> None:
        await self.queue.put(_Envelope(command, None))

    async def _run(self) -> None:
        pending_replays: list[_Envelope] = []
        stopping = False

        while not stopping:
            try:
                item = await asyncio.wait_for(
                    self.queue.get(),
                    timeout=self.flush_interval_s,
                )
            except asyncio.TimeoutError:
                item = None

            if item is _STOP:
                stopping = True
            elif item is None:
                await self._flush_replays(pending_replays)
                pending_replays.clear()
            elif isinstance(item, _Envelope):
                command = item.command
                if isinstance(command, SaveReplayRecord):
                    pending_replays.append(item)
                elif isinstance(command, Flush):
                    exc = await self._flush_replays(pending_replays)
                    pending_replays.clear()

                    exc = exc or self._take_replay_flush_error()
                    if exc is None:
                        self._set_result(item, None)
                    else:
                        self._set_exception(item, exc)
                else:
                    exc = await self._flush_replays(pending_replays)
                    pending_replays.clear()
                    exc = exc or self._take_replay_flush_error()

                    if exc is None:
                        await self._execute_one(item)
                    else:
                        self._set_exception(item, exc)

            if len(pending_replays) >= self.replay_batch_size:
                await self._flush_replays(pending_replays)
                pending_replays.clear()

        await self._flush_replays(pending_replays)

    async def _execute_one(self, env: _Envelope) -> None:
        try:
            result = await self._run_db(self.repo.execute, env.command)
        except Exception as exc:
            self._set_exception(env, exc)
        else:
            self._set_result(env, result)

    async def _flush_replays(
        self,
        envelopes: list[_Envelope],
    ) -> Exception | None:
        if not envelopes:
            return None

        records = [
            env.command.record
            for env in envelopes
            if isinstance(env.command, SaveReplayRecord)
        ]
        try:
            replay_ids = await self._run_db(self.repo.save_replays_batch, records)
        except Exception as exc:
            for env in envelopes:
                self._set_exception(env, exc)

            if any(env.future is None for env in envelopes):
                self._replay_flush_error = exc
            return exc

        for env, replay_id in zip(envelopes, replay_ids):
            self._set_result(env, replay_id)
        return None

    async def _run_db(
        self,
        func: Callable[..., Any],
        *args: Any,
    ) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, func, *args)

    def _take_replay_flush_error(self) -> Exception | None:
        exc = self._replay_flush_error
        self._replay_flush_error = None
        return exc

    def _set_result(self, env: _Envelope, result: Any) -> None:
        if env.future is not None and not env.future.done():
            env.future.set_result(result)

    def _set_exception(self, env: _Envelope, exc: Exception) -> None:
        if env.future is not None and not env.future.done():
            env.future.set_exception(exc)


def open_writer() -> StorageWriter:
    from tracesurface.storage.sqlite.connection import init
    from tracesurface.storage.sqlite.repositories import SQLiteWriteRepository

    init()
    return StorageWriter(SQLiteWriteRepository())


def apply_command(command: StorageCommand) -> Any:
    async def _run() -> Any:
        writer = open_writer()
        await writer.start()
        try:
            return await writer.submit(command)
        finally:
            await writer.stop()

    return asyncio.run(_run())


@dataclass(slots=True)
class _Envelope:
    command: StorageCommand
    future: asyncio.Future[Any] | None

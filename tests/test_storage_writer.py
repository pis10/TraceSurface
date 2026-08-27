from __future__ import annotations

import threading
import unittest

from tracesurface.storage.sqlite.writer import StorageWriter


class _Repository:
    def __init__(self) -> None:
        self.thread_ids: list[int] = []

    def execute(self, command: object) -> object:
        self.thread_ids.append(threading.get_ident())
        return command

    def save_replays_batch(self, records: list[object]) -> list[int]:
        self.thread_ids.append(threading.get_ident())
        return list(range(1, len(records) + 1))

    def close(self) -> None:
        self.thread_ids.append(threading.get_ident())


class StorageWriterTests(unittest.IsolatedAsyncioTestCase):
    async def test_repository_work_stays_on_one_dedicated_thread(self) -> None:
        repository = _Repository()
        writer = StorageWriter(repository)  # type: ignore[arg-type]
        await writer.start()
        command = object()
        self.assertIs(await writer.submit(command), command)  # type: ignore[arg-type]
        await writer.stop()

        self.assertEqual(len(set(repository.thread_ids)), 1)
        self.assertNotEqual(repository.thread_ids[0], threading.get_ident())

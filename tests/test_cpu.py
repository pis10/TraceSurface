from __future__ import annotations

import asyncio
import multiprocessing
import os
import unittest
from concurrent.futures import ProcessPoolExecutor

from tracesurface.pipeline.cpu import CpuRunner


class CpuRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_cpu_work_runs_outside_the_event_loop_process(self) -> None:
        executor = ProcessPoolExecutor(
            max_workers=1,
            mp_context=multiprocessing.get_context("spawn"),
        )
        try:
            cpu = CpuRunner(executor)
            worker_pid = await cpu.run(os.getpid)
        finally:
            await asyncio.to_thread(
                executor.shutdown,
                wait=True,
                cancel_futures=True,
            )

        self.assertNotEqual(worker_pid, os.getpid())

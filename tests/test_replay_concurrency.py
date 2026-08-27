from __future__ import annotations

import asyncio
import unittest

from tracesurface.models import ReplayPlan, ReplayRecord, ReplayRequest
from tracesurface.replay.dedup import ReplayDedupStore
from tracesurface.replay.service import ReplayService


class _Transport:
    def __init__(self) -> None:
        self.active = 0
        self.peak = 0

    async def send(
        self,
        request: ReplayRequest,
        *,
        scan_id: int,
        referer: str,
    ) -> ReplayRecord:
        del referer
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0.02)
        self.active -= 1
        return ReplayRecord(
            resolution_id=request.resolution_id,
            scan_id=scan_id,
            domain="example.test",
            variant=request.variant,
            sent_url=request.url,
            sent_method=request.method,
            sent_query=None,
            sent_body=None,
            sent_headers=None,
            status=200,
            resp_headers=None,
            resp_ct="text",
            resp_len=0,
            resp_body="",
            time_ms=1,
            error=None,
        )


class ReplayConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_limit_is_global_across_site_plans(self) -> None:
        limiter = asyncio.Semaphore(2)
        transport = _Transport()
        dedup = ReplayDedupStore()

        def service() -> ReplayService:
            return ReplayService(
                transport=transport,  # type: ignore[arg-type]
                dedup=dedup,
                limiter=limiter,
                concurrency=2,
            )

        plans = [
            ReplayPlan(
                scan_id=site,
                target_url=f"https://example.test/{site}",
                requests=tuple(
                    ReplayRequest(
                        resolution_id=None,
                        method="GET",
                        url=f"https://example.test/{site}/{index}",
                    )
                    for index in range(5)
                ),
            )
            for site in range(2)
        ]
        await asyncio.gather(*(service().run(plan) for plan in plans))

        self.assertEqual(transport.peak, 2)

from __future__ import annotations

import unittest

from tracesurface.inference.cdp_match import merge_runtime_apis
from tracesurface.models import (
    ApiCandidate,
    ApiResolution,
    CallerInfo,
    CDPRequest,
    CDPReplayTarget,
    SourceLocation,
)
from tracesurface.replay.plan import ReplayPlanBuilder


def _resolution(
    *,
    method: str = "GET",
    url: str = "https://example.com/api/users",
    status: str = "inferred",
    tier: str | None = "L2",
) -> ApiResolution:
    return ApiResolution(
        candidate=ApiCandidate(
            path="/api/users",
            method=method,
            pattern="fetch",
            location=SourceLocation(
                url="https://example.com/app.js",
                line=10,
                col_start=4,
                col_end=20,
            ),
            params=(),
            caller=CallerInfo(),
        ),
        status=status,  # type: ignore[arg-type]
        full_url=url,
        tier=tier,  # type: ignore[arg-type]
    )


class MergeRuntimeApisTests(unittest.TestCase):
    def test_unmatched_cdp_request_becomes_confirmed_api(self) -> None:
        req = CDPRequest(
            request_url="https://example.com/api/captcha",
            request_path="/api/captcha",
            method="GET",
        )
        merged = merge_runtime_apis((_resolution(),), [req])
        self.assertEqual(len(merged), 2)
        captured = merged[-1]
        self.assertEqual(captured.status, "confirmed")
        self.assertEqual(captured.full_url, req.request_url)
        self.assertEqual(captured.candidate.pattern, "cdp")
        self.assertIsNotNone(captured.confirmed)
        self.assertEqual(captured.confirmed.url, req.request_url)

    def test_matching_inferred_api_is_promoted(self) -> None:
        url = "https://example.com/api/users"
        req = CDPRequest(request_url=url, request_path="/api/users", method="GET")
        merged = merge_runtime_apis((_resolution(url=url, status="inferred"),), [req])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].status, "confirmed")
        self.assertEqual(merged[0].full_url, url)
        self.assertIsNotNone(merged[0].confirmed)

    def test_empty_cdp_only_leaves_resolutions_unchanged(self) -> None:
        original = (_resolution(),)
        self.assertIs(merge_runtime_apis(original, []), original)


class CdpReplayLinkTests(unittest.TestCase):
    def test_cdp_replay_keeps_resolution_id(self) -> None:
        target = CDPReplayTarget(
            cdp_request_id=7,
            method="GET",
            url="https://example.com/api/captcha",
            resolution_id=42,
        )
        requests = ReplayPlanBuilder().build_cdp_requests(
            target,
            target_url="https://example.com/login",
        )
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].resolution_id, 42)
        self.assertEqual(requests[0].cdp_request_id, 7)
        self.assertEqual(requests[0].variant, "cdp")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from tracesurface.ai import repository as repo
from tracesurface.ai.models import (
    AISettings,
    InterfaceResult,
    SiteResult,
    StartAnalysis,
    validate_result,
)
from tracesurface.ai.service import (
    AnalysisManager,
    build_payload,
    safe_error,
    truncate_body,
)
from tracesurface.ai.settings import load_settings, public_settings, save_settings
from tracesurface.storage.sqlite.connection import connect, init
from tracesurface.storage.sqlite.queries import query_replays


class DatabaseCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(
            os.environ, {"TRACESURFACE_HOME": self.tmp.name, "TRACESURFACE_AI_KEY": ""}
        )
        self.env.start()
        init()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def scan(self, target="https://example.test", status="done"):
        with closing(connect()) as c:
            return c.execute(
                "INSERT INTO scans(target_url,domain,started_at,finished_at,status) VALUES(?,?,?,?,?)",
                (target, "example.test", 1, 2 if status == "done" else None, status),
            ).lastrowid

    def replay(self, scan_id, status=200, ct="json", body='{"code":401}', file=None):
        with closing(connect()) as c:
            return c.execute(
                "INSERT INTO verifications(scan_id,domain,sent_url,sent_method,status,resp_ct,resp_snippet,resp_len,resp_file,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    scan_id,
                    "example.test",
                    "https://example.test/api",
                    "GET",
                    status,
                    ct,
                    body,
                    len(body.encode()),
                    file,
                    1,
                ),
            ).lastrowid

    def config(self):
        return AISettings(api_key=SecretStr("test-key-only"))


class DataTests(DatabaseCase):
    def test_reuses_existing_filters_and_scan_scope(self):
        a, b = self.scan(), self.scan("https://other.test")
        expected = self.replay(a)
        self.replay(a, 403)
        self.replay(a, 200, "html")
        self.replay(b)
        ids = [r["id"] for r in repo.eligible_replays(a)]
        total, rows = query_replays(scan_id=a, buckets=["2xx"], resp_cts=["json"])
        self.assertEqual(ids, [expected])
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]["id"], expected)

    def test_utf8_truncation_and_original_preserved(self):
        text, cut, size = truncate_body("你好世界", 7)
        self.assertEqual(text, "你好")
        self.assertTrue(cut)
        self.assertEqual(size, 12)
        scan = self.scan()
        body = '{"rows":' + '"你好"' * 200 + "}"
        rid = self.replay(scan, body=body)
        settings = self.config().model_copy(update={"response_limit": 256})
        payload, ids = build_payload(repo.scan_record(scan), settings)
        self.assertEqual(ids, {rid})
        self.assertLessEqual(payload["records"][0]["response"]["submitted_bytes"], 256)
        with closing(connect()) as c:
            self.assertEqual(
                c.execute(
                    "SELECT resp_snippet FROM verifications WHERE id=?", (rid,)
                ).fetchone()[0],
                body,
            )

    def test_spilled_response_uses_file_not_preview(self):
        scan = self.scan()
        self.replay(scan, body="preview", file="body.bin")
        (Path(self.tmp.name) / "responses/body.bin").write_text('{"actual": true}')
        payload, _ = build_payload(repo.scan_record(scan), self.config())
        self.assertEqual(payload["records"][0]["response"]["body"], '{"actual": true}')
        (Path(self.tmp.name) / "responses/body.bin").unlink()
        with self.assertRaises(ValueError):
            build_payload(repo.scan_record(scan), self.config())

    def test_running_scan_skipped_and_never_automatically_enqueued(self):
        scan = self.scan(status="running")
        batch = repo.enqueue(StartAnalysis(scan_ids=[scan]), self.config())
        self.assertEqual(repo.jobs(batch, 0, 50)["items"][0]["status"], "skipped")
        with closing(connect()) as c:
            c.execute(
                "UPDATE scans SET status='done',finished_at=2 WHERE id=?", (scan,)
            )
        self.assertIsNone(repo.claim())

    def test_duplicate_active_and_completed_jobs(self):
        scan = self.scan()
        rid = self.replay(scan)
        first = repo.enqueue(StartAnalysis(scan_ids=[scan]), self.config())
        second = repo.enqueue(StartAnalysis(scan_ids=[scan], force=True), self.config())
        self.assertEqual(repo.jobs(second, 0, 50)["items"][0]["status"], "skipped")
        job = repo.claim()
        repo.save_result(
            job["id"],
            SiteResult(
                results=[
                    InterfaceResult(
                        replay_id=rid,
                        suspected=True,
                        reason="sensitive fields",
                        recommendation="confirm",
                    )
                ]
            ),
            set(),
            {},
        )
        third = repo.enqueue(StartAnalysis(scan_ids=[scan]), self.config())
        self.assertEqual(repo.jobs(third, 0, 50)["items"][0]["status"], "skipped")
        self.assertEqual(repo.findings(None, "", "", 0, 50)["total"], 1)
        self.assertEqual(repo.findings(first, "", "", 0, 50)["total"], 1)
        repo.enqueue(StartAnalysis(scan_ids=[scan], force=True), self.config())
        newer = repo.claim()
        repo.save_result(
            newer["id"],
            SiteResult(
                results=[
                    InterfaceResult(
                        replay_id=rid,
                        suspected=False,
                        reason="public data",
                        recommendation="",
                    )
                ]
            ),
            set(),
            {},
        )
        self.assertEqual(repo.findings(None, "", "", 0, 50)["total"], 0)
        self.assertEqual(repo.findings(first, "", "", 0, 50)["total"], 1)

    def test_selection_includes_all_filtered_pages(self):
        for i in range(57):
            self.scan(f"https://selected-{i}.test")
        self.scan("https://excluded.test")
        batch = repo.enqueue(StartAnalysis(search="selected-"), self.config())
        self.assertEqual(repo.jobs(batch, 0, 50)["total"], 57)
        self.assertEqual(len(repo.jobs(batch, 50, 50)["items"]), 7)

    def test_delete_scan_removes_findings_and_blocks_late_result(self):
        scan = self.scan()
        rid = self.replay(scan)
        repo.enqueue(StartAnalysis(scan_ids=[scan]), self.config())
        job = repo.claim()
        from tracesurface.storage.commands import PurgeTarget
        from tracesurface.storage.sqlite.writer import apply_command

        apply_command(PurgeTarget("https://example.test"))
        with self.assertRaises(ValueError):
            repo.save_result(
                job["id"],
                SiteResult(
                    results=[
                        InterfaceResult(
                            replay_id=rid,
                            suspected=True,
                            reason="test",
                            recommendation="",
                        )
                    ]
                ),
                set(),
                {},
            )
        self.assertEqual(repo.findings(None, "", "", 0, 50)["total"], 0)

    def test_no_legacy_migration_or_data_deletion(self):
        scan = self.scan()
        with closing(connect()) as c:
            c.execute("PRAGMA user_version=0")
        with self.assertRaisesRegex(RuntimeError, "不兼容"):
            init()
        with closing(connect()) as c:
            self.assertIsNotNone(
                c.execute("SELECT * FROM scans WHERE id=?", (scan,)).fetchone()
            )

    def test_cli_explains_incompatible_data_before_starting_services(self):
        from typer.testing import CliRunner

        from tracesurface.cli import app

        self.scan()
        with closing(connect()) as conn:
            conn.execute("PRAGMA user_version=0")
        with (
            patch("tracesurface.cli._ensure_browser") as browser,
            patch("tracesurface.server.run.run_report_server") as server,
        ):
            for command in (["serve"], ["scan", "https://example.test"]):
                result = CliRunner().invoke(app, command)
                self.assertEqual(result.exit_code, 1)
                self.assertIn("数据目录版本不兼容", result.output)
                self.assertIn("TRACESURFACE_HOME", result.output)
                self.assertNotIn("Traceback", result.output)
            browser.assert_not_called()
            server.assert_not_called()

    def test_key_never_in_public_config_or_batch(self):
        settings = self.config()
        save_settings(settings)
        save_settings(settings.model_copy(update={"api_key": SecretStr("")}))
        self.assertEqual(load_settings().api_key.get_secret_value(), "test-key-only")
        self.assertNotIn("test-key-only", json.dumps(public_settings(load_settings())))
        if os.name != "nt":
            self.assertEqual(
                (Path(self.tmp.name) / "ai-settings.json").stat().st_mode & 0o777, 0o600
            )
        scan = self.scan()
        repo.enqueue(StartAnalysis(scan_ids=[scan]), settings)
        with closing(connect()) as c:
            self.assertNotIn(
                "test-key-only",
                c.execute("SELECT settings_json FROM ai_batches").fetchone()[0],
            )

    def test_api_settings_validation_does_not_echo_key(self):
        from tracesurface.server.app import create_app

        with TestClient(create_app()) as client:
            result = client.put(
                "/api/ai/settings",
                json={"api_key": "secret-never-echo", "response_limit": 0},
            )
            self.assertEqual(result.status_code, 422)
            self.assertNotIn("secret-never-echo", result.text)
            self.assertEqual(
                client.put(
                    "/api/ai/settings", json={}, headers={"origin": "https://evil.test"}
                ).status_code,
                403,
            )
            self.assertEqual(client.post("/api/ai/batches", json={}).status_code, 400)


class OutputTests(unittest.TestCase):
    def test_exact_coverage_and_boolean_types(self):
        result = {
            "results": [
                {
                    "replay_id": 1,
                    "suspected": False,
                    "reason": "401",
                    "recommendation": "",
                }
            ]
        }
        self.assertFalse(validate_result(json.dumps(result), {1}).results[0].suspected)
        for ids in ({1, 2}, {2}):
            with self.assertRaises(ValueError):
                validate_result(json.dumps(result), ids)
        result["results"] *= 2
        with self.assertRaises(ValueError):
            validate_result(json.dumps(result), {1})
        result["results"] = result["results"][:1]
        result["results"][0]["suspected"] = "false"
        with self.assertRaises(ValidationError):
            validate_result(json.dumps(result), {1})

    def test_provider_error_is_sanitized(self):
        error = RuntimeError("api key secret and entire request body")
        self.assertNotIn("secret", safe_error(error))
        self.assertNotIn("secret", safe_error(ValueError("secret")))


class WorkerTests(DatabaseCase):
    def test_total_timeout_cancels_keepalive_and_allows_manual_retry(self):
        async def run():
            calls = 0
            cancelled = asyncio.Event()

            async def fake(payload, settings):
                nonlocal calls
                calls += 1
                if calls == 1:
                    try:
                        # Receiving periodic data must not extend the total deadline.
                        while True:
                            await asyncio.sleep(0.005)
                    finally:
                        cancelled.set()
                return SiteResult(
                    results=[
                        InterfaceResult(
                            replay_id=r["replay_id"],
                            suspected=False,
                            reason="login required",
                            recommendation="",
                        )
                        for r in payload["records"]
                    ]
                ), {}

            scan = self.scan()
            self.replay(scan)
            manager = AnalysisManager(fake)
            await manager.start()
            try:
                for expected in ["failed", "done"]:
                    batch = repo.enqueue(StartAnalysis(scan_ids=[scan]), self.config())
                    manager.notify(batch, self.config())
                    async with asyncio.timeout(2):
                        while True:
                            job = repo.jobs(batch, 0, 50)["items"][0]
                            if job["status"] not in {"queued", "running"}:
                                break
                            await asyncio.sleep(0.01)
                    self.assertEqual(job["status"], expected)
                    self.assertEqual(repo.findings(batch, "", "", 0, 50)["total"], 0)
                    if expected == "failed":
                        self.assertTrue(cancelled.is_set())
                        self.assertIn("超时", job["message"])
                        self.assertEqual(calls, 1)
                self.assertEqual(calls, 2)
            finally:
                await manager.close()

        with patch("tracesurface.ai.service.ANALYSIS_TIMEOUT_SECONDS", 0.05):
            asyncio.run(run())

    def test_site_isolation_empty_skip_failure_and_restart(self):
        async def run():
            calls = []

            async def fake(payload, settings):
                calls.append(payload)
                if "fail" in payload["site"]["target_url"]:
                    raise RuntimeError("secret key")
                return SiteResult(
                    results=[
                        InterfaceResult(
                            replay_id=r["replay_id"],
                            suspected=True,
                            reason="evidence",
                            recommendation="check",
                        )
                        for r in payload["records"]
                    ]
                ), {}

            ok = self.scan()
            fail = self.scan("https://fail.test")
            empty_scan = self.scan("https://empty.test")
            self.replay(ok)
            self.replay(fail)
            manager = AnalysisManager(fake)
            await manager.start()
            batch = repo.enqueue(
                StartAnalysis(scan_ids=[ok, fail, empty_scan]), self.config()
            )
            manager.notify(batch, self.config())
            try:
                for _ in range(200):
                    jobs = repo.jobs(batch, 0, 50)["items"]
                    if all(j["status"] not in {"queued", "running"} for j in jobs):
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(
                    {j["status"] for j in jobs}, {"done", "failed", "skipped"}
                )
                self.assertEqual(len(calls), 2)
                self.assertTrue(all(len(p["records"]) == 1 for p in calls))
                self.assertEqual(repo.findings(batch, "", "", 0, 50)["total"], 1)
                self.assertNotIn("secret key", json.dumps(jobs))
                repo.enqueue(StartAnalysis(scan_ids=[fail], force=True), self.config())
            finally:
                await manager.close()
            self.assertIsNone(repo.claim())

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()

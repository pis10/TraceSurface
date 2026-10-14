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

    def test_catalog_presets_use_registered_model_limits(self):
        from tracesurface.ai.catalog import model_catalog
        from tracesurface.ai.runtime import get_litellm

        catalog = model_catalog()
        self.assertEqual(len({p["id"] for p in catalog}), len(catalog))
        llm = get_litellm()
        for provider in catalog:
            self.assertTrue(provider["models"])
            for model in provider["models"]:
                info = llm.get_model_info(f"{provider['native']}/{model['value']}")
                self.assertLessEqual(
                    model["max_output_tokens"], info["max_output_tokens"]
                )
                self.assertGreaterEqual(model["max_output_tokens"], 1024)
        self.assertNotIn("api_key", json.dumps(catalog))

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
            manager.notify(batch, self.config().api_key)
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


class ModelCallTests(unittest.IsolatedAsyncioTestCase):
    async def test_deepseek_uses_json_once_and_no_tools(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, Mock

        from tracesurface.ai.service import analyze

        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(
                        content=json.dumps(
                            {
                                "results": [
                                    {
                                        "replay_id": 7,
                                        "suspected": False,
                                        "reason": "401",
                                        "recommendation": "",
                                    }
                                ]
                            }
                        )
                    ),
                )
            ],
            usage=None,
        )
        llm = SimpleNamespace(
            token_counter=Mock(return_value=20),
            get_model_info=Mock(return_value={"max_input_tokens": 1000000}),
            supports_response_schema=Mock(return_value=True),
            acompletion=AsyncMock(return_value=response),
        )
        payload = {"records": [{"replay_id": 7}]}
        with patch("tracesurface.ai.inference.get_litellm", return_value=llm):
            result, _ = await analyze(payload, AISettings(api_key=SecretStr("test")))
            self.assertFalse(result.results[0].suspected)
            self.assertEqual(llm.acompletion.await_count, 1)
            kwargs = llm.acompletion.call_args.kwargs
            self.assertEqual(kwargs["response_format"], {"type": "json_object"})
            self.assertNotIn("tools", kwargs)
            self.assertEqual(len(kwargs["messages"]), 2)
            llm.token_counter.return_value = 2000000
            with self.assertRaisesRegex(ValueError, "超过当前模型可用容量"):
                await analyze(payload, AISettings())
            self.assertEqual(llm.acompletion.await_count, 1)
            llm.token_counter.return_value = 20
            response.choices[0].finish_reason = "length"
            with self.assertRaisesRegex(ValueError, "未完整"):
                await analyze(payload, AISettings())

    async def test_custom_model_preserves_model_identifier(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, Mock

        from tracesurface.ai.service import analyze

        class Unmapped(Exception):
            pass

        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content='{"results":[]}'),
                )
            ],
            usage=None,
        )
        llm = SimpleNamespace(
            ModelNotMappedError=Unmapped,
            token_counter=Mock(return_value=20),
            get_model_info=Mock(side_effect=Unmapped),
            supports_response_schema=Mock(return_value=False),
            acompletion=AsyncMock(return_value=response),
        )
        with patch("tracesurface.ai.inference.get_litellm", return_value=llm):
            await analyze(
                {"records": []},
                AISettings(
                    provider_id="custom",
                    model="team/custom-local-model",
                    api_base="http://localhost:8000",
                ),
            )
        self.assertEqual(llm.acompletion.await_count, 1)
        self.assertEqual(
            llm.acompletion.call_args.kwargs["model"], "openai/team/custom-local-model"
        )


class BuildWorkaroundTests(unittest.TestCase):
    def test_nuitka_pattern_rewrite_compiles(self):
        import ast

        import yaml

        source = """def role_of_choice(choice):
    match choice:
        case StreamingChoices(delta=Delta(role=str() as role)) | {"delta": {"role": str() as role}} if role:
            return role
        case _:
            return "assistant"
"""
        # The replacement is intentionally scoped to the dependency's indentation.
        nested = "\n".join("    " + line for line in source.splitlines())
        config = yaml.safe_load(
            (
                Path(__file__).parents[1] / "scripts/nuitka-package.config.yml"
            ).read_text()
        )
        old, new = next(iter(config[0]["anti-bloat"][0]["replacements_plain"].items()))
        replacement = nested.replace(old, new)
        self.assertNotEqual(replacement, nested)
        ast.parse("class C:\n" + replacement)


if __name__ == "__main__":
    unittest.main()

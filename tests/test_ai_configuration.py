from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from tracesurface.ai.catalog import model_catalog
from tracesurface.ai.inference import analyze_configured, request_options
from tracesurface.ai.models import AISettings
from tracesurface.ai.runtime import get_litellm
from tracesurface.ai.settings import (
    load_settings,
    prepare_settings,
    public_settings,
    save_settings,
)
from tracesurface.storage.sqlite.connection import init


class ConfigurationTests(unittest.TestCase):
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

    def test_directory_covers_all_native_chat_models_without_alias_duplicates(self):
        llm = get_litellm()
        for provider in model_catalog():
            native = provider["native"]
            expected = {
                name.removeprefix(native + "/")
                for name, info in llm.model_cost.items()
                if info.get("litellm_provider") == native and info.get("mode") == "chat"
            }
            actual = [m["value"] for m in provider["models"]]
            self.assertEqual(set(actual), expected)
            self.assertEqual(len(actual), len(set(actual)))
        self.assertTrue(all(p["id"] == p["native"] for p in model_catalog()))

    def test_preset_uses_native_zai_and_fixed_endpoint(self):
        config = AISettings(
            provider_id="zai",
            model="glm-5.3",
            api_base="https://wrong.example",
        )
        prepared = prepare_settings(config)
        options, capacity = request_options(prepared, 84)
        self.assertEqual(options["model"], "zai/glm-5.3")
        self.assertEqual(options["api_base"], "https://open.bigmodel.cn/api/paas/v4")
        self.assertEqual(options["max_tokens"] + capacity, 1000000)
        self.assertGreater(options["max_tokens"], 1024)

    def test_old_settings_are_not_migrated_or_accepted(self):
        for field, value in {
            "protocol": "chat",
            "max_input_tokens": 128000,
            "max_output_tokens": 8192,
            "timeout_seconds": 300,
            "output_mode": "auto",
        }.items():
            with self.subTest(field=field), self.assertRaises(ValidationError):
                AISettings.model_validate({field: value})

    def test_invalid_settings_error_is_readable_and_keeps_the_file(self):
        from tracesurface.server.app import create_app

        path = Path(self.tmp.name, "ai-settings.json")
        original = '{"api_key":"local-test-secret","output_mode":"auto"}'
        path.write_text(original)
        with TestClient(create_app()) as client:
            response = client.get("/api/ai/settings")
        self.assertEqual(response.status_code, 409)
        self.assertIn("重新配置", response.json()["detail"])
        self.assertNotIn("local-test-secret", response.text)
        self.assertEqual(path.read_text(), original)

    def test_cold_server_can_load_catalog_while_other_requests_are_logged(self):
        program = """
import concurrent.futures
import json
import threading
import time
from urllib.request import urlopen
import uvicorn
from tracesurface.server.app import create_app

server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=0))
thread = threading.Thread(target=server.run, daemon=True)
thread.start()
try:
    deadline = time.monotonic() + 20
    while not server.started:
        assert thread.is_alive() and time.monotonic() < deadline, "startup timed out"
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    def get(path):
        with urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
            return json.load(response)
    paths = ["/api/ai/catalog", "/api/ai/settings", "/api/ai/batches"] * 4
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(get, paths))
    assert results[0]["providers"], "catalog is empty"
    assert results[1]["provider_id"] == "deepseek"
finally:
    server.should_exit = True
    thread.join(timeout=5)
assert not thread.is_alive(), "server did not stop"
"""
        result = subprocess.run(
            [sys.executable, "-c", program],
            capture_output=True,
            text=True,
            timeout=35,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Exception ignored", result.stderr)

    def test_reasoning_strength_is_not_silently_dropped(self):
        for ident, name in [("deepseek", "deepseek-flash"), ("zai", "glm-5.3")]:
            options, _ = request_options(
                AISettings(provider_id=ident, model=name, reasoning_effort="max")
            )
            self.assertEqual(options["extra_body"]["reasoning_effort"], "max")
        with self.assertRaises(ValueError):
            prepare_settings(
                AISettings(provider_id="zai", model="glm-5.3", reasoning_effort="off")
            )

    def test_key_is_reused_only_for_same_endpoint_and_environment_not_persisted(self):
        save_settings(AISettings(api_key=SecretStr("local-test-secret")))
        saved = AISettings(
            **{
                k: v
                for k, v in public_settings(load_settings()).items()
                if k != "has_api_key"
            }
        )
        self.assertEqual(
            prepare_settings(saved, require_key=True).api_key.get_secret_value(),
            "local-test-secret",
        )
        save_settings(saved)
        self.assertEqual(
            load_settings().api_key.get_secret_value(), "local-test-secret"
        )
        with self.assertRaises(ValueError):
            prepare_settings(
                AISettings(provider_id="zai", model="glm-5.3"),
                require_key=True,
            )
        Path(self.tmp.name, "ai-settings.json").unlink()
        with patch.dict(os.environ, {"TRACESURFACE_AI_KEY": "env-test-secret"}):
            resolved = prepare_settings(saved, require_key=True)
            self.assertEqual(resolved.api_key.get_secret_value(), "env-test-secret")
            save_settings(saved)
            self.assertNotIn(
                "env-test-secret", Path(self.tmp.name, "ai-settings.json").read_text()
            )

    def test_connection_probe_does_not_save_or_return_provider_error_contents(self):
        from tracesurface.server.app import create_app

        with TestClient(create_app()) as client:
            body = {
                "provider_id": "deepseek",
                "model": "deepseek-flash",
                "api_key": "local-test-secret",
            }
            with patch(
                "tracesurface.ai.inference.test_connection",
                new=AsyncMock(
                    side_effect=RuntimeError("local-test-secret sensitive response")
                ),
            ):
                response = client.post("/api/ai/test", json=body)
            self.assertFalse(response.json()["ok"])
            self.assertNotIn("local-test-secret", response.text)
            self.assertNotIn("sensitive response", response.text)
            self.assertFalse(Path(self.tmp.name, "ai-settings.json").exists())
            self.assertEqual(
                client.post(
                    "/api/ai/test",
                    json=body,
                    headers={"Origin": "https://foreign.example"},
                ).status_code,
                403,
            )

    def test_saving_cannot_reuse_a_key_for_another_provider(self):
        from tracesurface.server.app import create_app

        with (
            patch.dict(os.environ, {"TRACESURFACE_AI_KEY": "env-test-secret"}),
            TestClient(create_app()) as client,
        ):
            response = client.put(
                "/api/ai/settings",
                json={"provider_id": "deepseek", "model": "deepseek-flash"},
            )
            self.assertEqual(response.status_code, 200)
            path = Path(self.tmp.name, "ai-settings.json")
            before = path.read_text()
            self.assertNotIn("env-test-secret", before)
            response = client.put(
                "/api/ai/settings", json={"provider_id": "zai", "model": "glm-5.3"}
            )
            self.assertEqual(response.status_code, 422)
            self.assertEqual(path.read_text(), before)

    def test_unsupported_reasoning_is_rejected_before_call(self):
        with self.assertRaises(ValueError):
            request_options(
                AISettings(
                    provider_id="minimax", model="MiniMax-M2.5", reasoning_effort="high"
                )
            )


class InferenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_probe_validates_same_result_schema_and_ids(self):
        from tracesurface.ai.inference import test_connection

        llm = get_litellm()
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content='{"results":[]}'),
                )
            ],
            usage=None,
        )
        config = AISettings(
            provider_id="deepseek",
            model="deepseek-flash",
            api_key=SecretStr("placeholder"),
        )
        with patch.object(
            llm, "acompletion", new=AsyncMock(return_value=response)
        ) as call:
            with self.assertRaisesRegex(ValueError, "遗漏"):
                await test_connection(config)
            payload = json.loads(call.call_args.kwargs["messages"][1]["content"])
            self.assertEqual(payload["records"][0]["replay_id"], 1)
            self.assertIn("connection-test.invalid", str(payload))
            self.assertNotIn("tools", call.call_args.kwargs)

    async def test_input_budget_is_enforced_before_request(self):
        llm = get_litellm()
        with (
            patch.object(llm, "token_counter", return_value=2_000_000),
            patch.object(llm, "acompletion", new=AsyncMock()) as call,
        ):
            with self.assertRaisesRegex(ValueError, "容量"):
                await analyze_configured(
                    {"records": []},
                    AISettings(provider_id="deepseek", model="deepseek-flash"),
                )
            call.assert_not_awaited()

    async def test_native_sdk_wire_payload_preserves_reasoning_and_json(self):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                received.append(
                    json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                )
                body = json.dumps(
                    {
                        "id": "test",
                        "object": "chat.completion",
                        "created": 1,
                        "model": "test",
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": '{"results":[]}',
                                },
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 1,
                            "completion_tokens": 1,
                            "total_tokens": 2,
                        },
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for provider, name in [
                ("deepseek", "deepseek-flash"),
                ("zai", "glm-5.3"),
            ]:
                config = AISettings(
                    provider_id=provider,
                    model=name,
                    reasoning_effort="max",
                    api_key=SecretStr("placeholder"),
                )
                options, _ = request_options(config)
                options["api_base"] = f"http://127.0.0.1:{server.server_port}/v1"
                await get_litellm().acompletion(
                    messages=[{"role": "user", "content": "Return a JSON object."}],
                    **options,
                )
                self.assertEqual(received[-1]["reasoning_effort"], "max")
                self.assertEqual(received[-1]["model"], name)
            self.assertEqual(received[0]["response_format"], {"type": "json_object"})
            self.assertEqual(received[0]["thinking"], {"type": "enabled"})
        finally:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            thread.join()

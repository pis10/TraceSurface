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
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient
from pydantic import SecretStr

from tracesurface.ai.catalog import catalog_response, model_catalog, model_details
from tracesurface.ai.inference import (
    analyze_configured,
    request_options,
    test_connection,
)
from tracesurface.ai.models import AISettings
from tracesurface.ai.runtime import get_litellm
from tracesurface.ai.settings import (
    prepare_settings,
    save_settings,
)
from tracesurface.storage.sqlite.connection import init


def custom(**updates):
    return AISettings(
        **{
            "mode": "custom",
            "provider_id": "openai",
            "model": "org/private-model",
            "api_base": "https://models.test/v1",
            "api_key": SecretStr("test-key"),
            "max_input_tokens": 32000,
            "max_output_tokens": 8192,
            **updates,
        }
    )


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

    def test_catalog_uses_sdk_without_authentication_or_endpoint_discovery(self):
        llm = get_litellm()
        model_catalog.cache_clear()
        with patch.object(
            llm, "get_llm_provider", side_effect=AssertionError("must not authenticate")
        ):
            catalog = model_catalog()
        ids = {p["id"] for p in catalog}
        self.assertTrue(
            {
                "deepseek",
                "anthropic",
                "azure",
                "openrouter",
                "gemini",
            }
            <= ids
        )
        self.assertGreater(len(ids), 50)
        self.assertTrue(
            {"custom", "bedrock", "ollama", "vertex_ai", "github_copilot"}.isdisjoint(
                ids
            )
        )
        expected = {p: set() for p in ids}
        for route, info in llm.model_cost.items():
            if info.get("mode") != "chat":
                continue
            prefix, separator, name = route.partition("/")
            native = (
                prefix if separator and prefix in ids else info.get("litellm_provider")
            )
            if native in ids:
                expected[native].add(name if separator and native == prefix else route)
        for provider in catalog:
            self.assertEqual(
                {m["value"] for m in provider["models"]}, expected[provider["id"]]
            )

        model_details.cache_clear()
        with patch.object(
            llm, "get_model_info", side_effect=AssertionError("no remote discovery")
        ):
            for provider in catalog:
                if provider["models"]:
                    model_details(provider["id"], provider["models"][0]["value"])

    def test_native_uses_sdk_capacity_and_default_endpoint(self):
        value = AISettings(
            api_key=SecretStr("test-key"), max_input_tokens=1024, max_output_tokens=256
        )
        options, capacity = request_options(value, 84)
        self.assertEqual(options["model"], "deepseek-flash")
        self.assertEqual(options["custom_llm_provider"], "deepseek")
        self.assertNotIn("api_base", options)
        self.assertEqual(
            options["max_tokens"] + capacity,
            model_details("deepseek", "deepseek-flash")["max_input_tokens"],
        )
        self.assertNotIn("tools", options)
        self.assertFalse(options["drop_params"])

    def test_custom_never_infers_capabilities_even_for_known_model_name(self):
        value = custom().model_copy(
            update={
                "model": "gpt-4o",
                "reasoning_mode": "effort",
                "reasoning_effort": "high",
                "json_mode": True,
            }
        )
        with patch(
            "tracesurface.ai.catalog.model_details",
            side_effect=AssertionError("no lookup"),
        ):
            options, capacity = request_options(value)
        self.assertEqual(options["model"], "gpt-4o")
        self.assertEqual(options["extra_body"]["reasoning_effort"], "high")
        self.assertEqual(options["response_format"], {"type": "json_object"})
        self.assertEqual(capacity + options["max_tokens"], 32000)
        self.assertEqual(value.max_input_tokens, 32000)
        with self.assertRaisesRegex(ValueError, "填写"):
            prepare_settings(value.model_copy(update={"max_input_tokens": None}))

    def test_custom_reasoning_modes_are_forwarded_without_model_name_rules(self):
        for mode, expected in [
            ("enabled", {"type": "enabled"}),
            ("disabled", {"type": "disabled"}),
            ("adaptive", {"type": "adaptive"}),
            ("budget", {"type": "enabled", "budget_tokens": 2048}),
        ]:
            with self.subTest(mode=mode):
                value = custom(
                    reasoning_mode=mode,
                    thinking_budget=2048 if mode == "budget" else None,
                )
                options, _ = request_options(value)
                self.assertEqual(options["extra_body"]["thinking"], expected)
                self.assertNotIn("reasoning_effort", options)
        with self.assertRaisesRegex(ValueError, "思考预算"):
            prepare_settings(custom(reasoning_mode="budget", thinking_budget=8192))

    def test_reasoning_efforts_unwrap_optional_sdk_type(self):
        details = model_details("openai", "gpt-5")
        self.assertIn("effort", details["reasoning_modes"])
        self.assertTrue({"low", "medium", "high"} <= set(details["reasoning_efforts"]))
        self.assertNotIn("none", details["reasoning_efforts"])
        self.assertNotIn("xhigh", details["reasoning_efforts"])
        self.assertNotIn("max", details["reasoning_efforts"])
        self.assertIn("minimal", details["reasoning_efforts"])
        self.assertEqual(
            model_details("openai", "o3-mini")["reasoning_efforts"],
            ["low", "medium", "high"],
        )

    def test_catalog_does_not_patch_controls_collapsed_by_sdk(self):
        for model in ("deepseek-flash", "deepseek-v4-flash", "deepseek-reasoner"):
            details = model_details("deepseek", model)
            self.assertNotIn("effort", details["reasoning_modes"])
            self.assertNotIn("budget", details["reasoning_modes"])
            self.assertIn("enabled", details["reasoning_modes"])
            self.assertIn("disabled", details["reasoning_modes"])

    def test_custom_formats_are_separate_from_provider_catalog(self):
        protocols = {p["id"] for p in catalog_response()["protocols"]}
        self.assertEqual(protocols, {"openai", "anthropic", "gemini"})
        with self.assertRaisesRegex(ValueError, "API 格式"):
            prepare_settings(custom(provider_id="deepseek"))
        with self.assertRaisesRegex(ValueError, "API 地址"):
            prepare_settings(custom(api_base=""))

    def test_custom_declarations_are_isolated_between_requests(self):
        first, cap1 = request_options(custom())
        second, cap2 = request_options(
            custom().model_copy(update={"max_input_tokens": 64000})
        )
        self.assertEqual(cap2 - cap1, 32000)
        self.assertEqual(first["max_tokens"], second["max_tokens"])

    def test_credentials_stay_in_scope(self):
        value = custom().model_copy(
            update={
                "api_key": SecretStr("test-secret"),
                "api_base": "https://model.test/v1",
            }
        )
        save_settings(value)
        empty = value.model_copy(update={"api_key": SecretStr("")})
        self.assertEqual(
            prepare_settings(empty, credentials=True).api_key.get_secret_value(),
            "test-secret",
        )
        for updates in [
            {"provider_id": "anthropic"},
            {"api_base": "https://other.test/v1"},
        ]:
            with self.assertRaisesRegex(ValueError, "API Key"):
                prepare_settings(empty.model_copy(update=updates), credentials=True)
        if os.name != "nt":
            self.assertEqual(
                Path(self.tmp.name, "ai-settings.json").stat().st_mode & 0o777, 0o600
            )

    def test_missing_key_cannot_fall_back_to_environment(self):
        value = custom(api_key=SecretStr(""))
        with patch.dict(
            os.environ,
            {
                "TRACESURFACE_AI_KEY": "environment-secret",
                "OPENAI_API_KEY": "environment-secret",
            },
        ):
            with self.assertRaisesRegex(ValueError, "密钥"):
                request_options(value)
            with self.assertRaisesRegex(ValueError, "API Key"):
                prepare_settings(value, credentials=True)

    def test_invalid_or_old_settings_are_not_migrated(self):
        from tracesurface.server.app import create_app

        path = Path(self.tmp.name, "ai-settings.json")
        for original in [
            '{"api_key":"old-secret"}',
            '{"format_version":1,"api_key":"old-secret"}',
            '{"format_version":2,"auth_mode":"none"}',
            "broken",
        ]:
            path.write_text(original)
            with TestClient(create_app()) as client:
                response = client.get("/api/ai/settings")
            self.assertEqual(response.status_code, 409)
            self.assertIn("重新配置", response.json()["detail"])
            self.assertNotIn("old-secret", response.text)
            self.assertEqual(path.read_text(), original)

    def test_connection_probe_does_not_save_or_expose_errors(self):
        from tracesurface.server.app import create_app

        body = custom().model_dump(mode="json")
        body["api_key"] = "test-key"
        with TestClient(create_app()) as client:
            with patch(
                "tracesurface.ai.inference.test_connection",
                new=AsyncMock(side_effect=RuntimeError("secret response")),
            ):
                response = client.post("/api/ai/test", json=body)
            self.assertFalse(response.json()["ok"])
            self.assertNotIn("secret response", response.text)
            self.assertFalse(Path(self.tmp.name, "ai-settings.json").exists())
            self.assertEqual(
                client.post(
                    "/api/ai/test",
                    json=body,
                    headers={"Origin": "https://foreign.example"},
                ).status_code,
                403,
            )

    def test_api_key_is_required_and_legacy_auth_modes_are_rejected(self):
        from tracesurface.server.app import create_app

        body = custom(api_key=SecretStr("")).model_dump(mode="json")
        body["api_key"] = ""
        with TestClient(create_app()) as client:
            self.assertFalse(client.get("/api/ai/settings").json()["ready"])
            for path, method in [
                ("/api/ai/settings", client.put),
                ("/api/ai/test", client.post),
            ]:
                response = method(path, json=body)
                self.assertEqual(response.status_code, 422)
                self.assertIn("API Key", response.text)
            body["api_key"] = "test-key"
            self.assertEqual(client.put("/api/ai/settings", json=body).status_code, 200)
            for field, value in [
                ("auth_mode", "none"),
                ("connection_params", {"aws_session_token": "secret"}),
            ]:
                response = client.put("/api/ai/settings", json={**body, field: value})
                self.assertEqual(response.status_code, 422)
                self.assertNotIn("secret", response.text)
            self.assertNotIn("auth_mode", client.get("/api/ai/settings").json())
            self.assertIn(
                "没有选中站点",
                client.post("/api/ai/batches", json={"scan_ids": []}).text,
            )

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


class InferenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_input_budget_is_enforced_before_request(self):
        llm = SimpleNamespace(
            token_counter=Mock(return_value=50000), acompletion=AsyncMock()
        )
        with (
            patch("tracesurface.ai.inference.get_litellm", return_value=llm),
            self.assertRaisesRegex(ValueError, "容量"),
        ):
            await analyze_configured({"records": []}, custom())
        llm.acompletion.assert_not_called()

    async def test_connection_test_checks_coverage_and_result_schema(self):
        llm = SimpleNamespace(
            token_counter=Mock(return_value=100),
            acompletion=AsyncMock(
                return_value=SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            finish_reason="stop",
                            message=SimpleNamespace(content='{"results":[]}'),
                        )
                    ],
                    usage=None,
                )
            ),
        )
        with (
            patch("tracesurface.ai.inference.get_litellm", return_value=llm),
            self.assertRaisesRegex(ValueError, "遗漏"),
        ):
            await test_connection(custom())
        self.assertEqual(llm.acompletion.await_count, 1)
        self.assertNotIn("tools", llm.acompletion.call_args.kwargs)

    async def test_actual_sdk_custom_and_native_wire_payloads(self):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                received.append((self.path, data))
                result = '{"results":[{"replay_id":1,"suspected":false,"reason":"requires login","recommendation":""}]}'
                if ":generateContent" in self.path:
                    body = {
                        "candidates": [
                            {
                                "content": {
                                    "role": "model",
                                    "parts": [{"text": result}],
                                },
                                "finishReason": "STOP",
                                "index": 0,
                            }
                        ],
                        "usageMetadata": {
                            "promptTokenCount": 10,
                            "candidatesTokenCount": 20,
                            "totalTokenCount": 30,
                        },
                    }
                elif self.path.endswith("/messages"):
                    body = {
                        "id": "test",
                        "type": "message",
                        "role": "assistant",
                        "model": data["model"],
                        "content": [{"type": "text", "text": result}],
                        "stop_reason": "end_turn",
                        "usage": {"input_tokens": 10, "output_tokens": 20},
                    }
                else:
                    body = {
                        "id": "test",
                        "object": "chat.completion",
                        "created": 1,
                        "model": data["model"],
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": result},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 20,
                            "total_tokens": 30,
                        },
                    }
                raw = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            config = custom(
                api_base=base + "/v1",
                reasoning_mode="effort",
                reasoning_effort="high",
                json_mode=True,
            )
            self.assertTrue((await test_connection(config))["ok"])
            self.assertEqual(received[-1][1]["model"], "org/private-model")
            self.assertEqual(received[-1][1]["reasoning_effort"], "high")
            self.assertEqual(
                received[-1][1]["response_format"], {"type": "json_object"}
            )
            # Unknown OpenAI-compatible models retain explicitly declared thinking.
            budget = config.model_copy(
                update={"reasoning_mode": "budget", "thinking_budget": 2048}
            )
            self.assertTrue((await test_connection(budget))["ok"])
            self.assertEqual(
                received[-1][1]["thinking"], {"type": "enabled", "budget_tokens": 2048}
            )
            native = AISettings(api_base=base + "/v1", api_key=SecretStr("placeholder"))
            self.assertTrue((await test_connection(native))["ok"])
            self.assertEqual(received[-1][1]["model"], "deepseek-flash")
            # User-declared extensions use the selected API format, without a
            # special case for this model or changes to its native SDK adapter.
            for effort in ("low", "high", "max"):
                configured = custom(
                    model="deepseek-flash",
                    api_base=base + "/v1",
                    reasoning_mode="effort",
                    reasoning_effort=effort,
                )
                self.assertTrue((await test_connection(configured))["ok"])
                self.assertEqual(received[-1][1]["reasoning_effort"], effort)
            # A custom endpoint also supports an existing non-OpenAI adapter.
            anthropic = custom(
                api_base=base, reasoning_mode="budget", thinking_budget=2048
            ).model_copy(
                update={"provider_id": "anthropic", "model": "claude-3-7-sonnet-latest"}
            )
            self.assertTrue((await test_connection(anthropic))["ok"])
            self.assertEqual(received[-1][0], "/v1/messages")
            self.assertEqual(
                received[-1][1]["thinking"], {"type": "enabled", "budget_tokens": 2048}
            )
            gemini = custom(
                provider_id="gemini",
                model="gemini-2.5-flash",
                api_base=base,
                reasoning_mode="budget",
                thinking_budget=2048,
            )
            self.assertTrue((await test_connection(gemini))["ok"])
            self.assertIn(":generateContent", received[-1][0])
            self.assertEqual(
                received[-1][1]["generationConfig"]["thinkingConfig"]["thinkingBudget"],
                2048,
            )
            self.assertTrue(all("tools" not in body for _, body in received))
        finally:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            thread.join()

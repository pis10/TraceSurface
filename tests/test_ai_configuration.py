from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from tracesurface.ai.catalog import (
    EFFORTS,
    NATIVE_EFFORTS,
    catalog_response,
    model_details,
)
from tracesurface.ai.inference import analyze_configured, create_model, test_connection
from tracesurface.ai.models import AISettings
from tracesurface.ai.service import safe_error
from tracesurface.ai.settings import (
    load_settings,
    prepare_settings,
    public_settings,
    save_settings,
)
from tracesurface.storage.sqlite.connection import init


def custom(**updates):
    return AISettings(
        **{
            "mode": "custom",
            "provider_id": "openai-chat",
            "model": "org/private-model",
            "api_base": "https://models.test/v1",
            "api_key": "test-key",
            "context_window": 32000,
            "max_output_tokens": 8192,
            **updates,
        }
    )


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"TRACESURFACE_HOME": self.tmp.name})
        self.env.start()
        init()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_catalog_uses_installed_sdk_models(self):
        from pydantic_ai.models import known_model_names

        catalog = catalog_response()
        providers = {p["id"]: p for p in catalog["providers"]}
        self.assertTrue(
            {
                "deepseek",
                "moonshotai",
                "zai",
                "alibaba",
                "openai",
                "anthropic",
                "google",
            }
            <= providers.keys()
        )
        self.assertTrue(
            {"ollama", "bedrock", "azure", "github-copilot"}.isdisjoint(providers)
        )
        known = known_model_names()
        for provider, entry in providers.items():
            self.assertEqual(
                {m["value"] for m in entry["models"]},
                {m.split(":", 1)[1] for m in known if m.startswith(provider + ":")},
            )
        self.assertEqual(
            {p["id"] for p in catalog["protocols"]},
            {"openai-chat", "openai-responses", "anthropic", "google"},
        )
        self.assertEqual(catalog["efforts"], EFFORTS)

    def test_native_capacity_is_not_required_or_sent(self):
        from tracesurface.ai.inference import model_settings

        for value in [
            AISettings(),
            AISettings(provider_id="alibaba", model="private-model"),
        ]:
            value = prepare_settings(value)
            self.assertIsNone(value.context_window)
            self.assertIsNone(value.max_output_tokens)
            self.assertNotIn("max_tokens", model_settings(value))

    def test_custom_capabilities_do_not_inherit_known_model_profile(self):
        for provider, model in [
            ("openai-chat", "gpt-5"),
            ("openai-responses", "gpt-5"),
            ("anthropic", "claude-opus-4-7"),
            ("google", "gemini-3-pro-preview"),
        ]:
            with self.subTest(provider=provider):
                value = custom(provider_id=provider, model=model)
                configured = create_model(value)
                self.assertEqual(configured.profile["context_window"], 32000)
                self.assertFalse(configured.profile["supports_json_schema_output"])
                self.assertFalse(configured.profile["thinking_always_enabled"])
                self.assertEqual(prepare_settings(value).reasoning_efforts, [])

    def test_effort_declarations_default_and_roundtrip(self):
        value = custom(
            reasoning_efforts=["low", "high", "xhigh"],
            reasoning_effort="high",
        )
        save_settings(prepare_settings(value))
        restored = load_settings()
        self.assertEqual(restored.reasoning_efforts, ["low", "high", "xhigh"])
        self.assertEqual(restored.reasoning_effort, "high")
        for changes in [
            {"reasoning_efforts": []},
            {"reasoning_efforts": ["low"]},
            {"reasoning_effort": "max"},
        ]:
            with self.assertRaisesRegex(ValueError, "支持"):
                prepare_settings(value.model_copy(update=changes))
        with self.assertRaises(ValidationError):
            custom(reasoning_efforts=["arbitrary"])
        with self.assertRaisesRegex(ValueError, "重复"):
            prepare_settings(custom(reasoning_efforts=["low", "low"]))

    def test_budget_capacity_and_protocol_validation(self):
        for changes in [
            {"context_window": None},
            {"max_output_tokens": 32000},
            {"provider_id": "deepseek"},
            {"api_base": ""},
        ]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                prepare_settings(custom(**changes))

    def test_only_effort_is_configurable(self):
        for field, value in [
            ("reasoning_mode", "effort"),
            ("thinking_budget", 2048),
            ("output_mode", "native"),
        ]:
            with self.assertRaises(ValidationError):
                custom(**{field: value})
        self.assertEqual(prepare_settings(custom()).reasoning_effort, "")

    def test_credentials_are_local_scoped_and_never_returned(self):
        value = custom()
        save_settings(value)
        empty = value.model_copy(update={"api_key": AISettings().api_key})
        self.assertEqual(
            prepare_settings(empty, credentials=True).api_key.get_secret_value(),
            "test-key",
        )
        for changes in [
            {"provider_id": "openai-responses"},
            {"api_base": "https://other.test"},
            {"mode": "catalog", "provider_id": "openai"},
        ]:
            with self.assertRaisesRegex(ValueError, "API Key"):
                prepare_settings(empty.model_copy(update=changes), credentials=True)
        self.assertNotIn("test-key", json.dumps(public_settings(load_settings())))
        if os.name != "nt":
            self.assertEqual(
                Path(self.tmp.name, "ai-settings.json").stat().st_mode & 0o777, 0o600
            )

    def test_old_settings_are_rejected_without_migration(self):
        from tracesurface.server.app import create_app

        path = Path(self.tmp.name, "ai-settings.json")
        for raw in ['{"format_version":4,"api_key":"old-secret"}', "broken"]:
            path.write_text(raw)
            with TestClient(create_app()) as client:
                response = client.get("/api/ai/settings")
            self.assertEqual(response.status_code, 409)
            self.assertNotIn("old-secret", response.text)
            self.assertEqual(path.read_text(), raw)

    def test_api_validation_key_and_origin(self):
        from tracesurface.server.app import create_app

        body = custom().model_dump(mode="json")
        body["api_key"] = ""
        with TestClient(create_app()) as client:
            self.assertEqual(client.put("/api/ai/settings", json=body).status_code, 422)
            body["api_key"] = "test-key"
            response = client.put("/api/ai/settings", json=body)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn("test-key", response.text)
            self.assertEqual(
                client.post(
                    "/api/ai/test", json=body, headers={"Origin": "https://evil.test"}
                ).status_code,
                403,
            )
            body.update(
                reasoning_efforts=["low"],
                reasoning_effort="high",
            )
            response = client.put("/api/ai/settings", json=body)
            self.assertEqual(response.status_code, 422)
            self.assertNotIn("test-key", response.text)


class WireTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.received = []
        self.result = {
            "results": [
                {
                    "replay_id": 1,
                    "suspected": False,
                    "reason": "login required",
                    "recommendation": "",
                }
            ]
        }
        self.status = 200
        self.finish = "stop"
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.received.append((self.path, body))
                result = json.dumps(owner.result)
                path = self.path.split("?")[0]
                if owner.status != 200:
                    response = {
                        "error": {
                            "message": "secret service response",
                            "type": "server_error",
                        }
                    }
                elif path.endswith("/responses"):
                    response = dict(
                        id="resp_local",
                        object="response",
                        created_at=1,
                        status="completed",
                        model=body["model"],
                        output=[
                            dict(
                                type="message",
                                id="msg_local",
                                status="completed",
                                role="assistant",
                                content=[
                                    dict(
                                        type="output_text", text=result, annotations=[]
                                    )
                                ],
                            )
                        ],
                    )
                elif path.endswith("/messages"):
                    response = dict(
                        id="msg_local",
                        type="message",
                        role="assistant",
                        model=body["model"],
                        content=[dict(type="text", text=result)],
                        stop_reason="end_turn",
                        usage=dict(input_tokens=12, output_tokens=10),
                    )
                elif ":generateContent" in path:
                    response = dict(
                        candidates=[
                            dict(
                                content=dict(role="model", parts=[dict(text=result)]),
                                finishReason="STOP",
                                index=0,
                            )
                        ],
                        usageMetadata=dict(
                            promptTokenCount=12,
                            candidatesTokenCount=10,
                            totalTokenCount=22,
                        ),
                    )
                else:
                    response = dict(
                        id="chat_local",
                        object="chat.completion",
                        created=1,
                        model=body["model"],
                        choices=[
                            dict(
                                index=0,
                                message=dict(role="assistant", content=result),
                                finish_reason=owner.finish,
                            )
                        ],
                        usage=dict(
                            prompt_tokens=12, completion_tokens=10, total_tokens=22
                        ),
                    )
                content_type = "application/json"
                if (
                    owner.status == 200
                    and path.endswith("/messages")
                    and body.get("stream")
                ):
                    message = {**response, "content": [], "stop_reason": None}
                    events = [
                        dict(type="message_start", message=message),
                        dict(
                            type="content_block_start",
                            index=0,
                            content_block=dict(type="text", text=""),
                        ),
                        dict(
                            type="content_block_delta",
                            index=0,
                            delta=dict(type="text_delta", text=result),
                        ),
                        dict(type="content_block_stop", index=0),
                        dict(
                            type="message_delta",
                            delta=dict(stop_reason="end_turn", stop_sequence=None),
                            usage=dict(output_tokens=10),
                        ),
                        dict(type="message_stop"),
                    ]
                    data = "".join(
                        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
                        for event in events
                    ).encode()
                    content_type = "text/event-stream"
                else:
                    data = json.dumps(response).encode()
                self.send_response(owner.status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.counter = patch(
            "tracesurface.ai.inference.estimate_input_tokens", return_value=100
        )
        self.counter.start()

    async def asyncTearDown(self):
        import asyncio

        self.counter.stop()
        await asyncio.to_thread(self.server.shutdown)
        self.server.server_close()
        self.thread.join(timeout=2)

    def config(self, **updates):
        return custom(api_base=self.base + "/v1", **updates)

    async def test_four_formats_one_request_no_tools(self):
        for protocol, path in [
            ("openai-chat", "/chat/completions"),
            ("openai-responses", "/responses"),
            ("anthropic", "/messages"),
            ("google", ":generateContent"),
        ]:
            with self.subTest(protocol=protocol):
                before = len(self.received)
                result, _ = await analyze_configured(
                    {"records": [{"replay_id": 1}]},
                    self.config(provider_id=protocol),
                )
                self.assertFalse(result.results[0].suspected)
                self.assertEqual(len(self.received), before + 1)
                url, body = self.received[-1]
                self.assertIn(path, url)
                self.assertFalse(body.get("tools"))
                if protocol.startswith("openai"):
                    self.assertEqual(body["model"], "org/private-model")
                if protocol == "openai-responses":
                    self.assertFalse(body["store"])

    async def test_custom_efforts_are_not_translated_or_guessed(self):
        for effort in EFFORTS:
            for protocol in ["openai-chat", "openai-responses"]:
                await test_connection(
                    self.config(
                        provider_id=protocol,
                        model="gpt-5",
                        reasoning_efforts=EFFORTS,
                        reasoning_effort=effort,
                    )
                )
                body = self.received[-1][1]
                actual = (
                    body.get("reasoning_effort")
                    if protocol == "openai-chat"
                    else body["reasoning"]["effort"]
                )
                self.assertEqual(actual, effort)

    async def test_custom_anthropic_and_google_effort(self):
        for protocol in ["anthropic", "google"]:
            for effort in ["low", "high"]:
                await test_connection(
                    self.config(
                        provider_id=protocol,
                        reasoning_efforts=["low", "high"],
                        reasoning_effort=effort,
                    )
                )
                body = self.received[-1][1]
                if protocol == "anthropic":
                    self.assertEqual(body["output_config"]["effort"], effort)
                    self.assertEqual(body["thinking"], {"type": "adaptive"})
                else:
                    thinking = body["generationConfig"]["thinkingConfig"]
                    self.assertEqual(
                        thinking.get("thinkingLevel", thinking.get("thinking_level")),
                        effort.upper(),
                    )

    async def test_default_omits_all_thinking_parameters(self):
        for protocol in ["openai-chat", "openai-responses", "anthropic", "google"]:
            await test_connection(self.config(provider_id=protocol))
            body = self.received[-1][1]
            for key in ["reasoning_effort", "thinking", "reasoning"]:
                self.assertNotIn(key, body)
            self.assertNotIn("thinkingConfig", body.get("generationConfig", {}))

    async def test_invalid_output_and_id_coverage_do_not_retry(self):
        for result in [
            {"results": "wrong"},
            {"results": []},
            {
                "results": [
                    {
                        "replay_id": 1,
                        "suspected": "false",
                        "reason": "x",
                        "recommendation": "",
                    }
                ]
            },
        ]:
            self.result = result
            before = len(self.received)
            with self.assertRaises(Exception):
                await test_connection(self.config())
            self.assertEqual(len(self.received), before + 1)

    async def test_truncated_response_is_not_success(self):
        self.finish = "length"
        with self.assertRaises(Exception):
            await test_connection(self.config())
        self.assertEqual(len(self.received), 1)

    async def test_http_failure_is_not_retried_or_exposed(self):
        self.status = 500
        for protocol in ["openai-chat", "openai-responses", "anthropic", "google"]:
            before = len(self.received)
            with self.assertRaises(Exception) as caught:
                await test_connection(self.config(provider_id=protocol))
            self.assertEqual(len(self.received), before + 1)
            self.assertNotIn("secret", safe_error(caught.exception))

    async def test_empty_and_oversized_sites_do_not_send_requests(self):
        result, _ = await analyze_configured({"records": []}, self.config())
        self.assertEqual(result.results, [])
        with patch(
            "tracesurface.ai.inference.estimate_input_tokens", return_value=50000
        ):
            with self.assertRaisesRegex(ValueError, "容量"):
                await test_connection(self.config())
        self.assertEqual(self.received, [])

    async def test_native_deepseek_glm_kimi_effort_reaches_wire(self):
        for provider, model in [
            ("deepseek", "deepseek-v4-flash"),
            ("zai", "glm-5.2"),
            ("moonshotai", "kimi-k2.5"),
        ]:
            self.assertEqual(
                model_details(provider, model)["reasoning_efforts"], NATIVE_EFFORTS
            )
            for effort in ["low", "high", "xhigh"]:
                with self.subTest(provider=provider, effort=effort):
                    await test_connection(
                        self.config(
                            mode="catalog",
                            provider_id=provider,
                            model=model,
                            reasoning_effort=effort,
                        )
                    )
                    body = self.received[-1][1]
                    self.assertEqual(body["reasoning_effort"], effort)
                    self.assertNotIn("max_tokens", body)
                    self.assertFalse(body.get("tools"))

    async def test_native_glm_mapping_is_owned_by_sdk(self):
        await test_connection(
            self.config(
                mode="catalog",
                provider_id="zai",
                model="glm-5.3",
                reasoning_effort="xhigh",
            )
        )
        self.assertEqual(self.received[-1][1]["reasoning_effort"], "max")
        await test_connection(
            self.config(
                mode="catalog",
                provider_id="zai",
                model="glm-4.7",
                reasoning_effort="high",
            )
        )
        self.assertNotIn("reasoning_effort", self.received[-1][1])
        self.assertEqual(self.received[-1][1]["thinking"]["type"], "enabled")

    async def test_native_output_format_uses_profile_automatically(self):
        for provider, model in [
            ("openai", "gpt-4o"),
            ("anthropic", "claude-sonnet-4-6"),
            ("google", "gemini-2.5-flash"),
        ]:
            await test_connection(
                self.config(mode="catalog", provider_id=provider, model=model)
            )
            body = self.received[-1][1]
            self.assertFalse(body.get("tools"))
            if provider == "openai":
                self.assertEqual(body["text"]["format"]["type"], "json_schema")
            elif provider == "anthropic":
                self.assertEqual(body["output_config"]["format"]["type"], "json_schema")
            else:
                self.assertIn("responseJsonSchema", body["generationConfig"])


if __name__ == "__main__":
    unittest.main()

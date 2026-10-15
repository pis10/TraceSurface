"""One site, one request, a validated result; shared by analysis and connection tests."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict
from typing import Any, cast

from pydantic_ai import Agent, NativeOutput, PromptedOutput, TextOutput
from pydantic_ai.models import Model, infer_model
from pydantic_ai.profiles import DEFAULT_PROFILE, ModelProfile
from pydantic_ai.providers import infer_provider_class
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from tracesurface.ai.models import AISettings, SiteResult, validate_result
from tracesurface.ai.prompt import SYSTEM_PROMPT
from tracesurface.ai.settings import prepare_settings


def model_settings(settings: AISettings) -> ModelSettings:
    options: dict[str, Any] = {"timeout": 300, "openai_store": False}
    if settings.mode == "custom":
        options["max_tokens"] = settings.max_output_tokens
    effort = settings.reasoning_effort
    if effort:
        if settings.mode == "catalog":
            # Native adapters own all model-specific translation and defaults.
            options["thinking"] = effort
        elif settings.provider_id in {"openai-chat", "openai-responses"}:
            options["openai_reasoning_effort"] = effort
        elif settings.provider_id == "anthropic":
            options["anthropic_effort"] = effort
            options["anthropic_thinking"] = {"type": "adaptive"}
        elif settings.provider_id == "google":
            options["google_thinking_config"] = {"thinking_level": effort.upper()}
    return cast(ModelSettings, options)


def create_model(settings: AISettings) -> Model:
    key = settings.api_key.get_secret_value().strip()
    if not key:
        raise ValueError("AI 密钥不可用，请重新配置")
    provider_id = settings.provider_id
    cls = infer_provider_class(provider_id)
    kwargs: dict[str, Any] = {"api_key": key}
    if provider_id in {"anthropic", "google"} and settings.api_base:
        kwargs["base_url"] = settings.api_base
    if provider_id == "google":
        from google.genai.types import HttpRetryOptions

        kwargs["retry_options"] = HttpRetryOptions(attempts=1)
    provider = cls(**kwargs)
    if provider_id != "google":
        provider.client.max_retries = 0
        if settings.api_base:
            provider.client.base_url = settings.api_base
    model = infer_model(
        f"{provider_id}:{settings.model}", provider_factory=lambda _: provider
    )
    if settings.mode == "custom":
        # Replace, rather than merge with, name-based provider capabilities.
        profile = cast(
            ModelProfile,
            {
                **DEFAULT_PROFILE,
                "context_window": settings.context_window,
                "supports_json_schema_output": False,
                "supports_json_object_output": False,
            },
        )
        model = cast(Any, type(model))(
            settings.model, provider=provider, profile=lambda _: profile
        )
    return model


def estimate_input_tokens(payload: str) -> int:
    import tiktoken

    # This is a local estimate, not a provider-specific token count.
    return len(tiktoken.get_encoding("cl100k_base").encode_ordinary(payload)) + 1024


def parse_text_result(text: str) -> SiteResult:
    return SiteResult.model_validate_json(text)


async def analyze_configured(
    payload: dict[str, Any], settings: AISettings
) -> tuple[SiteResult, dict[str, Any]]:
    settings = prepare_settings(settings)
    if not payload["records"]:
        return SiteResult(results=[]), {}
    prompt = json.dumps(payload, ensure_ascii=False)
    schema = json.dumps(SiteResult.model_json_schema(), ensure_ascii=False)
    if settings.mode == "custom":
        tokens = await asyncio.to_thread(
            estimate_input_tokens, SYSTEM_PROMPT + schema + prompt
        )
        input_limit = (settings.context_window or 0) - (settings.max_output_tokens or 0)
        if tokens > input_limit:
            raise ValueError(
                f"本站输入约 {tokens} tokens，超过当前模型可用容量 {input_limit}；请选择上下文更大的模型"
            )
    model = create_model(settings)
    instructions = SYSTEM_PROMPT
    if model.profile.get("supports_json_schema_output"):
        output = NativeOutput(SiteResult)
    elif model.profile.get("supports_json_object_output"):
        output = PromptedOutput(SiteResult)
    else:
        output = TextOutput(parse_text_result)
        instructions += "\nJSON Schema: " + schema
    agent = Agent(
        model,
        instructions=instructions,
        output_type=output,
        model_settings=model_settings(settings),
        retries=0,
    )
    async with agent:
        run = await agent.run(prompt, usage_limits=UsageLimits(request_limit=1))
    if run.response.finish_reason not in {None, "stop"}:
        raise ValueError("AI 未完整返回结果，请重试或更换模型")
    result = validate_result(
        run.output.model_dump_json(), {r["replay_id"] for r in payload["records"]}
    )
    return result, asdict(run.usage)


async def test_connection(settings: AISettings) -> dict[str, Any]:
    payload = {
        "site": {"target_url": "https://connection-test.invalid"},
        "scan_description": "连接测试：这是虚构响应，只验证模型连接与输出格式。",
        "records": [
            {
                "replay_id": 1,
                "request": {
                    "method": "GET",
                    "url": "https://connection-test.invalid/test",
                },
                "response": {"status": 200, "body": '{"code":401,"message":"请登录"}'},
            }
        ],
    }
    started = time.monotonic()
    await asyncio.wait_for(analyze_configured(payload, settings), timeout=60)
    return {
        "ok": True,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
        "message": "连接正常，结果格式校验通过",
    }

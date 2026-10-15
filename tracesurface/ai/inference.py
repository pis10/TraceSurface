"""Site analysis with bounded format correction, shared by connection tests."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict
from typing import Any, cast

from pydantic_ai import Agent, NativeOutput, PromptedOutput, capture_run_messages
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models import Model, infer_model
from pydantic_ai.profiles import DEFAULT_PROFILE, ModelProfile
from pydantic_ai.providers import infer_provider_class
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RunUsage, UsageLimits

from tracesurface.ai.models import AISettings, SiteResult
from tracesurface.ai.output import AIOutputError, parse_text_result, validate_coverage
from tracesurface.ai.prompt import SYSTEM_PROMPT
from tracesurface.ai.settings import prepare_settings

MAX_OUTPUT_ATTEMPTS = 4


def check_completion(response: ModelResponse) -> None:
    if response.finish_reason == "length":
        raise ValueError(
            "AI 输出达到 token 上限而被截断，请增大最大输出或降低思考强度后重试"
        )
    if response.finish_reason == "content_filter":
        raise ValueError("AI 服务拒绝返回分析结果，请检查模型服务的内容限制")
    if response.finish_reason not in {None, "stop"}:
        raise ValueError("AI 未完整返回结果，请重试或更换模型")


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


async def analyze_configured(
    payload: dict[str, Any], settings: AISettings
) -> tuple[SiteResult, dict[str, Any]]:
    settings = prepare_settings(settings)
    if not payload["records"]:
        return SiteResult(results=[]), {}
    prompt = json.dumps(payload, ensure_ascii=False)
    schema = json.dumps(SiteResult.model_json_schema(), ensure_ascii=False)
    model = create_model(settings)
    instructions = SYSTEM_PROMPT
    if model.profile.get("supports_json_schema_output"):
        output = NativeOutput(SiteResult)
    elif model.profile.get("supports_json_object_output"):
        output = PromptedOutput(SiteResult)
    else:
        # Parse after checking finish_reason so a truncated JSON document is not
        # misreported as a schema error (or needlessly retried at the same budget).
        output = str
        instructions += "\nJSON Schema: " + schema
    agent: Agent[None, Any] = Agent(
        model,
        instructions=instructions,
        output_type=output,
        model_settings=model_settings(settings),
        retries=0,
    )
    usage = RunUsage()
    expected_ids = {r["replay_id"] for r in payload["records"]}
    correction = ""
    async with agent:
        for attempt in range(MAX_OUTPUT_ATTEMPTS):
            request = prompt + correction
            if settings.mode == "custom":
                tokens = await asyncio.to_thread(
                    estimate_input_tokens, instructions + request
                )
                input_limit = (settings.context_window or 0) - (
                    settings.max_output_tokens or 0
                )
                if tokens > input_limit:
                    raise ValueError(
                        f"本站输入约 {tokens} tokens，超过当前模型可用容量 {input_limit}；请选择上下文更大的模型"
                    )
            try:
                with capture_run_messages() as messages:
                    try:
                        run = await agent.run(
                            request,
                            usage=usage,
                            usage_limits=UsageLimits(request_limit=MAX_OUTPUT_ATTEMPTS),
                        )
                    except UnexpectedModelBehavior:
                        # Native schema parsing can fail before agent.run returns.
                        response = next(
                            (
                                m
                                for m in reversed(messages)
                                if isinstance(m, ModelResponse)
                            ),
                            None,
                        )
                        if response is not None:
                            check_completion(response)
                        raise AIOutputError(
                            "AI 未返回符合约定的 JSON 结果，请严格遵循 JSON Schema"
                        ) from None
                check_completion(run.response)
                result = (
                    parse_text_result(run.output)
                    if isinstance(run.output, str)
                    else run.output
                )
                validate_coverage(result, expected_ids)
                return result, asdict(usage)
            except AIOutputError as exc:
                if attempt == MAX_OUTPUT_ATTEMPTS - 1:
                    raise AIOutputError(
                        f"{exc}；自动纠错 {MAX_OUTPUT_ATTEMPTS - 1} 次后仍失败，未保存为分析成功"
                    ) from None
                # Start fresh to bound context growth and avoid feeding malformed
                # output back as instructions. Only our sanitized diagnostic is added.
                correction = (
                    f"\n\n上次输出校验失败：{exc}。请重新分析上面的全部 records，返回完整结果。"
                    "只输出 JSON 对象；不要 Markdown、思考过程或说明文字。"
                )
    raise AssertionError("unreachable")


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

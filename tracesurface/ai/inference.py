"""The same LiteLLM request builder powers connection tests and site reviews."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from tracesurface.ai.models import AISettings, SiteResult, validate_result
from tracesurface.ai.prompt import SYSTEM_PROMPT
from tracesurface.ai.runtime import get_litellm
from tracesurface.ai.settings import prepare_settings


def request_options(
    settings: AISettings, record_count: int = 1
) -> tuple[dict[str, Any], int]:
    settings = prepare_settings(settings)
    # Custom capacities are user declarations; never infer from the model name.
    context = int(settings.max_input_tokens or 0)
    output_limit = int(settings.max_output_tokens or 0)
    reserve = settings.thinking_budget or (
        8192 if settings.reasoning_mode != "default" else 0
    )
    output = min(output_limit, context - 512, max(4096, record_count * 256 + reserve))
    if (
        settings.reasoning_mode == "budget"
        and (settings.thinking_budget or 0) >= output
    ):
        raise ValueError("AI 思考预算占满输出空间，请增大容量或减少思考预算")
    options: dict[str, Any] = {
        "model": settings.model,
        "custom_llm_provider": settings.provider_id,
        "max_tokens": output,
        "timeout": 300,
        "num_retries": 0,
        "stream": False,
        "drop_params": False,
    }
    if not settings.api_key.get_secret_value().strip():
        raise ValueError("AI 密钥不可用，请重新配置")
    options["api_key"] = settings.api_key.get_secret_value()
    if settings.api_base:
        options["api_base"] = settings.api_base
    mode = settings.reasoning_mode
    if mode == "effort":
        options["reasoning_effort"] = settings.reasoning_effort
    elif mode == "budget":
        options["thinking"] = {
            "type": "enabled",
            "budget_tokens": settings.thinking_budget,
        }
    elif mode != "default":
        options["thinking"] = {"type": mode}
    if settings.mode == "custom":
        from litellm.llms.openai.chat.gpt_transformation import OpenAIGPTConfig

        from tracesurface.ai.catalog import chat_config

        if isinstance(
            chat_config(settings.provider_id, settings.model), OpenAIGPTConfig
        ):
            # User-declared extensions on an OpenAI-compatible wire must survive
            # even when the SDK has no capability entry for this model name.
            extra = {
                key: options.pop(key)
                for key in ("thinking", "reasoning_effort")
                if key in options
            }
            if extra:
                options["extra_body"] = extra
    if settings.json_mode:
        options["response_format"] = {"type": "json_object"}
    return options, max(0, context - output)


async def analyze_configured(
    payload: dict[str, Any], settings: AISettings
) -> tuple[SiteResult, dict[str, Any]]:
    llm = await asyncio.to_thread(get_litellm)
    options, input_limit = await asyncio.to_thread(
        request_options, settings, len(payload["records"])
    )
    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT
            + "\nJSON Schema: "
            + json.dumps(SiteResult.model_json_schema(), ensure_ascii=False),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    tokens = await asyncio.to_thread(
        llm.token_counter,
        model=f"{settings.provider_id}/{settings.model}",
        messages=messages,
    )
    if tokens > input_limit:
        raise ValueError(
            f"本站输入约 {tokens} tokens，超过当前模型可用容量 {input_limit}；请选择上下文更大的模型"
        )
    response = await llm.acompletion(messages=messages, **options)
    choice = response.choices[0]
    if choice.finish_reason != "stop" or not choice.message.content:
        raise ValueError("AI 未完整返回结果，请重试或更换模型")
    result = validate_result(
        choice.message.content, {r["replay_id"] for r in payload["records"]}
    )
    usage = response.usage.model_dump() if response.usage else {}
    return result, usage


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

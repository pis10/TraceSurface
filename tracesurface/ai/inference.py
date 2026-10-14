"""The same LiteLLM request builder powers connection tests and site reviews."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from tracesurface.ai.catalog import find_model
from tracesurface.ai.models import AISettings, SiteResult, validate_result
from tracesurface.ai.prompt import SYSTEM_PROMPT
from tracesurface.ai.runtime import get_litellm


def request_options(
    settings: AISettings, record_count: int = 1
) -> tuple[dict[str, Any], int]:
    llm = get_litellm()
    item: dict[str, Any] = {}
    if settings.provider_id == "custom":
        route = f"openai/{settings.model}"
        base = settings.api_base
        native = route.split("/")[0]
        try:
            info = llm.get_model_info(route)
        except (ValueError, llm.ModelNotMappedError):
            info = {}
        context = info.get("max_input_tokens") or 128000
        output_limit = info.get("max_output_tokens") or 16384
        if settings.reasoning_effort != "auto":
            raise ValueError("自定义服务使用模型默认思考设置")
    else:
        provider, item = find_model(settings.provider_id, settings.model)
        if settings.reasoning_effort not in item["reasoning_options"]:
            raise ValueError("当前模型不支持所选思考设置")
        native = provider["native"]
        route, base = f"{native}/{settings.model}", provider["api_base"]
        context, output_limit = item["context"], item["max_output_tokens"]
    reserve = (
        8192
        if item.get("reasoning") and settings.reasoning_effort not in {"off", "none"}
        else 0
    )
    output = min(int(output_limit), 131072, max(4096, record_count * 256 + reserve))
    options: dict[str, Any] = {
        "model": route,
        "api_key": settings.api_key.get_secret_value(),
        "api_base": base or None,
        "max_tokens": output,
        "timeout": 300,
        "num_retries": 0,
        "stream": False,
    }
    effort = settings.reasoning_effort
    if effort in {"on", "off"}:
        thinking = {"type": "enabled" if effort == "on" else "disabled"}
        if native == "moonshot":
            options["extra_body"] = {"thinking": thinking}
        else:
            options["thinking"] = thinking
    elif effort != "auto":
        if native in {"deepseek", "zai"}:
            # The installed native adapters omit these strengths. Preserve the
            # official field through LiteLLM's documented extra_body mechanism.
            options["extra_body"] = {"reasoning_effort": effort}
            if native == "deepseek":
                options["thinking"] = {"type": "enabled"}
        else:
            options["reasoning_effort"] = effort
    if item.get("supports_json"):
        options["response_format"] = {"type": "json_object"}
    return options, max(0, int(context) - output)


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
        llm.token_counter, model=options["model"], messages=messages
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

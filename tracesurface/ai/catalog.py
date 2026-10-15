"""A view of the installed LiteLLM SDK, not a second provider/model registry."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib.metadata import version
from typing import Any, get_args

from tracesurface.ai.runtime import get_litellm

# Presentation only: labels/order never determine provider availability.
LABELS = {
    "deepseek": "DeepSeek",
    "zai": "智谱 GLM",
    "moonshot": "Kimi",
    "minimax": "MiniMax",
    "dashscope": "阿里百炼",
    "volcengine": "火山方舟",
    "xiaomi_mimo": "小米 MiMo",
    "tencent": "腾讯混元",
    "modelscope": "魔搭",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "gemini": "Google Gemini",
    "azure": "Azure",
    "bedrock": "Amazon Bedrock",
    "vertex_ai": "Vertex AI",
    "openrouter": "OpenRouter",
    "ollama": "Ollama",
    "openai_like": "OpenAI Compatible",
}
PREFERRED = list(LABELS)[:9]


@lru_cache(maxsize=1)
def connection_fields() -> list[str]:
    from litellm.types.router import CredentialLiteLLMParams

    return sorted(set(CredentialLiteLLMParams.model_fields) - {"api_key", "api_base"})


@lru_cache(maxsize=1)
def model_catalog() -> list[dict[str, Any]]:
    llm = get_litellm()
    from litellm.llms.openai_like.json_loader import JSONProviderRegistry

    adapters: set[str] = set(JSONProviderRegistry.list_providers())
    for provider in llm.provider_list:
        # This adapter requires user code, which this application never runs.
        if provider.value == "custom":
            continue
        try:
            config = llm.ProviderConfigManager.get_provider_chat_config(
                "model", provider
            )
        except (ValueError, KeyError, NotImplementedError):
            continue
        if config is not None:
            adapters.add(provider.value)

    models: dict[str, dict[str, dict[str, Any]]] = {p: {} for p in adapters}
    for route, info in llm.model_cost.items():
        if info.get("mode") != "chat":
            continue
        # Read static registry keys only. Route resolution may initiate OAuth.
        prefix, separator, suffix = route.partition("/")
        native = (
            prefix if separator and prefix in models else info.get("litellm_provider")
        )
        if native in models:
            name = suffix if separator and prefix == native else route
            models[native][name] = {"value": name, "label": name, "registry_key": route}
    result = [
        {
            "id": native,
            "label": LABELS.get(native, native),
            "models": sorted(items.values(), key=lambda m: m["value"].casefold()),
        }
        for native, items in models.items()
    ]
    return sorted(
        result,
        key=lambda p: (
            PREFERRED.index(p["id"]) if p["id"] in PREFERRED else len(PREFERRED),
            p["label"].casefold(),
        ),
    )


def find_provider(provider_id: str) -> dict[str, Any]:
    provider = next((p for p in model_catalog() if p["id"] == provider_id), None)
    if provider is None:
        raise ValueError("请选择当前 LiteLLM 支持的接入方式")
    return provider


def chat_config(provider_id: str, model: str):
    llm = get_litellm()
    from litellm.llms.openai_like.dynamic_config import create_config_class
    from litellm.llms.openai_like.json_loader import JSONProviderRegistry

    try:
        provider = llm.LlmProviders(provider_id)
    except ValueError:
        definition = JSONProviderRegistry.get(provider_id)
        if definition is None:
            raise ValueError("当前接入方式没有聊天模型适配器") from None
        return create_config_class(definition)()
    return llm.ProviderConfigManager.get_provider_chat_config(model, provider)


def reasoning_controls(
    provider_id: str, model: str, info: dict[str, Any], params: list[str]
) -> tuple[list[str], list[str]]:
    """Use SDK transformations to avoid advertising controls it silently collapses."""
    from openai.types.shared.reasoning_effort import ReasoningEffort

    modes, efforts = ["default"], []
    if not info.get("supports_reasoning"):
        return modes, efforts
    config = chat_config(provider_id, model)

    def mapped(values: dict[str, Any]) -> str:
        try:
            output = config.map_openai_params(values, {}, model, False)
            return json.dumps(output, sort_keys=True) if output else ""
        except Exception:  # noqa: BLE001 -- an unsupported optional control is omitted
            return ""

    if "reasoning_effort" in params:
        candidates = info.get("reasoning_effort_levels") or list(
            get_args(ReasoningEffort)
        )
        choices = {
            str(level): mapped({"reasoning_effort": level}) for level in candidates
        }
        # Some adapters translate every nonzero strength into the same on/off switch.
        strengths = {
            payload for level, payload in choices.items() if level != "none" and payload
        }
        if len(strengths) > 1:
            modes.append("effort")
            efforts = [level for level, payload in choices.items() if payload]
    if "thinking" in params:
        enabled = mapped({"thinking": {"type": "enabled"}})
        disabled = mapped({"thinking": {"type": "disabled"}})
        if enabled:
            modes.append("enabled")
        if disabled and disabled != enabled:
            modes.append("disabled")
        budgets = [
            mapped({"thinking": {"type": "enabled", "budget_tokens": n}})
            for n in (2048, 4096)
        ]
        if all(budgets) and budgets[0] != budgets[1]:
            modes.append("budget")
        if info.get("supports_adaptive_thinking") and mapped(
            {"thinking": {"type": "adaptive"}}
        ):
            modes.append("adaptive")
    return modes, efforts


@lru_cache(maxsize=2048)
def model_details(provider_id: str, model: str) -> dict[str, Any]:
    provider = find_provider(provider_id)
    entry = next((m for m in provider["models"] if m["value"] == model), None)
    if entry is None:
        raise ValueError("模型不在目录中，请选择自定义接入填写模型信息")
    llm = get_litellm()
    # get_model_info may contact a provider even for catalog entries. Settings
    # browsing uses bundled metadata; tests and analysis make model requests.
    info = llm.model_cost[entry["registry_key"]]
    params = (
        llm.get_supported_openai_params(model=model, custom_llm_provider=provider_id)
        or []
    )
    modes, efforts = reasoning_controls(provider_id, model, info, params)
    return {
        "max_input_tokens": info.get("max_input_tokens") or info.get("max_tokens"),
        "max_output_tokens": info.get("max_output_tokens"),
        "reasoning_modes": modes,
        "reasoning_efforts": efforts,
        "json_mode": "response_format" in params
        and bool(info.get("supports_response_schema")),
    }


def catalog_response() -> dict[str, Any]:
    return {
        "providers": [
            {
                **provider,
                "models": [
                    {"value": model["value"], "label": model["label"]}
                    for model in provider["models"]
                ],
            }
            for provider in model_catalog()
        ],
        "connection_fields": connection_fields(),
        "source": f"LiteLLM {version('litellm')}",
    }

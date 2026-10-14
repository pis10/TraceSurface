"""LiteLLM's model registry, with verified domestic service endpoints."""

from __future__ import annotations

from functools import lru_cache
from importlib.metadata import version
from typing import Any

from tracesurface.ai.runtime import get_litellm

# Profiles describe access/billing endpoints, never maintain model-name lists.
PROFILES = [
    ("deepseek", "DeepSeek", "https://api.deepseek.com", "deepseek"),
    ("zai", "智谱 GLM", "https://open.bigmodel.cn/api/paas/v4", "zai"),
    ("moonshot", "Kimi", "https://api.moonshot.cn/v1", "moonshot"),
    ("minimax", "MiniMax", "https://api.minimax.cn/v1", "minimax"),
    (
        "dashscope",
        "阿里百炼",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "dashscope",
    ),
    (
        "volcengine",
        "火山方舟",
        "https://ark.cn-beijing.volces.com/api/v3",
        "volcengine",
    ),
    ("xiaomi_mimo", "小米 MiMo", "https://api.xiaomimimo.com/v1", "xiaomi_mimo"),
]
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


def reasoning_options(
    native: str, name: str, info: dict[str, Any], params: list[str]
) -> list[str]:
    if not info.get("supports_reasoning"):
        return ["auto"]
    values = [v for v in (info.get("reasoning_effort_levels") or []) if v in EFFORTS]
    if values and "reasoning_effort" in params:
        return ["auto", *values]
    # Capability corrections for the pinned LiteLLM release, verified against
    # provider docs. These are parameter mappings, not a second model directory.
    if native == "deepseek" and (
        name == "deepseek-flash" or name.startswith("deepseek-v4")
    ):
        return ["auto", "off", "low", "high", "max"]
    if native == "zai" and name.startswith("glm-5.3"):
        return ["auto", "low", "high", "max"]
    if native == "zai" and name.startswith("glm-5.2"):
        return ["auto", "high", "max"]
    if native == "moonshot" and name in {"kimi-k2.5", "kimi-k2.6"}:
        return ["auto", "off", "on"]
    if native == "minimax":
        return ["auto", "off", "on"] if name == "MiniMax-M3" else ["auto"]
    if "thinking" in params:
        return ["auto", "off", "on"]
    # Do not invent effort levels when the SDK only advertises reasoning=True.
    values = [v for v in EFFORTS if info.get(f"supports_{v}_reasoning_effort") is True]
    return ["auto", *values] if "reasoning_effort" in params else ["auto"]


@lru_cache(maxsize=1)
def model_catalog() -> list[dict[str, Any]]:
    llm = get_litellm()
    result = []
    for ident, label, base, native in PROFILES:
        models = {}
        # Include every chat model registered for this native provider. The
        # SDK may register both prefixed names and aliases; normalize once.
        names = llm.get_valid_models(
            check_provider_endpoint=False, custom_llm_provider=native
        )
        names = set(names) | {
            route
            for route, metadata in llm.model_cost.items()
            if metadata.get("litellm_provider") == native
            and metadata.get("mode") == "chat"
        }
        for route in names:
            name = route.removeprefix(f"{native}/")
            try:
                info = llm.get_model_info(f"{native}/{name}")
            except (ValueError, llm.ModelNotMappedError):
                continue
            if info.get("mode") != "chat":
                continue
            params = llm.get_supported_openai_params(model=f"{native}/{name}") or []
            models[name] = {
                "value": name,
                "label": name,
                "context": info.get("max_input_tokens")
                or info.get("max_tokens")
                or 128000,
                "max_output_tokens": info.get("max_output_tokens") or 16384,
                "reasoning": bool(info.get("supports_reasoning")),
                "reasoning_options": reasoning_options(native, name, info, params),
                "supports_json": "response_format" in params
                and bool(info.get("supports_response_schema")),
                "native": native,
            }
        items = sorted(models.values(), key=lambda m: m["value"], reverse=True)
        if ident == "deepseek":
            items.sort(key=lambda m: m["value"] != "deepseek-flash")
        if items:
            result.append(
                {
                    "id": ident,
                    "label": label,
                    "api_base": base,
                    "native": native,
                    "models": items,
                }
            )
    return result


def catalog_response() -> dict[str, Any]:
    return {"providers": model_catalog(), "source": f"LiteLLM {version('litellm')}"}


def find_model(provider_id: str, model: str) -> tuple[dict[str, Any], dict[str, Any]]:
    provider = next((p for p in model_catalog() if p["id"] == provider_id), None)
    if not provider:
        raise ValueError("请选择服务商")
    item = next((m for m in provider["models"] if m["value"] == model), None)
    if not item:
        raise ValueError(
            "模型不在当前 LiteLLM 目录中，请选择列表中的模型或使用自定义服务"
        )
    return provider, item

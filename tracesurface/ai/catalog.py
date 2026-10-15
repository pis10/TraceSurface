"""Read the installed SDK's catalog and profiles without authenticating."""

from functools import lru_cache
from importlib.metadata import version
from typing import Any, get_args

from pydantic_ai.models import (
    OpenAIChatCompatibleProvider,
    infer_model_profile,
    known_model_names,
)
from pydantic_ai.providers import infer_provider_class
from pydantic_ai.settings import ThinkingEffort
from typing_inspection.introspection import get_literal_values

NATIVE_EFFORTS = list(get_args(ThinkingEffort))
# Custom endpoints may expose the native max level beyond the unified SDK enum.
EFFORTS = [*NATIVE_EFFORTS, "max"]
CUSTOM_PROTOCOLS = [
    {"id": "openai-chat", "label": "OpenAI Chat Completions"},
    {"id": "openai-responses", "label": "OpenAI Responses"},
    {"id": "anthropic", "label": "Anthropic Messages"},
    {"id": "google", "label": "Gemini generateContent"},
]
LABELS = {
    "deepseek": "DeepSeek",
    "moonshotai": "Kimi",
    "zai": "智谱 GLM",
    "alibaba": "阿里百炼",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "google": "Google Gemini",
    "openrouter": "OpenRouter",
}
# Product scope: the four installed message APIs, using a single API key.
EXCLUDED = {
    "azure",
    "github-copilot",
    "github",
    "litellm",
    "ollama",
    "vllm",
    "snowflake",
    "heroku",
}


@lru_cache(maxsize=1)
def model_catalog() -> list[dict[str, Any]]:
    compatible = set(
        get_literal_values(
            OpenAIChatCompatibleProvider.__value__, unpack_type_aliases="eager"
        )
    )
    ids = (compatible | {"openai", "anthropic", "google"}) - EXCLUDED
    catalog: dict[str, dict[str, Any]] = {}
    for name in ids:
        try:
            infer_provider_class(name)
        except (ImportError, ValueError):
            continue
        catalog[name] = {"id": name, "label": LABELS.get(name, name), "models": []}
    for qualified in known_model_names():
        provider, _, model = qualified.partition(":")
        if provider in catalog:
            catalog[provider]["models"].append({"value": model, "label": model})
    preferred = list(LABELS)
    for provider in catalog.values():
        provider["models"] = sorted(
            provider["models"], key=lambda m: m["value"].casefold()
        )
    return sorted(
        catalog.values(),
        key=lambda p: (
            preferred.index(p["id"]) if p["id"] in preferred else len(preferred),
            p["label"],
        ),
    )


def find_provider(provider_id: str) -> dict[str, Any]:
    provider = next((p for p in model_catalog() if p["id"] == provider_id), None)
    if provider is None:
        raise ValueError("请选择支持的服务商")
    return provider


@lru_cache(maxsize=2048)
def model_details(provider_id: str, model: str) -> dict[str, Any]:
    find_provider(provider_id)
    profile = infer_model_profile(f"{provider_id}:{model}")
    return {
        "reasoning_efforts": NATIVE_EFFORTS if profile.get("supports_thinking") else [],
    }


def catalog_response() -> dict[str, Any]:
    return {
        "providers": model_catalog(),
        "protocols": CUSTOM_PROTOCOLS,
        "efforts": EFFORTS,
        "source": f"Pydantic AI {version('pydantic-ai-slim')}",
    }

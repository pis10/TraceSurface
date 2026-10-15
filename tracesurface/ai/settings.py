from __future__ import annotations

import json
import os
import tempfile
from typing import Any

from pydantic import SecretStr

from tracesurface.ai.models import AISettings
from tracesurface.storage.sqlite.connection import get_home

SECRET_FIELDS = {"api_key"}


class InvalidSettingsError(ValueError):
    """Old or invalid files are never migrated or rewritten implicitly."""


def load_settings() -> AISettings:
    path = get_home() / "ai-settings.json"
    try:
        if path.exists():
            raw = json.loads(path.read_text())
            if not isinstance(raw, dict) or raw.get("format_version") != 4:
                raise ValueError("incompatible")
            settings = AISettings.model_validate(raw)
        else:
            settings = AISettings()
    except (ValueError, OSError):
        raise InvalidSettingsError(
            "模型配置格式不兼容或无法读取，请移走数据目录中的 ai-settings.json 后重新配置。"
        ) from None
    return settings


def public_settings(settings: AISettings) -> dict[str, Any]:
    return {
        **settings.model_dump(exclude=SECRET_FIELDS),
        "has_api_key": bool(settings.api_key.get_secret_value()),
        "ready": (get_home() / "ai-settings.json").exists(),
    }


def credential_scope(settings: AISettings) -> tuple[str, str, str]:
    return settings.mode, settings.provider_id, settings.api_base.rstrip("/")


def with_saved_credentials(settings: AISettings) -> AISettings:
    path = get_home() / "ai-settings.json"
    previous = load_settings() if path.exists() else AISettings()
    key = settings.api_key.get_secret_value()
    if credential_scope(previous) == credential_scope(settings) and not key:
        key = previous.api_key.get_secret_value()
    return settings.model_copy(update={"api_key": SecretStr(key)})


def save_settings(settings: AISettings) -> None:
    settings = with_saved_credentials(settings)
    payload = {
        **settings.model_dump(exclude=SECRET_FIELDS),
        "api_key": settings.api_key.get_secret_value(),
    }
    fd, temporary = tempfile.mkstemp(dir=get_home(), prefix=".ai-settings-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        os.replace(temporary, get_home() / "ai-settings.json")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def prepare_settings(value: AISettings, *, credentials: bool = False) -> AISettings:
    from tracesurface.ai.catalog import CUSTOM_PROTOCOLS, model_details

    if value.mode == "custom":
        if value.provider_id not in {item["id"] for item in CUSTOM_PROTOCOLS}:
            raise ValueError("请选择支持的 API 格式")
        if not value.api_base:
            raise ValueError("请填写模型服务的 API 地址")
        if value.provider_id == "openai-responses" and value.reasoning_mode not in {
            "default",
            "effort",
            "disabled",
        }:
            raise ValueError("Responses 格式请选择默认设置、按强度或关闭思考")
        if value.provider_id == "google" and value.reasoning_mode == "adaptive":
            raise ValueError("Gemini 格式请选择默认设置、按强度或按 token 预算")
        if value.provider_id == "anthropic" and value.output_mode == "json":
            raise ValueError("Messages 格式请选择提示词约束或 JSON Schema")
    else:
        details = model_details(value.provider_id, value.model)
        if value.reasoning_mode not in details["reasoning_modes"]:
            raise ValueError("当前模型目录未提供所选思考方式，请使用自定义接入")
        value = value.model_copy(
            update={
                "context_window": None,
                "max_output_tokens": None,
                "reasoning_efforts": details["reasoning_efforts"],
                "output_mode": details["output_mode"],
            }
        )
    if value.mode == "custom":
        if not value.context_window or not value.max_output_tokens:
            raise ValueError("请填写上下文容量和最大输出 tokens")
        if value.max_output_tokens >= value.context_window:
            raise ValueError("最大输出需小于上下文容量，给输入保留空间")
    if len(value.reasoning_efforts) != len(set(value.reasoning_efforts)):
        raise ValueError("支持的思考强度不能重复")
    if (
        value.reasoning_mode == "effort"
        and value.reasoning_effort not in value.reasoning_efforts
    ):
        raise ValueError("请勾选支持的思考强度，并从中选择默认强度")
    if value.reasoning_mode == "budget" and (
        not value.thinking_budget
        or value.thinking_budget >= (value.max_output_tokens or 0)
    ):
        raise ValueError("思考预算需至少 1024 tokens，且小于输出上限")
    if credentials:
        value = with_saved_credentials(value)
        if not value.api_key.get_secret_value().strip():
            raise ValueError("请填写当前接入方式的 API Key")
    return value

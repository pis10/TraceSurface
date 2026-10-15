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
            if not isinstance(raw, dict) or raw.get("format_version") != 3:
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


def credential_scope(settings: AISettings) -> tuple[str, str]:
    return settings.provider_id, settings.api_base.rstrip("/")


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
    from tracesurface.ai.catalog import CUSTOM_PROTOCOLS, find_provider, model_details

    find_provider(value.provider_id)
    if value.mode == "custom":
        if value.provider_id not in {item["id"] for item in CUSTOM_PROTOCOLS}:
            raise ValueError("请选择支持的 API 格式")
        if not value.api_base:
            raise ValueError("请填写模型服务的 API 地址")
    if value.mode == "catalog":
        details = model_details(value.provider_id, value.model)
        if value.reasoning_mode not in details["reasoning_modes"]:
            raise ValueError("当前模型不支持所选思考方式")
        if (
            value.reasoning_mode == "effort"
            and details["reasoning_efforts"]
            and value.reasoning_effort not in details["reasoning_efforts"]
        ):
            raise ValueError("当前模型不支持所选思考强度")
        value = value.model_copy(
            update={
                k: details[k]
                for k in ("max_input_tokens", "max_output_tokens", "json_mode")
            }
        )
    if not value.max_input_tokens or not value.max_output_tokens:
        raise ValueError("请在自定义接入中填写最大输入和最大输出 tokens")
    if value.reasoning_mode == "effort" and not value.reasoning_effort.strip():
        raise ValueError("请填写或选择思考强度")
    if value.reasoning_mode == "budget" and (
        not value.thinking_budget or value.thinking_budget >= value.max_output_tokens
    ):
        raise ValueError("思考预算需至少 1024 tokens，且小于最大输出 tokens")
    if credentials:
        value = with_saved_credentials(value)
        if not value.api_key.get_secret_value().strip():
            raise ValueError("请填写当前接入方式的 API Key")
    return value

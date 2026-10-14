from __future__ import annotations

import json
import os
import tempfile
from typing import Any

from tracesurface.ai.models import AISettings
from tracesurface.storage.sqlite.connection import get_home


class InvalidSettingsError(ValueError):
    """Invalid files need a fresh configuration, never automatic conversion."""


def load_settings() -> AISettings:
    path = get_home() / "ai-settings.json"
    try:
        settings = (
            AISettings.model_validate_json(path.read_text())
            if path.exists()
            else AISettings()
        )
    except (ValueError, OSError):
        raise InvalidSettingsError(
            "模型配置无法读取或格式不兼容，请移走数据目录中的 ai-settings.json 后重新配置。"
        ) from None
    if not settings.api_key.get_secret_value() and os.environ.get(
        "TRACESURFACE_AI_KEY"
    ):
        from pydantic import SecretStr

        settings.api_key = SecretStr(os.environ["TRACESURFACE_AI_KEY"])
    return settings


def public_settings(settings: AISettings) -> dict[str, Any]:
    return {
        **settings.model_dump(exclude={"api_key"}),
        "has_api_key": bool(settings.api_key.get_secret_value()),
    }


def save_settings(settings: AISettings) -> None:
    # Empty key means preserve the current key. Never return it to the browser.
    path = get_home() / "ai-settings.json"
    key = settings.api_key.get_secret_value()
    if not key and path.exists():
        previous = AISettings.model_validate_json(path.read_text())
        if credential_scope(previous) == credential_scope(settings):
            key = previous.api_key.get_secret_value()
    payload = {**settings.model_dump(exclude={"api_key"}), "api_key": key}
    fd, temporary = tempfile.mkstemp(dir=get_home(), prefix=".ai-settings-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def credential_scope(settings: AISettings) -> tuple[str, str]:
    return settings.provider_id, settings.api_base.rstrip("/")


def prepare_settings(value: AISettings, *, require_key: bool = False) -> AISettings:
    from tracesurface.ai.catalog import find_model

    if value.provider_id != "custom":
        provider, model = find_model(value.provider_id, value.model)
        if value.reasoning_effort not in model["reasoning_options"]:
            raise ValueError("当前模型不支持所选思考设置")
        value = value.model_copy(update={"api_base": provider["api_base"]})
    else:
        if not value.api_base:
            raise ValueError("请填写 API 地址")
        if value.reasoning_effort != "auto":
            raise ValueError("自定义服务使用模型默认思考设置")
    if require_key and not value.api_key.get_secret_value():
        previous = load_settings()
        if credential_scope(previous) == credential_scope(value):
            value = value.model_copy(update={"api_key": previous.api_key})
        if not value.api_key.get_secret_value():
            raise ValueError("请填写当前服务商的 API Key")
    return value

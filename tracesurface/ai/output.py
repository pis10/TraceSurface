"""Recover presentation wrappers without repairing or inventing analysis data."""

from __future__ import annotations

import json
import re

from pydantic import ValidationError

from tracesurface.ai.models import SiteResult


class AIOutputError(ValueError):
    """A locally generated, safe diagnostic that can also guide a correction."""


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise AIOutputError("AI 返回的 JSON 包含重复字段，无法确定结果")
        result[key] = value
    return result


def parse_text_result(text: str) -> SiteResult:
    text = text.strip().lstrip("\ufeff").strip()
    # Some compatible gateways put reasoning in content instead of reasoning_content.
    while text.startswith("<think>"):
        end = text.find("</think>")
        if end < 0:
            raise AIOutputError("AI 只返回了未结束的思考内容，没有完整 JSON 结果")
        text = text[end + len("</think>") :].strip()
    if not text:
        raise AIOutputError("AI 返回正文为空，没有 JSON 结果")

    # Accept one complete JSON value surrounded by prose / Markdown. Do not search
    # inside an invalid value for a valid fragment, or choose between two answers.
    decoder = json.JSONDecoder(object_pairs_hook=_unique_object)
    values = []
    remaining = text
    while match := re.search(r"[\[{]", remaining):
        try:
            value, end = decoder.raw_decode(remaining[match.start() :])
        except json.JSONDecodeError:
            raise AIOutputError("AI 返回的 JSON 语法不完整或不合法") from None
        values.append(value)
        remaining = remaining[match.start() + end :]
    if len(values) != 1:
        raise AIOutputError("AI 必须返回且只返回一个完整 JSON 对象")
    try:
        return SiteResult.model_validate(values[0])
    except ValidationError as exc:
        # Never include model-supplied values or arbitrary field names in diagnostics.
        fields = {"results", "replay_id", "suspected", "reason", "recommendation"}
        invalid = sorted(
            {
                str(part)
                for error in exc.errors(include_input=False, include_url=False)
                for part in error["loc"]
                if part in fields
            }
        )
        detail = "、".join(invalid) or "顶层结构"
        raise AIOutputError(
            f"AI 返回字段不符合约定（{detail}）；replay_id 必须为整数，suspected 必须为布尔值，"
            "reason 必须为非空字符串，recommendation 必须为字符串，且不得添加额外字段"
        ) from None


def validate_coverage(result: SiteResult, expected_ids: set[int]) -> None:
    actual = [item.replay_id for item in result.results]
    unique = set(actual)
    missing, extra, duplicates = (
        len(expected_ids - unique),
        len(unique - expected_ids),
        len(actual) - len(unique),
    )
    if missing or extra or duplicates:
        raise AIOutputError(
            f"AI 返回的接口编号不完整：遗漏 {missing} 个，多出 {extra} 个，重复 {duplicates} 个；"
            "每个输入 replay_id 必须恰好返回一次"
        )

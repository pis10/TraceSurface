from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class AISettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format_version: Literal[5] = 5
    mode: Literal["catalog", "custom"] = "catalog"
    provider_id: str = Field(default="deepseek", min_length=1, max_length=100)
    model: str = Field(default="deepseek-v4-flash", min_length=1, max_length=200)
    api_base: str = Field(default="", max_length=500)
    api_key: SecretStr = SecretStr("")
    context_window: int | None = Field(default=None, ge=1024, le=100000000)
    max_output_tokens: int | None = Field(default=None, ge=256, le=10000000)
    reasoning_effort: Literal[
        "", "minimal", "low", "medium", "high", "xhigh", "max"
    ] = ""
    reasoning_efforts: list[
        Literal["minimal", "low", "medium", "high", "xhigh", "max"]
    ] = Field(default_factory=list, max_length=6)
    response_limit: int = Field(default=8192, ge=256, le=1048576)

    @field_validator("api_base")
    @classmethod
    def validate_base(cls, value: str) -> str:
        from urllib.parse import urlsplit

        value = value.strip().rstrip("/")
        if value:
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"https", "http"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("API 地址必须为不含密钥的 HTTP(S) 地址")
        return value


class InterfaceResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    replay_id: int
    suspected: bool
    reason: str = Field(min_length=1, max_length=3000)
    recommendation: str = Field(max_length=3000)


class SiteResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    results: list[InterfaceResult]


class StartAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scan_ids: list[int] | None = Field(default=None, max_length=20000)
    search: str = Field(default="", max_length=500)
    target: str = Field(default="", max_length=2000)
    force: bool = False


def validate_result(content: str, expected_ids: set[int]) -> SiteResult:
    result = SiteResult.model_validate_json(content)
    actual = [r.replay_id for r in result.results]
    if len(actual) != len(set(actual)) or set(actual) != expected_ids:
        raise ValueError("AI 返回的接口编号存在遗漏、重复或不属于本次输入")
    return result

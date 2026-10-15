from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from tracesurface.ai import repository as repo
from tracesurface.ai.inference import analyze_configured as analyze
from tracesurface.ai.models import AISettings, SiteResult
from tracesurface.storage.sqlite.queries import get_replay


def truncate_body(text: str, limit: int) -> tuple[str, bool, int]:
    raw = text.encode("utf-8")
    return raw[:limit].decode("utf-8", errors="ignore"), len(raw) > limit, len(raw)


def build_payload(
    scan: dict[str, Any], settings: AISettings
) -> tuple[dict[str, Any], set[int]]:
    records = []
    truncated_ids: set[int] = set()
    for item in repo.eligible_replays(scan["id"]):
        replay = get_replay(item["id"])
        if replay is None or replay.get("resp_file_error"):
            raise ValueError("重放数据缺失或响应文件无法读取，请检查扫描数据")
        if replay.get("resp_file"):
            from tracesurface.storage.sqlite.queries import response_path

            if not response_path(replay["resp_file"]).exists():
                raise ValueError("重放响应文件已丢失，无法分析")
        body, truncated, captured_bytes = truncate_body(
            replay.get("resp_snippet") or "", settings.response_limit
        )
        truncated = truncated or bool(replay.get("resp_truncated"))
        if truncated:
            truncated_ids.add(replay["id"])
        records.append(
            {
                "replay_id": replay["id"],
                "request": {
                    "method": replay["sent_method"],
                    "url": replay["sent_url"],
                    "headers": replay.get("sent_headers"),
                    "query": replay.get("sent_query"),
                    "body": replay.get("sent_body"),
                },
                "response": {
                    "status": replay["status"],
                    "headers": replay.get("resp_headers"),
                    "body": body,
                    "truncated": truncated,
                    "reported_bytes": replay.get("resp_full_len")
                    or replay.get("resp_len"),
                    "captured_text_bytes": captured_bytes,
                    "submitted_bytes": len(body.encode("utf-8")),
                },
            }
        )
    return {
        "site": scan,
        "scan_description": "前端动态追踪及静态推导接口的无认证重放；只分析 2xx && JSON；正文按 UTF-8 字节上限截取。",
        "records": records,
    }, truncated_ids


def safe_error(exc: Exception) -> str:
    # Provider exceptions can contain API keys, input bodies and response content.
    # Never persist or return their raw text.
    kind = type(exc).__name__
    if kind in {"AuthenticationError", "PermissionDeniedError"}:
        return "模型认证失败，请检查密钥及账户权限"
    if kind == "RateLimitError":
        return "模型限流或余额不足，请检查账户后重试"
    if kind == "ContextWindowExceededError":
        return "本站输入超过模型上下文限制，请选择上下文更大的模型"
    if kind == "NotFoundError":
        return "找不到所选模型，请检查模型名称及账户权限"
    if kind == "APIConnectionError":
        return "无法连接模型服务，请检查网络或 API 地址"
    if kind in {"BadRequestError", "UnsupportedParamsError"}:
        return "模型未接受当前配置，请检查所选模型及思考设置"
    if "Timeout" in kind:
        return "模型请求超时，请稍后重试"
    if kind in {"ValidationError", "UnexpectedModelBehavior", "UsageLimitExceeded"}:
        return "AI 返回格式不符合约定，未保存为分析成功，请重试"
    # Only locally generated ValueErrors have trusted messages.
    if type(exc) is ValueError and str(exc).startswith(
        ("AI ", "本站输入", "重放", "分析期间")
    ):
        return str(exc)
    status = getattr(exc, "status_code", None)
    if status in {401, 403}:
        return "模型认证失败，请检查密钥及账户权限"
    if status == 429:
        return "模型限流或余额不足，请检查账户后重试"
    if status == 404:
        return "找不到所选模型，请检查模型名称及 API 地址"
    if status == 400:
        return "模型未接受当前配置，请检查所选模型及思考设置"
    suffix = f" / HTTP {status}" if isinstance(status, int) else ""
    return f"模型调用失败{suffix}，请检查模型、账户权限和网络"


class AnalysisManager:
    def __init__(
        self,
        analyzer: Callable[..., Awaitable[tuple[SiteResult, dict[str, Any]]]] = analyze,
    ):
        self.analyzer = analyzer
        self.workers: list[asyncio.Task[None]] = []
        self.credentials: dict[int, AISettings] = {}
        self.wake = asyncio.Event()
        self.submission_lock = asyncio.Lock()

    async def start(self) -> None:
        await asyncio.to_thread(repo.interrupt_pending)
        self.workers = [asyncio.create_task(self.worker()) for _ in range(3)]

    async def close(self) -> None:
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        await asyncio.to_thread(repo.interrupt_pending)
        self.credentials.clear()

    def notify(self, batch_id: int, settings: AISettings) -> None:
        self.credentials[batch_id] = settings.model_copy(deep=True)
        self.wake.set()

    async def worker(self) -> None:
        while True:
            self.wake.clear()
            async with self.submission_lock:
                job = await asyncio.to_thread(repo.claim)
            if not job:
                try:
                    await asyncio.wait_for(self.wake.wait(), 2)
                except TimeoutError:
                    pass
                continue
            try:
                scan = await asyncio.to_thread(repo.scan_record, job["scan_id"])
                if not scan or scan["status"] != "done":
                    await asyncio.to_thread(
                        repo.update_job,
                        job["id"],
                        status="skipped",
                        message="扫描数据已删除、替换或尚未完成",
                        finished_at=int(time.time()),
                    )
                    continue
                settings = AISettings.model_validate_json(job["settings_json"])
                credentials = self.credentials.get(job["batch_id"])
                if credentials is None:
                    raise ValueError("AI 配置不可用，请重新发起分析")
                settings.api_key = credentials.api_key
                payload, truncated = await asyncio.to_thread(
                    build_payload, scan, settings
                )
                await asyncio.to_thread(
                    repo.update_job,
                    job["id"],
                    input_count=len(payload["records"]),
                    truncated_count=len(truncated),
                )
                if not payload["records"]:
                    await asyncio.to_thread(
                        repo.update_job,
                        job["id"],
                        status="skipped",
                        message="无符合 2xx && JSON 条件的重放响应",
                        finished_at=int(time.time()),
                    )
                    continue
                result, usage = await self.analyzer(payload, settings)
                await asyncio.to_thread(
                    repo.save_result, job["id"], result, truncated, usage
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 -- isolate provider failures per site
                await asyncio.to_thread(
                    repo.update_job,
                    job["id"],
                    status="failed",
                    message=safe_error(exc),
                    finished_at=int(time.time()),
                )

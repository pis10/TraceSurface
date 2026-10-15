from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError

from tracesurface.ai import repository as repo
from tracesurface.ai.catalog import catalog_response, model_details
from tracesurface.ai.models import AISettings, StartAnalysis
from tracesurface.ai.settings import (
    load_settings,
    prepare_settings,
    public_settings,
    save_settings,
)


def same_origin(request: Request) -> None:
    if request.method not in {"GET", "HEAD"}:
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.headers.get("host"):
            raise HTTPException(403, "仅允许从本地报告页面操作 AI 配置和任务")


router = APIRouter(prefix="/api/ai", dependencies=[Depends(same_origin)])


@router.get("/catalog")
def catalog():
    return catalog_response()


@router.get("/catalog/model")
def catalog_model(provider: str, model: str):
    try:
        return model_details(provider, model)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/settings")
def settings():
    return public_settings(load_settings())


@router.put("/settings")
async def put_settings(request: Request):
    # Avoid FastAPI's default validation response echoing an API key.
    try:
        value = AISettings.model_validate(await request.json())
    except (ValidationError, ValueError):
        raise HTTPException(422, "配置无效，请检查模型、API 地址和数值范围") from None
    try:
        value = prepare_settings(value)
        prepare_settings(value, credentials=True)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    save_settings(value)
    return public_settings(load_settings())


@router.post("/test")
async def connection_test(request: Request):
    from tracesurface.ai.inference import test_connection
    from tracesurface.ai.service import safe_error

    try:
        value = AISettings.model_validate(await request.json())
    except (ValidationError, ValueError):
        raise HTTPException(422, "配置无效，请检查模型和 API 地址") from None
    try:
        value = prepare_settings(value, credentials=True)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    try:
        return await test_connection(value)
    except Exception as exc:  # noqa: BLE001 - never expose raw provider errors
        return {"ok": False, "message": safe_error(exc)}


@router.get("/sites")
def sites(
    search: str = "",
    target: str = "",
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
):
    return repo.sites(search, target, offset, limit)


@router.post("/batches")
async def start(body: StartAnalysis, request: Request):
    config = load_settings()
    if not public_settings(config)["ready"]:
        raise HTTPException(400, "请先保存模型设置")
    try:
        config = prepare_settings(config)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    manager = request.app.state.ai_manager
    async with manager.submission_lock:
        try:
            batch_id = await asyncio.to_thread(repo.enqueue, body, config)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        manager.notify(batch_id, config)
    return {"batch_id": batch_id}


@router.get("/batches")
def batches():
    return {"items": repo.batches()}


@router.get("/batches/{batch_id}/jobs")
def jobs(
    batch_id: int, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=500)
):
    return repo.jobs(batch_id, offset, limit)


@router.get("/findings")
def findings(
    batch_id: int | None = None,
    target: str = "",
    search: str = "",
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
):
    return repo.findings(batch_id, target, search, offset, limit)

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

import tracesurface.storage.sqlite.queries as queries
from tracesurface.server.routes.common import parse_csv

router = APIRouter()


@router.get("/api/replays")
def replays(
    search: str = "",
    search_field: Literal["url", "body", "dom"] | None = None,
    domain: str = "",
    target: str = "",
    methods: str | None = None,
    buckets: str | None = None,
    resp_cts: str | None = None,
    grades: str | None = None,
    origins: str | None = None,
    deny_keywords: list[str] | None = Query(default=None),
    sort: Literal[
        "created_at",
        "-created_at",
        "status",
        "-status",
        "resp_len",
        "-resp_len",
    ] = "-created_at",
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
):
    total, items = queries.query_replays(
        search=search,
        search_field=search_field,
        domain=domain,
        target=target,
        methods=parse_csv(methods),
        buckets=parse_csv(buckets),
        resp_cts=parse_csv(resp_cts),
        grades=parse_csv(grades),
        origins=parse_csv(origins),
        deny_keywords=deny_keywords,
        sort=sort,
        offset=offset,
        limit=limit,
    )
    return {"total": total, "items": items}


@router.get("/api/replays/{replay_id}")
def replay_detail(replay_id: int):
    replay = queries.get_replay(replay_id, include_full_body=True)
    if not replay:
        raise HTTPException(404, "replay not found")
    return replay


@router.get("/api/replays/{replay_id}/file")
def replay_file(replay_id: int):
    replay = queries.get_replay(replay_id, include_full_body=False)
    if not replay or not replay.get("resp_file"):
        raise HTTPException(404, "no response file")
    path = queries.response_path(replay["resp_file"])

    if not path.exists():
        raise HTTPException(404, "file missing on disk")

    raw_ct = "application/octet-stream"
    headers = replay.get("resp_headers") or {}
    if isinstance(headers, dict):
        raw_ct = headers.get("content-type") or headers.get("Content-Type") or raw_ct
    return FileResponse(
        str(path),
        media_type=raw_ct,
        filename=f"tracesurface-replay-{replay_id}",
    )

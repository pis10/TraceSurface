from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query

import tracesurface.storage.sqlite.queries as queries
from tracesurface.server.routes.common import parse_csv

router = APIRouter()


@router.get("/api/resolutions")
def resolutions(
    search: str = "",
    target: str = "",
    methods: str | None = None,
    grades: str | None = None,
    sort: Literal["grade", "id"] = "grade",
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
):
    total, items = queries.query_resolutions(
        search=search,
        target=target,
        methods=parse_csv(methods),
        grades=parse_csv(grades),
        sort=sort,
        offset=offset,
        limit=limit,
    )
    return {"total": total, "items": items}


@router.get("/api/resolutions/{resolution_id}")
def resolution_detail(resolution_id: int):
    res = queries.get_resolution(resolution_id)
    if not res:
        raise HTTPException(404, "resolution not found")
    return res

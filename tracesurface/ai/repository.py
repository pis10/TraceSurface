from __future__ import annotations

import json
import time
from contextlib import closing
from typing import Any

from tracesurface.ai.models import AISettings, SiteResult, StartAnalysis
from tracesurface.storage.sqlite.connection import connect
from tracesurface.storage.sqlite.queries import query_replays

PROMPT_VERSION = "replay-review-v1"


def sites(
    search: str = "", target: str = "", offset: int = 0, limit: int = 50
) -> dict[str, Any]:
    where, params = site_filter(search, target)
    with closing(connect()) as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM scans s WHERE {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT s.*, (SELECT COUNT(*) FROM verifications v WHERE v.scan_id=s.id) AS replay_count, "
            "(SELECT status FROM ai_jobs j WHERE j.scan_id=s.id ORDER BY id DESC LIMIT 1) AS ai_status "
            f"FROM scans s WHERE {where} ORDER BY s.id DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
    return {"total": total, "items": [dict(row) for row in rows]}


def site_filter(search: str, target: str) -> tuple[str, list[Any]]:
    clauses = [
        "s.id = (SELECT MAX(s2.id) FROM scans s2 WHERE s2.target_url=s.target_url)"
    ]
    params: list[Any] = []
    if search:
        clauses.append("instr(lower(s.target_url), lower(?)) > 0")
        params.append(search)
    if target:
        clauses.append("s.target_url=?")
        params.append(target)
    return " AND ".join(clauses), params


def enqueue(selection: StartAnalysis, settings: AISettings) -> int:
    with closing(connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            where, params = site_filter(selection.search, selection.target)
            scans = conn.execute(
                f"SELECT s.* FROM scans s WHERE {where}", params
            ).fetchall()
            if selection.scan_ids is not None:
                ids = set(selection.scan_ids)
                scans = [s for s in scans if s["id"] in ids]
                if ids != {s["id"] for s in scans}:
                    raise ValueError("选中的扫描记录已变化，请刷新站点列表")
            if not scans:
                raise ValueError("没有选中站点")
            batch_id = conn.execute(
                "INSERT INTO ai_batches(created_at,settings_json,prompt_version) VALUES(?,?,?)",
                (
                    int(time.time()),
                    json.dumps(settings.model_dump(exclude={"api_key"})),
                    PROMPT_VERSION,
                ),
            ).lastrowid
            for scan in scans:
                state, message = "queued", ""
                if scan["status"] != "done" or scan["finished_at"] is None:
                    state, message = (
                        "skipped",
                        "扫描完成后再分析"
                        if scan["status"] == "running"
                        else "扫描失败，请重新扫描后分析",
                    )
                elif conn.execute(
                    "SELECT 1 FROM ai_jobs WHERE scan_id=? AND status IN ('queued','running')",
                    (scan["id"],),
                ).fetchone():
                    state, message = "skipped", "该站点已在分析队列中"
                elif (
                    not selection.force
                    and conn.execute(
                        "SELECT 1 FROM ai_jobs WHERE scan_id=? AND status='done'",
                        (scan["id"],),
                    ).fetchone()
                ):
                    state, message = "skipped", "已有分析结果；如需更新，请选择重新分析"
                conn.execute(
                    "INSERT INTO ai_jobs(batch_id,scan_id,target_url,status,message,finished_at) VALUES(?,?,?,?,?,?)",
                    (
                        batch_id,
                        scan["id"],
                        scan["target_url"],
                        state,
                        message,
                        int(time.time()) if state == "skipped" else None,
                    ),
                )
            conn.execute("COMMIT")
            return int(batch_id or 0)
        except Exception:
            conn.execute("ROLLBACK")
            raise


def claim() -> dict[str, Any] | None:
    with closing(connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT j.*, b.settings_json FROM ai_jobs j JOIN ai_batches b ON b.id=j.batch_id WHERE j.status='queued' ORDER BY j.id LIMIT 1"
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE ai_jobs SET status='running', started_at=? WHERE id=?",
                (int(time.time()), row["id"]),
            )
        conn.execute("COMMIT")
        return dict(row) if row else None


def update_job(job_id: int, **values) -> None:
    allowed = {
        "status",
        "message",
        "input_count",
        "truncated_count",
        "suspected_count",
        "input_tokens",
        "output_tokens",
        "finished_at",
    }
    if not values or not set(values) <= allowed:
        raise ValueError("invalid job fields")
    with closing(connect()) as conn:
        conn.execute(
            "UPDATE ai_jobs SET "
            + ",".join(f"{key}=?" for key in values)
            + " WHERE id=?",
            [*values.values(), job_id],
        )


def interrupt_pending() -> None:
    with closing(connect()) as conn:
        conn.execute(
            "UPDATE ai_jobs SET status='failed', message='报告服务已停止，分析中断；请手动重试', finished_at=? WHERE status IN ('queued','running')",
            (int(time.time()),),
        )


def scan_record(scan_id: int | None) -> dict[str, Any] | None:
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
        return dict(row) if row else None


def eligible_replays(scan_id: int) -> list[dict[str, Any]]:
    # Reuse exactly the existing report filter. No extra keyword exclusion.
    items: list[dict[str, Any]] = []
    while True:
        total, page = query_replays(
            scan_id=scan_id,
            buckets=["2xx"],
            resp_cts=["json"],
            offset=len(items),
            limit=500,
        )
        items.extend(page)
        if len(items) >= total or not page:
            return items


def save_result(
    job_id: int, result: SiteResult, truncated: set[int], usage: dict[str, Any]
) -> None:
    with closing(connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            job = conn.execute(
                "SELECT scan_id,status FROM ai_jobs WHERE id=?", (job_id,)
            ).fetchone()
            if not job or job["scan_id"] is None or job["status"] != "running":
                raise ValueError("分析期间扫描数据已删除或替换")
            conn.executemany(
                "INSERT INTO ai_results(job_id,replay_id,suspected,reason,recommendation,truncated) VALUES(?,?,?,?,?,?)",
                [
                    (
                        job_id,
                        r.replay_id,
                        int(r.suspected),
                        r.reason,
                        r.recommendation,
                        int(r.replay_id in truncated),
                    )
                    for r in result.results
                ],
            )
            conn.execute(
                "UPDATE ai_jobs SET status='done', suspected_count=?, input_tokens=?, output_tokens=?, finished_at=? WHERE id=?",
                (
                    sum(r.suspected for r in result.results),
                    usage.get("prompt_tokens", 0),
                    usage.get("completion_tokens", 0),
                    int(time.time()),
                    job_id,
                ),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def batches() -> list[dict[str, Any]]:
    with closing(connect()) as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT b.id,b.created_at,COUNT(j.id) AS total, "
                "SUM(j.status='done') AS done,SUM(j.status='failed') AS failed,SUM(j.status='skipped') AS skipped, "
                "SUM(j.status='queued') AS queued,SUM(j.status='running') AS running,SUM(j.suspected_count) AS suspected "
                "FROM ai_batches b LEFT JOIN ai_jobs j ON j.batch_id=b.id GROUP BY b.id ORDER BY b.id DESC LIMIT 100"
            )
        ]


def jobs(batch_id: int, offset: int, limit: int) -> dict[str, Any]:
    with closing(connect()) as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM ai_jobs WHERE batch_id=?", (batch_id,)
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM ai_jobs WHERE batch_id=? ORDER BY id LIMIT ? OFFSET ?",
            (batch_id, limit, offset),
        )
        return {"total": total, "items": [dict(r) for r in rows]}


def findings(
    batch_id: int | None, target: str, search: str, offset: int, limit: int
) -> dict[str, Any]:
    clauses = ["r.suspected=1", "j.status='done'"]
    params: list[Any] = []
    if batch_id is not None:
        clauses.append("j.batch_id=?")
        params.append(batch_id)
    else:
        clauses.append(
            "j.id=(SELECT MAX(j2.id) FROM ai_jobs j2 WHERE j2.scan_id=j.scan_id AND j2.status='done')"
        )
        clauses.append(
            "j.scan_id=(SELECT MAX(s.id) FROM scans s WHERE s.target_url=j.target_url)"
        )
    if target:
        clauses.append("j.target_url=?")
        params.append(target)
    if search:
        clauses.append(
            "(instr(lower(j.target_url),lower(?))>0 OR instr(lower(v.sent_url),lower(?))>0 OR instr(lower(r.reason),lower(?))>0)"
        )
        params.extend([search] * 3)
    base = (
        "FROM ai_results r JOIN ai_jobs j ON j.id=r.job_id JOIN verifications v ON v.id=r.replay_id JOIN ai_batches b ON b.id=j.batch_id WHERE "
        + " AND ".join(clauses)
    )
    with closing(connect()) as conn:
        total = conn.execute("SELECT COUNT(*) " + base, params).fetchone()[0]
        rows = conn.execute(
            "SELECT r.*,j.target_url,j.scan_id,j.batch_id,v.sent_url,v.sent_method,v.status,b.created_at,json_extract(b.settings_json,'$.model') AS model "
            + base
            + " ORDER BY j.id DESC,r.replay_id LIMIT ? OFFSET ?",
            params + [limit, offset],
        )
        return {"total": total, "items": [dict(r) for r in rows]}

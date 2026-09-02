from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from tracesurface.config import DEFAULT_SETTINGS
from tracesurface.storage.sqlite.connection import connect, get_home

_RESOLUTION_TIER_ORDER = (
    "CASE r.inference_tier WHEN 'L1' THEN 1 WHEN 'L2' THEN 2 "
    "WHEN 'L3' THEN 3 WHEN 'L4' THEN 4 ELSE 5 END, r.id DESC"
)


def query_resolutions(
    *,
    target: str = "",
    methods: list[str] | None = None,
    tiers: list[str] | None = None,
    statuses: list[str] | None = None,
    search: str = "",
    sort: str = "tier",
    offset: int = 0,
    limit: int = 50,
) -> tuple[int, list[dict[str, Any]]]:
    wheres: list[str] = []
    params: list[Any] = []
    if target:
        wheres.append("r.scan_id IN (SELECT id FROM scans WHERE target_url = ?)")
        params.append(target)
    if methods:
        ph = ",".join("?" * len(methods))
        wheres.append(f"s.method IN ({ph})")
        params.extend(methods)
    if tiers:
        known_tiers = {"L1", "L2", "L3", "L4"}
        sel = [t for t in tiers if t in known_tiers]
        if sel and set(sel) != known_tiers:
            ph = ",".join("?" * len(sel))
            wheres.append(f"r.inference_tier IN ({ph})")
            params.extend(sel)
    if statuses:
        sel = [
            s
            for s in statuses
            if s in {"confirmed", "inferred", "ast_full", "not_inferred"}
        ]
        if sel:
            ph = ",".join("?" * len(sel))
            wheres.append(f"r.category IN ({ph})")
            params.extend(sel)
    if search:
        wheres.append("(r.full_url LIKE ? OR s.source_js LIKE ?)")
        kw = f"%{search}%"
        params.extend([kw, kw])
    where_sql = " AND ".join(wheres) if wheres else "1=1"

    order = _RESOLUTION_TIER_ORDER if sort == "tier" else "r.id DESC"

    base = (
        f"FROM api_resolutions r JOIN api_sinks s ON s.id = r.sink_id WHERE {where_sql}"
    )
    count_sql = f"SELECT COUNT(*) {base}"
    select_sql = (
        "SELECT r.id, s.method, r.full_url, r.category, r.inference_tier, "
        "r.base_source, r.binding_rule, "
        "(SELECT e.evidence_id FROM resolution_evidence e "
        " WHERE e.resolution_id = r.id AND e.evidence_kind = 'cdp_request' "
        " LIMIT 1) AS cdp_request_id "
        f"{base} ORDER BY {order} LIMIT ? OFFSET ?"
    )
    conn = connect()
    try:
        total = conn.execute(count_sql, params).fetchone()[0]
        rows = conn.execute(select_sql, params + [limit, offset]).fetchall()
        items = [dict(r) for r in rows]
    finally:
        conn.close()
    return total, items


def get_resolution(resolution_id: int) -> dict[str, Any] | None:
    conn = connect()
    try:
        r = conn.execute(
            "SELECT r.*, s.method, s.ast_path, s.source_js, s.line, s.col_start, "
            "s.pattern, s.params_json "
            "FROM api_resolutions r JOIN api_sinks s ON s.id = r.sink_id WHERE r.id=?",
            (resolution_id,),
        ).fetchone()
        if not r:
            return None
        d = dict(r)
        d["params"] = json.loads(d["params_json"]) if d.get("params_json") else []

        reps = conn.execute(
            "SELECT id, variant, sent_method, sent_url, status, resp_ct, resp_len, time_ms, error, created_at "
            "FROM verifications WHERE resolution_id=? ORDER BY created_at DESC",
            (resolution_id,),
        ).fetchall()
        d["verifications"] = [dict(x) for x in reps]

        ev = conn.execute(
            "SELECT evidence_kind, evidence_id, role "
            "FROM resolution_evidence WHERE resolution_id=?",
            (resolution_id,),
        ).fetchall()
        d["evidence"] = [dict(x) for x in ev]
        return d
    finally:
        conn.close()


_BUCKET_SQL = {
    "2xx": "(status >= 200 AND status < 300)",
    "3xx": "(status >= 300 AND status < 400)",
    "4xx": "(status >= 400 AND status < 500)",
    "5xx": "(status >= 500)",
}

_SORT_COLUMNS = {
    "status": "status",
    "resp_len": "resp_len",
    "created_at": "created_at",
}

_SEARCH_FIELDS = {
    "url": "sent_url LIKE ?",
    "body": "COALESCE(resp_snippet,'') LIKE ?",
    "dom": "domain LIKE ?",
}


def query_replays(
    *,
    search: str = "",
    search_field: str | None = None,
    domain: str = "",
    target: str = "",
    methods: list[str] | None = None,
    buckets: list[str] | None = None,
    resp_cts: list[str] | None = None,
    tiers: list[str] | None = None,
    origins: list[str] | None = None,
    deny_keywords: list[str] | None = None,
    sort: str = "-created_at",
    offset: int = 0,
    limit: int = 50,
) -> tuple[int, list[dict[str, Any]]]:
    wheres: list[str] = ["status IS NOT NULL"]
    params: list[Any] = []
    if domain:
        wheres.append("domain = ?")
        params.append(domain)
    if target:
        wheres.append("scan_id IN (SELECT id FROM scans WHERE target_url = ?)")
        params.append(target)
    if methods:
        placeholders = ",".join("?" * len(methods))
        wheres.append(f"sent_method IN ({placeholders})")
        params.extend(methods)
    if buckets:
        frags = [_BUCKET_SQL[b] for b in buckets if b in _BUCKET_SQL]
        if frags:
            wheres.append("(" + " OR ".join(frags) + ")")
    if resp_cts:
        known = {"json", "html", "text"}
        selected_known = [c for c in resp_cts if c in known]
        has_other = "other" in resp_cts
        clauses: list[str] = []
        if selected_known:
            ph = ",".join("?" * len(selected_known))
            clauses.append(f"resp_ct IN ({ph})")
            params.extend(selected_known)
        if has_other:
            clauses.append("(resp_ct IS NULL OR resp_ct NOT IN ('json','html','text'))")
        if clauses:
            wheres.append("(" + " OR ".join(clauses) + ")")
    if tiers:
        known_tiers = {"L1", "L2", "L3", "L4"}
        selected = [t for t in tiers if t in known_tiers]
        if selected and set(selected) != known_tiers:
            ph = ",".join("?" * len(selected))
            wheres.append(f"inference_tier IN ({ph})")
            params.extend(selected)
    if origins:
        want_cdp = "cdp" in origins
        want_inferred = "inferred" in origins
        if want_cdp and not want_inferred:
            wheres.append("cdp_request_id IS NOT NULL")
        elif want_inferred and not want_cdp:
            wheres.append("cdp_request_id IS NULL")
    if deny_keywords:
        for kw in deny_keywords:
            safe = kw.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
            wheres.append(r"COALESCE(resp_snippet,'') NOT LIKE ? ESCAPE '\'")
            params.append(f"%{safe}%")
    if search:
        kw = f"%{search}%"
        if search_field:
            if search_field not in _SEARCH_FIELDS:
                raise ValueError(f"invalid search_field: {search_field}")
            wheres.append(_SEARCH_FIELDS[search_field])
            params.append(kw)
        else:
            wheres.append(
                "(sent_url LIKE ? OR domain LIKE ? OR COALESCE(resp_snippet,'') LIKE ?)"
            )
            params.extend([kw, kw, kw])

    where_sql = " AND ".join(wheres)

    desc = sort.startswith("-")
    col_name = sort.lstrip("-")
    if col_name not in _SORT_COLUMNS:
        raise ValueError(f"invalid sort: {sort}")
    col = _SORT_COLUMNS[col_name]
    direction = "DESC" if desc else "ASC"

    count_sql = f"SELECT COUNT(*) FROM verifications WHERE {where_sql}"

    select_sql = (
        f"SELECT id, sent_url, sent_method, status, resp_ct, resp_len, "
        f"inference_tier, domain, cdp_request_id, "
        f"SUBSTR(resp_snippet, 1, 256) AS resp_snippet "
        f"FROM verifications WHERE {where_sql} "
        f"ORDER BY {col} {direction}, id DESC LIMIT ? OFFSET ?"
    )

    conn = connect()
    try:
        total = conn.execute(count_sql, params).fetchone()[0]
        rows = conn.execute(select_sql, params + [limit, offset]).fetchall()
        items = [_replay_row_to_dict(r) for r in rows]
    finally:
        conn.close()
    return total, items


def get_replay(
    replay_id: int, *, include_full_body: bool = True
) -> dict[str, Any] | None:
    conn = connect()
    try:
        r = conn.execute(
            "SELECT * FROM verifications WHERE id=?", (replay_id,)
        ).fetchone()
        if not r:
            return None
        d = _replay_row_to_dict(r)

        if d.get("resp_file"):
            p = get_home() / "responses" / d["resp_file"]
            if p.exists():
                file_size = p.stat().st_size
                resp_len = int(d.get("resp_len") or 0)
                if resp_len > file_size:
                    d["resp_truncated"] = True
                    d["resp_full_len"] = resp_len

        if include_full_body and d.get("resp_file") and d.get("resp_ct") != "bin":
            p = get_home() / "responses" / d["resp_file"]
            if p.exists():
                try:
                    raw = p.read_bytes()[
                        : DEFAULT_SETTINGS.storage.response_detail_limit
                    ]
                    d["resp_snippet"] = raw.decode("utf-8", errors="replace")
                except OSError as exc:
                    d["resp_file_error"] = f"{type(exc).__name__}: {exc}"
        return d
    finally:
        conn.close()


def query_cdp_requests(
    *,
    target: str = "",
    methods: list[str] | None = None,
    q: str = "",
    limit: int = 200,
    offset: int = 0,
) -> tuple[int, list[dict[str, Any]]]:
    wheres: list[str] = []
    params: list[Any] = []
    if target:
        wheres.append("scan_id IN (SELECT id FROM scans WHERE target_url = ?)")
        params.append(target)
    if methods:
        ph = ",".join("?" * len(methods))
        wheres.append(f"method IN ({ph})")
        params.extend(methods)
    if q:
        wheres.append("(request_url LIKE ? OR COALESCE(post_data,'') LIKE ?)")
        kw = f"%{q}%"
        params.extend([kw, kw])

    where_sql = " AND ".join(wheres) if wheres else "1=1"

    count_sql = f"SELECT COUNT(*) FROM cdp_requests WHERE {where_sql}"
    select_sql = (
        "SELECT id, method, request_url, request_path, response_status, response_size, "
        "COALESCE("
        "  json_extract(response_headers, '$.\"content-type\"'),"
        "  json_extract(response_headers, '$.\"Content-Type\"')"
        ") AS resp_ct "
        f"FROM cdp_requests WHERE {where_sql} "
        "ORDER BY id ASC LIMIT ? OFFSET ?"
    )

    conn = connect()
    try:
        total = conn.execute(count_sql, params).fetchone()[0]
        rows = conn.execute(select_sql, params + [limit, offset]).fetchall()
        items = [dict(r) for r in rows]
    finally:
        conn.close()
    return total, items


def get_cdp_request(req_id: int) -> dict[str, Any] | None:
    conn = connect()
    try:
        r = conn.execute("SELECT * FROM cdp_requests WHERE id=?", (req_id,)).fetchone()
        if not r:
            return None
        d = dict(r)

        d["frames"] = json.loads(d["frames_json"]) if d.get("frames_json") else []
        for k in ("request_headers", "response_headers"):
            d[k] = json.loads(d[k]) if d.get(k) else {}

        reps = conn.execute(
            "SELECT id, sent_method, sent_url, status, resp_ct, resp_len, time_ms, error, "
            "created_at FROM verifications WHERE cdp_request_id=? ORDER BY created_at DESC",
            (req_id,),
        ).fetchall()
        d["verifications"] = [dict(x) for x in reps]

        if not d.get("response_body") and d.get("response_file"):
            p = get_home() / "responses" / d["response_file"]
            if p.exists():
                try:
                    full_size = p.stat().st_size
                    raw = p.read_bytes()[
                        : DEFAULT_SETTINGS.storage.response_detail_limit
                    ]
                    d["response_body"] = raw.decode("utf-8", errors="replace")

                    if full_size > DEFAULT_SETTINGS.storage.response_detail_limit:
                        d["response_body_truncated"] = True
                        d["response_body_full_size"] = full_size
                except OSError as exc:
                    d["response_body_error"] = f"{type(exc).__name__}: {exc}"
        return d
    finally:
        conn.close()


def query_secrets(
    *,
    target: str = "",
    groups: list[str] | None = None,
    sensitive: bool | None = None,
    q: str = "",
    limit: int = 200,
    offset: int = 0,
) -> tuple[int, list[dict[str, Any]]]:
    wheres: list[str] = []
    params: list[Any] = []
    if target:
        wheres.append("scan_id IN (SELECT id FROM scans WHERE target_url = ?)")
        params.append(target)
    if groups:
        placeholders = ",".join("?" for _ in groups)
        wheres.append(f"rule_group IN ({placeholders})")
        params.extend(groups)
    if sensitive is not None:
        wheres.append("sensitive = ?")
        params.append(1 if sensitive else 0)
    if q:
        wheres.append("value LIKE ?")
        params.append(f"%{q}%")
    where_sql = " AND ".join(wheres) if wheres else "1=1"

    group_sql = (
        "SELECT s.target_url, sec.rule_group, sec.sensitive, sec.rule_id, sec.value, "
        "MIN(sec.id) AS id, COUNT(*) AS occurrence_count, "
        "COUNT(DISTINCT sec.source_js) AS source_count "
        "FROM secrets sec JOIN scans s ON s.id = sec.scan_id "
        f"WHERE {where_sql} "
        "GROUP BY s.target_url, sec.rule_group, sec.sensitive, sec.rule_id, sec.value"
    )

    count_sql = f"SELECT COUNT(*) FROM ({group_sql}) grouped"
    select_sql = (
        "WITH grouped AS (" + group_sql + ") "
        "SELECT sec.id, sec.scan_id, sec.rule_group, sec.sensitive, sec.rule_id, "
        "sec.value, sec.source_js, sec.line, sec.col_start, "
        "grouped.target_url, grouped.occurrence_count, grouped.source_count "
        "FROM grouped JOIN secrets sec ON sec.id = grouped.id "
        "ORDER BY grouped.occurrence_count DESC, sec.id DESC LIMIT ? OFFSET ?"
    )

    conn = connect()
    try:
        total = conn.execute(count_sql, params).fetchone()[0]
        rows = conn.execute(select_sql, params + [limit, offset]).fetchall()
        items = [dict(r) for r in rows]
    finally:
        conn.close()
    return total, items


def get_secret(secret_id: int) -> dict[str, Any] | None:
    conn = connect()
    try:
        r = conn.execute("SELECT * FROM secrets WHERE id=?", (secret_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["metadata"] = json.loads(d["metadata_json"]) if d.get("metadata_json") else {}

        src_rows = conn.execute(
            "SELECT s2.source_js, cnt.count AS count, s2.line, s2.col_start "
            "FROM (SELECT source_js, COUNT(*) AS count, MIN(id) AS rep_id FROM secrets "
            "      WHERE scan_id=? AND rule_id=? AND value=? GROUP BY source_js) cnt "
            "JOIN secrets s2 ON s2.id = cnt.rep_id "
            "ORDER BY cnt.count DESC, s2.source_js",
            (d["scan_id"], d["rule_id"], d["value"]),
        ).fetchall()
        d["sources"] = [dict(x) for x in src_rows]
        return d
    finally:
        conn.close()


def secret_facets(*, target: str = "") -> dict[str, dict[str, int]]:
    wheres: list[str] = []
    params: list[Any] = []
    if target:
        wheres.append("scan_id IN (SELECT id FROM scans WHERE target_url = ?)")
        params.append(target)
    where = " WHERE " + " AND ".join(wheres) if wheres else ""

    conn = connect()
    try:
        group_rows = conn.execute(
            f"SELECT rule_group, COUNT(*) AS n FROM secrets{where} "
            "GROUP BY rule_group ORDER BY n DESC, rule_group",
            params,
        ).fetchall()
        sens_rows = conn.execute(
            f"SELECT sensitive, COUNT(*) AS n FROM secrets{where} GROUP BY sensitive",
            params,
        ).fetchall()
        return {
            "groups": {r["rule_group"]: r["n"] for r in group_rows},
            "sensitive": {
                ("sensitive" if r["sensitive"] else "normal"): r["n"] for r in sens_rows
            },
        }
    finally:
        conn.close()


def query_targets() -> list[dict[str, Any]]:
    sql = """
        SELECT
            s.target_url,
            (SELECT COUNT(*) FROM api_resolutions WHERE scan_id = s.id) AS api_count,
            (SELECT COUNT(*) FROM verifications    WHERE scan_id = s.id) AS replay_count,
            s.started_at  AS last_scan_at,
            s.finished_at AS last_finished_at
        FROM scans s
        ORDER BY s.started_at DESC
    """
    conn = connect()
    try:
        rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def query_stats() -> dict[str, int]:
    sql = """
        SELECT
            COUNT(*)                                                    AS total,
            SUM(CASE WHEN status>=200 AND status<300 THEN 1 ELSE 0 END) AS s2xx,
            SUM(CASE WHEN status>=300 AND status<400 THEN 1 ELSE 0 END) AS s3xx,
            SUM(CASE WHEN status>=400 AND status<500 THEN 1 ELSE 0 END) AS s4xx,
            SUM(CASE WHEN status>=500              THEN 1 ELSE 0 END)   AS s5xx,
            SUM(CASE WHEN inference_tier='L0'      THEN 1 ELSE 0 END)   AS t_l0,
            SUM(CASE WHEN inference_tier='L1'      THEN 1 ELSE 0 END)   AS t_l1,
            SUM(CASE WHEN inference_tier='L2'      THEN 1 ELSE 0 END)   AS t_l2,
            SUM(CASE WHEN inference_tier='L3'      THEN 1 ELSE 0 END)   AS t_l3,
            SUM(CASE WHEN inference_tier='L4'      THEN 1 ELSE 0 END)   AS t_l4
        FROM verifications
        WHERE status IS NOT NULL
    """
    conn = connect()
    try:
        r = conn.execute(sql).fetchone()

        target_count = (
            conn.execute(
                "SELECT COUNT(DISTINCT target_url) AS n FROM scans"
            ).fetchone()["n"]
            or 0
        )

        return {
            "total": r["total"] or 0,
            "target_count": target_count,
            "s2xx": r["s2xx"] or 0,
            "s3xx": r["s3xx"] or 0,
            "s4xx": r["s4xx"] or 0,
            "s5xx": r["s5xx"] or 0,
            "t_l0": r["t_l0"] or 0,
            "t_l1": r["t_l1"] or 0,
            "t_l2": r["t_l2"] or 0,
            "t_l3": r["t_l3"] or 0,
            "t_l4": r["t_l4"] or 0,
        }
    finally:
        conn.close()


def query_domains(*, limit: int = 1000) -> list[dict[str, Any]]:
    sql = """
        SELECT
            domain,
            COUNT(*) AS replay_count
        FROM verifications
        WHERE status IS NOT NULL
    """
    sql += " GROUP BY domain ORDER BY domain ASC LIMIT ?"

    conn = connect()
    try:
        rows = conn.execute(sql, (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def response_path(filename: str) -> Path:
    return get_home() / "responses" / filename


def _replay_row_to_dict(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    for k in ("sent_query", "sent_body", "sent_headers", "resp_headers"):
        if d.get(k):
            d[k] = json.loads(d[k])
    return d

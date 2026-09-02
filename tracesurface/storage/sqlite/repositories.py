from __future__ import annotations

import json
import os
import sqlite3
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from tracesurface.config import DEFAULT_SETTINGS
from tracesurface.frozen import to_jsonable
from tracesurface.models import (
    CDPRequest,
    InferenceResult,
    ReplayRecord,
    ScanResult,
    ScanStatus,
    ScanSummary,
    SecretMatch,
)
from tracesurface.sources import remove_all_sources, remove_scan_sources
from tracesurface.storage.commands import (
    CreateScan,
    FinishScan,
    InferenceWriteResult,
    PurgeAll,
    PurgeTarget,
    SaveInference,
    StorageCommand,
)
from tracesurface.storage.sqlite.connection import (
    cdp_response_file,
    connect,
    get_home,
    response_file,
)
from tracesurface.urls import dedup_key

_IN_BATCH = 500
_COUNT_KEYS = (
    "scans",
    "api_sinks",
    "api_resolutions",
    "verifications",
    "resolution_evidence",
    "cdp_requests",
    "secrets",
    "files",
)


def domain_of(url: str) -> str:
    parsed = urlparse(url)
    return parsed.hostname or ""


class SQLiteWriteRepository:
    def __init__(self, conn: sqlite3.Connection | None = None) -> None:
        self._conn = conn

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = connect()
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def execute(self, command: StorageCommand) -> Any:
        if isinstance(command, CreateScan):
            return self.create_scan(command.target_url, command.wait_ms)
        if isinstance(command, PurgeTarget):
            return self.purge_target(command.target_url)
        if isinstance(command, PurgeAll):
            return self.purge_all()
        if isinstance(command, SaveInference):
            return self.save_inference(command.scan_id, command.inference)
        if isinstance(command, FinishScan):
            return self.finish_scan(
                command.scan_id,
                status=command.status,
                summary=command.summary,
            )
        raise TypeError(f"unsupported storage command: {type(command).__name__}")

    def create_scan(self, target_url: str, wait_ms: int) -> int:
        cur = self.conn.execute(
            "INSERT INTO scans(target_url, domain, started_at, wait_ms, status) "
            "VALUES(?,?,?,?,'running')",
            (target_url, domain_of(target_url), int(time.time()), wait_ms),
        )
        return cur.lastrowid or 0

    def finish_scan(
        self,
        scan_id: int,
        *,
        status: ScanStatus = "done",
        summary: ScanSummary | None = None,
    ) -> None:
        self.conn.execute(
            "UPDATE scans SET finished_at=?, status=?, js_count=?, ast_total=?, "
            "route_count=?, visited_route_count=?, productive_route_count=? WHERE id=?",
            (
                int(time.time()),
                status,
                summary.js_count if summary else 0,
                summary.ast_total if summary else 0,
                summary.route_count if summary else 0,
                summary.visited_route_count if summary else 0,
                summary.productive_route_count if summary else 0,
                scan_id,
            ),
        )

    def save_inference(
        self, scan_id: int, inference: InferenceResult
    ) -> InferenceWriteResult:
        result = inference.result
        conn = self.conn

        conn.execute("BEGIN IMMEDIATE")
        try:
            cdp_id_by_key = self._insert_cdp_requests(
                conn,
                scan_id,
                result.all_cdp_requests,
            )

            resolution_id_map = self._insert_sinks_and_resolutions(
                conn,
                scan_id,
                result,
                cdp_id_by_key,
            )

            self._insert_secrets(conn, scan_id, result.secrets)
            conn.execute("COMMIT")
            return InferenceWriteResult(
                resolution_ids=resolution_id_map,
                cdp_ids=cdp_id_by_key,
            )
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def save_replays_batch(self, records: list[ReplayRecord]) -> list[int]:
        if not records:
            return []

        replay_ids: list[int] = []
        conn = self.conn

        conn.execute("BEGIN IMMEDIATE")
        try:
            for rec in records:
                values, payload = _replay_row(rec)
                cur = conn.execute(
                    "INSERT INTO verifications(resolution_id, cdp_request_id, scan_id, domain, variant, "
                    "sent_url, sent_method, "
                    "sent_query, sent_body, sent_headers, status, resp_headers, resp_ct, resp_len, "
                    "resp_snippet, resp_file, time_ms, error, created_at, grade, "
                    "base_source, binding_rule, why_not_higher_tier) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    values,
                )
                replay_id = cur.lastrowid or 0
                replay_ids.append(replay_id)

                if payload is not None:
                    filename = _write_replay_payload(replay_id, payload)
                    conn.execute(
                        "UPDATE verifications SET resp_file=? WHERE id=?",
                        (filename, replay_id),
                    )

            conn.execute("COMMIT")
            return replay_ids
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def purge_target(self, target_url: str) -> dict[str, int]:
        counts = _empty_counts()
        conn = self.conn

        scan_ids = [
            row["id"]
            for row in conn.execute(
                "SELECT id FROM scans WHERE target_url = ?",
                (target_url,),
            ).fetchall()
        ]

        if not scan_ids:
            return counts

        replay_ids: list[int] = []
        cdp_spillover_ids: list[int] = []
        for i in range(0, len(scan_ids), _IN_BATCH):
            chunk = scan_ids[i : i + _IN_BATCH]
            ph = ",".join("?" * len(chunk))
            replay_ids.extend(
                row["id"]
                for row in conn.execute(
                    f"SELECT id FROM verifications WHERE scan_id IN ({ph})",
                    chunk,
                ).fetchall()
            )
            cdp_spillover_ids.extend(
                row["id"]
                for row in conn.execute(
                    f"SELECT id FROM cdp_requests WHERE scan_id IN ({ph}) "
                    f"AND response_file IS NOT NULL",
                    chunk,
                ).fetchall()
            )

        conn.execute("BEGIN IMMEDIATE")
        try:
            counts["resolution_evidence"] = _delete_in_batches(
                conn,
                "DELETE FROM resolution_evidence WHERE resolution_id IN "
                "(SELECT id FROM api_resolutions WHERE scan_id IN ({ph}))",
                scan_ids,
            )
            counts["verifications"] = _delete_in_batches(
                conn,
                "DELETE FROM verifications WHERE scan_id IN ({ph})",
                scan_ids,
            )
            counts["api_resolutions"] = _delete_in_batches(
                conn,
                "DELETE FROM api_resolutions WHERE scan_id IN ({ph})",
                scan_ids,
            )
            counts["api_sinks"] = _delete_in_batches(
                conn,
                "DELETE FROM api_sinks WHERE scan_id IN ({ph})",
                scan_ids,
            )
            counts["cdp_requests"] = _delete_in_batches(
                conn,
                "DELETE FROM cdp_requests WHERE scan_id IN ({ph})",
                scan_ids,
            )
            counts["secrets"] = _delete_in_batches(
                conn,
                "DELETE FROM secrets WHERE scan_id IN ({ph})",
                scan_ids,
            )

            cur = conn.execute("DELETE FROM scans WHERE target_url = ?", (target_url,))
            counts["scans"] = cur.rowcount
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        counts["files"] += _unlink_files(
            [response_file(replay_id) for replay_id in replay_ids]
            + [cdp_response_file(req_id) for req_id in cdp_spillover_ids],
        )

        for scan_id in scan_ids:
            counts["files"] += remove_scan_sources(scan_id)
        return counts

    def purge_all(self) -> dict[str, int]:
        counts = _empty_counts()

        resp_dir = get_home() / "responses"
        response_files = list(resp_dir.iterdir()) if resp_dir.exists() else []

        conn = self.conn

        conn.execute("BEGIN IMMEDIATE")
        try:
            cur = conn.execute("DELETE FROM resolution_evidence")
            counts["resolution_evidence"] = cur.rowcount
            cur = conn.execute("DELETE FROM verifications")
            counts["verifications"] = cur.rowcount
            cur = conn.execute("DELETE FROM api_resolutions")
            counts["api_resolutions"] = cur.rowcount
            cur = conn.execute("DELETE FROM api_sinks")
            counts["api_sinks"] = cur.rowcount
            cur = conn.execute("DELETE FROM cdp_requests")
            counts["cdp_requests"] = cur.rowcount
            cur = conn.execute("DELETE FROM secrets")
            counts["secrets"] = cur.rowcount
            cur = conn.execute("DELETE FROM scans")
            counts["scans"] = cur.rowcount
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        counts["files"] += _unlink_files(response_files)
        counts["files"] += remove_all_sources()
        return counts

    def _insert_sinks_and_resolutions(
        self,
        conn: sqlite3.Connection,
        scan_id: int,
        result: ScanResult,
        cdp_id_by_key: dict[str, int],
    ) -> dict[int, int]:
        sink_id_by_key: dict[tuple[Any, ...], int] = {}
        index_to_resolution: dict[int, int] = {}
        for idx, m in enumerate(result.apis):
            a = m.fact

            params_json = json.dumps(
                [
                    {"name": p.name, "location": p.location, "default": p.default}
                    for p in a.params
                ],
                ensure_ascii=False,
            )

            sink_key = (
                a.method,
                a.path,
                a.location.url,
                a.location.line,
                a.location.col_start,
                a.pattern,
                params_json,
            )

            sink_id = sink_id_by_key.get(sink_key)
            if sink_id is None:
                cur = conn.execute(
                    "INSERT INTO api_sinks(scan_id, method, ast_path, source_js, "
                    "line, col_start, pattern, params_json) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        scan_id,
                        a.method,
                        a.path,
                        a.location.url,
                        a.location.line,
                        a.location.col_start,
                        a.pattern,
                        params_json,
                    ),
                )
                sink_id = cur.lastrowid or 0
                sink_id_by_key[sink_key] = sink_id

            cur = conn.execute(
                "INSERT INTO api_resolutions(sink_id, scan_id, full_url, grade, "
                "base_source, binding_rule, why_not_higher_tier) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    sink_id,
                    scan_id,
                    m.full_url or a.path,
                    m.grade,
                    m.base_source,
                    m.binding_rule,
                    m.why_not_higher_tier,
                ),
            )
            resolution_id = cur.lastrowid or 0
            index_to_resolution[idx] = resolution_id

            if m.grade == "runtime" and m.confirmed is not None:
                cdp_id = cdp_id_by_key.get(
                    dedup_key(m.confirmed.method, m.confirmed.url)
                )

                if cdp_id is not None:
                    conn.execute(
                        "INSERT INTO resolution_evidence(resolution_id, "
                        "evidence_kind, evidence_id, role) VALUES(?,?,?,?)",
                        (resolution_id, "cdp_request", cdp_id, "confirmed_by"),
                    )
        return index_to_resolution

    def _insert_cdp_requests(
        self,
        conn: sqlite3.Connection,
        scan_id: int,
        cdp_requests: Sequence[CDPRequest],
    ) -> dict[str, int]:
        if not cdp_requests:
            return {}
        id_by_key: dict[str, int] = {}
        spillovers: list[tuple[int, str]] = []
        for r in cdp_requests:
            inline_body: str | None = None
            need_file = False
            if r.response_body:
                blen = len(r.response_body.encode("utf-8", errors="replace"))
                if blen <= DEFAULT_SETTINGS.storage.response_inline_limit:
                    inline_body = r.response_body
                else:
                    need_file = True

            cur = conn.execute(
                "INSERT INTO cdp_requests(scan_id, method, request_url, request_path, "
                "query_string, post_data, content_type, frames_json, "
                "request_headers, response_status, response_headers, "
                "response_body, response_file, response_size) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    scan_id,
                    r.method,
                    r.request_url,
                    r.request_path,
                    r.query_string or None,
                    r.post_data or None,
                    r.content_type or None,
                    json.dumps(
                        [
                            {
                                "url": f.url,
                                "func": f.func,
                                "line": f.line,
                                "col": f.col,
                            }
                            for f in r.frames
                        ],
                        ensure_ascii=False,
                    ),
                    json.dumps(r.request_headers, ensure_ascii=False)
                    if r.request_headers
                    else None,
                    r.response_status,
                    json.dumps(r.response_headers, ensure_ascii=False)
                    if r.response_headers
                    else None,
                    inline_body,
                    None,
                    r.response_size or None,
                ),
            )

            id_by_key.setdefault(r.dedup_key, cur.lastrowid or 0)
            if need_file:
                spillovers.append((cur.lastrowid or 0, r.response_body))

        for req_id, body in spillovers:
            filename = _write_cdp_payload(req_id, body)
            conn.execute(
                "UPDATE cdp_requests SET response_file=? WHERE id=?",
                (filename, req_id),
            )
        return id_by_key

    def _insert_secrets(
        self,
        conn: sqlite3.Connection,
        scan_id: int,
        secrets: Sequence[SecretMatch],
    ) -> None:
        if not secrets:
            return
        rows = [
            (
                scan_id,
                s.rule_id,
                s.rule_group,
                1 if s.sensitive else 0,
                s.value,
                s.source_js,
                s.line,
                s.col_start,
                s.context_before or None,
                s.context_line or None,
                s.context_after or None,
                json.dumps(to_jsonable(s.metadata), ensure_ascii=False)
                if s.metadata
                else None,
            )
            for s in secrets
        ]
        conn.executemany(
            "INSERT INTO secrets(scan_id, rule_id, rule_group, sensitive, value, source_js, "
            "line, col_start, context_before, context_line, context_after, "
            "metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )


def key_counts(rows: list[sqlite3.Row]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        key = dedup_key(row["sent_method"], row["sent_url"])
        counts[key] = counts.get(key, 0) + 1
    return counts


def load_replayed_key_counts() -> dict[str, int]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT sent_method, sent_url FROM verifications"
        ).fetchall()
        return key_counts(rows)
    finally:
        conn.close()


def load_replayed_key_counts_for_target(target_url: str) -> dict[str, int]:
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT r.sent_method, r.sent_url
            FROM verifications r
            JOIN scans s ON s.id = r.scan_id
            WHERE s.target_url = ?
            """,
            (target_url,),
        ).fetchall()
        return key_counts(rows)
    finally:
        conn.close()


def _empty_counts() -> dict[str, int]:
    return {key: 0 for key in _COUNT_KEYS}


def _unlink_files(paths: Sequence[Path]) -> int:
    removed = 0
    for path in paths:
        if path.is_file():
            path.unlink()
            removed += 1
    return removed


def _delete_in_batches(
    conn: sqlite3.Connection,
    sql_template: str,
    ids: list[int],
) -> int:
    total = 0

    for i in range(0, len(ids), _IN_BATCH):
        chunk = ids[i : i + _IN_BATCH]
        placeholders = ",".join("?" * len(chunk))
        cur = conn.execute(sql_template.format(ph=placeholders), chunk)
        total += cur.rowcount
    return total


def _write_replay_payload(replay_id: int, payload: str | bytes) -> str:
    final_path = response_file(replay_id)

    tmp_path = final_path.with_suffix(final_path.suffix + ".tmp")
    if isinstance(payload, bytes):
        tmp_path.write_bytes(payload)
    else:
        tmp_path.write_text(
            payload,
            encoding="utf-8",
            errors="replace",
        )
    os.replace(tmp_path, final_path)
    return f"{replay_id}.bin"


def _replay_row(rec: ReplayRecord) -> tuple[tuple[Any, ...], str | bytes | None]:
    resp_len = rec.resp_len
    resp_body = rec.resp_body
    resp_bytes = rec.resp_bytes

    if resp_bytes is not None:
        resp_len = max(resp_len, len(resp_bytes))
        resp_bytes = resp_bytes[: DEFAULT_SETTINGS.storage.response_detail_limit]
    elif resp_body is not None:
        raw_body = resp_body.encode("utf-8", errors="replace")
        resp_len = max(resp_len, len(raw_body))

        if len(raw_body) > DEFAULT_SETTINGS.storage.response_detail_limit:
            resp_body = raw_body[
                : DEFAULT_SETTINGS.storage.response_detail_limit
            ].decode("utf-8", errors="replace")

    inline_body: str | None = None
    payload: str | bytes | None = None
    if resp_bytes is not None:
        payload = resp_bytes
    elif resp_body is not None:
        if (
            len(resp_body.encode("utf-8", errors="replace"))
            <= DEFAULT_SETTINGS.storage.response_inline_limit
        ):
            inline_body = resp_body
        else:
            payload = resp_body

    return (
        (
            rec.resolution_id,
            rec.cdp_request_id,
            rec.scan_id,
            rec.domain,
            rec.variant,
            rec.sent_url,
            rec.sent_method,
            rec.sent_query,
            rec.sent_body,
            rec.sent_headers,
            rec.status,
            rec.resp_headers,
            rec.resp_ct,
            resp_len,
            inline_body,
            None,
            rec.time_ms,
            rec.error,
            int(time.time()),
            rec.grade,
            rec.base_source,
            rec.binding_rule,
            rec.why_not_higher_tier,
        ),
        payload,
    )


def _write_cdp_payload(req_id: int, body: str) -> str:
    final_path = cdp_response_file(req_id)

    tmp_path = final_path.with_suffix(final_path.suffix + ".tmp")
    tmp_path.write_text(body, encoding="utf-8", errors="replace")
    os.replace(tmp_path, final_path)
    return f"cdp_{req_id}.bin"

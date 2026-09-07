from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from tracesurface.config import DEFAULT_SETTINGS

SCHEMA_FILE = Path(__file__).parent / "schema.sql"


def get_home() -> Path:
    base = os.environ.get("TRACESURFACE_HOME")
    home = Path(base).expanduser() if base else Path.home() / ".tracesurface"

    (home / "responses").mkdir(parents=True, exist_ok=True)
    (home / "logs").mkdir(parents=True, exist_ok=True)
    return home


def db_path() -> Path:
    return get_home() / "tracesurface.db"


def response_file(replay_id: int) -> Path:
    return get_home() / "responses" / f"{replay_id}.bin"


def cdp_response_file(req_id: int) -> Path:
    return get_home() / "responses" / f"cdp_{req_id}.bin"


def auth_path() -> Path:
    return get_home() / "auth.json"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(
        db_path(),
        isolation_level=None,
        timeout=DEFAULT_SETTINGS.storage.sqlite_busy_timeout_s,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init() -> None:
    """确保数据目录与库就绪：建目录、连接、按 schema.sql 幂等建表。"""
    get_home()
    conn = connect()
    try:
        conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
    finally:
        conn.close()

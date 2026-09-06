from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

_ABSOLUTE_URL_RE = re.compile(r"^([a-z][a-z\d+\-.]*:)?//", re.IGNORECASE)


def is_absolute_url(url: str) -> bool:
    return bool(url) and _ABSOLUTE_URL_RE.match(url) is not None


def combine_urls(base: str, relative: str) -> str:
    if is_absolute_url(relative):
        return relative

    if not base:
        return relative
    base = base.rstrip("/")

    if not relative:
        return base

    return f"{base}/{relative.lstrip('/')}"


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""


def canonical_origin_key(url: str) -> tuple[str, int | None]:
    """归一化源点（剥离 www 前缀、默认端口归零），用于跨站跳转判定。"""
    parts = urlsplit(url)
    port = parts.port
    default_port = (
        443 if parts.scheme == "https" else 80 if parts.scheme == "http" else None
    )
    if port == default_port:
        port = None
    host = (parts.hostname or "").lower().strip(".")
    return (host[4:] if host.startswith("www.") else host, port)


def host_of(url: str) -> str:
    netloc = urlsplit(url).netloc or url
    return netloc.split("@")[-1]


def dedup_key(method: str, url: str) -> str:
    m = (method or "GET").upper()
    parts = urlsplit(url)
    path = parts.path or ""

    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]

    if parts.scheme and parts.netloc:
        norm = urlunsplit((parts.scheme, parts.netloc, path, "", ""))
    else:
        norm = path
    return f"{m} {norm}"

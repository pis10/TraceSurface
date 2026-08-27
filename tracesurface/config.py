from __future__ import annotations

from dataclasses import dataclass, field

from tracesurface.policies import DEFAULT_RESPONSE_BODY_CAPTURE_LIMIT

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


@dataclass(frozen=True, slots=True)
class WorkerSettings:
    site_concurrency: int = 3
    cpu_workers: int = 1


@dataclass(frozen=True, slots=True)
class BrowserSettings:
    user_agent: str = DEFAULT_USER_AGENT
    ignore_https_errors: bool = True


@dataclass(frozen=True, slots=True)
class HTTPSettings:
    concurrency: int = 10
    timeout_s: float = 10
    tls_verify: bool = False


@dataclass(frozen=True, slots=True)
class CollectionSettings:
    redirect_guard_enabled: bool = True
    bootstrap_wait_ms: int = 7000
    bootstrap_goto_timeout_ms: int = 10000
    cdp_stack_depth: int = 128
    response_body_capture_limit: int = DEFAULT_RESPONSE_BODY_CAPTURE_LIMIT

    discovery_max_rounds: int = 3
    js_download_timeout_s: float = 10

    chunk_brute_force_max: int = 1000
    chunk_eval_timeout_ms: int = 30_000

    mfe_validate_timeout_s: float = 3
    mfe_harvest_max_ids_per_body: int = 50
    mfe_max_external_ids: int = 150

    route_total_timeout_ms: int = 5000


@dataclass(frozen=True, slots=True)
class RouteMaterializationSettings:
    path_fill: str = "1"
    param_fill: object = 0


@dataclass(frozen=True, slots=True)
class ReplaySettings:
    concurrency: int = 5
    timeout_s: float = 10.0
    max_redirects: int = 3
    response_body_capture_limit: int = DEFAULT_RESPONSE_BODY_CAPTURE_LIMIT


@dataclass(frozen=True, slots=True)
class StorageSettings:
    sqlite_busy_timeout_s: float = 10.0
    response_inline_limit: int = 64 * 1024
    response_detail_limit: int = 1024 * 1024


@dataclass(frozen=True, slots=True)
class TraceSurfaceSettings:
    workers: WorkerSettings = field(default_factory=WorkerSettings)
    browser: BrowserSettings = field(default_factory=BrowserSettings)
    http: HTTPSettings = field(default_factory=HTTPSettings)
    collection: CollectionSettings = field(default_factory=CollectionSettings)
    route_materialization: RouteMaterializationSettings = field(
        default_factory=RouteMaterializationSettings,
    )
    replay: ReplaySettings = field(default_factory=ReplaySettings)
    storage: StorageSettings = field(default_factory=StorageSettings)


DEFAULT_SETTINGS = TraceSurfaceSettings()

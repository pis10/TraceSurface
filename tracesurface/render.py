from __future__ import annotations

import time
from collections.abc import Sequence

from tracesurface import ui
from tracesurface.models import ReplayStats, ScanSummary
from tracesurface.pipeline.messages import BatchScanOutcome, ScanProgress, StageFailure
from tracesurface.urls import host_of

__all__ = [
    "render_scan_header",
    "record_scan_success",
    "record_scan_skipped",
    "record_scan_failure",
    "render_summary",
]

_STATUS_STYLE = {
    "s2xx": ("2xx", "green"),
    "s3xx": ("3xx", "cyan"),
    "s4xx": ("4xx", "yellow"),
    "s5xx": ("5xx", "red"),
    "serr": ("err", "dim"),
}

_STAGE_CN = {
    "collect": "采集",
    "extraction": "提取",
    "inference": "推导",
    "storage": "落库",
}

_CARD_WIDTH = 56
_HOST_MAX = 40


def render_scan_header(
    *,
    urls: Sequence[str],
    collection_workers: int,
    extraction_workers: int,
    inference_workers: int,
    replay_concurrency: int,
    replay_opt: bool,
    auth_label: str,
) -> None:
    ui.brand("前端 API 发现")

    target = _host(urls[0]) if len(urls) == 1 else f"{len(urls)} 个站点"

    replay_part = _metric("发包", replay_concurrency if replay_opt else "关闭")
    pipeline = ui.join_dot(
        [
            _metric("采集", collection_workers),
            _metric("提取", extraction_workers),
            _metric("推导", inference_workers),
            replay_part,
        ]
    )
    ui.kv_block(
        [
            ("目标", target),
            ("流水线", pipeline),
            ("登录态", auth_label),
        ]
    )


def record_scan_success(
    p: ScanProgress,
    stats: ReplayStats,
    *,
    do_replay: bool,
) -> None:
    index_label = f"{p.index:0{len(str(p.total))}d}/{p.total}"
    _status_line(
        ui.SYM.ok, "tracesurface.ok", index_label, _host(p.job.target_url), p.elapsed
    )
    _warning_details(p.summary)

    metrics = [
        _metric("调用点", p.summary.ast_total),
        f"[tracesurface.dim]已确认[/] [green]{p.summary.confirmed_count}[/]",
        _metric("仅CDP", p.summary.cdp_only_count),
        f"[tracesurface.dim]分层[/] L1 {p.summary.tier_l1} / "
        f"L2 {p.summary.tier_l2} / L3 {p.summary.tier_l3} / L4 {p.summary.tier_l4}",
    ]

    if p.summary.secret_count:
        metrics.append(f"[tracesurface.dim]敏感[/] [red]{p.summary.secret_count}[/]")
    _detail(ui.join_dot(metrics))

    if p.summary.route_count:
        _detail(
            ui.join_dot(
                [
                    _metric("路由 发现", p.summary.route_count),
                    _metric("访问", p.summary.visited_route_count),
                    _metric("有效", p.summary.productive_route_count),
                ]
            )
        )

    if do_replay and stats["total"]:
        _detail(_replay_metrics(stats))


def record_scan_skipped(p: ScanProgress) -> None:
    index_label = f"{p.index:0{len(str(p.total))}d}/{p.total}"
    _status_line(
        ui.SYM.warn,
        "tracesurface.warn",
        index_label,
        _host(p.job.target_url),
        p.elapsed,
    )
    _warning_details(p.summary)


def record_scan_failure(i: int, total: int, item: StageFailure) -> None:
    index_label = f"{i:0{len(str(total))}d}/{total}"

    elapsed = time.perf_counter() - item.started_at
    _status_line(
        ui.SYM.fail, "tracesurface.fail", index_label, _host(item.url), elapsed
    )

    stage_cn = _STAGE_CN.get(item.stage, item.stage)

    detail = f"{type(item.error).__name__}: {item.error}"
    if len(detail) > 100:
        detail = detail[:99] + "…"
    _detail(
        f"[tracesurface.fail]{ui.escape(stage_cn)}失败[/] "
        f"[tracesurface.dim]{ui.SYM.sep}[/] {ui.escape(detail)}"
    )


def render_summary(
    results: list[BatchScanOutcome],
    elapsed: float,
    *,
    do_replay: bool,
) -> None:
    total = len(results)
    ok = sum(1 for r in results if r.ok)
    skipped = sum(1 for r in results if r.skipped)

    failed = total - ok - skipped

    summaries = [r.summary for r in results if r.summary is not None]

    sum_ast = sum(s.ast_total for s in summaries)
    sum_conf = sum(s.confirmed_count for s in summaries)
    sum_cdp_only = sum(s.cdp_only_count for s in summaries)
    l1 = sum(s.tier_l1 for s in summaries)
    l2 = sum(s.tier_l2 for s in summaries)
    l3 = sum(s.tier_l3 for s in summaries)
    l4 = sum(s.tier_l4 for s in summaries)
    secrets = sum(s.secret_count for s in summaries)

    site_value = f"{ok}/{total} 成功"
    if skipped:
        site_value += f" [tracesurface.dim]{ui.SYM.sep}[/] [yellow]{skipped} 跳过[/]"
    if failed:
        site_value += f" [tracesurface.dim]{ui.SYM.sep}[/] [red]{failed} 失败[/]"

    rows: list[tuple[str, str]] = [
        ("站点", site_value),
        (
            "调用点",
            ui.join_dot(
                [
                    _metric("合计", sum_ast),
                    f"[tracesurface.dim]已确认[/] [green]{sum_conf}[/]",
                    _metric("仅CDP", sum_cdp_only),
                ]
            ),
        ),
        ("分层", ui.join_dot([f"L1 {l1}", f"L2 {l2}", f"L3 {l3}", f"L4 {l4}"])),
    ]

    if do_replay:
        agg = _aggregate_stats(results)
        if agg["total"]:
            rows.append(("发包", _replay_metrics(agg)))

    if secrets:
        rows.append(
            (
                "敏感",
                f"{secrets} 命中[tracesurface.dim]（详情见 tracesurface serve "
                f"{ui.SYM.arrow} Secrets）[/]",
            )
        )

    rows.append(("耗时", f"{elapsed:.1f}s"))

    ui.section("完成")
    ui.kv_block(rows)


def _host(url: str) -> str:
    host = host_of(url)

    if len(host) > _HOST_MAX:
        host = host[: _HOST_MAX - 1] + "…"
    return host


def _metric(label: str, value: object) -> str:
    return f"[tracesurface.dim]{label}[/] {value}"


def _replay_metrics(stats: ReplayStats, *, label: str = "发包") -> str:
    parts = [_metric(label, stats["total"])]
    for key, (name, color) in _STATUS_STYLE.items():
        parts.append(f"[tracesurface.dim]{name}[/] [{color}]{stats[key]}[/]")
    return ui.join_dot(parts)


def _status_line(
    symbol: str, symbol_style: str, index_label: str, host: str, elapsed: float
) -> None:
    plain_left = f"{symbol} [{index_label}] {host}"
    right = f"{elapsed:.1f}s"
    gap = max(1, _CARD_WIDTH - len(plain_left) - len(right))

    line = (
        f"  [{symbol_style}]{symbol}[/] "
        f"[tracesurface.dim][{index_label}][/] {host}"
        f"{' ' * gap}[tracesurface.dim]{right}[/]"
    )
    ui.console.print(line)


def _detail(text: str) -> None:
    ui.console.print(f"     {text}")


def _warning_details(summary: ScanSummary) -> None:
    seen: set[tuple[str, str]] = set()
    for warning in summary.warnings:
        key = (warning.code, warning.message)
        if key in seen:
            continue
        seen.add(key)

        _detail(f"[tracesurface.warn]警告[/] {ui.escape(warning.message)}")


def _aggregate_stats(results: list[BatchScanOutcome]) -> ReplayStats:
    agg: ReplayStats = {
        "total": 0,
        "s2xx": 0,
        "s3xx": 0,
        "s4xx": 0,
        "s5xx": 0,
        "serr": 0,
    }
    for r in results:
        if not r.stats:
            continue

        for key in agg:
            agg[key] += r.stats[key]
    return agg

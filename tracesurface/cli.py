from __future__ import annotations

import asyncio
import time
from functools import partial
from pathlib import Path
from typing import Any

import typer

from tracesurface import __version__, ui
from tracesurface.auth import (
    auth_state_age,
    load_auth_state,
    save_login_state,
)
from tracesurface.config import DEFAULT_SETTINGS
from tracesurface.pipeline.messages import ScanOutput
from tracesurface.pipeline.runner import PipelineRunner, ScanRequest
from tracesurface.render import (
    record_scan_failure,
    record_scan_skipped,
    record_scan_success,
    render_scan_header,
    render_summary,
)
from tracesurface.server.run import run_report_server
from tracesurface.storage.sqlite.connection import auth_path, db_path

_APP_HELP = """前端 API 发现与验证工具

自动发现 SPA / 微前端站点调用的 API 接口，并发包验证可达性。
适用于 API 资产盘点与未授权 / 弱鉴权探测。
"""

app = typer.Typer(
    help=_APP_HELP,
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _version_callback(value: bool) -> None:
    if value:
        ui.console.print(
            f"  [tracesurface.brand]{ui.SYM.brand} TraceSurface v{__version__}[/]"
        )
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        None,
        "--version",
        "-V",
        is_eager=True,
        callback=_version_callback,
        help="显示版本号",
    ),
) -> None:
    pass


def _resolve_auth(
    explicit_path: Path | None,
    no_auth: bool,
) -> tuple[dict[str, Any] | None, str]:
    if no_auth:
        return None, "已禁用（--no-auth）"

    if explicit_path is not None:
        if not explicit_path.exists():
            ui.abort(f"auth 文件不存在：{explicit_path}")
        path = explicit_path
        label_path = str(path)
    else:
        path = auth_path()
        if not path.exists():
            return None, "未配置（tracesurface login 可启用）"
        label_path = "~/.tracesurface/auth.json"

    try:
        state = load_auth_state(path)
    except Exception as e:
        ui.abort(f"无法读取 auth 文件 {path}：{e}")

    age = auth_state_age(path)
    return state, f"{ui.escape(label_path)} [tracesurface.dim]{ui.SYM.sep}[/] {age}"


def _read_targets(file: Path | None, url: str | None) -> list[str]:
    if file:
        if not file.exists():
            ui.abort(f"文件不存在：{file}")

        urls = list(
            dict.fromkeys(
                line.strip()
                for line in file.read_text(encoding="utf-8-sig").splitlines()
                if line.strip() and not line.strip().startswith("#")
            )
        )
    else:
        assert url is not None
        urls = [url]

    if not urls:
        ui.abort("没有有效的 URL")
    return urls


@app.command(short_help="扫描站点，发现 API 并发包验证")
def scan(
    url: str | None = typer.Argument(
        None, help="要扫描的站点 URL", rich_help_panel="目标"
    ),
    file: Path | None = typer.Option(
        None,
        "--file",
        "-f",
        help="从文件批量读取 URL（每行一个，# 开头为注释）",
        rich_help_panel="目标",
    ),
    site_concurrency: int = typer.Option(
        DEFAULT_SETTINGS.workers.collection_workers,
        "--sites",
        "-s",
        min=1,
        max=15,
        help=f"同时扫描的站点数（默认 {DEFAULT_SETTINGS.workers.collection_workers}，最多 15）",
        rich_help_panel="并发",
    ),
    replay_concurrency: int = typer.Option(
        DEFAULT_SETTINGS.replay.request_concurrency,
        "--rate",
        "-r",
        min=1,
        max=50,
        help=(
            f"每个站点的发包并发数（单目标不超此值，避免触发 WAF；默认 "
            f"{DEFAULT_SETTINGS.replay.request_concurrency}，最多 50）"
        ),
        rich_help_panel="并发",
    ),
    replay_opt: bool = typer.Option(
        True,
        "--replay/--no-replay",
        help="是否扫描后自动发包验证（默认开启）",
        rich_help_panel="发包",
    ),
    allow_destructive: bool = typer.Option(
        False,
        "--allow-destructive",
        help="放行破坏性方法（DELETE/PUT/PATCH/HEAD/OPTIONS），默认仅发 GET/POST",
        rich_help_panel="发包",
    ),
    auth: Path | None = typer.Option(
        None,
        "--auth",
        help="指定登录态文件（默认 ~/.tracesurface/auth.json）",
        rich_help_panel="登录态",
    ),
    no_auth: bool = typer.Option(
        False,
        "--no-auth",
        help="本次扫描不加载任何登录态（强制无登录态探测）",
        rich_help_panel="登录态",
    ),
    headed: bool = typer.Option(
        False,
        "--headed",
        help="显示浏览器窗口，可手动点按触发更多接口（批量慎用）",
        rich_help_panel="调试",
    ),
    wait_ms: int = typer.Option(
        DEFAULT_SETTINGS.collection.bootstrap_wait_ms,
        "--wait-ms",
        min=0,
        help=(
            f"首屏观察时长，毫秒（默认 {DEFAULT_SETTINGS.collection.bootstrap_wait_ms}；"
            "--headed 下会等满整窗供手动操作）"
        ),
        rich_help_panel="调试",
    ),
    extraction_workers: int = typer.Option(
        DEFAULT_SETTINGS.workers.extraction_workers,
        "--extract-workers",
        min=1,
        max=8,
        help=(
            f"提取进程数（AST / inline / secrets，默认 "
            f"{DEFAULT_SETTINGS.workers.extraction_workers}，最多 8）"
        ),
        rich_help_panel="高级",
    ),
    inference_workers: int = typer.Option(
        DEFAULT_SETTINGS.workers.inference_workers,
        "--infer-workers",
        min=1,
        max=8,
        help=(
            f"推导进程数（CDP 对齐 / baseURL / L1–L3，默认 "
            f"{DEFAULT_SETTINGS.workers.inference_workers}，最多 8）"
        ),
        rich_help_panel="高级",
    ),
    replay_workers: int = typer.Option(
        DEFAULT_SETTINGS.workers.replay_workers,
        "--replay-sites",
        min=1,
        max=15,
        help=(
            f"同时发包的站点数（总出站并发 = 本值 × -r；默认 "
            f"{DEFAULT_SETTINGS.workers.replay_workers}，最多 15）"
        ),
        rich_help_panel="高级",
    ),
) -> None:
    if auth is not None and no_auth:
        ui.abort("--auth 与 --no-auth 不能同时指定")
    if url and file:
        ui.abort("不能同时指定 URL 与 --file")

    if not url and not file:
        import click

        ui.console.print(click.get_current_context().get_help())
        raise typer.Exit(0)

    urls = _read_targets(file, url)

    collection_workers = min(site_concurrency, len(urls))
    auth_state, auth_label = _resolve_auth(auth, no_auth)

    render_scan_header(
        urls=urls,
        collection_workers=collection_workers,
        extraction_workers=extraction_workers,
        inference_workers=inference_workers,
        replay_concurrency=replay_concurrency,
        replay_opt=replay_opt,
        auth_label=auth_label,
    )

    if allow_destructive and replay_opt:
        ui.warn(
            "已放行破坏性方法 [tracesurface.dim]·[/] "
            "DELETE/PUT/PATCH/HEAD/OPTIONS 也会发包，请确认授权范围"
        )

    if headed:
        wait_s = max(0, wait_ms) / 1000
        ui.notice(
            f"浏览器将打开并等待约 {wait_s:.0f}s，"
            "可手动点菜单/按钮触发更多接口（CDP 被动采集）"
        )

    ui.configure_logging()
    t0 = time.perf_counter()

    scan_output = ScanOutput(
        success=partial(record_scan_success, do_replay=replay_opt),
        skipped=record_scan_skipped,
        failure=record_scan_failure,
    )

    results = asyncio.run(
        PipelineRunner().run(
            ScanRequest(
                urls=tuple(urls),
                wait_ms=wait_ms,
                site_concurrency=collection_workers,
                replay_concurrency=replay_concurrency,
                do_replay=replay_opt,
                extraction_workers=extraction_workers,
                inference_workers=inference_workers,
                replay_workers=replay_workers,
                auth_state=auth_state,
                headed=headed,
                allow_destructive=allow_destructive,
                output=scan_output,
            )
        )
    )

    render_summary(results, time.perf_counter() - t0, do_replay=replay_opt)


@app.command(short_help="登录目标站点并保存登录态，供后续扫描复用")
def login(
    url: str | None = typer.Argument(
        None,
        help="登录起始 URL（不传则浏览器空白启动，自行输入地址）",
    ),
    output: Path | None = typer.Option(
        None,
        "-o",
        "--output",
        help="登录态保存路径（默认 ~/.tracesurface/auth.json）",
    ),
) -> None:
    out_path = output if output is not None else auth_path()
    ui.brand("登录态采集")

    def warn_goto(exc: Exception) -> None:
        ui.warn(f"打开页面失败（{ui.escape(str(exc))}），浏览器仍打开，可手动输入地址")

    def notice_ready() -> None:
        ui.notice("浏览器已打开，请完成登录（可访问多个站点，登录态一并保存）")
        ui.console.print(
            f"    [tracesurface.dim]完成后回终端按 Enter 保存 {ui.SYM.sep} Ctrl+C 取消[/]"
        )

    try:
        asyncio.run(
            save_login_state(
                url=url,
                output_path=out_path,
                notice=notice_ready,
                warn=warn_goto,
            )
        )
    except KeyboardInterrupt:
        ui.warn("已取消，未保存登录态")
        raise typer.Exit(130)

    ui.success(
        f"已保存登录态 [tracesurface.dim]{ui.SYM.arrow}[/] {ui.escape(str(out_path))}"
    )


@app.command(short_help="下载扫描所需的 Chromium 浏览器（仅首次需要）")
def install_browser() -> None:
    import subprocess
    import sys

    ui.brand("浏览器安装")
    ui.notice("下载 Playwright Chromium，仅在首次使用或升级 Playwright 后需要")

    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"], check=True
        )
    except subprocess.CalledProcessError as e:
        ui.abort(f"Chromium 下载失败（exit {e.returncode}），请检查网络后重试")

    ui.success("Chromium 已就绪")


@app.command(short_help="启动本地报告站，浏览扫描结果")
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="绑定地址"),
    port: int = typer.Option(8765, "--port", help="端口"),
    reload: bool = typer.Option(False, "--reload", help="开发模式热重载"),
) -> None:
    ui.brand("报告服务")
    ui.kv_block(
        [
            ("地址", ui.escape(f"http://{host}:{port}")),
            ("数据库", ui.escape(str(db_path()))),
        ]
    )
    ui.notice("服务已启动，按 Ctrl+C 停止")
    run_report_server(host=host, port=port, reload=reload)

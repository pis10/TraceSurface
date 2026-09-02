
import { ArrowDown, ArrowUp, ChevronsUpDown, Clipboard, ExternalLink, FileText, Link2, Terminal, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { BodyBlock } from "@/components/shared/BodyBlock";
import { DetailPanel } from "@/components/shared/DetailPanel";
import { DetailTabs } from "@/components/shared/DetailTabs";
import { EmptyState } from "@/components/shared/EmptyState";
import { TableSkeleton } from "@/components/shared/TableSkeleton";
import { HeaderGrid } from "@/components/shared/HeaderGrid";
import { KvGrid } from "@/components/shared/KvGrid";
import { Pager } from "@/components/shared/Pager";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { bucketFromStatus, fmtBytes, fmtRelTime, GRADE_HINT, GRADE_LABEL } from "@/lib/format";
import { parseSearchPrefix, isFilterAtDefault, type FilterState, type SortState } from "@/lib/filters";
import { type HighValueState } from "@/lib/high-value";
import { requestToCurl, requestToRawHttp } from "@/lib/request";
import { cn, safeJsonObject } from "@/lib/utils";
import type { PageResult, ReplayDetail, ReplayListItem } from "@/types/api";

type ReplaysViewProps = {
  filters: FilterState;
  highValue: HighValueState;
  sort: SortState;
  onSortChange: (sort: SortState) => void;
  onResultLabel: (label: string) => void;
  toast: (message: string) => void;
};

type DetailTab = "response" | "request" | "ast";

export function ReplaysView({ filters, highValue, sort, onSortChange, onResultLabel, toast }: ReplaysViewProps) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [data, setData] = useState<PageResult<ReplayListItem>>({ total: 0, items: [] });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [selected, setSelected] = useState<ReplayDetail | null>(null);
  const [detailTab, setDetailTab] = useState<DetailTab>("response");

  const params = useMemo(() => {
    const parsed = parseSearchPrefix(filters.search);
    const query: Record<string, string | number | string[]> = {
      search: parsed.search,
      search_field: parsed.search_field,
      domain: filters.domain,
      target: filters.target,
      methods: filters.methods.join(","),
      buckets: filters.buckets.join(","),
      resp_cts: filters.respCts.join(","),
      sort: `${sort.asc ? "" : "-"}${sort.k}`,
      offset: (page - 1) * pageSize,
      limit: pageSize,
    };
    if (highValue.on) {
      const keywords = [...highValue.builtinEnabled, ...highValue.customKeywords];
      if (keywords.length) query.deny_keywords = keywords;
    }
    return query;
  }, [filters, highValue, page, pageSize, sort]);

  // 筛选条件变更时重置页码和已选详情
  useEffect(() => {
    setPage(1);
    setSelectedId(null);
    setSelected(null);
  }, [filters.search, filters.domain, filters.target, filters.methods, filters.buckets, filters.respCts, highValue.on, highValue.builtinEnabled, highValue.customKeywords, sort]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    api
      .replays(params)
      .then((next) => {
        if (cancelled) return;
        setData(next);
        onResultLabel(`${next.total} 条`);
      })
      .catch((err: Error) => {
        if (cancelled) return;
        setError(err.message);
        onResultLabel("-");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    // 取消正在进行的请求，避免竞态条件
    return () => {
      cancelled = true;
    };
  }, [params, onResultLabel]);

  const selectReplay = async (id: number) => {
    setSelectedId(id);
    setSelected(null);
    try {
      const detail = await api.replay(id);
      setSelected(detail);
    } catch {
      toast("加载详情失败");
    }
  };

  const changeSort = (key: SortState["k"]) => {
    onSortChange(sort.k === key ? { k: key, asc: !sort.asc } : { k: key, asc: false });
  };

  return (
    <div className="flex min-h-0 flex-1 overflow-hidden">
      <section className="flex min-w-0 flex-1 flex-col bg-surface-content">
        <div className="min-h-0 flex-1 overflow-auto">
          <table className={cn("data-table", selectedId ? "min-w-[900px]" : "min-w-[1040px]")}>
            <colgroup>
              <col className={selectedId ? "w-[74px]" : "w-[86px]"} />
              <col className={selectedId ? "w-[74px]" : "w-[82px]"} />
              <col className={selectedId ? "w-[62px]" : "w-[72px]"} />
              <col />
              <col className={selectedId ? "w-[78px]" : "w-[86px]"} />
              <col className={selectedId ? "w-[116px]" : "w-[126px]"} />
              <col className={selectedId ? "w-[170px]" : "w-[190px]"} />
            </colgroup>
            <thead>
              <tr>
                <th>
                  <SortHeader label="Status" column="status" sort={sort} onChange={changeSort} />
                </th>
                <th>Method</th>
                <th>Tier</th>
                <th>Path</th>
                <th>
                  <SortHeader label="Size" column="resp_len" sort={sort} onChange={changeSort} align="right" />
                </th>
                <th>Type</th>
                <th>API host</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <TableSkeleton columns={7} />
              ) : error ? (
                <tr><td colSpan={7} className="py-10 text-center text-red">{error}</td></tr>
              ) : data.total ? (
                data.items.map((item, index) => (
                  <ReplayRow
                    key={item.id}
                    item={item}
                    selected={item.id === selectedId}
                    delay={index < 24 ? `${index * 14}ms` : undefined}
                    onClick={() => selectReplay(item.id)}
                  />
                ))
              ) : (
                <tr>
                  <td colSpan={7}>
                    {isFilterAtDefault(filters) ? (
                      <EmptyState title="还没扫过任何站点" hint="运行 tracesurface scan https://example.com 开始扫描" />
                    ) : (
                      <EmptyState title="没有匹配的请求" hint="调整筛选条件，或点 reset 回到默认" />
                    )}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <Pager
          total={data.total}
          page={page}
          pageSize={pageSize}
          onChange={(next) => {
            setPage(next.page);
            setPageSize(next.pageSize);
          }}
        />
      </section>
      {selectedId ? (
        <ReplayDetailPanel
          id={selectedId}
          replay={selected}
          tab={detailTab}
          onTabChange={setDetailTab}
          onClose={() => {
            setSelectedId(null);
            setSelected(null);
          }}
          toast={toast}
        />
      ) : null}
    </div>
  );
}

// 可排序表头，Active 时显示排序方向图标
function SortHeader({
  label,
  column,
  sort,
  align = "left",
  onChange,
}: {
  label: string;
  column: SortState["k"];
  sort: SortState;
  align?: "left" | "right";
  onChange: (column: SortState["k"]) => void;
}) {
  const active = sort.k === column;
  const Icon = !active ? ChevronsUpDown : sort.asc ? ArrowUp : ArrowDown;
  return (
    <button
      className={cn(
        "inline-flex w-full items-center gap-1.5 text-text-4 transition-colors hover:text-text-2",
        active && "text-brand",
        align === "right" && "justify-end",
      )}
      onClick={() => onChange(column)}
      title={active ? (sort.asc ? "升序" : "降序") : "点击排序"}
    >
      <span>{label}</span>
      <Icon className={cn("h-3.5 w-3.5", active ? "text-brand" : "text-text-4 opacity-50")} />
    </button>
  );
}

// 单行重放数据：状态码、方法、置信层、路径、响应片段
function ReplayRow({ item, selected, delay, onClick }: { item: ReplayListItem; selected: boolean; delay?: string; onClick: () => void }) {
  const bucket = bucketFromStatus(item.status, item.error);
  const path = item.sent_url.replace(/^https?:\/\/[^/]+/, "") || "/";
  const preview = (item.resp_snippet || "").replace(/\s+/g, " ").slice(0, 120);
  return (
    <tr className={cn("animate-fade-up", selected && "selected")} style={{ animationDelay: delay }} onClick={onClick}>
      <td><span className={`status-cell status-${bucket}`}><span className="status-bar" />{item.error || item.status == null ? "ERR" : item.status}</span></td>
      <td><span className={`method-badge method-${item.sent_method}`}>{item.sent_method}</span></td>
      <td>{item.grade ? <span className={`tier-badge tier-${item.grade}`} title={GRADE_HINT[item.grade] || ""}>{GRADE_LABEL[item.grade] || item.grade}</span> : <span className="text-text-4">-</span>}</td>
      <td className="min-w-0" title={item.sent_url}>
        <div className="flex items-center gap-1.5">
          {item.cdp_request_id ? <span className="tag shrink-0 text-brand" title="浏览器真实请求的无认证重放">NET</span> : null}
          <span className="overflow-hidden text-ellipsis whitespace-nowrap font-mono text-[12.5px] text-text">{path}</span>
        </div>
        {preview ? <div className="mt-1 overflow-hidden text-ellipsis whitespace-nowrap font-mono text-[11px] text-text-3">{preview}</div> : null}
      </td>
      <td className="text-right font-mono text-[12px] text-text-2">{fmtBytes(item.resp_len)}</td>
      <td title={item.resp_ct || "-"}><span className="tag">{item.resp_ct || "-"}</span></td>
      <td className="overflow-hidden text-ellipsis whitespace-nowrap font-mono text-[11.5px] text-text-3" title={item.domain || ""}>{item.domain || ""}</td>
    </tr>
  );
}

// 右侧详情面板：响应体、请求头、来源追溯、复制按钮
function ReplayDetailPanel({
  id,
  replay,
  tab,
  onTabChange,
  onClose,
  toast,
}: {
  id: number;
  replay: ReplayDetail | null;
  tab: DetailTab;
  onTabChange: (tab: DetailTab) => void;
  onClose: () => void;
  toast: (message: string) => void;
}) {
  const bodySectionRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!replay || (tab !== "response" && tab !== "request")) return;
    window.requestAnimationFrame(() => {
      bodySectionRef.current?.scrollIntoView({ block: "start" });
    });
  }, [replay?.id, tab]);

  if (!replay) {
    return (
      <DetailPanel>
        <div className="flex h-full flex-col items-center justify-center gap-2 font-mono text-text-3">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-line-2 border-t-brand" />
          <span className="text-[11px] uppercase text-text-4">REQUEST #{id}</span>
        </div>
      </DetailPanel>
    );
  }

  const bucket = bucketFromStatus(replay.status, replay.error);
  const reqHeaders = safeJsonObject(replay.sent_headers);
  const respHeaders = safeJsonObject(replay.resp_headers);
  const req = { method: replay.sent_method, url: replay.sent_url, headers: reqHeaders, body: replay.sent_body };
  const copy = async (value: string, message = "已复制") => {
    await navigator.clipboard?.writeText(value);
    toast(message);
  };

  return (
    <DetailPanel>
      <div className="detail-header">
        <button type="button" className="absolute right-4 top-4 inline-flex h-7 w-7 items-center justify-center rounded-full border border-line-2 text-text-3 transition-colors hover:bg-ink-2 hover:text-text" onClick={onClose} title="关闭详情">
          <X className="h-4 w-4" />
        </button>
        <div className="mb-3 flex items-center gap-2">
          <span className={`method-badge method-${replay.sent_method}`}>{replay.sent_method}</span>
          <span className={`status-cell status-${bucket}`}><span className="status-bar" />{replay.error || replay.status == null ? "ERR" : replay.status}</span>
        </div>
        <button className="block break-all text-left font-mono text-[13px] leading-5 text-text hover:text-brand" title="点击复制完整 URL" onClick={() => copy(replay.sent_url)}>
          {replay.sent_url}
        </button>
        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[10.5px] text-text-3">
          <span className="min-w-0 break-all">API host <b className="text-text">{replay.domain || "-"}</b></span>
          <span className="min-w-0 break-all">SIZE <b className="text-text">{fmtBytes(replay.resp_len)}</b></span>
          <span className="min-w-0 break-all">TYPE <b className="text-text">{replay.resp_ct || "-"}</b></span>
          <span className="min-w-0 break-all">TIME <b className="text-text">{replay.time_ms ?? 0}ms</b></span>
          {replay.cdp_request_id ? <span className="min-w-0 break-all">SOURCE <b className="text-brand">浏览器真实请求重放</b></span> : null}
        </div>
      </div>
      <DetailTabs
        value={tab}
        items={[
          { value: "response", label: "Response" },
          { value: "request", label: "Request" },
          { value: "ast", label: "Provenance" },
        ]}
        onChange={onTabChange}
      />
      <div className="detail-body">{tab === "response" ? <ReplayResponse replay={replay} headers={respHeaders} bucket={bucket} bodyRef={bodySectionRef} /> : tab === "request" ? <ReplayRequest replay={replay} headers={reqHeaders} bodyRef={bodySectionRef} /> : <ReplayProvenance replay={replay} />}</div>
      <div className="detail-actions">
        <Button variant="default" onClick={() => copy(requestToCurl(req))}><Terminal className="h-3.5 w-3.5" />Copy as cURL</Button>
        <Button variant="subtle" onClick={() => copy(requestToRawHttp(req))}><FileText className="h-3.5 w-3.5" />Copy as HTTP</Button>
        <Button variant="subtle" onClick={() => copy(replay.sent_url)}><Link2 className="h-3.5 w-3.5" />Copy URL</Button>
        <Button variant="subtle" onClick={() => copy(replay.resp_snippet || "", replay.resp_truncated ? "已复制 1MB 截断片段" : "已复制")}><Clipboard className="h-3.5 w-3.5" />Copy response</Button>
      </div>
    </DetailPanel>
  );
}

// 响应 Tab：状态摘要、响应头、响应正文
function ReplayResponse({ replay, headers, bucket, bodyRef }: { replay: ReplayDetail; headers: Record<string, unknown>; bucket: string; bodyRef?: RefObject<HTMLElement | null> }) {
  const isBinary = replay.resp_ct === "bin" && replay.resp_file;
  const captureLimit = 1024 * 1024;
  const originalRespLen = replay.resp_full_len ?? replay.resp_len ?? 0;
  const capturedRespLen = replay.resp_truncated ? Math.min(originalRespLen, captureLimit) : originalRespLen;
  return (
    <>
      <section className="section">
        <div className="section-title">Status</div>
        <KvGrid
          items={[
            { label: "Status", value: <span className={`status-${bucket}`}>{replay.error || replay.status}</span> },
            { label: "Time", value: `${replay.time_ms ?? 0} ms` },
            { label: "Length", value: fmtBytes(replay.resp_len) },
            { label: "Grade", value: replay.grade ? <span className={`tier-badge tier-${replay.grade}`} title={GRADE_HINT[replay.grade] || ""}>{GRADE_LABEL[replay.grade] || replay.grade}</span> : "-", hidden: !replay.grade },
            { label: "Base 出处", value: replay.base_source, hidden: !replay.base_source },
            { label: "为何非更高 tier", value: replay.why_not_higher_tier, hidden: !replay.why_not_higher_tier },
            { label: "备注", value: "正文超 1MB，已截断展示", hidden: !replay.resp_truncated },
          ]}
        />
      </section>
      <section className="section">
        <div className="section-title">Response Headers</div>
        <HeaderGrid headers={headers} />
      </section>
      <section ref={bodyRef} className="section">
        <div className="section-title">Response Body</div>
        {replay.resp_truncated ? (
          <div className="trunc-banner">
            正文已截断 {fmtBytes(capturedRespLen)} / 原始 {fmtBytes(originalRespLen)}
            {replay.resp_file ? <a className="ml-2 text-brand underline" href={`/api/replays/${replay.id}/file`} download>下载已捕获片段</a> : null}
          </div>
        ) : null}
        {isBinary ? (
          <div className="body-empty text-brand">
            二进制响应 · {fmtBytes(replay.resp_len)} · <a className="text-brand underline" href={`/api/replays/${replay.id}/file`} download>下载响应文件</a>
          </div>
        ) : (
          <BodyBlock value={replay.resp_snippet || replay.error} />
        )}
      </section>
    </>
  );
}

// 请求 Tab：请求 URL、Headers、Body
function ReplayRequest({ replay, headers, bodyRef }: { replay: ReplayDetail; headers: Record<string, unknown>; bodyRef?: RefObject<HTMLElement | null> }) {
  return (
    <>
      <section className="section">
        <div className="section-title">Target</div>
        <KvGrid
          items={[
            { label: "Method", value: replay.sent_method },
            { label: "URL", value: replay.sent_url },
            { label: "Query", value: typeof replay.sent_query === "object" ? JSON.stringify(replay.sent_query) : String(replay.sent_query || ""), hidden: !replay.sent_query },
          ]}
        />
      </section>
      <section className="section">
        <div className="section-title">Request Headers</div>
        <HeaderGrid headers={headers} />
      </section>
      <section ref={bodyRef} className="section">
        <div className="section-title">Request Body</div>
        <BodyBlock value={replay.sent_body} empty="无请求 body" />
      </section>
    </>
  );
}

// 来源追溯 Tab：展示该 API 的推导变体、关联 API ID、扫描 ID 等元信息
function ReplayProvenance({ replay }: { replay: ReplayDetail }) {
  return (
    <section className="section">
      <div className="section-title">Provenance</div>
      <KvGrid
        items={[
          { label: "来源", value: replay.cdp_request_id ? "浏览器真实请求的无认证重放" : "推导候选发包" },
          { label: "Variant", value: replay.variant || "-" },
          { label: "API ID", value: replay.resolution_id ? `#${replay.resolution_id}` : "-", hidden: !replay.resolution_id },
          { label: "CDP 请求 ID", value: replay.cdp_request_id ? `#${replay.cdp_request_id}` : "-", hidden: !replay.cdp_request_id },
          { label: "Scan ID", value: replay.scan_id ? `#${replay.scan_id}` : "-" },
          { label: "Created", value: replay.created_at ? `${fmtRelTime(replay.created_at)} ago` : "-" },
        ]}
      />
      {replay.cdp_request_id ? (
        <a href={`/api/cdp_requests/${replay.cdp_request_id}`} target="_blank" className="mt-3 inline-flex items-center gap-1 text-[11px] text-brand hover:underline" rel="noreferrer">
          查看对应的真实请求 (JSON)
          <ExternalLink className="h-3 w-3" />
        </a>
      ) : replay.resolution_id ? (
        <a href={`/api/resolutions/${replay.resolution_id}`} target="_blank" className="mt-3 inline-flex items-center gap-1 text-[11px] text-brand hover:underline" rel="noreferrer">
          查看 resolution 元信息 (JSON)
          <ExternalLink className="h-3 w-3" />
        </a>
      ) : null}
    </section>
  );
}


import { ExternalLink, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { DetailPanel } from "@/components/shared/DetailPanel";
import { EmptyState } from "@/components/shared/EmptyState";
import { KvGrid } from "@/components/shared/KvGrid";
import { Pager } from "@/components/shared/Pager";
import { TableSkeleton } from "@/components/shared/TableSkeleton";
import { api } from "@/lib/api";
import { isFilterAtDefault, parseSearchPrefix, type FilterState } from "@/lib/filters";
import { STATUS_HINT, TIER_HINT } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { PageResult, ResolutionDetail, ResolutionListItem } from "@/types/api";

type Props = {
  filters: FilterState;
  onResultLabel: (label: string) => void;
  toast: (message: string) => void;
};

const STATUS_LABEL: Record<string, string> = {
  confirmed: "confirmed",
  inferred: "inferred",
  ast_full: "ast-full",
  not_inferred: "no-base",
};

const stripHost = (url: string) => url.replace(/^https?:\/\/[^/]+/, "") || "/";
const hostOf = (url: string) => (url.match(/^https?:\/\/([^/]+)/) || [])[1] || "";

export function ApiSurfaceView({ filters, onResultLabel, toast }: Props) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [data, setData] = useState<PageResult<ResolutionListItem>>({ total: 0, items: [] });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [selected, setSelected] = useState<ResolutionDetail | null>(null);

  const params = useMemo(() => {
    const parsed = parseSearchPrefix(filters.search);
    return {
      search: parsed.search,
      target: filters.target,
      methods: filters.methods.join(","),
      offset: (page - 1) * pageSize,
      limit: pageSize,
    };
  }, [filters, page, pageSize]);

  useEffect(() => {
    setPage(1);
    setSelectedId(null);
    setSelected(null);
  }, [filters.search, filters.target, filters.methods]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    api
      .resolutions(params)
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
    return () => {
      cancelled = true;
    };
  }, [params, onResultLabel]);

  const select = async (id: number) => {
    setSelectedId(id);
    setSelected(null);
    try {
      setSelected(await api.resolution(id));
    } catch {
      toast("加载详情失败");
    }
  };

  return (
    <div className="flex min-h-0 flex-1 overflow-hidden">
      <section className="flex min-w-0 flex-1 flex-col bg-surface-content">
        <div className="min-h-0 flex-1 overflow-auto">
          <table className={cn("data-table", selectedId ? "min-w-[640px]" : "min-w-[760px]")}>
            <colgroup>
              <col className="w-[82px]" />
              <col className="w-[60px]" />
              <col className="w-[100px]" />
              <col />
            </colgroup>
            <thead>
              <tr>
                <th>Method</th>
                <th>Tier</th>
                <th>Status</th>
                <th>Full URL</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <TableSkeleton columns={4} />
              ) : error ? (
                <tr><td colSpan={4} className="py-10 text-center text-red">{error}</td></tr>
              ) : data.total ? (
                data.items.map((item, index) => (
                  <Row
                    key={item.id}
                    item={item}
                    selected={item.id === selectedId}
                    delay={index < 24 ? `${index * 14}ms` : undefined}
                    onClick={() => select(item.id)}
                  />
                ))
              ) : (
                <tr>
                  <td colSpan={4}>
                    {isFilterAtDefault(filters) ? (
                      <EmptyState title="还没扫过任何站点" hint="运行 tracesurface scan https://example.com 开始扫描" />
                    ) : (
                      <EmptyState title="没有匹配的 API" hint="调整筛选条件，或点 reset 回到默认" />
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
        <DetailPane
          id={selectedId}
          res={selected}
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

function Row({ item, selected, delay, onClick }: { item: ResolutionListItem; selected: boolean; delay?: string; onClick: () => void }) {
  const host = hostOf(item.full_url);
  return (
    <tr className={cn("animate-fade-up", selected && "selected")} style={{ animationDelay: delay }} onClick={onClick}>
      <td><span className={`method-badge method-${item.method}`}>{item.method === "UNKNOWN" ? "?" : item.method}</span></td>
      <td>{item.inference_tier ? <span className={`tier-badge tier-${item.inference_tier}`} title={TIER_HINT}>{item.inference_tier}</span> : <span className="text-text-4">-</span>}</td>
      <td><span className="tag" title={STATUS_HINT[item.category] || item.category}>{STATUS_LABEL[item.category] || item.category}</span></td>
      <td className="min-w-0" title={item.full_url}>
        <div className="flex items-center gap-1.5">
          {item.cdp_request_id ? <span className="tag shrink-0 text-brand" title="浏览器运行时捕获的真实请求">NET</span> : null}
          <span className="overflow-hidden text-ellipsis whitespace-nowrap font-mono text-[12.5px] text-text">{stripHost(item.full_url)}</span>
        </div>
        {host ? <div className="mt-1 overflow-hidden text-ellipsis whitespace-nowrap font-mono text-[11px] text-text-3">{host}</div> : null}
      </td>
    </tr>
  );
}

function DetailPane({ id, res, onClose, toast }: { id: number; res: ResolutionDetail | null; onClose: () => void; toast: (message: string) => void }) {
  if (!res) {
    return (
      <DetailPanel>
        <div className="flex h-full flex-col items-center justify-center gap-2 font-mono text-text-3">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-line-2 border-t-brand" />
          <span className="text-[11px] uppercase text-text-4">RESOLUTION #{id}</span>
        </div>
      </DetailPanel>
    );
  }
  const copy = async (value: string) => {
    await navigator.clipboard?.writeText(value);
    toast("已复制");
  };
  const loc = res.source_js ? `${stripHost(res.source_js)}:${res.line ?? 0}` : "-";
  return (
    <DetailPanel>
      <div className="detail-header">
        <button type="button" className="absolute right-4 top-4 inline-flex h-7 w-7 items-center justify-center rounded-full border border-line-2 text-text-3 transition-colors hover:bg-ink-2 hover:text-text" onClick={onClose} title="关闭详情">
          <X className="h-4 w-4" />
        </button>
        <div className="mb-3 flex items-center gap-2">
          <span className={`method-badge method-${res.method}`}>{res.method === "UNKNOWN" ? "?" : res.method}</span>
          {res.inference_tier ? <span className={`tier-badge tier-${res.inference_tier}`} title={TIER_HINT}>{res.inference_tier}</span> : null}
          {res.evidence?.some((item) => item.evidence_kind === "cdp_request") ? <span className="tag text-brand" title="浏览器运行时捕获的真实请求">NET</span> : null}
          <span className="tag" title={STATUS_HINT[res.category] || res.category}>{STATUS_LABEL[res.category] || res.category}</span>
        </div>
        <button className="block break-all text-left font-mono text-[13px] leading-5 text-text hover:text-brand" title="点击复制完整 URL" onClick={() => copy(res.full_url)}>
          {res.full_url}
        </button>
      </div>
      <div className="detail-body">
        <section className="section">
          <div className="section-title">推导</div>
          <KvGrid
            items={[
              { label: "Tier", value: res.inference_tier ? <span className={`tier-badge tier-${res.inference_tier}`} title={TIER_HINT}>{res.inference_tier}</span> : "-", hidden: !res.inference_tier },
              { label: "Status", value: <span title={STATUS_HINT[res.category] || res.category}>{STATUS_LABEL[res.category] || res.category}</span> },
              { label: "Base 出处", value: res.base_source, hidden: !res.base_source },
              { label: "绑定规则", value: res.binding_rule, hidden: !res.binding_rule },
              { label: "为何非更高 tier", value: res.why_not_higher_tier, hidden: !res.why_not_higher_tier },
            ]}
          />
        </section>
        <section className="section">
          <div className="section-title">来源 sink</div>
          <KvGrid
            items={[
              { label: "AST 路径", value: res.ast_path || "-" },
              { label: "位置", value: loc },
              { label: "Pattern", value: res.pattern || "-", hidden: !res.pattern },
            ]}
          />
        </section>
        <section className="section">
          <div className="section-title">证据{res.evidence?.length ? ` (${res.evidence.length})` : ""}</div>
          {res.evidence?.length ? (
            <div className="flex flex-col gap-1 font-mono text-[11.5px] text-text-3">
              {res.evidence.map((e, i) => (
                <div key={i}>{e.role} · {e.evidence_kind} #{e.evidence_id}</div>
              ))}
            </div>
          ) : (
            <div className="body-empty">无运行时证据（非 confirmed）</div>
          )}
        </section>
        <section className="section">
          <div className="section-title">验证{res.verifications?.length ? ` (${res.verifications.length})` : ""}</div>
          {res.verifications?.length ? (
            <div className="flex flex-col gap-1">
              {res.verifications.map((v) => (
                <a key={v.id} href={`/api/replays/${v.id}`} target="_blank" rel="noreferrer" className="flex items-center gap-2 font-mono text-[11.5px] text-text-3 hover:text-brand">
                  <span className="tag">{v.variant || v.sent_method}</span>
                  <span>{v.status ?? "ERR"}</span>
                  <ExternalLink className="h-3 w-3" />
                </a>
              ))}
            </div>
          ) : (
            <div className="body-empty">尚未发包验证</div>
          )}
        </section>
      </div>
    </DetailPanel>
  );
}

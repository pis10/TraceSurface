import { useEffect, useState } from "react";
import {
  Play,
  Settings,
  SearchCheck,
  Loader2,
  CircleAlert,
} from "lucide-react";
import { api } from "@/lib/api";
import type { FilterState } from "@/lib/filters";
import type { AIBatch, AIFinding } from "@/types/ai";
import type { ReplayDetail } from "@/types/api";
import { Button } from "@/components/ui/button";
import { SelectPopover } from "@/components/shared/SelectPopover";
import { Pager } from "@/components/shared/Pager";
import { ReplayDetailPanel } from "@/components/replays/ReplaysView";
import { SitePicker } from "./SitePicker";
import { SettingsDialog } from "./SettingsDialog";
import { JobsDialog } from "./JobsDialog";
import { empty } from "./state";

export function AIView({
  filters,
  toast,
  onResultLabel,
}: {
  filters: FilterState;
  toast: (s: string) => void;
  onResultLabel: (s: string) => void;
}) {
  const [batches, setBatches] = useState<AIBatch[]>([]);
  const [batch, setBatch] = useState<number | undefined>();
  const [data, setData] = useState(empty<AIFinding>);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [selectOpen, setSelectOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [checkingSettings, setCheckingSettings] = useState(false);
  const [startAfterSettings, setStartAfterSettings] = useState(false);
  const [jobsOpen, setJobsOpen] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<AIFinding | null>(null);
  const [replay, setReplay] = useState<ReplayDetail | null>(null);
  const [detailTab, setDetailTab] = useState<"response" | "request" | "ast">(
    "response",
  );
  const [version, setVersion] = useState(0);
  const current = batches.find((b) => b.id === batch);
  const progress = current ?? batches[0];
  const active = batches.some((b) => b.queued + b.running > 0);

  const startAnalysis = async () => {
    setCheckingSettings(true);
    try {
      const settings = await api.aiSettings();
      if (settings.has_api_key) setSelectOpen(true);
      else {
        setStartAfterSettings(true);
        setSettingsOpen(true);
        toast("先配置模型，保存后继续选择站点");
      }
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setCheckingSettings(false);
    }
  };

  useEffect(() => {
    setPage(1);
    setSelected(null);
  }, [filters.target, filters.search, batch]);
  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        const [bs, findings] = await Promise.all([
          api.aiBatches(),
          api.aiFindings({
            target: filters.target,
            search: filters.search,
            batch_id: batch,
            offset: (page - 1) * pageSize,
            limit: pageSize,
          }),
        ]);
        if (cancelled) return;
        setBatches(bs.items);
        setData(findings);
        setError("");
        onResultLabel(`${findings.total} 条疑似`);
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
    };
    void refresh();
    const timer = window.setInterval(refresh, 3000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [
    filters.target,
    filters.search,
    batch,
    page,
    pageSize,
    version,
    onResultLabel,
  ]);
  useEffect(() => {
    let cancelled = false;
    setReplay(null);
    if (selected)
      api
        .replay(selected.replay_id)
        .then((r) => {
          if (!cancelled) setReplay(r);
        })
        .catch(() => {
          if (!cancelled) {
            toast("原始报文已删除或无法读取");
            setSelected(null);
          }
        });
    return () => {
      cancelled = true;
    };
  }, [selected, toast]);

  return (
    <>
      <div className="flex flex-wrap items-center gap-3 border-b border-line-2 px-5 py-3">
        <Button
          variant="default"
          disabled={checkingSettings}
          onClick={() => void startAnalysis()}
        >
          {checkingSettings ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Play className="h-3.5 w-3.5" />
          )}
          开始分析
        </Button>
        <Button variant="subtle" onClick={() => setSettingsOpen(true)}>
          <Settings className="h-3.5 w-3.5" />
          模型设置
        </Button>
        <SelectPopover
          label="分析批次"
          icon="none"
          value={batch ? String(batch) : ""}
          allLabel="全部最新结果"
          className="h-9 max-w-[340px]"
          options={batches.map((b) => ({
            value: String(b.id),
            label: `批次 #${b.id} · ${new Date(b.created_at * 1000).toLocaleString()}`,
            meta: `${b.total} 个站点`,
          }))}
          onChange={(value) => setBatch(value ? Number(value) : undefined)}
        />
        {progress && (
          <Button variant="subtle" onClick={() => setJobsOpen(true)}>
            任务详情
          </Button>
        )}
        <span className="ml-auto text-xs text-text-3">
          {active ? (
            <span className="inline-flex items-center gap-2">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              分析中
            </span>
          ) : (
            ""
          )}
        </span>
      </div>
      {progress && (
        <div
          className="border-b border-line-2 px-5 py-2 text-xs text-text-3"
          role="status"
        >
          任务 #{progress.id} · {progress.total} 个站点 · 完成 {progress.done} ·
          分析中 {progress.running} · 排队 {progress.queued} · 失败{" "}
          {progress.failed} · 跳过 {progress.skipped}
        </div>
      )}
      {error && (
        <p role="alert" className="p-3 text-red">
          {error}
        </p>
      )}
      <div className="flex min-h-0 flex-1 overflow-hidden">
        <section className="flex min-w-0 flex-1 flex-col">
          <div className="min-h-0 flex-1 overflow-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>站点</th>
                  <th>疑似接口</th>
                  <th>判断依据</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((item) => (
                  <tr
                    key={`${item.job_id}-${item.replay_id}`}
                    className={
                      selected?.replay_id === item.replay_id &&
                      selected.job_id === item.job_id
                        ? "selected"
                        : ""
                    }
                    onClick={() => setSelected(item)}
                    tabIndex={0}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") setSelected(item);
                    }}
                  >
                    <td className="max-w-[240px] break-all font-mono text-xs">
                      {item.target_url}
                    </td>
                    <td className="max-w-[360px] break-all">
                      <span
                        className={`method-badge method-${item.sent_method}`}
                      >
                        {item.sent_method}
                      </span>
                      <div className="mt-2 font-mono text-xs">
                        {item.sent_url}
                      </div>
                    </td>
                    <td className="max-w-[500px] text-xs leading-6">
                      {item.reason}
                    </td>
                  </tr>
                ))}
                {!data.total && (
                  <tr>
                    <td colSpan={3}>
                      <div className="flex min-h-[300px] flex-col items-center justify-center gap-3 py-12 text-center">
                        <div className="flex h-12 w-12 items-center justify-center rounded-2xl border border-line-2 bg-surface-chrome text-text-3">
                          {active ? (
                            <Loader2 className="h-5 w-5 animate-spin" />
                          ) : progress?.failed ? (
                            <CircleAlert className="h-5 w-5" />
                          ) : (
                            <SearchCheck className="h-5 w-5" />
                          )}
                        </div>
                        <p className="text-sm font-medium text-text">
                          {active
                            ? "正在分析"
                            : progress?.failed && !progress.done
                              ? "分析未完成"
                              : batches.length
                                ? "暂无疑似接口"
                                : "还没有分析结果"}
                        </p>
                        <p className="max-w-sm text-xs leading-6 text-text-3">
                          {active
                            ? "结果会陆续显示，可以离开页面稍后查看。"
                            : progress?.failed
                              ? `${progress.failed} 个站点分析失败，请在任务详情中查看原因并重试。`
                              : batches.length
                                ? "当前范围内没有标记为疑似的接口。"
                                : "选择已扫描的站点，分析重放结果。"}
                        </p>
                      </div>
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
            onChange={(p) => {
              setPage(p.page);
              setPageSize(p.pageSize);
            }}
          />
        </section>
        {selected && (
          <ReplayDetailPanel
            id={selected.replay_id}
            replay={replay}
            tab={detailTab}
            onTabChange={setDetailTab}
            onClose={() => setSelected(null)}
            toast={toast}
            review={
              <div className="max-h-60 shrink-0 overflow-auto border-b border-line-2 bg-[var(--brand-soft)] px-5 py-4 text-xs leading-6">
                <div className="font-semibold text-text">判断依据</div>
                <p className="whitespace-pre-wrap text-text-2">
                  {selected.reason}
                </p>
                <div className="mt-3 font-semibold text-text">验证建议</div>
                <p className="whitespace-pre-wrap text-text-2">
                  {selected.recommendation || "结合接口访问要求人工确认。"}
                </p>
                <p className="mt-3 font-mono text-[10px] text-text-3">
                  {selected.model} · Scan #{selected.scan_id}
                </p>
              </div>
            }
          />
        )}
      </div>
      {selectOpen && (
        <SitePicker
          target={filters.target}
          onClose={() => setSelectOpen(false)}
          onStarted={(id) => {
            setBatch(id);
            setVersion((v) => v + 1);
            setSelectOpen(false);
          }}
        />
      )}
      {settingsOpen && (
        <SettingsDialog
          onClose={() => {
            setSettingsOpen(false);
            setStartAfterSettings(false);
          }}
          onSaved={() => {
            toast("模型设置已保存");
            if (startAfterSettings) setSelectOpen(true);
          }}
        />
      )}
      {jobsOpen && progress && (
        <JobsDialog
          batch={progress.id}
          onClose={() => setJobsOpen(false)}
          onRetry={(id) => {
            setBatch(id);
            setVersion((v) => v + 1);
          }}
        />
      )}
    </>
  );
}

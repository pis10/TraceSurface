import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import type { AISite } from "@/types/ai";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Pager } from "@/components/shared/Pager";
import { empty, statusLabel } from "./state";

export function SitePicker({
  target,
  model,
  onClose,
  onStarted,
}: {
  target: string;
  model: string;
  onClose: () => void;
  onStarted: (id: number) => void;
}) {
  const [search, setSearch] = useState("");
  const [data, setData] = useState(empty<AISite>);
  const [page, setPage] = useState(1);
  const [chosen, setChosen] = useState<Set<number>>(new Set());
  const [all, setAll] = useState(false);
  const [force, setForce] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setData(empty());
    api
      .aiSites({ search, target, offset: (page - 1) * 50, limit: 50 })
      .then((r) => {
        if (!cancelled) {
          setData(r);
          setError("");
        }
      })
      .catch((e) => {
        if (!cancelled) setError(e.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [search, target, page]);
  const start = async () => {
    setBusy(true);
    setError("");
    try {
      const r = await api.startAI({
        ...(all ? { search, target } : { scan_ids: [...chosen] }),
        force,
      });
      onStarted(r.batch_id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const count = all ? data.total : chosen.size;
  return (
    <Dialog
      open
      onOpenChange={(v) => {
        if (!v) onClose();
      }}
    >
      <DialogContent className="w-[min(850px,94vw)]">
        <DialogHeader>
          <DialogTitle>选择站点</DialogTitle>
          <DialogDescription>
            只分析 2xx JSON 响应。没有符合条件接口或尚未扫完的站点会跳过。
            {target && `当前站点：${target}`}
          </DialogDescription>
        </DialogHeader>
        <div className="mx-6 mb-4 flex flex-wrap items-center justify-between gap-2 rounded-md border border-line-2 bg-surface-content px-3 py-2.5 text-xs"><span className="text-text-3">本次使用 <span className="ml-2 font-mono text-text-2">{model}</span></span><span className="text-text-3">每个站点独立分析</span></div>
        <div className="flex flex-wrap items-center gap-3 px-6 pb-3">
          <Input
            className="min-w-[180px] flex-1"
            aria-label="筛选站点"
            placeholder="按站点地址筛选"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
              setAll(false);
              setChosen(new Set());
            }}
          />
          <label className="flex shrink-0 items-center gap-2 text-xs">
            <Checkbox
              checked={all}
              onCheckedChange={(checked) => {
                setAll(checked === true);
                setChosen(new Set());
              }}
            />{" "}
            全选筛选结果（{data.total}）
          </label>
        </div>
        <div className="min-h-0 flex-1 overflow-auto px-6">
          <table className="data-table">
            <thead>
              <tr>
                <th>选择</th>
                <th>站点</th>
                <th>扫描</th>
                <th>AI</th>
              </tr>
            </thead>
            <tbody>
              {!data.items.length && (
                <tr>
                  <td
                    colSpan={4}
                    className="py-10 text-center text-xs text-text-3"
                  >
                    {loading
                      ? "正在读取站点…"
                      : search || target
                        ? "没有符合条件的站点"
                        : "还没有扫描记录，先扫描站点再来分析。"}
                  </td>
                </tr>
              )}
              {data.items.map((s) => (
                <tr key={s.id}>
                  <td>
                    <Checkbox
                      aria-label={`选择 ${s.target_url}`}
                      checked={all || chosen.has(s.id)}
                      disabled={all}
                      onCheckedChange={(checked) =>
                        setChosen((old) => {
                          const n = new Set(old);
                          if (checked === true) n.add(s.id);
                          else n.delete(s.id);
                          return n;
                        })
                      }
                    />
                  </td>
                  <td className="break-all text-xs">
                    {s.target_url}
                    <div className="mt-1 text-text-3">
                      {s.replay_count} 条重放
                    </div>
                  </td>
                  <td>
                    {s.status === "done"
                      ? "已完成"
                      : s.status === "running"
                        ? "扫描中"
                        : "失败"}
                  </td>
                  <td>{s.ai_status ? statusLabel[s.ai_status] : "未分析"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <Pager
          total={data.total}
          page={page}
          pageSize={50}
          pageSizes={[50]}
          onChange={(p) => setPage(p.page)}
        />
        <div className="border-t border-line-2 p-5">
          <div className="mb-3 flex items-center justify-between text-xs"><span>已选 <strong>{count.toLocaleString()}</strong> 个站点</span><span className="text-text-3">结果汇总到同一列表</span></div>
          <label className="flex items-center gap-2 text-xs">
            <Checkbox
              checked={force}
              onCheckedChange={(checked) => setForce(checked === true)}
            />{" "}
            重新分析已完成的站点
          </label>
          {error && (
            <p role="alert" className="mt-2 text-xs text-red">
              {error}
            </p>
          )}
          <Button
            variant="default"
            className="mt-3 w-full"
            disabled={busy || loading || !count}
            onClick={start}
          >
            {busy ? "正在提交…" : `分析 ${count} 个站点`}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

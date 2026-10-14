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
import { useCallback } from "react";
import { RefreshCw } from "lucide-react";
import type { AIJob } from "@/types/ai";
import { Pager } from "@/components/shared/Pager";
import { empty, statusLabel } from "./state";

export function JobsDialog({
  batch,
  onClose,
  onRetry,
}: {
  batch: number;
  onClose: () => void;
  onRetry: (id: number) => void;
}) {
  const [data, setData] = useState(empty<AIJob>);
  const [page, setPage] = useState(1);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const refresh = useCallback(
    () =>
      api
        .aiJobs(batch, { offset: (page - 1) * 50, limit: 50 })
        .then(setData)
        .catch((e) => setError(e.message)),
    [batch, page],
  );
  useEffect(() => {
    void refresh();
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
  }, [refresh]);
  const retry = async (id: number) => {
    setBusy(true);
    try {
      const r = await api.startAI({ scan_ids: [id], force: true });
      onRetry(r.batch_id);
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog
      open
      onOpenChange={(v) => {
        if (!v) onClose();
      }}
    >
      <DialogContent className="w-[min(850px,94vw)]">
        <DialogHeader>
          <DialogTitle>批次 #{batch} · 分析进度</DialogTitle>
          <DialogDescription>
            关闭页面后继续分析。停止报告服务后，未完成的任务需要重试。
          </DialogDescription>
        </DialogHeader>
        <div className="min-h-0 overflow-auto px-6">
          <table className="data-table">
            <thead>
              <tr>
                <th>站点</th>
                <th>状态</th>
                <th>详情</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((j) => (
                <tr key={j.id}>
                  <td className="max-w-[250px] break-all text-xs">
                    {j.target_url}
                  </td>
                  <td className="whitespace-nowrap">
                    {j.scan_id === null ? "数据已清理" : statusLabel[j.status]}
                  </td>
                  <td className="text-xs leading-6">
                    {j.message ||
                      `${j.input_count} 条接口${j.status === "done" ? ` · ${j.suspected_count} 条疑似` : ""}`}
                    {j.status === "failed" && j.scan_id && (
                      <Button
                        variant="subtle"
                        size="sm"
                        disabled={busy}
                        onClick={() => retry(j.scan_id!)}
                      >
                        <RefreshCw className="h-3 w-3" />
                        重试
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {error && (
          <p role="alert" className="px-6 text-red">
            {error}
          </p>
        )}
        <Pager
          total={data.total}
          page={page}
          pageSize={50}
          pageSizes={[50]}
          onChange={(p) => setPage(p.page)}
        />
      </DialogContent>
    </Dialog>
  );
}

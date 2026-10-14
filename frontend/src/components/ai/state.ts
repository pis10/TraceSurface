import type { PageResult } from "@/types/api";
export const statusLabel: Record<string, string> = {
  queued: "排队中",
  running: "分析中",
  done: "已完成",
  failed: "失败",
  skipped: "已跳过",
};
export const empty = <T>(): PageResult<T> => ({ total: 0, items: [] });

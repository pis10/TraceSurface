import type { AISettings } from "@/types/ai";

export function credentialScope(config: AISettings) {
  return `${config.provider_id}|${config.api_base.trim().replace(/\/$/, "")}`;
}

export const effortLabels: Record<string, string> = {
  auto: "模型默认",
  off: "关闭",
  on: "开启",
  none: "关闭",
  minimal: "最低",
  low: "低",
  medium: "中",
  high: "高",
  xhigh: "更高",
  max: "最高",
};

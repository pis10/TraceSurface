import type { AISettings, ReasoningMode } from "@/types/ai";

export function credentialScope(config: AISettings) {
  return `${config.provider_id}|${config.api_base.trim().replace(/\/$/, "")}`;
}
export const reasoningLabels: Record<ReasoningMode, string> = {
  default: "使用服务默认设置",
  effort: "按强度",
  budget: "按 token 预算",
  enabled: "开启思考",
  disabled: "关闭思考",
  adaptive: "自适应思考",
};
export function tokens(value: number | null | undefined) {
  return value ? value.toLocaleString() : "未提供";
}

import type { AISettings, ReasoningMode } from "@/types/ai";

export function credentialScope(config: AISettings) {
  return `${config.auth_mode}|${config.provider_id}|${config.api_base.trim().replace(/\/$/, "")}`;
}
export const reasoningLabels: Record<ReasoningMode, string> = {
  default: "使用服务默认设置",
  effort: "按强度",
  budget: "按 token 预算",
  enabled: "开启思考",
  disabled: "关闭思考",
  adaptive: "自适应思考",
};
export const effortLabels: Record<string, string> = {
  none: "none · 关闭", minimal: "minimal · 最低", low: "low · 低",
  medium: "medium · 中", high: "high · 高", xhigh: "xhigh · 更高", max: "max · 最高",
};
export function tokens(value: number | null | undefined) {
  return value ? value.toLocaleString() : "未提供";
}

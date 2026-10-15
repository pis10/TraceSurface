import type { AISettings } from "@/types/ai";

export function credentialScope(config: AISettings) {
  return `${config.mode}|${config.provider_id}|${config.api_base.trim().replace(/\/$/, "")}`;
}

export type ReasoningMode = "default" | "effort" | "budget" | "enabled" | "disabled" | "adaptive";
export type AISettings = {
  format_version: 4;
  mode: "catalog" | "custom";
  provider_id: string;
  model: string;
  api_base: string;
  has_api_key: boolean;
  ready: boolean;
  context_window: number | null;
  max_output_tokens: number | null;
  reasoning_mode: ReasoningMode;
  reasoning_effort: string;
  reasoning_efforts: string[];
  thinking_budget: number | null;
  output_mode: "prompt" | "json" | "native";
  response_limit: number;
};
export type AISettingsInput = Omit<AISettings, "has_api_key" | "ready"> & {
  api_key: string;
};
export type AIModelDetails = {
  reasoning_modes: ReasoningMode[];
  reasoning_efforts: string[];
  output_mode: "prompt" | "json" | "native";
};
export type AISite = {
  id: number;
  target_url: string;
  status: string;
  replay_count: number;
  ai_status: string | null;
};
export type AIBatch = {
  id: number;
  created_at: number;
  total: number;
  done: number;
  failed: number;
  skipped: number;
  queued: number;
  running: number;
  suspected: number;
};
export type AIJob = {
  id: number;
  scan_id: number | null;
  target_url: string;
  status: string;
  message: string;
  input_count: number;
  suspected_count: number;
};
export type AIFinding = {
  job_id: number;
  replay_id: number;
  target_url: string;
  sent_url: string;
  sent_method: string;
  reason: string;
  recommendation: string;
  scan_id: number;
  batch_id: number;
  model: string;
  created_at: number;
};

export type AIModel = { value: string; label: string };
export type AIProvider = { id: string; label: string; models: AIModel[] };
export type AICatalog = { providers: AIProvider[]; source: string; efforts: string[]; protocols: Array<{ id: string; label: string }> };
export type AIConnectionTest = {
  ok: boolean;
  message: string;
  elapsed_ms?: number;
};

export type ReasoningMode = "default" | "effort" | "budget" | "enabled" | "disabled" | "adaptive";
export type AISettings = {
  format_version: 2;
  mode: "catalog" | "custom";
  provider_id: string;
  model: string;
  api_base: string;
  auth_mode: "key" | "environment" | "none";
  has_api_key: boolean;
  configured_params: string[];
  ready: boolean;
  max_input_tokens: number | null;
  max_output_tokens: number | null;
  reasoning_mode: ReasoningMode;
  reasoning_effort: string;
  thinking_budget: number | null;
  json_mode: boolean;
  response_limit: number;
};
export type AISettingsInput = Omit<AISettings, "has_api_key" | "configured_params" | "ready"> & {
  api_key: string;
  connection_params: Record<string, string>;
};
export type AIModelDetails = {
  max_input_tokens: number | null;
  max_output_tokens: number | null;
  reasoning_modes: ReasoningMode[];
  reasoning_efforts: string[];
  json_mode: boolean;
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
export type AICatalog = { providers: AIProvider[]; source: string; connection_fields: string[] };
export type AIConnectionTest = {
  ok: boolean;
  message: string;
  elapsed_ms?: number;
};

export type AISettings = {
  provider_id: string;
  reasoning_effort: string;
  model: string;
  api_base: string;
  has_api_key: boolean;
  response_limit: number;
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

export type AIModel = {
  value: string;
  label: string;
  context: number;
  max_output_tokens: number;
  reasoning: boolean;
  reasoning_options: string[];
};
export type AIProvider = {
  id: string;
  label: string;
  api_base: string;
  models: AIModel[];
};
export type AICatalog = { providers: AIProvider[]; source: string };
export type AIConnectionTest = {
  ok: boolean;
  message: string;
  elapsed_ms?: number;
};

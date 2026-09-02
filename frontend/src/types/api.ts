
export type Stats = {
  total: number;
  target_count: number;
  s2xx: number;
  s3xx: number;
  s4xx: number;
  s5xx: number;
  t_l1: number;
  t_l2: number;
  t_l3: number;
  t_l4: number;
};

export type DomainSummary = {
  domain: string;
  replay_count: number;
};

export type TargetSummary = {
  target_url: string;
  api_count: number;
  replay_count: number;
  last_scan_at?: number | null;
  last_finished_at?: number | null;
};

export type ReplayListItem = {
  id: number;
  sent_url: string;
  sent_method: string;
  status?: number | null;
  error?: string | null;
  resp_ct?: string | null;
  resp_len?: number | null;
  inference_tier?: "L1" | "L2" | "L3" | "L4" | null;
  domain?: string | null;
  resp_snippet?: string | null;

  cdp_request_id?: number | null;
};

export type ReplayDetail = ReplayListItem & {
  sent_query?: unknown;
  sent_body?: unknown;
  sent_headers?: Record<string, unknown> | string | null;
  resp_headers?: Record<string, unknown> | string | null;
  resp_file?: string | null;
  resp_truncated?: boolean;
  resp_full_len?: number | null;
  time_ms?: number | null;
  variant?: string | null;
  resolution_id?: number | null;
  scan_id?: number | null;
  created_at?: number | null;
  base_source?: string | null;
  binding_rule?: string | null;
  why_not_higher_tier?: string | null;
};

export type ApiStatus = "confirmed" | "inferred" | "ast_full" | "not_inferred";

export type ResolutionListItem = {
  id: number;
  method: string;
  full_url: string;
  category: ApiStatus;
  inference_tier?: "L1" | "L2" | "L3" | "L4" | null;
  base_source?: string | null;
  binding_rule?: string | null;
  cdp_request_id?: number | null;
};

export type VerificationSummary = {
  id: number;
  variant?: string | null;
  sent_method: string;
  sent_url: string;
  status?: number | null;
  resp_ct?: string | null;
  resp_len?: number | null;
  time_ms?: number | null;
  error?: string | null;
  created_at?: number | null;
};

export type ResolutionEvidence = {
  evidence_kind: string;
  evidence_id: number;
  role: string;
};

export type ResolutionDetail = ResolutionListItem & {
  ast_path?: string | null;
  source_js?: string | null;
  line?: number | null;
  col_start?: number | null;
  pattern?: string | null;
  params?: unknown;
  why_not_higher_tier?: string | null;
  scan_id?: number | null;
  verifications?: VerificationSummary[];
  evidence?: ResolutionEvidence[];
};

export type SecretFacets = {
  groups: Record<string, number>;
  sensitive: Record<string, number>;
};

export type SecretListItem = {
  id: number;
  rule_id: string;
  rule_group: string;
  sensitive: number;
  value: string;
  source_js: string;
  line: number;
  col_start: number;
  occurrence_count?: number;
  source_count?: number;
};

export type SecretSource = {
  source_js: string;
  count: number;
  line: number;
  col_start: number;
};

export type SecretDetail = SecretListItem & {
  context_before?: string | null;
  context_line?: string | null;
  context_after?: string | null;
  metadata?: Record<string, unknown>;
  sources?: SecretSource[];
};

export type PageResult<T> = {
  total: number;
  items: T[];
};

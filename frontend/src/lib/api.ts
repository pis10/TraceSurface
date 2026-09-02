

import type {
  DomainSummary,
  PageResult,
  ReplayDetail,
  ReplayListItem,
  ResolutionDetail,
  ResolutionListItem,
  SecretDetail,
  SecretFacets,
  SecretListItem,
  Stats,
  TargetSummary,
} from "@/types/api";

type QueryValue = string | number | boolean | Array<string | number | boolean> | null | undefined;
type Query = Record<string, QueryValue>;

async function request<T>(path: string, params?: Query): Promise<T> {
  let url = path;
  if (params) {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value == null || value === "" || value === false) continue;
      if (Array.isArray(value)) value.forEach((item) => query.append(key, String(item)));
      else query.set(key, String(value));
    }
    const raw = query.toString();
    if (raw) url = `${path}?${raw}`;
  }
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`API ${path} failed: ${response.status}`);
  return response.json() as Promise<T>;
}

// 通用 DELETE 请求
async function remove<T>(path: string, params: Query): Promise<T> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value != null) query.set(key, String(value));
  }
  const response = await fetch(`${path}?${query.toString()}`, { method: "DELETE" });
  if (!response.ok) throw new Error(`API ${path} failed: ${response.status}`);
  return response.json() as Promise<T>;
}

export const api = {
  stats: () => request<Stats>("/api/stats"),
  domains: () => request<{ items: DomainSummary[] }>("/api/domains", { limit: 9999 }),
  targets: () => request<{ items: TargetSummary[] }>("/api/targets"),
  resolutions: (params: Query) => request<PageResult<ResolutionListItem>>("/api/resolutions", params),
  resolution: (id: number) => request<ResolutionDetail>(`/api/resolutions/${id}`),
  replays: (params: Query) => request<PageResult<ReplayListItem>>("/api/replays", params),
  replay: (id: number) => request<ReplayDetail>(`/api/replays/${id}`),
  secretFacets: (params?: Query) => request<SecretFacets>("/api/secrets/facets", params),
  secrets: (params: Query) => request<PageResult<SecretListItem>>("/api/secrets", params),
  secret: (id: number) => request<SecretDetail>(`/api/secrets/${id}`),
  purgeTarget: (targetUrl: string) => remove<{ ok: boolean; counts: Record<string, number> }>("/api/data", { target_url: targetUrl }),
  purgeAll: () => remove<{ ok: boolean; counts: Record<string, number> }>("/api/data", { all: "1" }),
};

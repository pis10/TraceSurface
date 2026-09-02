
export const METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH"] as const;
export const BUCKETS = ["2xx", "3xx", "4xx", "5xx"] as const;
export const RESPONSE_TYPES = ["json", "html", "text", "other"] as const;

export type Method = (typeof METHODS)[number];
export type Bucket = (typeof BUCKETS)[number];
export type ResponseType = (typeof RESPONSE_TYPES)[number];

export type FilterState = {
  search: string;
  domain: string;
  target: string;
  methods: Method[];
  buckets: Bucket[];
  respCts: ResponseType[];
};

export type SortState = {
  k: "created_at" | "status" | "resp_len";
  asc: boolean;
};

export function defaultFilters(): FilterState {
  return {
    search: "",
    domain: "",
    target: "",
    methods: [...METHODS],
    buckets: ["2xx"],
    respCts: ["json", "text", "other"],
  };
}

export function sanitizeFilters(value: unknown): FilterState {
  const base = defaultFilters();
  if (!value || typeof value !== "object") return base;
  const raw = value as Partial<FilterState>;
  const keep = <T extends string>(items: readonly T[], source: unknown, fallback: T[]) => {
    if (!Array.isArray(source)) return fallback;
    const selected = source.filter((item): item is T => items.includes(item as T));
    return selected.length ? Array.from(new Set(selected)) : fallback;
  };
  return {
    search: typeof raw.search === "string" ? raw.search : base.search,
    domain: "",
    target: typeof raw.target === "string" ? raw.target : base.target,
    methods: keep(METHODS, raw.methods, base.methods),
    buckets: keep(BUCKETS, raw.buckets, base.buckets),
    respCts: keep(RESPONSE_TYPES, raw.respCts, base.respCts),
  };
}

export function parseSearchPrefix(value: string) {
  if (!value) return { search: "", search_field: "" };
  const match = value.match(/^(url|body|dom):\s*(.*)$/i);
  if (!match) return { search: value, search_field: "" };
  return { search: match[2], search_field: match[1].toLowerCase() };
}

export function isFilterAtDefault(filters: FilterState) {
  const base = defaultFilters();
  const same = (a: string[], b: string[]) => a.length === b.length && a.every((item) => b.includes(item));
  return (
    filters.search === base.search &&
    filters.domain === base.domain &&
    filters.target === base.target &&
    same(filters.methods, base.methods) &&
    same(filters.buckets, base.buckets) &&
    same(filters.respCts, base.respCts)
  );
}

export function toggleFilterItem<T extends string>(items: T[], all: readonly T[], value: T, solo: boolean) {
  if (solo) {
    return items.length === 1 && items[0] === value ? [...all] : [value];
  }
  return items.includes(value) ? items.filter((item) => item !== value) : [...items, value];
}

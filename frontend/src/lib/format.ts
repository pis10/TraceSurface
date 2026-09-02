
export const TIER_HINT = "L1–L4 表示 URL 推导把握度，不是优先级；所有结果都要看。";

export const STATUS_HINT: Record<string, string> = {
  confirmed: "浏览器真实请求已对齐到源码调用点",
  inferred: "从 JavaScript 推导出的 URL",
  ast_full: "源码里已经是完整 URL",
  not_inferred: "找到了调用点，但还拼不出完整 URL",
};

export function fmtRelTime(ts?: number | null) {
  if (!ts) return "";
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86400)}d`;
}

// 取 URL 的 host，解析失败（如相对路径）返回空串
export function hostFromUrl(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return "";
  }
}

// 格式化字节大小为人类可读形式
export function fmtBytes(n?: number | null) {
  if (!n) return "-";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

// 格式化时长为可读形式（秒/分钟/小时）
export function fmtDuration(sec?: number | null) {
  if (sec == null || sec < 0) return "-";
  if (sec < 60) return `${Math.round(sec)}s`;
  const minutes = Math.floor(sec / 60);
  if (sec < 3600) {
    const seconds = Math.floor(sec % 60);
    return seconds ? `${minutes}m ${seconds}s` : `${minutes}m`;
  }
  const hours = Math.floor(sec / 3600);
  const remMinutes = Math.floor((sec % 3600) / 60);
  return remMinutes ? `${hours}h ${remMinutes}m` : `${hours}h`;
}

// 根据 HTTP 状态码（或 error）映射到 2xx/3xx/4xx/5xx/err 桶
export function bucketFromStatus(status?: number | null, error?: unknown) {
  if (error || status == null) return "err";
  if (status < 300) return "2xx";
  if (status < 400) return "3xx";
  if (status < 500) return "4xx";
  return "5xx";
}

// 截取 Content-Type 的主类型（去掉 charset 等参数）
export function shortContentType(value?: string | null) {
  return value ? String(value).split(";")[0].trim() : "";
}

// 美化 JSON 输出，自动检测字符串中的 JSON 并格式化
export function prettyJson(value: unknown) {
  if (!value) return "";
  if (typeof value === "object") {
    try {
      return JSON.stringify(value, null, 2);
    } catch {
      return String(value);
    }
  }
  const text = String(value);
  if (text.length > 512 * 1024) return text;
  const head = text.slice(0, 16).trimStart();
  if (head[0] !== "{" && head[0] !== "[") return text;
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

// 从 headers 对象中按 name（大小写不敏感）取值
export function headerValue(headers: Record<string, unknown>, name: string) {
  const wanted = name.toLowerCase();
  for (const [key, value] of Object.entries(headers || {})) {
    if (key.toLowerCase() === wanted) return String(value || "");
  }
  return "";
}

// 将 JS 源文件 URL 缩短为 .../末尾路径#锚点 的紧凑形式
export function shortSourceLabel(value?: string | null) {
  if (!value) return "";
  try {
    const [main, ...anchorParts] = value.split("#");
    const anchor = anchorParts.length ? `#${anchorParts.join("#")}` : "";
    const url = new URL(main);
    const segments = url.pathname.split("/").filter(Boolean);
    const tail = segments.slice(-2).join("/") || url.host;
    return `.../${tail}${anchor}`;
  } catch {
    return value.length > 60 ? `...${value.slice(-60)}` : value;
  }
}

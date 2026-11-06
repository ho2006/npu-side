export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export async function apiJson(path: string, init?: RequestInit): Promise<unknown> {
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  if (init?.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  let response: Response;
  try {
    response = await fetch(path, { ...init, headers });
  } catch (error) {
    const message = error instanceof Error ? error.message : "网络请求失败";
    throw new ApiError(message, 0);
  }
  const text = await response.text();
  const payload = text ? parseJson(text) : null;
  if (!response.ok) {
    throw new ApiError(errorDetail(payload, text, response.status), response.status);
  }
  return payload ?? {};
}

export function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "请求失败";
}

function parseJson(text: string): unknown {
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

function errorDetail(payload: unknown, text: string, status: number): string {
  const detail = isRecord(payload) ? payload.detail : undefined;
  const rendered = renderDetail(detail);
  if (rendered) return rendered;
  const trimmed = text.trim();
  if (trimmed) return trimmed.slice(0, 2000);
  return `请求失败（${status}）`;
}

function renderDetail(detail: unknown): string {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => {
      if (typeof item === "string") return item;
      if (isRecord(item) && typeof item.msg === "string") return item.msg;
      return JSON.stringify(item);
    });
    return messages.filter(Boolean).join("\n");
  }
  if (isRecord(detail)) {
    const message = typeof detail.message === "string" ? detail.message : "";
    const receipt = isRecord(detail.receipt) ? detail.receipt : null;
    const errors = receipt && Array.isArray(receipt.errors) ? receipt.errors : [];
    const extra = errors
      .map((item) => (isRecord(item) && typeof item.message === "string" ? item.message : ""))
      .filter(Boolean)
      .join("；");
    if (message && extra) return `${message} ${extra}`;
    if (message) return message;
    return JSON.stringify(detail);
  }
  return "";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

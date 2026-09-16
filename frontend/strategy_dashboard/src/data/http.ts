import type { ApiError } from "./types";

async function parseJsonSafe(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!text) return {};
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return { message: text };
  }
}

function toApiError(payload: unknown, fallback: string): ApiError {
  if (payload && typeof payload === "object") {
    const p = payload as Record<string, unknown>;
    return {
      code: String(p.code ?? "HTTP_ERROR"),
      message: String(p.message ?? fallback),
      details: (p.details as Record<string, unknown>) ?? {},
      request_id: p.request_id ? String(p.request_id) : undefined,
      timestamp_utc: p.timestamp_utc ? String(p.timestamp_utc) : undefined,
    };
  }
  return { code: "HTTP_ERROR", message: fallback };
}

export async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(path, { cache: "no-store" });
  const data = await parseJsonSafe(res);
  if (!res.ok) {
    throw toApiError(data, `${res.status} ${res.statusText}`);
  }
  return data as T;
}

export async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  const data = await parseJsonSafe(res);
  if (!res.ok) {
    throw toApiError(data, `${res.status} ${res.statusText}`);
  }
  return data as T;
}

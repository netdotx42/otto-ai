export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public details?: unknown) {
    super(message);
  }
}

export async function api<T = unknown>(path: string, init: RequestInit & { idempotencyKey?: string } = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("content-type")) headers.set("content-type", "application/json");
  if (init.idempotencyKey) headers.set("idempotency-key", init.idempotencyKey);
  let res: Response;
  try {
    res = await fetch(`${API_URL}/v1${path}`, { ...init, headers, credentials: "include" });
  } catch {
    throw new ApiError(0, "network_error", "Cannot reach the AGENT//98 API. Check your connection or the API status.");
  }
  const text = await res.text();
  const body = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const e = body?.error ?? {};
    throw new ApiError(res.status, e.code ?? "http_error", e.message ?? res.statusText, e.details);
  }
  return body as T;
}

export const post = <T,>(path: string, body: unknown, idempotencyKey?: string) =>
  api<T>(path, { method: "POST", body: JSON.stringify(body), idempotencyKey });

export function newIdempotencyKey() {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : String(Date.now()) + Math.random();
}

/** Minimal SSE-over-POST reader (EventSource only supports GET). */
export async function* streamSSE(path: string, body: unknown, signal?: AbortSignal): AsyncGenerator<{ event: string; data: any }> {
  const res = await fetch(`${API_URL}/v1${path}`, {
    method: "POST", credentials: "include", signal,
    headers: { "content-type": "application/json", accept: "text/event-stream" }, body: JSON.stringify(body),
  });
  if (!res.ok || !res.body) {
    const t = await res.text().catch(() => "");
    let msg = res.statusText;
    try { msg = JSON.parse(t).error.message; } catch {}
    throw new ApiError(res.status, "stream_error", msg);
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let idx;
    while ((idx = buf.search(/\r?\n\r?\n/)) >= 0) {
      const chunk = buf.slice(0, idx);
      buf = buf.slice(idx).replace(/^\r?\n\r?\n/, "");
      let event = "message";
      const data: string[] = [];
      for (const line of chunk.split(/\r?\n/)) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      if (data.length) {
        try { yield { event, data: JSON.parse(data.join("\n")) }; } catch { /* ignore keep-alives */ }
      }
    }
  }
}

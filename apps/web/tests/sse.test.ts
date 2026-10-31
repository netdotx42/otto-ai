import { describe, expect, it, vi } from "vitest";
import { streamSSE } from "@/lib/api";

function streamOf(chunks: string[]) {
  const enc = new TextEncoder();
  return new ReadableStream({ start(c) { chunks.forEach((s) => c.enqueue(enc.encode(s))); c.close(); } });
}

describe("streamSSE", () => {
  it("parses events split across chunks", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(streamOf(["event: tool\r\ndata: {\"module\":\"SIM", "ULATOR\"}\r\n\r\nevent: token\r\ndata: {\"text\":\"hi\"}\r\n\r\n: ping\r\n\r\n"]), { status: 200 })));
    const out = [];
    for await (const e of streamSSE("/x", {})) out.push(e);
    expect(out).toEqual([{ event: "tool", data: { module: "SIMULATOR" } }, { event: "token", data: { text: "hi" } }]);
  });
  it("raises structured API errors", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ error: { message: "Sign in" } }), { status: 401 })));
    await expect(async () => { for await (const _ of streamSSE("/x", {})) void _; }).rejects.toThrow("Sign in");
  });
});

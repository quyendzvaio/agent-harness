// Types and calls for the agent-harness API (docs/PRD.md §11). Everything goes through the
// same-origin proxy at /api/backend.

export const API = "/api/backend";

export type Conversation = { id: string; title: string; created_at: string; updated_at: string };
export type Message = {
  id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  turn_id: string | null;
  created_at: string;
  metadata: Record<string, unknown>;
};
export type Evidence = { label: string; kind: string; url: string; locator: string; quote: string };
export type Artifact = { id: string; filename: string; media_type: string; size_bytes: number; created_at: string };
export type Skill = {
  name: string;
  description: string;
  source_types: string[];
  allowed_tools: string[];
  trust: string;
  limits: Record<string, number> | null;
  hooks_enabled: boolean;
};
export type Readiness = { status: string; checks: Record<string, string> };

export type CitationReport = { valid: string[]; invalid: string[]; uncited_sentences: number; total_sentences: number };
export type Usage = {
  model_calls: number;
  tool_calls: number;
  tokens_in: number;
  tokens_out: number;
  model?: string | null;
};

/** One SSE event of POST /v1/chat. */
export type ChatEvent =
  | { event: "conversation"; data: { conversation_id: string; turn_id: string } }
  | { event: "token"; data: { text: string; message_id?: string } }
  | { event: "retract"; data: { message_id: string } }
  | { event: "skill_loaded"; data: { name: string; limits?: Record<string, number> } }
  | { event: "tool_started"; data: { call_id: string; tool: string; input: Record<string, unknown> } }
  | {
      event: "tool_finished";
      data: { call_id: string; tool: string; status: string; evidence_labels: string[]; duration_ms: number };
    }
  | { event: "citation_report"; data: CitationReport }
  | { event: "artifact"; data: { id: string; filename: string; size_bytes: number } }
  | { event: "error"; data: { code: string; message: string } }
  | { event: "done"; data: { usage: Usage } };

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

function headers(apiKey: string, extra?: Record<string, string>): HeadersInit {
  return { ...(apiKey ? { "X-API-Key": apiKey } : {}), ...extra };
}

async function toError(response: Response): Promise<ApiError> {
  let code = `http_${response.status}`;
  let message = response.statusText;
  try {
    const body = await response.json();
    const err = body.error ?? body;
    if (typeof err?.code === "string") code = err.code;
    if (typeof err?.message === "string") message = err.message;
    else if (body.detail) message = JSON.stringify(body.detail);
  } catch {
    /* not JSON */
  }
  return new ApiError(response.status, code, message);
}

export async function getJson<T>(path: string, apiKey: string): Promise<T> {
  const response = await fetch(`${API}${path}`, { headers: headers(apiKey), cache: "no-store" });
  if (!response.ok) throw await toError(response);
  return response.json() as Promise<T>;
}

export async function readiness(): Promise<Readiness> {
  const response = await fetch(`${API}/readyz`, { cache: "no-store" });
  if (response.status === 200 || response.status === 503) return response.json();
  throw await toError(response);
}

export const artifactUrl = (id: string) => `${API}/v1/artifacts/${id}`;

/** POST /v1/chat and yield its Server-Sent Events as they arrive. */
export async function* streamChat(
  message: string,
  conversationId: string | null,
  apiKey: string,
  signal: AbortSignal,
): AsyncGenerator<ChatEvent> {
  const response = await fetch(`${API}/v1/chat`, {
    method: "POST",
    headers: headers(apiKey, { "Content-Type": "application/json", Accept: "text/event-stream" }),
    body: JSON.stringify({ message, conversation_id: conversationId }),
    signal,
  });
  if (!response.ok || !response.body) throw await toError(response);

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value.replace(/\r\n/g, "\n");
    let end: number;
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      const parsed = parseBlock(block);
      if (parsed) yield parsed;
    }
  }
  const tail = parseBlock(buffer);
  if (tail) yield tail;
}

function parseBlock(block: string): ChatEvent | null {
  let event = "message";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
  }
  if (!data.length) return null;
  return { event, data: JSON.parse(data.join("\n")) } as ChatEvent;
}

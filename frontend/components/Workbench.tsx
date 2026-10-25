"use client";

import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import {
  ApiError,
  getJson,
  readiness,
  streamChat,
  type Artifact,
  type Conversation,
  type Evidence,
  type Message,
  type Readiness,
  type Skill,
} from "@/lib/api";
import { EvidencePanel } from "./EvidencePanel";
import { Markdown } from "./Markdown";
import { TurnActivity, type TurnState } from "./TurnActivity";

type Item = {
  id: string;
  role: "user" | "assistant";
  text: string;
  /** Streamed text per model message, so a retracted (retried) message can be removed. */
  parts?: { id: string; text: string }[];
  turn?: TurnState;
};

const EXAMPLES = [
  "Phân tích kiến trúc https://github.com/pallets/itsdangerous",
  "Vẽ sơ đồ kiến trúc chi tiết https://github.com/quyendzvaio/AI_SOC",
  "Phân tích kiến trúc https://github.com/kennethreitz/setup.py và xuất file md",
  "Giải thích bài viết https://martinfowler.com/articles/microservices.html",
];
const KEY_STORAGE = "agent-harness.apiKey";

// The API key lives in localStorage (a per-browser convenience), with an in-memory fallback when
// storage is blocked; read through useSyncExternalStore so the server render stays "".
let memoryKey = "";
const keyListeners = new Set<() => void>();
function readKey(): string {
  try {
    return localStorage.getItem(KEY_STORAGE) ?? "";
  } catch {
    return memoryKey;
  }
}
function writeKey(value: string) {
  memoryKey = value;
  try {
    if (value) localStorage.setItem(KEY_STORAGE, value);
    else localStorage.removeItem(KEY_STORAGE);
  } catch {
    /* storage unavailable */
  }
  keyListeners.forEach((listener) => listener());
}
function subscribeKey(listener: () => void) {
  keyListeners.add(listener);
  window.addEventListener("storage", listener);
  return () => {
    keyListeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}
function setUrlConversation(id: string | null) {
  const url = new URL(window.location.href);
  if (id) url.searchParams.set("c", id);
  else url.searchParams.delete("c");
  window.history.replaceState(null, "", url);
}
const message = (error: unknown) =>
  error instanceof ApiError
    ? `${error.code}: ${error.message}`
    : error instanceof Error
      ? error.message
      : String(error);

export function Workbench() {
  const apiKey = useSyncExternalStore(subscribeKey, readKey, () => "");
  const [ready, setReady] = useState<Readiness | null | "down">(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [panel, setPanel] = useState<"none" | "sidebar" | "evidence">("none");
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const [now, setNow] = useState(0);

  const known = useMemo(
    () => (conversationId ? new Set(evidence.map((e) => e.label)) : null),
    [conversationId, evidence],
  );

  const refreshSidebar = useCallback(async (key: string) => {
    const [health, convs, sk] = await Promise.allSettled([
      readiness(),
      getJson<Conversation[]>("/v1/conversations?limit=100", key),
      getJson<Skill[]>("/v1/skills", key),
    ]);
    setReady(health.status === "fulfilled" ? health.value : "down");
    if (convs.status === "fulfilled") setConversations(convs.value);
    if (sk.status === "fulfilled") setSkills(sk.value);
    const failure = [convs, sk].find((r) => r.status === "rejected");
    setNotice(failure ? message(failure.reason) : null);
  }, []);

  const refreshEvidence = useCallback(async (id: string, key: string) => {
    setEvidenceLoading(true);
    try {
      const [ev, files] = await Promise.all([
        getJson<Evidence[]>(`/v1/conversations/${id}/evidence`, key),
        getJson<Artifact[]>(`/v1/conversations/${id}/artifacts`, key),
      ]);
      setEvidence(ev);
      setArtifacts(files);
    } catch (error) {
      setNotice(message(error));
    } finally {
      setEvidenceLoading(false);
    }
  }, []);

  const openConversation = useCallback(
    async (id: string | null, key: string) => {
      setConversationId(id);
      setUrlConversation(id);
      setSelected(null);
      setEvidence([]);
      setArtifacts([]);
      setItems([]);
      setPanel("none");
      if (!id) return;
      try {
        const messages = await getJson<Message[]>(`/v1/conversations/${id}/messages`, key);
        setItems(
          messages
            .filter((m) => m.role === "user" || m.role === "assistant")
            .map((m) => ({ id: m.id, role: m.role as Item["role"], text: m.content })),
        );
      } catch (error) {
        setNotice(message(error));
      }
      refreshEvidence(id, key);
    },
    [refreshEvidence],
  );

  useEffect(() => {
    const key = readKey();
    // Initial load on mount and restoring the conversation named in the URL (an external source).
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refreshSidebar(key);
    const fromUrl = new URLSearchParams(window.location.search).get("c");
    if (fromUrl) openConversation(fromUrl, key);
  }, [refreshSidebar, openConversation]);

  // Tick once a second while a turn runs, for the elapsed-time counter.
  useEffect(() => {
    if (!streaming) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [streaming]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [items]);

  const cite = (label: string) => {
    setSelected(label);
    setPanel("evidence");
  };

  async function send(text: string) {
    const body = text.trim();
    if (!body || streaming) return;
    const assistantId = crypto.randomUUID();
    const turn: TurnState = { status: "streaming", skills: [], tools: [], artifacts: [], startedAt: Date.now() };
    setItems((prev) => [
      ...prev,
      { id: crypto.randomUUID(), role: "user", text: body },
      { id: assistantId, role: "assistant", text: "", turn },
    ]);
    setDraft("");
    setNow(turn.startedAt);
    setStreaming(true);
    const update = (fn: (item: Item) => Item) =>
      setItems((prev) => prev.map((it) => (it.id === assistantId ? fn(it) : it)));
    const updateTurn = (fn: (t: TurnState) => TurnState) => update((it) => ({ ...it, turn: fn(it.turn!) }));

    const controller = new AbortController();
    abortRef.current = controller;
    let currentId = conversationId;
    try {
      for await (const ev of streamChat(body, conversationId, apiKey, controller.signal)) {
        switch (ev.event) {
          case "conversation":
            if (!currentId) {
              currentId = ev.data.conversation_id;
              setConversationId(currentId);
              setUrlConversation(currentId);
            }
            updateTurn((t) => ({ ...t, turnId: ev.data.turn_id }));
            break;
          case "token": {
            const { text, message_id: messageId = "" } = ev.data;
            update((it) => {
              const parts = [...(it.parts ?? [])];
              const last = parts.at(-1);
              if (last && last.id === messageId) parts[parts.length - 1] = { ...last, text: last.text + text };
              else parts.push({ id: messageId, text });
              return { ...it, parts, text: it.text + text };
            });
            break;
          }
          case "retract":
            // That model call broke mid-stream and is being retried: drop its partial text.
            update((it) => {
              const parts = (it.parts ?? []).filter((p) => p.id !== ev.data.message_id);
              return {
                ...it,
                parts,
                text: parts.map((p) => p.text).join(""),
                turn: { ...it.turn!, retries: (it.turn!.retries ?? 0) + 1 },
              };
            });
            break;
          case "skill_loaded":
            updateTurn((t) => ({
              ...t,
              skills: [...t.skills, ev.data.name],
              skillLimits: ev.data.limits ?? t.skillLimits,
            }));
            break;
          case "tool_started":
            updateTurn((t) => ({
              ...t,
              tools: [...t.tools, { call_id: ev.data.call_id, tool: ev.data.tool, input: ev.data.input }],
            }));
            break;
          case "tool_finished":
            updateTurn((t) => ({
              ...t,
              tools: t.tools.map((r) => (r.call_id === ev.data.call_id ? { ...r, ...ev.data } : r)),
            }));
            if (currentId && ev.data.evidence_labels.length) refreshEvidence(currentId, apiKey);
            break;
          case "citation_report":
            updateTurn((t) => ({ ...t, citation: ev.data }));
            break;
          case "artifact":
            updateTurn((t) => ({ ...t, artifacts: [...t.artifacts, ev.data] }));
            break;
          case "error":
            updateTurn((t) => ({ ...t, status: "error", error: ev.data, finishedAt: Date.now() }));
            break;
          case "done":
            updateTurn((t) => ({ ...t, status: "done", usage: ev.data.usage, finishedAt: Date.now() }));
            break;
        }
      }
    } catch (error) {
      if (controller.signal.aborted) {
        updateTurn((t) => ({ ...t, status: "cancelled", finishedAt: Date.now() }));
      } else {
        const err =
          error instanceof ApiError
            ? { code: error.code, message: error.message }
            : { code: "network", message: message(error) };
        updateTurn((t) => ({ ...t, status: "error", error: err, finishedAt: Date.now() }));
      }
    } finally {
      updateTurn((t) =>
        t.status === "streaming"
          ? {
              ...t,
              status: "error",
              error: { code: "stream_closed", message: "Stream đóng trước event done/error" },
              finishedAt: Date.now(),
            }
          : t,
      );
      abortRef.current = null;
      setStreaming(false);
      if (currentId) refreshEvidence(currentId, apiKey);
      refreshSidebar(apiKey);
    }
  }

  const readyDot =
    ready === null
      ? "bg-zinc-400"
      : ready === "down"
        ? "bg-red-500"
        : ready.status === "ready"
          ? "bg-emerald-500"
          : "bg-amber-500";
  const readyText =
    ready === null
      ? "đang kiểm tra…"
      : ready === "down"
        ? "không kết nối được backend"
        : Object.entries(ready.checks)
            .map(([k, v]) => `${k}: ${v}`)
            .join("\n");

  const sidebar = (
    <nav className="flex h-full min-h-0 flex-col">
      <div className="space-y-3 border-b border-zinc-200 p-3 dark:border-zinc-800">
        <div className="flex items-center justify-between">
          <h1 className="text-sm font-bold">Agent Harness</h1>
          <span className="flex items-center gap-1.5 text-xs text-zinc-500" title={readyText}>
            <span className={`h-2 w-2 rounded-full ${readyDot}`} />
            {ready === null ? "…" : ready === "down" ? "offline" : ready.status}
          </span>
        </div>
        <button
          type="button"
          disabled={streaming}
          onClick={() => openConversation(null, apiKey)}
          className="w-full rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
        >
          + Hội thoại mới
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {conversations.length === 0 && <p className="px-1 text-xs text-zinc-500">Chưa có hội thoại.</p>}
        {conversations.map((c) => (
          <button
            key={c.id}
            type="button"
            disabled={streaming}
            onClick={() => openConversation(c.id, apiKey)}
            className={`mb-0.5 block w-full truncate rounded px-2 py-1.5 text-left text-xs disabled:opacity-60 ${
              c.id === conversationId
                ? "bg-zinc-200 font-medium dark:bg-zinc-800"
                : "hover:bg-zinc-100 dark:hover:bg-zinc-900"
            }`}
            title={`${c.title}\n${new Date(c.updated_at).toLocaleString()}`}
          >
            {c.title || "(không tiêu đề)"}
          </button>
        ))}
      </div>
      <details className="border-t border-zinc-200 p-3 text-xs dark:border-zinc-800">
        <summary className="cursor-pointer font-semibold">Skills ({skills.length})</summary>
        <ul className="mt-2 max-h-64 space-y-2 overflow-y-auto">
          {skills.map((s) => (
            <li key={s.name}>
              <div className="flex items-center gap-1">
                <span className="font-mono font-semibold">{s.name}</span>
                <span className="rounded bg-zinc-100 px-1 text-[10px] text-zinc-500 dark:bg-zinc-800">{s.trust}</span>
              </div>
              <p className="text-zinc-500">{s.description}</p>
            </li>
          ))}
        </ul>
      </details>
      <details className="border-t border-zinc-200 p-3 text-xs dark:border-zinc-800">
        <summary className="cursor-pointer font-semibold">API key {apiKey ? "(đã đặt)" : ""}</summary>
        <p className="mt-1 text-zinc-500">
          Chỉ cần khi backend bật AUTH_ENABLED. Hoặc đặt BACKEND_API_KEY trong frontend/.env.local.
        </p>
        <input
          type="password"
          value={apiKey}
          onChange={(e) => writeKey(e.target.value)}
          onBlur={() => refreshSidebar(apiKey)}
          placeholder="X-API-Key"
          className="mt-2 w-full rounded border border-zinc-300 bg-white px-2 py-1 dark:border-zinc-700 dark:bg-zinc-900"
        />
      </details>
    </nav>
  );

  return (
    <div className="flex h-dvh overflow-hidden bg-white text-zinc-900 dark:bg-zinc-950 dark:text-zinc-100">
      <div className="hidden w-64 shrink-0 border-r border-zinc-200 md:block dark:border-zinc-800">{sidebar}</div>

      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-2 border-b border-zinc-200 px-4 py-2 text-sm dark:border-zinc-800">
          <button
            type="button"
            className="rounded px-2 py-1 hover:bg-zinc-100 md:hidden dark:hover:bg-zinc-900"
            onClick={() => setPanel("sidebar")}
          >
            ☰
          </button>
          <span className="min-w-0 flex-1 truncate font-medium">
            {conversationId
              ? (conversations.find((c) => c.id === conversationId)?.title ?? conversationId)
              : "Hội thoại mới"}
          </span>
          <button
            type="button"
            className="rounded px-2 py-1 text-xs text-zinc-600 hover:bg-zinc-100 lg:hidden dark:text-zinc-400 dark:hover:bg-zinc-900"
            onClick={() => setPanel("evidence")}
          >
            Evidence ({evidence.length})
          </button>
        </header>

        {notice && (
          <div className="flex items-start gap-2 bg-amber-50 px-4 py-2 text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-200">
            <span className="flex-1">{notice}</span>
            <button type="button" onClick={() => setNotice(null)}>
              ✕
            </button>
          </div>
        )}

        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto max-w-3xl px-4 py-6">
            {items.length === 0 && (
              <div className="mt-10 space-y-4 text-center">
                <h2 className="text-lg font-semibold">Thử agent với một GitHub repo hoặc bài blog</h2>
                <div className="grid gap-2 sm:grid-cols-2">
                  {EXAMPLES.map((ex) => (
                    <button
                      key={ex}
                      type="button"
                      onClick={() => setDraft(ex)}
                      className="rounded-lg border border-zinc-200 p-3 text-left text-sm text-zinc-700 hover:border-indigo-400 hover:bg-indigo-50/50 dark:border-zinc-800 dark:text-zinc-300 dark:hover:bg-indigo-950/30"
                    >
                      {ex}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {items.map((item) =>
              item.role === "user" ? (
                <div key={item.id} className="mb-6 flex justify-end">
                  <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl bg-indigo-600 px-4 py-2 text-sm text-white">
                    {item.text}
                  </div>
                </div>
              ) : (
                <div key={item.id} className="mb-8">
                  {item.turn && <TurnActivity turn={item.turn} now={now} onCite={cite} />}
                  {item.text ? (
                    <Markdown text={item.text} known={known} onCite={cite} final={item.turn?.status !== "streaming"} />
                  ) : (
                    item.turn?.status === "streaming" && (
                      <p className="animate-pulse text-sm text-zinc-400">Đang suy nghĩ…</p>
                    )
                  )}
                </div>
              ),
            )}
            <div ref={bottomRef} />
          </div>
        </div>

        <form
          className="border-t border-zinc-200 p-3 dark:border-zinc-800"
          onSubmit={(e) => {
            e.preventDefault();
            send(draft);
          }}
        >
          <div className="mx-auto flex max-w-3xl items-end gap-2">
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  send(draft);
                }
              }}
              rows={Math.min(8, Math.max(2, draft.split("\n").length))}
              placeholder="Dán link GitHub/blog… (Enter gửi, Shift+Enter xuống dòng)"
              title="Thêm “và xuất file md” vào tin nhắn để nhận file báo cáo."
              className="min-w-0 flex-1 resize-none rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm outline-none focus:border-indigo-500 dark:border-zinc-700 dark:bg-zinc-900"
            />
            {streaming ? (
              <button
                type="button"
                onClick={() => abortRef.current?.abort()}
                className="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-500"
              >
                Dừng
              </button>
            ) : (
              <button
                type="submit"
                disabled={!draft.trim()}
                className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-40"
              >
                Gửi
              </button>
            )}
          </div>
        </form>
      </main>

      <div className="hidden w-96 shrink-0 border-l border-zinc-200 lg:block dark:border-zinc-800">
        <EvidencePanel evidence={evidence} artifacts={artifacts} selected={selected} loading={evidenceLoading} />
      </div>

      {panel !== "none" && (
        <div className="fixed inset-0 z-20 flex bg-black/40 lg:hidden" onClick={() => setPanel("none")}>
          <div
            className={`h-full w-[85%] max-w-sm bg-white dark:bg-zinc-950 ${panel === "evidence" ? "ml-auto" : ""}`}
            onClick={(e) => e.stopPropagation()}
          >
            {panel === "sidebar" ? (
              sidebar
            ) : (
              <EvidencePanel evidence={evidence} artifacts={artifacts} selected={selected} loading={evidenceLoading} />
            )}
          </div>
        </div>
      )}
    </div>
  );
}

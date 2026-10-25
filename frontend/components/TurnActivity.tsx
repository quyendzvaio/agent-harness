"use client";

import { useState } from "react";
import { artifactUrl, type CitationReport, type Usage } from "@/lib/api";

export type ToolRun = {
  call_id: string;
  tool: string;
  input: Record<string, unknown>;
  status?: string;
  evidence_labels?: string[];
  duration_ms?: number;
};

export type TurnState = {
  status: "streaming" | "done" | "error" | "cancelled";
  turnId?: string;
  skills: string[];
  skillLimits?: Record<string, number>;
  tools: ToolRun[];
  citation?: CitationReport;
  usage?: Usage;
  error?: { code: string; message: string };
  artifacts: { id: string; filename: string; size_bytes: number }[];
  /** Model calls that broke mid-stream and were retried. */
  retries?: number;
  startedAt: number;
  finishedAt?: number;
};

const STATUS_STYLE: Record<string, string> = {
  ok: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  running: "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
};
const BAD = "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300";

function summarize(input: Record<string, unknown>): string {
  return Object.entries(input)
    .filter(([key, value]) => key !== "url" && value !== "" && value !== null && value !== undefined)
    .map(([key, value]) => `${key}=${typeof value === "string" ? value : JSON.stringify(value)}`)
    .join("  ");
}

export function TurnActivity({ turn, now, onCite }: { turn: TurnState; now: number; onCite: (label: string) => void }) {
  const [open, setOpen] = useState(true);
  const running = turn.tools.filter((t) => !t.status).length;
  const failed = turn.tools.filter((t) => t.status && t.status !== "ok").length;
  const seconds = Math.max(0, (turn.finishedAt ?? now) - turn.startedAt) / 1000;

  return (
    <div className="mb-3 rounded-lg border border-zinc-200 bg-zinc-50 text-xs dark:border-zinc-800 dark:bg-zinc-900/60">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full flex-wrap items-center gap-2 px-3 py-2 text-left text-zinc-600 dark:text-zinc-400"
      >
        <span className="font-mono">{open ? "▾" : "▸"}</span>
        {turn.skills.map((s) => (
          <span
            key={s}
            className="rounded bg-violet-100 px-1.5 py-px font-medium text-violet-700 dark:bg-violet-950 dark:text-violet-300"
          >
            skill: {s}
          </span>
        ))}
        <span>
          {turn.tools.length} tool call{turn.tools.length === 1 ? "" : "s"}
          {running > 0 && ` · ${running} đang chạy`}
          {failed > 0 && <span className="text-red-600 dark:text-red-400"> · {failed} lỗi</span>}
        </span>
        {!!turn.retries && (
          <span className="text-amber-600 dark:text-amber-400" title="Kết nối tới model bị đứt giữa chừng; đã gọi lại">
            ↻ gọi lại model {turn.retries} lần
          </span>
        )}
        {turn.status === "streaming" && (
          <span className="animate-pulse text-amber-600">● đang chạy {seconds.toFixed(0)}s</span>
        )}
        {turn.status !== "streaming" && <span>· {seconds.toFixed(1)}s</span>}
      </button>

      {open && (
        <div className="space-y-2 border-t border-zinc-200 px-3 py-2 dark:border-zinc-800">
          {turn.skillLimits && (
            <div className="text-zinc-500">
              Ngân sách skill:{" "}
              {Object.entries(turn.skillLimits)
                .map(([k, v]) => `${k}=${v}`)
                .join(", ")}
            </div>
          )}
          {turn.tools.length > 0 && (
            <ol className="space-y-1">
              {turn.tools.map((t) => (
                <li key={t.call_id} className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                  <span
                    className={`rounded px-1.5 py-px font-mono ${t.status ? (STATUS_STYLE[t.status] ?? BAD) : STATUS_STYLE.running}`}
                  >
                    {t.status ?? "…"}
                  </span>
                  <span className="font-mono font-semibold text-zinc-800 dark:text-zinc-200">{t.tool}</span>
                  <span className="min-w-0 break-all font-mono text-zinc-500">{summarize(t.input)}</span>
                  {t.duration_ms !== undefined && <span className="text-zinc-400">{t.duration_ms} ms</span>}
                  {t.evidence_labels?.map((label) => (
                    <button
                      key={label}
                      type="button"
                      onClick={() => onCite(label)}
                      className="rounded bg-indigo-100 px-1 font-mono text-indigo-700 hover:bg-indigo-200 dark:bg-indigo-950 dark:text-indigo-300"
                    >
                      {label}
                    </button>
                  ))}
                </li>
              ))}
            </ol>
          )}
          {turn.citation && (
            <div className="flex flex-wrap gap-x-3 text-zinc-600 dark:text-zinc-400">
              <span>
                Trích dẫn hợp lệ: <b className="text-emerald-600">{turn.citation.valid.length}</b>
              </span>
              <span>
                Không hợp lệ:{" "}
                <b className={turn.citation.invalid.length ? "text-red-600" : ""}>
                  {turn.citation.invalid.length ? turn.citation.invalid.join(", ") : 0}
                </b>
              </span>
              <span>
                Câu chưa trích dẫn: {turn.citation.uncited_sentences}/{turn.citation.total_sentences}
              </span>
            </div>
          )}
          {turn.usage && (
            <div className="text-zinc-500">
              {turn.usage.model ?? "model"} · {turn.usage.model_calls} lần gọi model · {turn.usage.tool_calls} tool ·{" "}
              {turn.usage.tokens_in.toLocaleString()} token vào / {turn.usage.tokens_out.toLocaleString()} ra
            </div>
          )}
          {turn.artifacts.map((a) => (
            <a
              key={a.id}
              href={artifactUrl(a.id)}
              className="inline-flex items-center gap-1 rounded border border-indigo-300 px-2 py-1 text-indigo-700 hover:bg-indigo-50 dark:border-indigo-800 dark:text-indigo-300 dark:hover:bg-indigo-950"
            >
              ⬇ {a.filename} <span className="text-zinc-400">({(a.size_bytes / 1024).toFixed(1)} KB)</span>
            </a>
          ))}
        </div>
      )}
      {turn.error && (
        <div className={`rounded-b-lg px-3 py-2 ${BAD}`}>
          <b className="font-mono">{turn.error.code}</b>: {turn.error.message}
        </div>
      )}
      {turn.status === "cancelled" && <div className="px-3 py-2 text-zinc-500">Đã dừng.</div>}
    </div>
  );
}

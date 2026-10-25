"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { artifactUrl, type Artifact, type Evidence } from "@/lib/api";

export function EvidencePanel({
  evidence,
  artifacts,
  selected,
  loading,
}: {
  evidence: Evidence[];
  artifacts: Artifact[];
  selected: string | null;
  loading: boolean;
}) {
  const [filter, setFilter] = useState("");
  const refs = useRef(new Map<string, HTMLElement>());

  useEffect(() => {
    if (selected) refs.current.get(selected)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [selected]);

  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return evidence;
    return evidence.filter((e) => `${e.label} ${e.locator} ${e.quote}`.toLowerCase().includes(q));
  }, [evidence, filter]);

  return (
    <aside className="flex h-full min-h-0 flex-col">
      <div className="border-b border-zinc-200 p-3 dark:border-zinc-800">
        <div className="mb-2 flex items-baseline justify-between">
          <h2 className="text-sm font-semibold">Evidence</h2>
          <span className="text-xs text-zinc-500">{loading ? "đang tải…" : `${evidence.length} mục`}</span>
        </div>
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Lọc theo nhãn, file, nội dung…"
          className="w-full rounded-md border border-zinc-300 bg-white px-2 py-1 text-xs outline-none focus:border-indigo-500 dark:border-zinc-700 dark:bg-zinc-900"
        />
      </div>

      {artifacts.length > 0 && (
        <div className="space-y-1 border-b border-zinc-200 p-3 dark:border-zinc-800">
          <h3 className="text-xs font-semibold text-zinc-500">File đã xuất</h3>
          {artifacts.map((a) => (
            <a
              key={a.id}
              href={artifactUrl(a.id)}
              className="block truncate text-xs text-indigo-600 hover:underline dark:text-indigo-400"
            >
              ⬇ {a.filename}
            </a>
          ))}
        </div>
      )}

      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
        {!evidence.length && !loading && (
          <p className="text-xs text-zinc-500">
            Chưa có evidence. Mỗi tool đọc nguồn tạo ra một nhãn [E#] mà câu trả lời trích dẫn.
          </p>
        )}
        {shown.map((e) => (
          <article
            key={e.label}
            ref={(el) => {
              if (el) refs.current.set(e.label, el);
              else refs.current.delete(e.label);
            }}
            className={`scroll-mt-2 rounded-md border p-2 text-xs ${
              selected === e.label
                ? "border-indigo-500 bg-indigo-50 ring-1 ring-indigo-500 dark:bg-indigo-950/50"
                : "border-zinc-200 dark:border-zinc-800"
            }`}
          >
            <header className="mb-1 flex items-baseline gap-2">
              <span className="font-mono font-bold text-indigo-700 dark:text-indigo-300">{e.label}</span>
              <span className="rounded bg-zinc-100 px-1 text-[10px] uppercase text-zinc-500 dark:bg-zinc-800">
                {e.kind}
              </span>
              <a
                href={e.url}
                target="_blank"
                rel="noreferrer"
                className="min-w-0 truncate font-mono text-zinc-600 hover:underline dark:text-zinc-400"
                title={e.url}
              >
                {e.locator}
              </a>
            </header>
            <Quote text={e.quote} expanded={selected === e.label} />
          </article>
        ))}
      </div>
    </aside>
  );
}

function Quote({ text, expanded }: { text: string; expanded: boolean }) {
  const [open, setOpen] = useState(false);
  const long = text.length > 400;
  const show = open || expanded || !long ? text : `${text.slice(0, 400)}…`;
  return (
    <>
      <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded bg-zinc-100 p-2 font-mono text-[11px] leading-snug text-zinc-800 dark:bg-zinc-900 dark:text-zinc-300">
        {show}
      </pre>
      {long && !expanded && (
        <button
          type="button"
          onClick={() => setOpen(!open)}
          className="mt-1 text-[11px] text-indigo-600 hover:underline dark:text-indigo-400"
        >
          {open ? "Thu gọn" : `Xem đủ (${text.length.toLocaleString()} ký tự)`}
        </button>
      )}
    </>
  );
}

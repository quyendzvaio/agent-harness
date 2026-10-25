"use client";

import { useEffect, useState } from "react";
import { repairMermaid } from "@/lib/mermaidRepair";

let renderCount = 0;

/** Renders a ```mermaid block (the agent's architecture_diagram output) with SVG/PNG download. */
export function MermaidDiagram({ code, final }: { code: string; final: boolean }) {
  const [svg, setSvg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showCode, setShowCode] = useState(false);
  const [repaired, setRepaired] = useState(false);

  useEffect(() => {
    if (!final) return;
    let cancelled = false;
    const id = `mermaid-${++renderCount}`;
    (async () => {
      const mermaid = (await import("mermaid")).default;
      const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        theme: dark ? "dark" : "neutral",
        look: "handDrawn", // also for diagrams the model wrote itself
        handDrawnSeed: 7, // stable strokes across re-renders
        htmlLabels: false, // plain SVG text: exportable to PNG through a canvas
        flowchart: { htmlLabels: false },
      });
      const attempts = [code, repairMermaid(code)].filter((c, i, all) => all.indexOf(c) === i);
      let firstError: unknown = null;
      for (const [i, source] of attempts.entries()) {
        try {
          const result = await mermaid.render(`${id}-${i}`, source);
          if (!cancelled) {
            setSvg(result.svg);
            setRepaired(i > 0);
            setError(null);
          }
          return;
        } catch (err) {
          document.getElementById(`d${id}-${i}`)?.remove(); // mermaid leaves its scratch element on errors
          firstError ??= err;
        }
      }
      if (!cancelled) setError(firstError instanceof Error ? firstError.message : String(firstError));
    })();
    return () => {
      cancelled = true;
    };
  }, [code, final]);

  if (!final) {
    return (
      <div className="my-3 rounded-lg border border-dashed border-zinc-300 p-4 text-center text-xs text-zinc-500 dark:border-zinc-700">
        Sơ đồ sẽ hiển thị khi câu trả lời hoàn tất…
      </div>
    );
  }

  return (
    <figure className="my-4 rounded-lg border border-zinc-200 dark:border-zinc-800">
      <div className="flex flex-wrap items-center gap-2 border-b border-zinc-200 px-3 py-1.5 text-xs dark:border-zinc-800">
        <span className="font-medium text-zinc-500">Sơ đồ</span>
        {repaired && (
          <span
            className="text-amber-600 dark:text-amber-400"
            title="Model tự viết Mermaid sai cú pháp; nhãn đã được đặt trong ngoặc kép"
          >
            đã tự sửa cú pháp
          </span>
        )}
        <span className="flex-1" />
        {svg && (
          <>
            <button
              type="button"
              className={BUTTON}
              onClick={() => download(new Blob([svg], { type: "image/svg+xml" }), "svg")}
            >
              Tải SVG
            </button>
            <button type="button" className={BUTTON} onClick={() => toPng(svg).then((png) => download(png, "png"))}>
              Tải PNG
            </button>
          </>
        )}
        <button type="button" className={BUTTON} onClick={() => setShowCode(!showCode)}>
          {showCode ? "Ẩn mã" : "Xem mã"}
        </button>
      </div>
      {error && (
        <p className="bg-red-50 px-3 py-2 text-xs text-red-700 dark:bg-red-950 dark:text-red-300">
          Không vẽ được sơ đồ: {error}
        </p>
      )}
      {svg && !showCode && (
        <div className="overflow-x-auto p-3 [&_svg]:mx-auto [&_svg]:h-auto" dangerouslySetInnerHTML={{ __html: svg }} />
      )}
      {!svg && !error && <p className="p-4 text-center text-xs text-zinc-400">Đang vẽ…</p>}
      {(showCode || error) && (
        <pre className="max-h-96 overflow-auto bg-zinc-50 p-3 font-mono text-[11px] dark:bg-zinc-900">{code}</pre>
      )}
    </figure>
  );
}

const BUTTON =
  "rounded border border-zinc-300 px-2 py-0.5 text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800";

function download(blob: Blob, ext: string) {
  const url = URL.createObjectURL(blob);
  const link = Object.assign(document.createElement("a"), { href: url, download: `architecture.${ext}` });
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function toPng(svg: string, scale = 2): Promise<Blob> {
  const image = new Image();
  image.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
  await image.decode();
  const canvas = document.createElement("canvas");
  canvas.width = Math.ceil(image.naturalWidth * scale);
  canvas.height = Math.ceil(image.naturalHeight * scale);
  const context = canvas.getContext("2d")!;
  context.fillStyle = window.matchMedia("(prefers-color-scheme: dark)").matches ? "#09090b" : "#ffffff";
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.drawImage(image, 0, 0, canvas.width, canvas.height);
  return new Promise((resolve, reject) =>
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("PNG export failed"))), "image/png"),
  );
}

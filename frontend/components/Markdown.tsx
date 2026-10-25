"use client";

import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { MermaidDiagram } from "./MermaidDiagram";

const CITATION = /\[(E\d+(?:\s*[,;]\s*E\d+)*)\]/g;

/** `[E3]` / `[E3, E5]` -> links the renderer turns into citation chips (code spans are left alone). */
function linkCitations(text: string): string {
  return text
    .split(/(```[\s\S]*?```|`[^`\n]*`)/g)
    .map((part, i) =>
      i % 2 === 1
        ? part
        : part.replace(CITATION, (_, labels: string) =>
            labels
              .split(/\s*[,;]\s*/)
              .map((label) => `[${label}](#cite-${label})`)
              .join(""),
          ),
    )
    .join("");
}

export function Markdown({
  text,
  known,
  onCite,
  final = true,
}: {
  text: string;
  /** Labels that exist in this conversation; others are shown as invalid. Null = not loaded yet. */
  known: Set<string> | null;
  onCite: (label: string) => void;
  /** False while the answer still streams: diagrams wait for their complete code. */
  final?: boolean;
}) {
  const components: Components = {
    pre({ node, children }) {
      const code = node?.children[0];
      const classes = code?.type === "element" ? code.properties.className : undefined;
      if (Array.isArray(classes) && classes.includes("language-mermaid") && code?.type === "element") {
        const source = code.children.map((c) => (c.type === "text" ? c.value : "")).join("");
        return <MermaidDiagram code={source.trimEnd()} final={final} />;
      }
      return <pre>{children}</pre>;
    },
    a({ href, children }) {
      if (href?.startsWith("#cite-")) {
        const label = href.slice(6);
        const invalid = known !== null && !known.has(label);
        return (
          <button
            type="button"
            onClick={() => onCite(label)}
            title={invalid ? `${label} không tồn tại trong evidence của hội thoại` : `Xem ${label}`}
            className={`mx-0.5 inline-flex items-center rounded px-1.5 py-px align-baseline font-mono text-[0.72em] font-semibold ${
              invalid
                ? "bg-red-100 text-red-700 line-through dark:bg-red-950 dark:text-red-300"
                : "bg-indigo-100 text-indigo-700 hover:bg-indigo-200 dark:bg-indigo-950 dark:text-indigo-300 dark:hover:bg-indigo-900"
            }`}
          >
            {label}
          </button>
        );
      }
      return (
        <a href={href} target="_blank" rel="noreferrer" className="text-indigo-600 underline dark:text-indigo-400">
          {children}
        </a>
      );
    },
  };
  return (
    <div className="prose-chat">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {linkCitations(text)}
      </ReactMarkdown>
    </div>
  );
}

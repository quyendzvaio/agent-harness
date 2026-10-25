// Best-effort fix for Mermaid flowcharts written by a model instead of the architecture_diagram tool.
// The usual failure is an unquoted label with parentheses, slashes or dashes: `API[API (FastAPI) - main.py]`.
// Quoting node and edge labels fixes most of them; anything else is left to the error display.

const ID = String.raw`([A-Za-z_][\w-]*)`;
const RULES: [RegExp, string][] = [
  [new RegExp(String.raw`${ID}\[\[(?!")([^\]"\n]*)\]\]`, "g"), '$1[["$2"]]'],
  [new RegExp(String.raw`${ID}\[\((?!")([^)"\n]*)\)\]`, "g"), '$1[("$2")]'],
  [new RegExp(String.raw`${ID}\(\((?!")([^)"\n]*)\)\)`, "g"), '$1(("$2"))'],
  [new RegExp(String.raw`${ID}\[(?![\[("/\\])([^\]"\n]*)\]`, "g"), '$1["$2"]'],
  [new RegExp(String.raw`${ID}\{(?![{"])([^}"\n]*)\}`, "g"), '$1{"$2"}'],
  [/\|(?!")([^|"\n]+)\|/g, '|"$1"|'],
];
const SKIP = /^\s*(%%|classDef\s|class\s|style\s|linkStyle\s|---|config:|look:|theme:)/;

export function repairMermaid(code: string): string {
  return code
    .split("\n")
    .map((line) => (SKIP.test(line) ? line : RULES.reduce((text, [rule, to]) => text.replace(rule, to), line)))
    .join("\n");
}

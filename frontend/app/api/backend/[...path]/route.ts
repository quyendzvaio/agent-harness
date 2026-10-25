import type { NextRequest } from "next/server";

// Server-side proxy to the FastAPI backend: the browser stays same-origin (no CORS), the API key
// stays on the server, and SSE bodies are passed through unbuffered.
const BACKEND_URL = (process.env.BACKEND_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
const FORWARD_REQUEST = ["accept", "content-type", "x-api-key"];
const FORWARD_RESPONSE = ["content-type", "content-disposition", "content-length", "etag", "www-authenticate"];

async function proxy(request: NextRequest, ctx: RouteContext<"/api/backend/[...path]">) {
  const { path } = await ctx.params;
  const target = `${BACKEND_URL}/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`;

  const headers = new Headers();
  for (const name of FORWARD_REQUEST) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  if (!headers.has("x-api-key") && process.env.BACKEND_API_KEY) {
    headers.set("x-api-key", process.env.BACKEND_API_KEY);
  }

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body: request.method === "GET" || request.method === "HEAD" ? undefined : await request.arrayBuffer(),
      signal: request.signal, // the user pressing Stop cancels the backend turn
      cache: "no-store",
      redirect: "manual",
    });
  } catch (error) {
    if (request.signal.aborted) return new Response(null, { status: 499 });
    return Response.json(
      { error: { code: "backend_unreachable", message: `Cannot reach ${BACKEND_URL}: ${String(error)}` } },
      { status: 502 },
    );
  }

  const out = new Headers();
  for (const name of FORWARD_RESPONSE) {
    const value = upstream.headers.get(name);
    if (value) out.set(name, value);
  }
  if (out.get("content-type")?.startsWith("text/event-stream")) {
    out.set("cache-control", "no-cache, no-transform");
    out.set("x-accel-buffering", "no");
  }
  return new Response(upstream.body, { status: upstream.status, headers: out });
}

export { proxy as GET, proxy as POST };

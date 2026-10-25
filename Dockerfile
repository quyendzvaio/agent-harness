FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra graph --no-install-project
COPY src ./src
COPY alembic.ini ./
RUN uv sync --frozen --no-dev --extra graph
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
# Run `agent-harness init` once (e.g. `docker compose run --rm api agent-harness init`) before serving.
CMD ["agent-harness", "serve", "--host", "0.0.0.0", "--port", "8000"]

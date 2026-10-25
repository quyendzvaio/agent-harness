# Research Agent Harness

Research agent đọc hiểu sâu **GitHub repository** và **blog kỹ thuật**, trả lời qua **FastAPI + SSE**, trích dẫn kiểm chứng được (`[E#]`), mở rộng bằng **custom skill** (`SKILL.md`). Agent dùng LLM tool-calling (LangChain `create_agent`, model qua endpoint tương thích OpenAI, mặc định Groq); toàn bộ policy nằm trong harness, độc lập framework.

Tài liệu: [docs/PRD.md](docs/PRD.md) (yêu cầu) · [docs/PLAN.md](docs/PLAN.md) (kiến trúc & kế hoạch).

## Chạy nhanh

```bash
cp .env.example .env            # điền LLM_API_KEY (BASE_URL, LLM_MODEL tùy chọn)
docker compose up -d postgres minio
uv sync --extra graph          # bỏ --extra graph nếu không cần github_code_graph
uv run agent-harness init       # migrate DB, tạo schema checkpoint, tạo bucket MinIO
uv run agent-harness serve      # http://127.0.0.1:8000/docs

curl -N -X POST localhost:8000/v1/chat -H 'Content-Type: application/json' \
  -d '{"message":"phân tích kiến trúc https://github.com/kennethreitz/setup.py"}'
```

Giao diện thử thủ công (Next.js): `cd frontend && npm install && npm run dev` → http://localhost:3100 (xem [frontend/README.md](frontend/README.md)).

Thêm "và xuất file md" vào tin nhắn để nhận event `artifact`; tải file bằng `GET /v1/artifacts/{id}`.
Bind ra ngoài `127.0.0.1` bắt buộc `AUTH_ENABLED=true` + `API_KEY` + `ADMIN_API_KEY` (header `X-API-Key`).

## Test

```bash
uv run pytest                                   # offline: unit, contract (memory), integration, architecture
docker compose up -d postgres-test minio        # + contract/e2e trên Postgres và MinIO thật
TEST_DATABASE_URL=postgresql+psycopg://agent:agent@localhost:5434/agent_test \
TEST_MINIO_ENDPOINT=localhost:9000 uv run pytest
uv run pytest -m live tests/e2e                 # gọi LLM (BASE_URL) + GitHub thật
```

## Muốn thay X thì sửa ở đâu?

| Muốn thay | Sửa | Không phải sửa | Test chứng minh |
|---|---|---|---|
| Thêm một loại phân tích | Thả thư mục `skills/<tên>/SKILL.md` vào `SKILLS_USER_DIR`, gọi `POST /v1/skills/reload` | Không có dòng code nào | S1 |
| Engine điều phối (LangChain → khác) | Viết class có `async run(ctx) -> str` (xem `harness/engine.py`), truyền qua `Overrides.engine_factory` | api, application, harness, tools | S2 |
| Bật/tắt nguồn | `ENABLED_SOURCES` | Code | S3 |
| GitHub client / crawler | `sources/github/client.py` (hoặc truyền `Overrides.github_client`) | analyzer, tools, middleware, skill | S4 |
| Chính sách xuất file | `EXPORT_MODE` hoặc `domain/policies/export_policy.py` | Tools, engine, API | S5 |
| Kiểm tra trích dẫn | `research/citation_validator.py`, `domain/policies/citation_policy.py` | Mọi thứ khác | S6 |
| Kiểm tra ngữ nghĩa trích dẫn (Jev, ...) | Viết class `ClaimVerifier` (`ports/claim_verifier.py`), truyền `Overrides.claim_verifier` | Harness, tools, API | `test_claim_verifier_*` |
| Code graph mức symbol (graphify → khác) | Viết class `CodeGraphBuilder` (`ports/code_graph.py`), truyền `Overrides.code_graph_builder`; giới hạn `CODE_GRAPH_MAX_FILES`, `CODE_GRAPH_TIMEOUT_S` | Tools, skill, harness | `tests/integration/test_code_graph.py` |
| Sơ đồ kiến trúc (Mermaid → khác) | `research/diagram.py` (sinh code), `frontend/components/MermaidDiagram.tsx` (vẽ); khi nào vẽ: `_DIAGRAM_PHRASES` trong `application/intent_parser.py` | Tool, skill, engine | `tests/unit/test_diagram.py`, `tests/integration/test_diagram_tool.py` |
| Nhận dạng manifest/kênh của bản đồ hệ thống (image → loại, entrypoint → vai trò, thư viện client/server, cách đọc env) | `sources/github/system_map/catalog/*.yaml`; giới hạn `SYSTEM_MAP_MAX_FILES` | Code của `system_map/`, tool, skill | `test_system_map_extract`, `test_system_map_render`, `test_system_map_tool` |
| Ngân sách cho phân tích sâu | Frontmatter `limits:` của skill, trần `LIMITS_CEILING__*` | Code | `test_deep_dive_skill_*` |
| Giới hạn / retry | `LIMITS__*` hoặc `harness/middleware/{limits,retry}.py` | Tools, engine | S7 |
| Database | `adapters/postgres/` (cùng port `ports/repositories.py`) | Business code | S8 (contract test) |
| Lưu artifact (MinIO → S3/R2) | `adapters/storage/` hoặc chỉ `MINIO_ENDPOINT` | Business code | S10, S11 |
| Provider / model / model dự phòng | `BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_FALLBACK_MODELS`, `MODEL_IDLE_TIMEOUT_S` (mọi API tương thích OpenAI); provider khác hẳn: `adapters/models/` | Code | S12, `tests/unit/test_models.py` |
| Xác thực (API key → JWT) | `api/auth.py` | Mọi lớp khác | `test_auth_enabled_requires_keys` |

Quy tắc phụ thuộc giữa các package được kiểm tra tự động bởi `tests/architecture/test_import_rules.py`.

## Cấu trúc

```
src/agent_harness/
  domain/        kiểu dữ liệu, lỗi, policy thuần (không framework)
  ports/         Protocol cho persistence, artifact store, HTTP, skill catalog, claim verifier, code graph
  harness/       runtime, context, tool registry/executor, middleware, engine port, scripted engine
  skills/        parser/registry/hooks + skill built-in (builtin/*/SKILL.md)
  sources/       safe HTTP (chống SSRF), github/, blog/ — độc lập nhau
  research/      chunking, citation validator, system prompt
  capabilities/  đóng gói service thành tool, finalizer
  application/   chat, conversation, skill, artifact service; intent parser
  adapters/      langchain/, models/, postgres/ (+ Alembic), storage/ (MinIO), memory/, graphify/ (code graph)
  api/           FastAPI routers, SSE, auth, error mapping
  bootstrap/     composition root (container), app factory, init
  interfaces/    CLI: serve, init
frontend/        Next.js: chat SSE, tool call, evidence [E#] (proxy /api/backend → API)
```

## Viết một skill

```markdown
---
name: security-review
description: Rà soát các điểm nhạy cảm bảo mật trong một GitHub repo.
source_types: [github]
allowed_tools: [github_repo_overview, github_list_tree, github_read_file]
resources: [templates/report.md]
---
# Hướng dẫn cho agent ...
```

Skill trong `SKILLS_USER_DIR` chỉ là dạng khai báo. `hooks.py` (`on_load`, `post_tool`, `on_final`) chỉ chạy với skill built-in nằm trong repo.

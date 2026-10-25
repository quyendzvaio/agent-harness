# Implementation Plan — Research Agent Harness

> Đi kèm [PRD.md](PRD.md) · Cập nhật: 2026-10-21

## 1. Quyết định đã chốt

| Chủ đề | Quyết định |
|---|---|
| Kiến trúc nền | PA1 (vertical slice theo nguồn, evidence/citation, lộ trình từng bước), bổ sung 4 điểm của PA2: port điều phối, chống SSRF + retry, composition root, checkpointer có sẵn |
| Agent | **LLM tool-calling tự do** qua LangChain `create_agent`. Toàn bộ policy viết thuần Python trong harness, nối sang LangChain qua bridge |
| Skill | Thư mục `SKILL.md` + hook Python tùy chọn (chỉ chạy với skill trusted), nạp dần (progressive disclosure) |
| API | **FastAPI**, chat streaming qua **SSE** |
| Artifact | **MinIO tự host** (S3-compatible) qua port `ArtifactStore`, SDK `minio`. Bucket private, API stream file về client |
| Model | Model mặc định và model dự phòng qua một endpoint **tương thích OpenAI** (`BASE_URL`; chốt Groq 2026-10-22, trước đó OpenRouter) |
| Lưu trữ | **PostgreSQL 16**. SQLAlchemy 2 async + driver `psycopg` 3 (dùng chung driver với `langgraph-checkpoint-postgres`). Migration bằng Alembic |
| Code | Async toàn bộ. Tên package giữ là `agent_harness` |

## 2. Kiến trúc lớp

```
api (FastAPI) ──► application ──► harness ──► domain
                       │              │          ▲
                       ▼              ▼          │
                     ports ◄──────────┴──── adapters (langchain, postgres, fs, models)
                       ▲
capabilities ──► sources / research / skills ──► domain, ports
bootstrap/container.py: nơi duy nhất wiring mọi thứ theo settings
```

## 3. Cấu trúc thư mục (`src/agent_harness/`, đúng như đã implement)

```
domain/          conversation.py sources.py evidence.py skills.py events.py errors.py
                 policies/{source,export,execution,citation}_policy.py
ports/           repositories.py (repos + UnitOfWork + SourceCache) artifact_store.py
                 source_fetcher.py skill_source.py claim_verifier.py
harness/         engine.py (port AgentEngine + Finalizer) runtime.py context.py evidence_store.py
                 scripted.py (engine tất định, không LLM)
                 middleware/ base.py audit_log.py error_boundary.py limits.py (+ ModelGuard)
                             policy_guard.py retry.py untrusted_content.py tool_scope.py
                 tools/ spec.py registry.py executor.py validation.py
                 observability/ logger.py
skills/          parser.py registry.py hooks.py
                 builtin/{github-architecture,github-deep-dive,blog-explainer,export-report}/SKILL.md
sources/         registry.py  http.py (SafeHttpClient chống SSRF ở mỗi redirect + CachingFetcher)
                 github/{detector,client,snapshot,repository_reader,architecture_analyzer,dependency_graph}.py
                 blog/{detector,fetcher,article_parser}.py
research/        chunking.py citation_validator.py prompting.py prompts/system.md
capabilities/    source_tools.py skill_tools.py export_tools.py finalizers.py
application/     chat_service.py conversation_service.py skill_service.py artifact_service.py
                 intent_parser.py
adapters/        langchain/ engine.py bridge.py
                 models/ openai_compatible.py scripted.py (ScriptedChatModel cho test)
                 postgres/ db.py tables.py repositories.py checkpointer.py migrations/ (Alembic)
                 memory/ repositories.py artifact_store.py   # cùng contract test với postgres/minio
                 storage/ minio_artifact_store.py
api/             app.py deps.py auth.py errors.py sse.py schemas.py
                 routers/{chat,conversations,skills,artifacts,health}.py
interfaces/      cli.py (serve, init)
config/          settings.py
bootstrap/       container.py (composition root) web.py (app factory + lifespan) setup.py (init)
```

### Quy tắc phụ thuộc (kiểm bằng `tests/architecture/test_import_rules.py`)

| Package | Được import (nội bộ) | Cấm (thư viện ngoài) |
|---|---|---|
| `domain`, `ports`, `config` | `domain` (+ chính nó) | langchain, langgraph, fastapi, httpx, sqlalchemy, psycopg, minio |
| `harness` | `domain`, `ports` | như trên |
| `skills` | `domain`, `ports`, `harness.observability` | như trên |
| `research` | `domain`, `ports` | như trên |
| `sources` | `domain`, `ports`, `harness.observability` (`github` ↔ `blog` không import nhau) | langchain, fastapi, sqlalchemy, minio (được dùng httpx) |
| `capabilities` | `domain`, `ports`, `harness`, `skills`, `sources`, `research` | mọi framework/infra |
| `application` | `domain`, `ports`, `harness` | mọi framework/infra |
| `adapters.langchain`, `adapters.models` | `domain`, `ports`, `harness` | fastapi, sqlalchemy, minio |
| `adapters.postgres` | `domain`, `ports`, `config` | fastapi, minio, langchain (langgraph chỉ trong `checkpointer`) |
| `adapters.storage`, `adapters.memory` | `domain`, `ports` | framework còn lại |
| `api` | `domain`, `application`, `config` | langchain, sqlalchemy, httpx, minio; không import `adapters`/`sources`/`bootstrap` |
| `interfaces` | `config`, `bootstrap`, `harness.observability` | langchain, sqlalchemy, minio |
| `bootstrap` | tất cả | – |

Chỉ `adapters.langchain`, `adapters.models`, `adapters.postgres.checkpointer` và `bootstrap` được import LangChain/LangGraph. Test còn kiểm tra rằng import `domain`, `ports`, `harness`, `application`, `api` **không** kéo theo langchain/sqlalchemy/minio lúc runtime.

## 4. Các thiết kế chính

### 4.1 Luồng một lượt chat
1. `api/routers/chat.py` xác thực, gọi `ChatService.stream_turn(message, conversation_id)` và lấy event đầu tiên trước khi mở stream, để conversation không tồn tại trả về 404 thay vì một stream hỏng.
2. `ChatService` (một UnitOfWork) tạo hoặc lấy conversation, tạo `turn(status=running)`, ghi message của user, nạp evidence cũ của conversation.
3. `parse_intent` dựng `Intent` (urls, explicit_export, language), sau đó dựng `RunContext`.
4. `AgentRuntime.stream(ctx)` chạy `engine.run(ctx)` dưới `turn_timeout_s`, chạy các finalizer (hook `on_final` của skill, kiểm tra trích dẫn), rồi stream event từ `ctx.events`. Stream luôn kết thúc bằng đúng một event `done` hoặc `error`.
5. `LangChainAgentEngine.run`: dựng `create_agent(model, tools, middleware, checkpointer)` **cho từng lượt**, để tool và middleware gắn với `RunContext` của lượt đó. Gọi `astream(stream_mode=["messages","updates"])`: chunk của node `model` được phát thành event `token`, còn message AI cuối cùng là câu trả lời. Checkpointer dùng `thread_id = conversation_id`.
6. Khi gặp event kết thúc (`done`/`error`), `ChatService` **lưu trước rồi mới chuyển event cho client**: message của assistant, evidence mới, tool invocation, cập nhật turn.
7. Client ngắt kết nối thì ghi `turn.status=cancelled`. Việc ghi chạy trong một task riêng, để cancel scope của request không chặn được nó.

### 4.2 Middleware của harness (không phụ thuộc framework)
- Interface: `ToolMiddleware.__call__(call, ctx, next) -> ToolResult`. Phía model dùng `ModelGuard.before_model/after_model`.
- Chuỗi tool (ngoài cùng → trong cùng): `audit_log → error_boundary → limits → policy_guard → retry → untrusted_content → [skill post_tool hook] → validation → handler`.
  - `error_boundary` đặt ngoài `limits` và `policy_guard`, để mọi lỗi đều thành `ToolResult` có cấu trúc. Lỗi fatal (`limit_exceeded`) gán vào `ctx.fatal` và kết thúc lượt.
  - `retry` chỉ retry `TransientError` (timeout, 5xx, 429).
- `tool_scope.allowed_tool_names(ctx, registry)` là **một hàm duy nhất**, dùng ở cả hai phía: ẩn tool khỏi model và chặn lời gọi trong `policy_guard`.
- `adapters/langchain/bridge.py`:
  - Mỗi `ToolSpec` thành một `StructuredTool`, gọi `ToolExecutor.execute`, nên toàn bộ chuỗi middleware chạy bên trong.
  - `HarnessModelMiddleware.awrap_model_call` áp `ModelGuard`, lọc tool, chèn system prompt động.
  - `ModelFallbackMiddleware` của LangChain được gắn vào khi có model dự phòng.
- Engine `ScriptedEngine` (không LLM) dùng chung executor và guard. Test S2 chứng minh hai engine cho ra kết quả giống hệt nhau.

### 4.3 Skill
- Thư mục built-in có trust=`trusted`, `SKILLS_USER_DIR` có trust=`declarative`.
- `parser.py` đọc frontmatter YAML và validate bằng pydantic. Skill lỗi bị bỏ qua và ghi log.
- Tool `load_skill`, `read_skill_resource` (chống path traversal). Middleware `tool_scope` đọc `active_skill` từ state.
- Hook chạy qua `skills/hooks.py` với timeout. Lỗi trong hook chỉ được ghi log, không làm hỏng lượt chat.

### 4.4 PostgreSQL
- **Driver:** `postgresql+psycopg://` cho SQLAlchemy async, `psycopg_pool.AsyncConnectionPool` cho `AsyncPostgresSaver`. Hai pool tách riêng, cấu hình qua cùng một `DATABASE_URL`.
- **Schema:** bảng ứng dụng ở schema `public` (xem PRD §10), checkpoint ở schema `langgraph`.
- **Migration:**
  - Bảng ứng dụng: `alembic upgrade head`.
  - Bảng checkpoint và bucket: lệnh `agent-harness init` gọi `AsyncPostgresSaver.setup()` và tạo bucket MinIO nếu chưa có.
  - API **không** tự migrate khi khởi động. `/readyz` báo lỗi nếu schema chưa đúng revision.
- **Repository + UnitOfWork:** port nằm trong `ports/`. Có hai implementation là `adapters/postgres` và `adapters/memory`. Cả hai chạy chung **một bộ contract test**, để đảm bảo bản fake hành xử giống thật.
- **docker-compose.yml:**
  - `postgres:16`, cổng host **5433** (vì 5432 đã có dự án khác dùng), volume `pgdata`.
  - Service `postgres-test` dùng tmpfs, cổng 5434.
  - Service `api`.
- **Test:** `TEST_DATABASE_URL` trỏ vào `postgres-test`. Mỗi test chạy trong transaction rồi rollback. Không có biến này thì các test Postgres được skip với lý do rõ ràng.

### 4.5 Cấu hình (`config/settings.py`, pydantic-settings)
- `DATABASE_URL`, `LLM_API_KEY`, `BASE_URL`, `LLM_MODEL`, `LLM_FALLBACK_MODELS?` (danh sách, mặc định rỗng), `GITHUB_TOKEN?`
- `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET=artifacts`, `MINIO_SECURE=false`
- `AUTH_ENABLED`, `API_KEY?`, `ADMIN_API_KEY?` (xem §4.6)
- `SKILLS_USER_DIR`
- `ENABLED_SOURCES=github,blog`
- `LIMITS__MAX_MODEL_CALLS`, `LIMITS__MAX_TOOL_CALLS`, `LIMITS__TURN_TIMEOUT_S`, `LIMITS__MAX_FETCH_BYTES`
- `ALLOWED_HOSTS?`

### 4.6 Xác thực (đã chốt: cờ `AUTH_ENABLED`)
Lý do nên có API key khi API được mở ra ngoài `localhost`:
- **Tiền:** mỗi lượt chat dùng `LLM_API_KEY` của bạn. Endpoint mở là ai cũng tiêu được credit.
- **Agent fetch URL hộ người khác:** dù đã chống SSRF, một endpoint mở vẫn thành proxy miễn phí để fetch web, và tiêu hết rate limit của `GITHUB_TOKEN`.
- **Dữ liệu:** `/v1/conversations` và `/v1/artifacts` để lộ toàn bộ lịch sử và báo cáo.
- **Quyền vận hành:** `POST /v1/skills/reload` làm thay đổi hành vi của agent.

Quyết định: cờ `AUTH_ENABLED`. Tắt khi uvicorn chỉ bind vào `127.0.0.1`. Bật bắt buộc khi bind ra `0.0.0.0`: thiếu key thì app từ chối khởi động. Phần này chỉ nằm ở `api/auth.py` (một FastAPI dependency), không ảnh hưởng các lớp khác.

### 4.7 MinIO
- Port `ArtifactStore`: `put(key, data, media_type) -> StoredObject(uri, sha256, size)`, `open(key) -> AsyncIterator[bytes]`, `delete(key)`, `exists(key)`, `health()`.
- `minio_artifact_store.py` bọc SDK `minio` (vốn là sync) bằng `asyncio.to_thread`. Chuyển sang AWS S3 hay R2 chỉ cần đổi endpoint và credential.
- `ArtifactService.save()` ghi object trước, sau đó ghi dòng `artifacts` trong transaction. DB lỗi thì `delete(key)` để bù.
- docker-compose: `minio/minio`, cổng 9000 (API) và 9001 (console), volume `miniodata`. Test dùng bucket riêng tên `artifacts-test-<uuid>` và xóa sau khi chạy xong. Không có `TEST_MINIO_ENDPOINT` thì test MinIO được skip.

### 4.8 Model (endpoint tương thích OpenAI) và dự phòng
- `adapters/models/openai_compatible.py` tạo `ChatOpenAI(model=LLM_MODEL, base_url=BASE_URL)` của `langchain-openai`. Groq, OpenRouter (`https://openrouter.ai/api/v1`), vLLM hay Ollama đều chỉ cần đổi `BASE_URL`/`LLM_API_KEY`/`LLM_MODEL`.
- Có `LLM_FALLBACK_MODELS` thì engine thêm `ModelFallbackMiddleware` của LangChain, với các model dự phòng đi qua cùng endpoint.
- `ModelRetryMiddleware` (trong cùng, sau fallback) gọi lại lần gọi model lỗi tạm thời (`ModelError.is_retryable` của `langchain_core`: kết nối, timeout, 429, 5xx), `MODEL_MAX_RETRIES` lần. Lý do (2026-10-25): provider `kiraai.vn` cắt stream khi ~60–75s không có byte nào, làm hỏng cả lượt. Engine phát `retract {message_id}` khi một lần gọi mới bắt đầu trong lúc message đang stream chưa hoàn tất, để client bỏ phần chữ dở.
- **Hạn mức Groq free tier (đo 2026-10-22):** mọi model chat của tài khoản bị giới hạn ~6–8k token/phút (`qwen/qwen3.8-27b`: ITPM 7.000). Một lần gọi model của harness đã ~5k token input (system prompt + schema tool), nên lượt nào cần từ 2 lần gọi trở lên sẽ bị `413/429 rate_limit_exceeded`. Phân tích repo cần Dev Tier hoặc provider có hạn mức cao hơn.
- Model đã dùng được ghi vào `turns.metadata` và event `done.usage`.

## 5. Kịch bản thay thế, chứng minh bằng `tests/integration/test_swappability.py`

| # | Thay đổi | Kỳ vọng |
|---|---|---|
| S1 | Thả thư mục skill mới rồi gọi reload | Skill xuất hiện ở `/v1/skills`, agent nạp được, không sửa code |
| S2 | Đổi AgentEngine: LangChain ↔ `ScriptedEngine` | Bộ test API vẫn pass |
| S3 | `ENABLED_SOURCES=github` | URL blog trả `capability_unavailable`, GitHub vẫn chạy |
| S4 | Thay GitHub client bằng fake | Analyzer, middleware và skill không phải sửa |
| S5 | Export policy luôn từ chối | Model không thấy tool export, không có file nào được ghi, chat vẫn trả lời |
| S6 | Model bịa `[E99]` | `citation_report.invalid = ["E99"]` |
| S7 | Tool lỗi tạm thời / vượt `max_tool_calls` | Retry chạy, sau đó có `error{tool_failed / limit_exceeded}`, stream đóng sạch, `turn.status=failed` |
| S8 | Repository: memory ↔ Postgres | Cùng một bộ contract test, cả hai pass |
| S9 | Skill người dùng có `hooks.py` | Hook không chạy |
| S10 | ArtifactStore: memory ↔ MinIO | Cùng một bộ contract test (put/get/delete/exists), cả hai pass |
| S11 | MinIO không truy cập được khi export | `error{tool_failed}` cho riêng tool export, câu trả lời chat vẫn trả về, không có dòng `artifacts` mồ côi |
| S12 | Model chính lỗi, có model dự phòng | Lượt chat hoàn thành bằng model dự phòng, `done.usage.model` ghi đúng model đã dùng |

Các test khác:
- **Unit:** policy (gồm SSRF và redirect sang IP nội bộ), parser skill, tool registry/executor, từng middleware, citation validator, detector, article parser, intent parser.
- **Architecture:** kiểm quy tắc import ở §3.
- **E2E (marker `live`, mặc định skip):** chạy thật với LLM (`BASE_URL`) + GitHub.

## 6. Dependencies

- **Runtime:** `fastapi`, `uvicorn[standard]`, `pydantic-settings`, `sqlalchemy[asyncio]>=2`, `psycopg[binary]>=3`, `psycopg-pool`, `alembic`, `langgraph-checkpoint-postgres`, `minio`, `httpx`, `pyyaml`, cùng các gói đang có: `langchain`, `langchain-openai`, `python-dotenv`.
- **Dev:** `pytest`, `pytest-asyncio`, `respx` (mock httpx).
- **Dọn dẹp:**
  - Xóa `tools.py` ở root.
  - Entry script đổi thành `agent-harness = "agent_harness.interfaces.cli:main"`, gồm các lệnh con `serve` và `init`.
  - Thêm `.env.example`, `docker-compose.yml`, `Dockerfile`.
  - Thêm vào README bảng "muốn thay X thì sửa file nào".

## 7. Thứ tự triển khai (mỗi bước xong phải có test xanh)

| Bước | Nội dung | Điều kiện hoàn thành |
|---|---|---|
| 1 | `domain`, `ports`, `config`, test kiến trúc | Unit test policy pass; test kiến trúc pass |
| 2 | `harness`: tool spec/registry/executor, middleware | Test từng middleware và test chuỗi middleware |
| 3 | `skills` + 3 skill built-in | Test parser, loader, trust, path traversal |
| 4 | `sources` (safe http, github, blog) + `research` | Test với fixture + respx, không gọi mạng thật |
| 5 | `adapters/postgres`, `adapters/storage` (MinIO), `adapters/memory`, Alembic, docker-compose | Contract test của repository và ArtifactStore pass trên cả hai implementation |
| 6 | `capabilities` + `application` | Test service với memory repo + ScriptedEngine |
| 7 | `adapters/langchain` + model openai-compatible/scripted | Test engine với `ScriptedChatModel` có tool call |
| 8 | `api` + `bootstrap` + `cli` | Test API bằng httpx `ASGITransport`, parse SSE |
| 9 | Swappability S1–S12, README | Toàn bộ test pass |

## 8. Verification

```bash
docker compose up -d postgres postgres-test minio
uv sync
uv run alembic upgrade head && uv run agent-harness init
TEST_DATABASE_URL=postgresql+psycopg://agent:agent@localhost:5434/agent_test \
TEST_MINIO_ENDPOINT=localhost:9000 uv run pytest -q
uv run agent-harness serve
curl -N -X POST localhost:8000/v1/chat -H 'Content-Type: application/json' \
  -d '{"message":"phân tích kiến trúc https://github.com/<repo-nhỏ>"}'
```

Kết quả mong đợi:
- Stream có `conversation`, `skill_loaded`, `tool_*`, `token`, `citation_report`, `done`.
- Thêm "và xuất file md" vào tin nhắn thì có thêm event `artifact`, object xuất hiện trong bucket (xem tại console `localhost:9001`), và `GET /v1/artifacts/{id}` trả về file.
- Kiểm tra test kiến trúc: thêm tạm `import sqlalchemy` vào `application/chat_service.py`, test phải fail.

## 9. Ghi chú triển khai
- **Timeout của model:** `ChatOpenAI.timeout` tính bằng **giây** (120). (Bản OpenRouter cũ dùng `ChatOpenRouter.request_timeout` tính bằng mili giây; truyền nhầm giây thì mọi request timeout ngay và kẹt trong vòng retry của SDK.) `tests/unit/test_models.py` kiểm tra timeout và `BASE_URL`.
- **Image MinIO:** MinIO đã ngừng phát hành image community trên Docker Hub. `docker-compose.yml` được pin vào `quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z` (máy dev đã có sẵn), với `pull_policy: missing`.
- **Migration:** chạy sync qua driver `postgresql+psycopg` (psycopg 3 hỗ trợ cả sync lẫn async), nên gọi được cả khi đang có event loop chạy.
- **Giới hạn SSRF đã biết:** IP được kiểm tra trước khi httpx kết nối, nên DNS rebinding (DNS đổi IP giữa lúc kiểm tra và lúc kết nối) chưa được chặn. Khi lên production nên đặt thêm egress proxy hoặc firewall.
- **Evidence** dùng nhãn theo conversation (`E1`, `E2`… nối tiếp qua các lượt), unique theo `(conversation_id, label)`, lưu thẳng thông tin nguồn (kind/url/canonical_id) thay vì khóa ngoại sang `sources`. Bảng `sources` là cache HTTP với khóa `cache_key`.
- **Kết quả kiểm chứng (2026-10-21):**
  - 137 test pass trên Postgres + MinIO thật (126 pass offline, 11 skip).
  - Một lượt live với `minimax/minimax-m3.1-flash:free` trên `kennethreitz/setup.py`: 7 lần gọi model, 12 tool call, 5 trích dẫn hợp lệ, 0 trích dẫn bịa. File `.md` lưu vào MinIO và tải về được.

## 10. Phân tích repo sâu (2026-10-22)
- **Snapshot**: `RepositoryReader` tải tarball một lần (≤60MB nén, ≤512KB/file, ≤20k file, bỏ thư mục vendored/build, bỏ file nhị phân, không ghi ra đĩa — ngoại lệ duy nhất là lúc dựng code graph, xem §11), cache theo `(repo, ref)` trong process. Mọi tool GitHub đọc từ snapshot; repo quá lớn thì quay về API từng file (khi đó search/đồ thị/coverage trả `capability_unavailable`). Redirect sang host khác (api.github.com → codeload) không mang theo header `Authorization`.
- **Đồ thị phụ thuộc** (`sources/github/dependency_graph.py`): module index hỗ trợ src-layout, namespace package (PEP 420) và không để file lồng như `x/types.py` che stdlib. `from pkg import mod` nối tới submodule, `from pkg import func` nối tới `pkg`. Đo: 3.463 file Python ≈ 9s (chủ yếu `ast.parse`), chạy trong thread và cache theo snapshot. Giới hạn: không đọc tsconfig `paths`/`baseUrl` (alias `@/`, `~/`, `#` được đếm là unresolved; alias theo `baseUrl` bị coi là package ngoài), không thấy import động và liên kết qua chuỗi (plugin registry, `INSTALLED_APPS`). Code graph (§11) bù phần lớn các trường hợp này.
- **Entrypoint** lấy từ khai báo trước, đoán theo tên file chỉ là phương án cuối và được ghi rõ.
- **Đọc toàn repo** = skill `github-deep-dive`: xin ngân sách 50 model / 180 tool / 1200s (bị chặn bởi `LIMITS_CEILING__*`), đọc theo thứ tự fan-in, `save_note` sau mỗi module, `github_coverage` để biết còn thiếu gì; `ContextEditingMiddleware` xoá output tool cũ khi context > `CONTEXT_CLEAR_TRIGGER_TOKENS` (giữ overview, đồ thị, skill, ghi chú). `MODEL_REASONING_EFFORT` chỉnh mức thinking của model.
- **ClaimVerifier** (`ports/claim_verifier.py`): điểm cắm cho kiểm tra ngữ nghĩa trích dẫn (Jev); truyền qua `Overrides.claim_verifier`.

## 11. Code graph mức symbol qua graphify (2026-10-22)
- **Lý do không tự viết:** skill graphify cho Claude Code chỉ là kịch bản điều phối; phần làm việc là thư viện `graphifyy` (tree-sitter 25+ ngôn ngữ, resolve tsconfig/workspace/import động, calls/inherits/uses, phân cụm Leiden, god nodes). Agent của harness không có shell nên dùng thư viện đó sau một port, không dùng skill của nó.
- **Port** `ports/code_graph.py` (`CodeGraphBuilder.build(files) -> CodeGraph`); **adapter** `adapters/graphify/code_graph.py` là nơi duy nhất import `graphify`/`networkx` (kiểm bằng test kiến trúc). Cài bằng `uv sync --extra graph` (Docker image đã cài). Không cài thì `github_code_graph` trả `capability_unavailable`, các tool khác không đổi. Thay implementation: `Overrides.code_graph_builder` (`False` để tắt).
- **Dựng graph:** chỉ file code (`CODE_EXTENSIONS` của graphify) và manifest (pyproject, go.mod, Cargo.toml); bỏ đường dẫn có `..`. Ghi vào `tempfile.TemporaryDirectory` (0700), cache AST của graphify cũng nằm trong đó, xoá ngay sau khi dựng — đây là ngoại lệ duy nhất cho "snapshot không ghi đĩa". Chạy tuần tự (`parallel=False`: không fork process từ server đa luồng) dưới một lock của module, vì graphify giữ cache resolve trong biến toàn cục. Chỉ chạy pass cấu trúc: không LLM, không mạng, không subprocess.
- **Cache & timeout:** `RepositoryReader.code_graph` dựng một lần cho mỗi snapshot trong một task; lời gọi chờ tối đa `CODE_GRAPH_TIMEOUT_S`, quá thời gian thì báo "still being built" nhưng việc dựng vẫn chạy và lần gọi sau dùng lại kết quả. Repo có hơn `CODE_GRAPH_MAX_FILES` file code bị từ chối.
- **Tool** `github_code_graph` (`op`: overview/find/neighbors/path/cycles). Evidence: định nghĩa là `path:Lx (definition)`, quan hệ là `path:Lx (graph: <relation>)` — cố ý khác dạng `path:Lx-Ly` để không được tính là đã đọc file. Skill dặn: quan hệ `INFERRED/AMBIGUOUS` phải đọc code xác nhận.
- **Đo (fastapi, 1.222 file code):** ~30s dựng (tuần tự; song song ~20s), +4s phân cụm, ~375MB RSS; 10k node, 22k cạnh (3.373 `calls`, 558 `inherits`). Repo nhỏ < 1s. Lần import đầu của graphify trong process mất thêm vài giây.
- **Không dùng:** phần trích xuất ngữ nghĩa cho docs/paper/ảnh của graphify (cần subagent của Claude Code hoặc Gemini), export HTML/Obsidian, watch.

## 12. Đầu ra hỏng của model và sơ đồ kiến trúc (2026-10-25)
- **Vấn đề:** `gpt-oss-120b-free` qua `kiraai.vn`, context dài nhiều vòng tool: 11.526/15.808 ký tự câu trả lời là reasoning nháp ("We need to produce a summary…"), một câu lặp 43 lần. Provider không tách reasoning ra trường riêng.
- **Chặn:** `adapters/models/output_guard.py` (hàm thuần: dòng đầu có giọng lập kế hoạch; một dòng ≥20 ký tự lặp ≥6 lần trong 80 dòng cuối). `GuardedChatOpenAI` kiểm tra mỗi ~200 ký tự khi stream và raise `DegenerateOutputError` (`ModelError`, `is_retryable=True`), nên `ModelRetryMiddleware` gọi lại, `ModelFallbackMiddleware` chuyển model, engine phát `retract`. `MODEL_MAX_OUTPUT_TOKENS` (8192) chặn vòng lặp lọt lưới. System prompt cấm viết suy nghĩ nháp. Lần chạy thật thứ hai cho thấy rò rỉ dạng ngắn: model kết thúc lượt bằng "Read db create_all.", nên engine có thêm `_AnswerGuard` (trong cùng, sau retry): lượt tiếng Việt mà câu trả lời cuối không có dấu tiếng Việt (`domain/language.lacks_vietnamese`) hoặc câu trả lời cuối rỗng thì raise `UnusableAnswerError` (retryable). Chữ đi kèm tool call bị `retract`, vì nó không được lưu.
- **Kết thúc theo ngân sách** (`tool_scope.wrap_up`, dùng chung cho phía model và policy guard): còn ≤2 lần gọi model hoặc ≤3 tool call mà người dùng xin sơ đồ chưa vẽ thì chỉ còn tool `architecture_diagram`; lần gọi model cuối (hoặc hết tool call) thì không còn tool nào, và system prompt bảo viết câu trả lời ngay. Theo thời gian (`RunContext.seconds_left`): còn < 50% `turn_timeout_s` mà sơ đồ chưa vẽ thì chỉ còn tool sơ đồ; còn < 35% thì phải trả lời (model chậm cần vài phút để stream câu trả lời dài; lần chạy với `deepseek-v4-flash-free` đã vẽ xong sơ đồ nhưng hết 300s khi đang viết câu trả lời). Lý do: lần chạy thật thứ ba trên AI_SOC đọc tuần tự từng file tới khi `max_model_calls=16` cắt lượt, không có câu trả lời.
- **Sơ đồ:** cùng cơ chế với xuất file: `Intent.wants_diagram` (tất định) → tag `DIAGRAM` → `tool_scope`. `research/diagram.py` sinh Mermaid (frontmatter `look: handDrawn`, `id@{ shape }` theo loại node, `subgraph` theo `group`, cạnh nét đứt khi bất đồng bộ, escape `"`/`|`) và bảng bước có `[E#]`; nhãn evidence lạ → `invalid_input`. `DiagramFinalizer` (trước `CitationFinalizer`) nối sơ đồ model quên chép. Frontend: `mermaid@12.1.0` nạp động, `htmlLabels: false` để xuất PNG qua canvas, chỉ vẽ khi lượt đã xong.
- **Nhắc vẽ bằng tool** (`_DiagramNudge`): khi người dùng xin sơ đồ, tool đang được cung cấp, chưa vẽ gì mà model đã trả lời cuối (lần chạy thật: model tự viết `graph TD` sai cú pháp), engine gọi lại một lần kèm lời nhắc gọi `architecture_diagram`; chữ của lần trả lời đầu bị `retract`. Prompt cấm tự viết Mermaid. Frontend vẫn hiện lỗi parse kèm mã gốc nếu khối mermaid tự viết lọt qua.
- **Ép gọi tool sơ đồ:** lần chạy thật tiếp theo, ở bước bắt buộc vẽ, `gpt-oss` vẫn gọi các tool đọc đã bị ẩn (3 lần `policy_denied`) rồi tự viết Mermaid; khi chạy riêng thì model gọi được tool. Vì vậy ở bước này `HarnessModelMiddleware` đặt `tool_choice="required"`. Frontend: `lib/mermaidRepair.ts` đặt nhãn node/cạnh trong ngoặc kép khi khối Mermaid tự viết lỗi cú pháp, và `look: handDrawn` áp cho mọi sơ đồ.
- **Thời gian cho sơ đồ:** với `deepseek-v4-flash-free`, riêng lần gọi sinh tham số `architecture_diagram` mất ~100s, nên lượt 300s vẽ xong nhưng không kịp viết câu trả lời. Skill `github-architecture` nâng lên `turn_timeout_s: 600` (dưới trần 1200); model nhanh vẫn xong sớm. Lỗi lưu `tool_invocations` (LangChain truyền tham số lồng nhau dạng object pydantic, JSONB không serialize được) sửa ở bridge bằng `to_jsonable_python`.

## 13. Bản đồ hệ thống tất định — `github_system_map` (2026-10-25)
- **Vấn đề:** harness có tool mức code (import graph, symbol graph, read/search) nhưng không tool nào trả lời "process nào chạy, nối với nhau thế nào". `deepseek-v4-flash-free` phải đọc 12–35 file, 400–900s để tự ráp 12 node; `gpt-oss` không làm được; mọi cạnh của `architecture_diagram` chỉ cần một nhãn `[E#]` bất kỳ nên không chặn được suy luận bừa.
- **Cách làm:** máy hoá skill `reading-system-architecture` (10 câu Q0–Q9, nguồn sự thật cố định, hai mức tin cậy) thành package `sources/github/system_map/` (chỉ `yaml`, `ast`, `re`, `json`; không tree-sitter): `manifests.py` (Q0–Q2, Q7/Q8 phần manifest; compose merge variant/override/profile, Dockerfile exec/shell + `${VAR}`, k8s multi-doc + skaffold `image→context`, Procfile, package/pyproject scripts, Makefile, CI deploy → dev-host/external-hosted/undeployed), `envchain.py` (Q3: `env_readers.yaml` + BaseSettings field → giá trị theo thứ tự compose/k8s → env_file → Dockerfile → `.env.example` → mặc định code → phân giải host theo bảng Q3; biến dùng trong `command` của manifest cũng tính là "đọc"), `channels.py` (Q4: route server ↔ client call qua `base_url`/wrapper/`${VAR}`; topic; SSE/WS; gRPC proto ↔ `New*Client`/`Register*Server`, bỏ stub sinh tự động `genproto/`, `*_pb2*`, `*.pb.go`; Q6/Q8 tại điểm gọi: fail-open/closed, timeout, retry, boot-time, async), `stores.py` (Q5 + script ghi store → batch-job `manual`), `closure.py` (10 bất biến + claim README), `render.py` (5 phần + `diagram_plan`), catalog YAML (image→kind, entrypoint→role, connectors, env readers).
- **Tích hợp:** `RepositoryReader.system_map` (cache theo repo+ref dưới `asyncio.Lock`, xoá cùng snapshot); tool `github_system_map` (`capabilities/system_map_tools.py`, SOURCE, untrusted, trong `_KEEP_TOOLS`); `architecture_diagram` thêm `source=system_map|manual` với luật cạnh ở FR-17b; skill built-in `github-system-architecture` (chuyển thể từ skill người dùng, bản gốc không sửa); `github-architecture`/`github-deep-dive` có bước 0 gọi map; prompt ưu tiên `source=system_map`.
- **Đo được (offline, snapshot thật):** AI_SOC 0,30s — 8 process + 2 variant + 2 profile, 46 cạnh (collector/email-ingest/worker → api EXTRACTED cả hai cột; frontend → api qua `localhost:8000` INFERRED địa chỉ; api → kafka(security_events) → worker async), 8 khoảng trống đúng (redis không ai dùng, 2 script `psql` là batch-job manual, `_producer` stateful, 5 biến không đặt, hợp đồng HTTP ngầm). microservices-demo (k8s + skaffold) 0,97s — 12 process, 17 cạnh gRPC đúng chiều sau khi loại stub sinh tự động (trước đó 48 cạnh sai vì mỗi service chứa đủ client+server của mọi proto), `loadgenerator → frontend` qua `--host=http://${FRONTEND_ADDR}` trong command, `cartservice → redis-cart` writer+reader (C# `SetAsync/GetAsync`). learn-harness-engineering — "Không có topology runtime", 17 dev-host + GitHub Pages. 3.000 file tổng hợp: 11,9s → 1,1s sau khi bỏ `re.compile` trong vòng lặp propagate và `PurePosixPath` trong `Pattern.applies`.
- **Chưa làm:** kiểm tra live qua frontend với deepseek/gpt-oss (Verification 3–4 trong plan); rewrite/proxy (`next.config rewrites`, nginx `location`); Celery `@task(queue=)`; Helm values.


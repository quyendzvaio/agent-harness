# PRD — Research Agent Harness

> Trạng thái: **Approved v1** · Cập nhật: 2026-10-21
> Các mục có nhãn *(đề xuất — cần xác nhận)* là giả định chưa được chốt.

## 1. Bối cảnh

Người dùng muốn một **research agent** đọc hiểu sâu nội dung của **chính nguồn** được đưa vào, gồm:
- **GitHub repository**: cấu trúc, kiến trúc, luồng thực thi, các module, dependency.
- **Blog kỹ thuật**: luận điểm chính, giải thích kỹ thuật. Phải phân biệt rõ đâu là tác giả khẳng định, đâu là agent suy luận.

Kết quả trả về trong chat, có trích dẫn kiểm chứng được. Agent chỉ tạo file `.md` khi người dùng yêu cầu rõ ràng.

Bản thiết kế trước chỉ tập trung vào workflow LangGraph, còn logic nghiệp vụ nằm rải rác trong các node, nên rất khó tái sử dụng. Bản này tách **Agent Harness**, gồm policy, middleware, tool execution và observability, ra khỏi framework điều phối. Thêm vào đó là **custom skill** khai báo bằng file, để mở rộng năng lực mà không phải sửa code.

## 2. Vấn đề cần giải quyết

| # | Vấn đề | Hệ quả |
|---|---|---|
| P1 | Logic nghiệp vụ gắn chặt vào node của graph | Đổi một chính sách phải sửa nhiều module |
| P2 | LLM có thể bịa trích dẫn hoặc khẳng định không có căn cứ | Báo cáo không đáng tin |
| P3 | Agent tự fetch URL do người dùng đưa vào | Rủi ro SSRF, prompt injection từ nội dung nguồn |
| P4 | Thêm một loại phân tích mới đòi hỏi viết code | Chậm mở rộng, khó cho người không phải dev |
| P5 | Không kiểm soát được chi phí và vòng lặp tool | Tốn token, treo request |

## 3. Mục tiêu & KPI *(đã xác nhận)*

| Mục tiêu | KPI |
|---|---|
| Câu trả lời có căn cứ | ≥ 95% trích dẫn `[E#]` trong câu trả lời trỏ đúng tới evidence tồn tại; 100% trích dẫn sai bị đánh dấu |
| Phản hồi nhanh | Token đầu tiên được stream về trong ≤ 3s (p95, không tính thời gian model khởi động) |
| Chi phí kiểm soát được | Không lượt chat nào vượt `max_model_calls` / `max_tool_calls` đã cấu hình (không đặt ngưỡng chi phí USD theo lượt) |
| Dễ mở rộng | Thêm một skill mới chỉ cần thêm thư mục, sửa 0 dòng code Python |
| Dễ thay thế | Thay engine, model, nguồn hoặc policy chỉ động vào ≤ 1 package. Có test chứng minh (xem PLAN §5) |

## 4. Phạm vi

### In-scope (MVP)
- Chat API qua **FastAPI + SSE streaming**, hội thoại nhiều lượt, lưu vào **PostgreSQL**.
- Agent **LLM tool-calling tự do** (LangChain `create_agent`). Model mặc định lấy từ một endpoint **tương thích OpenAI** (`BASE_URL`, `LLM_MODEL`; mặc định Groq); có thể khai báo thêm danh sách model dự phòng trên cùng endpoint.
- Nguồn: **GitHub** (public repo, token tùy chọn) và **blog HTML**.
- **Custom skill** dạng `SKILL.md` kèm hook Python tùy chọn. Có 4 skill built-in: `github-architecture` (nhanh), `github-deep-dive` (đọc toàn repo), `blog-explainer`, `export-report`.
- Evidence và kiểm tra trích dẫn.
- Xuất file `.md` khi người dùng yêu cầu. File được lưu thành artifact trên **MinIO tự host** và tải về qua API.
- Policy: chống SSRF, giới hạn thực thi, quyền xuất file, phạm vi tool theo skill.
- Observability: structured log, metrics theo lượt chat, bảng audit các lần gọi tool.

### Out-of-scope v1 (đưa vào roadmap)
- Multi-agent, chạy job nền dài (Celery/Arq), nhiều nguồn trong một báo cáo tổng hợp.
- PDF, trang tài liệu API, repo private qua OAuth.
- Giao diện web (MVP chỉ có API, test bằng curl hoặc client SSE).
- Đa tenant, phân quyền theo người dùng, quản lý API key ngay trong ứng dụng.
- Upload skill qua API. MVP chỉ nạp skill từ thư mục trên server.

## 5. Người dùng & phân quyền

| Vai trò | Mô tả | Quyền |
|---|---|---|
| **Researcher** (người dùng API) | Dev hoặc kỹ sư muốn hiểu một repo hoặc bài viết | Chat, xem hội thoại, tải artifact của mình, xem danh sách skill |
| **Operator** (người vận hành) | Người deploy và cấu hình hệ thống | Mọi quyền của Researcher, cộng: thêm skill vào thư mục, `POST /v1/skills/reload`, chỉnh config/policy |

MVP là **single-tenant**. Xác thực dùng cờ `AUTH_ENABLED` *(đã chốt)*:
- Bật: mọi endpoint (trừ health) yêu cầu header `X-API-Key = API_KEY`; endpoint Operator yêu cầu `ADMIN_API_KEY`.
- Tắt: chỉ được phép khi bind vào `127.0.0.1`/`localhost`. Nếu bind ra ngoài mà tắt auth hoặc thiếu key thì app từ chối khởi động.

## 6. User stories

**US-1 — Phân tích repo**
Là Researcher, tôi muốn gửi link GitHub và nhận về phân tích kiến trúc, để nhanh chóng hiểu codebase.
- Given một URL repo public, when tôi gửi "phân tích kiến trúc <url>", then stream trả về các event `skill_loaded(github-architecture)`, `tool_*`, `token`, `citation_report`, `done`.
- Mỗi khẳng định về code trích dẫn evidence dạng `path:Lx-Ly`.

**US-2 — Giải thích blog**
Là Researcher, tôi muốn gửi link blog và nhận về tóm tắt và giải thích, có tách rõ ý của tác giả với suy luận của agent.
- Câu trả lời có hai nhóm rõ ràng: "Tác giả khẳng định" (có trích dẫn) và "Diễn giải/suy luận".

**US-3 — Hỏi tiếp trong hội thoại**
Là Researcher, tôi muốn hỏi tiếp trong cùng conversation mà không phải gửi lại link.
- Given `conversation_id` đã có, khi hỏi "module X gọi Y thế nào?", agent dùng lại ngữ cảnh và các nguồn đã đọc.

**US-4 — Xuất Markdown**
Là Researcher, tôi muốn lưu báo cáo thành file `.md` khi tôi yêu cầu.
- Có cụm như "xuất file md" hoặc "export markdown" trong lượt chat → có event `artifact {id, filename}`, tải được qua `GET /v1/artifacts/{id}`.
- Không có yêu cầu đó → **không** file nào được tạo, kể cả khi model cố gọi tool export.

**US-5 — Thêm skill mới**
Là Operator, tôi muốn thêm một skill (ví dụ `security-review`) bằng cách thả một thư mục vào, không cần deploy lại code.
- Sau khi gọi `POST /v1/skills/reload`, skill xuất hiện trong `GET /v1/skills`, và agent nạp được nó khi yêu cầu phù hợp.

**US-6 — An toàn khi fetch**
Là Operator, tôi muốn agent không bao giờ truy cập được địa chỉ nội bộ.
- URL trỏ tới `127.0.0.1`, `10.x`, `169.254.169.254`, `file://`… bị chặn, kể cả khi URL công khai redirect sang đó. Agent nhận lỗi policy rõ ràng.

**US-7 — Giới hạn chi phí**
Là Operator, tôi muốn giới hạn số lần gọi model/tool và thời gian chạy của mỗi lượt chat.
- Vượt giới hạn → event `error {code: "limit_exceeded"}`, stream đóng sạch, lượt chat được ghi `status=failed`.

## 7. Yêu cầu chức năng

### 7.1 Chat & hội thoại
- **FR-1** `POST /v1/chat` nhận `{conversation_id?, message}` và trả `text/event-stream`. Nếu không có `conversation_id` thì tạo conversation mới và gửi id qua event `conversation`.
- **FR-2** Lưu message của user và assistant (bảng `messages`), từng lần gọi tool (bảng `tool_invocations`) và metadata của lượt chat (status, số lần gọi, token, model đã dùng, thời gian) vào Postgres.
- **FR-3** Agent tiếp tục hội thoại qua LangGraph checkpointer trên Postgres, với `thread_id = conversation_id`.
- **FR-4** Client ngắt kết nối thì lượt chat bị hủy, ghi `status=cancelled`, không để task chạy mồ côi.

### 7.2 Phân tích ý định (tất định)
- **FR-5** `IntentParser` trích ra: danh sách URL, `explicit_export` và `wants_diagram` (từ khóa tiếng Việt/Anh, có xử lý phủ định), ngôn ngữ trả lời. Kết quả nằm trong `TurnContext` và LLM không ghi đè được.

### 7.3 Skill
- **FR-6** Nạp skill từ thư mục built-in (trust=`trusted`) và `SKILLS_USER_DIR` (trust=`declarative`).
- **FR-7** Frontmatter bắt buộc có `name`, `description`. Tùy chọn: `source_types`, `allowed_tools`, `resources`, `hooks`. Skill sai định dạng bị bỏ qua và ghi log lỗi, không làm hỏng các skill khác.
- **FR-8** System prompt chỉ chứa danh mục skill (tên + mô tả). Tool `load_skill(name)` trả về phần thân hướng dẫn. Tool `read_skill_resource(name, path)` chỉ đọc được file nằm trong thư mục của skill (chống path traversal).
- **FR-9** Khi có skill đang hoạt động, model chỉ thấy tool nền cộng với `allowed_tools` của skill đó.
- **FR-10** Hook (`on_load`, `post_tool`, `on_final`) chỉ chạy với skill trusted, có timeout, và lỗi trong hook không làm hỏng lượt chat.
- **FR-11** `GET /v1/skills`, `GET /v1/skills/{name}`, `POST /v1/skills/reload` (chỉ Operator).

### 7.4 Nguồn & tool
- **FR-12** GitHub tools, đọc từ **một snapshot toàn repo** (tải tarball một lần, cache trong process theo repo+ref; repo quá lớn thì quay về gọi API từng file):
  - `github_repo_overview`: metadata, README, file cấu hình, bản đồ cấu trúc, **entrypoint lấy từ khai báo** (pyproject scripts, package.json main/bin/scripts, Dockerfile CMD/ENTRYPOINT, Procfile, `__main__`, Go `package main`); chỉ đoán theo tên file khi repo không khai báo gì, và ghi rõ là đoán.
  - `github_dependency_graph`: đồ thị import nội bộ tất định (Python bằng AST, JS/TS và Go bằng regex + resolve theo file thật): package→package, file→file, fan-in, vòng phụ thuộc, thư viện ngoài, import không resolve được.
  - `github_search_code`: tìm text/regex trên toàn repo, lọc theo glob, trả dòng + ngữ cảnh.
  - `github_code_graph` (extra `graph`, dùng thư viện graphify): đồ thị **symbol** cho 25+ ngôn ngữ — class, hàm, method và quan hệ `calls / inherits / uses / imports / contains`, mỗi quan hệ có độ tin cậy `EXTRACTED / INFERRED / AMBIGUOUS`. Các thao tác: `overview` (abstraction cốt lõi, cụm code, vòng import, liên kết bất thường), `find`, `neighbors` (ai gọi/dùng/kế thừa X và ngược lại), `path` (A nối tới B qua đâu), `cycles`. Mỗi định nghĩa/quan hệ trả về là một evidence `[E#]` trỏ tới `file:dòng`. Không cài extra thì tool trả `capability_unavailable`.
  - `github_coverage`: đã đọc bao nhiêu file/dòng nguồn, file quan trọng nào chưa đọc (xếp theo số liên kết trong code graph nếu đã dựng, nếu không thì theo fan-in). Kết quả của graph/search không được tính là "đã đọc".
  - `github_list_tree`, `github_read_file` (≤400 dòng/lần, đánh số dòng).
  - `github_system_map` (`sources/github/system_map/`, thuần Python, máy hoá phương pháp `reading-system-architecture` Q0–Q9): đọc manifest trước (compose base + variant/override/profile, Dockerfile, k8s + skaffold, Procfile, package.json/pyproject scripts, Makefile, CI deploy), suy vai trò từ entrypoint, lần chuỗi env `code đọc → manifest đặt → phân giải host`, khớp **hai đầu** mỗi kênh (route HTTP prefix+path, SSE/WS, topic producer↔consumer, gRPC proto↔stub↔server, scheme DB/cache/storage, mount volume), gán writer/reader cho store, chạy 10 bất biến đóng (Q9) và biến claim trong README/docs thành giả thuyết. Kết quả 5 phần (dòng đầu, bảng node, bảng cạnh với hai cột tin cậy EXTRACTED/INFERRED tách riêng, sở hữu dữ liệu, khoảng trống); mỗi dòng một `[E#]` với locator `path:Lx (node|channel|store|env|gap)` — không tính là đã đọc file. Tham số `section`, `variant`, `process`, `limit`. Repo không có manifest runtime → nói rõ "Không có topology runtime". Catalog mẫu nhận dạng nằm trong `system_map/catalog/*.yaml`; `SYSTEM_MAP_MAX_FILES` giới hạn số file code quét mỗi process. Overview có thêm mục "Đọc để kiểm chứng" (≤8 cửa sổ `path start_line end_line` quanh cạnh INFERRED và khoảng trống trong code) dành cho model. Khi skill `github-system-architecture` đang chạy, overview đầu tiên của lượt được tool stream thẳng vào đầu câu trả lời (`message_id=system_map`, giữ ở đầu câu trả lời lưu lại bởi `AnswerPrefixFinalizer`); model chỉ viết `## Kiểm chứng` và `## Nhận định của agent`.
- **FR-13** Blog tool: `web_fetch_article` (fetch, sau đó tách title, heading, đoạn văn, bỏ phần điều hướng và quảng cáo).
- **FR-14** Mọi tool đọc nguồn đều đăng ký evidence và trả về nội dung kèm nhãn `[E#]`. Response HTTP thành công được cache trong Postgres (bảng `sources`, khóa theo URL) với TTL `SOURCE_CACHE_TTL_S`.
- **FR-15** Nguồn bị tắt trong config thì tool tương ứng trả `capability_unavailable`, các nguồn khác vẫn hoạt động.

### 7.5 Trích dẫn
- **FR-16b** Kiểm tra ngữ nghĩa (tuỳ chọn): port `ClaimVerifier` nhận từng câu có trích dẫn kèm evidence và trả `supported/unsupported/uncertain` (+ xác suất). Kết quả nằm trong `citation_report.claims`. Verifier lỗi/timeout không làm hỏng lượt chat. Implementation (ví dụ classifier Jev) do operator cung cấp.
- **FR-16** Sau câu trả lời cuối, `CitationValidator` đối chiếu `[E#]` với các evidence của lượt chat, rồi phát event `citation_report {valid, invalid, uncited_sentences}`.

### 7.6 Xuất file
- **FR-17** Tool `export_markdown(title, content)` chỉ hiện ra và chỉ chạy được khi `explicit_export=true`. File được lưu qua port `ArtifactStore`, implementation là **MinIO** (S3-compatible, bucket private). Object key có dạng `conversations/{conversation_id}/{artifact_id}/{filename}`. Metadata (gồm `storage_uri = s3://bucket/key`, `sha256`, `size_bytes`) nằm trong bảng `artifacts`. Nếu ghi MinIO thành công mà ghi DB thất bại thì xóa object để không để lại rác.
- **FR-17b** Sơ đồ kiến trúc: tool `architecture_diagram` chỉ hiện ra và chỉ chạy được khi `wants_diagram=true` ("vẽ sơ đồ…", "diagram"). Model mô tả node runtime (service, worker, scheduler, DB, queue, cache, API ngoài, client) và các luồng đánh số (1, 2.1, 2.2…), mỗi luồng phải trích dẫn evidence có thật; harness sinh Mermaid `look: handDrawn` cùng bảng các bước. Sơ đồ model quên chép vào câu trả lời được nối thêm tự động. Frontend vẽ sơ đồ, tải SVG/PNG. `source=system_map` (mặc định khi có `url`): harness tự dựng node/cạnh từ `github_system_map` (cạnh INFERRED có hậu tố `(?)`, queue/file vẽ nét đứt, đánh số BFS từ node biên). `source=manual`: mỗi cạnh phải trích dẫn evidence là code đã đọc (`github_read_file`/`github_search_code`, không phải README/tree/graph/dòng manifest); giữa hai node của map mà map không có kênh thì cần code của **cả hai đầu**, nếu không tool từ chối (`invalid_input`) và nêu cạnh + lý do.
- **FR-18** `GET /v1/artifacts/{id}` stream file từ MinIO qua API (giữ được kiểm soát truy cập, bucket không public). `GET /v1/conversations/{id}/artifacts` trả danh sách.

### 7.7 Vận hành
- **FR-19** `GET /healthz` (process còn sống), `GET /readyz` (kiểm tra Postgres, MinIO bucket và thư mục skill).
- **FR-22** Skill có thể xin ngân sách lớn hơn qua frontmatter `limits:` (số lần gọi model/tool, timeout); harness nâng ngân sách của lượt khi skill được nạp, tối đa bằng `LIMITS_CEILING__*`, và gia hạn deadline tương ứng. Tool `save_note` lưu ghi chú làm việc vào system prompt để không mất khi context bị dọn (`CONTEXT_CLEAR_TRIGGER_TOKENS`).
- **FR-21** Model chính và các model dự phòng cấu hình qua một endpoint tương thích OpenAI (`BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_FALLBACK_MODELS`). Lỗi tạm thời (đứt kết nối giữa stream, timeout, 429, 5xx) được gọi lại tối đa `MODEL_MAX_RETRIES` lần (mặc định 2, có backoff) trên từng model; vẫn lỗi thì thử lần lượt từng model dự phòng; hết thì báo `model_error`. Lỗi sai model/key/request báo ngay, không gọi lại. Chữ đã stream của lần gọi bị đứt được rút lại bằng event `retract`. Đầu ra hỏng (reasoning nháp lọt vào câu trả lời, vòng lặp một câu) bị chặn giữa stream và gọi lại như lỗi tạm thời (`MODEL_OUTPUT_GUARD`); `MODEL_MAX_OUTPUT_TOKENS` giới hạn mỗi lần gọi. Lượt hỏi tiếng Việt mà câu trả lời cuối không có chữ tiếng Việt nào (ghi chú nháp tiếng Anh) cũng bị gọi lại.
- **FR-20** Mỗi lần gọi tool được ghi vào `tool_invocations` (tên, input đã che dữ liệu nhạy cảm, trạng thái, thời gian, lỗi).

## 8. Luật nghiệp vụ

| ID | Luật | Thực thi |
|---|---|---|
| R1 | Chỉ fetch URL http/https công khai. Phân giải DNS rồi chặn IP private/loopback/link-local/metadata, kiểm tra lại ở **mỗi lần redirect**. Giới hạn bytes và timeout | `source_policy` + `policy_guard` |
| R2 | Chỉ xuất file khi lượt chat hiện tại có yêu cầu rõ ràng | `export_policy` + `tool_scope` + `policy_guard` |
| R3 | Giới hạn `max_model_calls`, `max_tool_calls`, `turn_timeout_s`, `max_fetch_bytes` | `execution_policy` + `limits` |
| R4 | Skill đang hoạt động giới hạn tập tool được dùng | `tool_scope` |
| R5 | Khẳng định cần có trích dẫn; trích dẫn sai bị đánh dấu; tách ý tác giả với suy luận của agent | `citation_policy` + `citation_check` |
| R6 | Nội dung nguồn là dữ liệu không tin cậy, được bọc `<source_data>` và system prompt cấm làm theo chỉ dẫn nằm bên trong | `untrusted_content` |
| R7 | Hook chỉ chạy với skill trusted | `skills/loader` |
| R8 | Capability bị tắt hoặc lỗi phải báo rõ, không lan sang phần khác | `sources/registry` + `error_boundary` |

## 9. Yêu cầu phi chức năng

| Nhóm | Yêu cầu |
|---|---|
| Hiệu năng | Token đầu tiên ≤ 3s p95; xử lý được ≥ 20 stream đồng thời trên 1 instance *(đề xuất)* |
| Độ tin cậy | Retry có backoff cho lỗi mạng tạm thời (tối đa 2 lần); stream luôn kết thúc bằng `done` hoặc `error` |
| Bảo mật | Chống SSRF (R1), chống path traversal khi đọc resource của skill, secret (gồm credential MinIO) chỉ lấy từ env và không bao giờ ghi vào log, bucket MinIO private, API key cho mọi endpoint khi `AUTH_ENABLED=true` (bắt buộc khi bind ra ngoài localhost) |
| Khả năng bảo trì | Quy tắc phụ thuộc giữa các package được kiểm tra bằng test kiến trúc; `domain` không import framework |
| Khả năng kiểm thử | Unit test chạy offline, không cần key; integration test chạy với Postgres thật qua docker-compose |
| Observability | Log JSON có `conversation_id`, `turn_id`, `tool`, `duration_ms`; metrics theo lượt chat lưu ở bảng `turns` |
| Triển khai | Docker image + docker-compose (api + postgres + minio). Migration bằng Alembic; bucket MinIO được tạo bằng lệnh `agent-harness init` |

## 10. Mô hình dữ liệu (PostgreSQL)

```mermaid
erDiagram
  conversations ||--o{ turns : has
  conversations ||--o{ messages : has
  turns ||--o{ messages : produces
  turns ||--o{ evidence : collects
  turns ||--o{ tool_invocations : audits
  turns ||--o{ artifacts : exports

  conversations { uuid id PK; text title; timestamptz created_at; timestamptz updated_at; jsonb metadata }
  turns { uuid id PK; uuid conversation_id FK; text status; bool explicit_export; text active_skill; int model_calls; int tool_calls; int tokens_in; int tokens_out; timestamptz started_at; timestamptz finished_at; jsonb error }
  messages { uuid id PK; uuid conversation_id FK; uuid turn_id FK; text role; text content; jsonb metadata; timestamptz created_at }
  sources { text cache_key PK; text kind; text url; jsonb payload; timestamptz fetched_at; timestamptz expires_at }
  evidence { bigint id PK; uuid conversation_id FK; uuid turn_id FK; text label; int ordinal; text source_kind; text source_url; text source_canonical_id; text locator; text quote }
  tool_invocations { uuid id PK; uuid turn_id FK; text tool_name; jsonb input; text status; int duration_ms; jsonb error; timestamptz created_at }
  artifacts { uuid id PK; uuid conversation_id FK; uuid turn_id FK; text filename; text media_type; text storage_uri; bigint size_bytes; text sha256; timestamptz created_at }
```

- `evidence`: nhãn `E#` nối tiếp trong cả conversation, ràng buộc unique `(conversation_id, label)`. Thông tin nguồn lưu ngay trên dòng evidence.
- `sources` là cache HTTP của các lần fetch (theo URL + Accept), có TTL.
- Các bảng checkpoint của LangGraph nằm trong schema riêng `langgraph`, do `AsyncPostgresSaver.setup()` quản lý. Ứng dụng không đọc trực tiếp các bảng này.
- **Bảng `messages` là nguồn sự thật cho lịch sử hiển thị.** Checkpointer chỉ phục vụ việc agent tiếp tục hội thoại.

## 11. API contract (tóm tắt)

| Method | Path | Quyền | Mô tả |
|---|---|---|---|
| POST | `/v1/chat` | Researcher | SSE stream một lượt chat |
| GET | `/v1/conversations` | Researcher | Danh sách hội thoại (phân trang) |
| GET | `/v1/conversations/{id}/messages` | Researcher | Lịch sử message |
| GET | `/v1/conversations/{id}/evidence` | Researcher | Bằng chứng `[E#]` của hội thoại (nguồn, locator, trích đoạn) |
| GET | `/v1/conversations/{id}/artifacts` | Researcher | Danh sách artifact |
| GET | `/v1/artifacts/{id}` | Researcher | Tải file |
| GET | `/v1/skills`, `/v1/skills/{name}` | Researcher | Danh mục và chi tiết skill |
| POST | `/v1/skills/reload` | Operator | Nạp lại thư mục skill |
| GET | `/healthz`, `/readyz` | – | Health check |

Các event SSE (`event:` / `data:` JSON):
- `conversation {conversation_id, turn_id}`
- `token {text, message_id?}`
- `retract {message_id}`: phần chữ đã stream của `message_id` không thuộc câu trả lời (lần gọi đó hỏng và được gọi lại, hoặc là lời dẫn đi kèm tool call, vốn không được lưu); client bỏ phần chữ này
- `skill_loaded {name}`
- `tool_started {call_id, tool, input}`
- `tool_finished {call_id, tool, status, evidence_labels, duration_ms}`
- `citation_report {...}`
- `artifact {id, filename}`
- `error {code, message}`
- `done {usage}`

Mã lỗi: `policy_denied`, `limit_exceeded`, `capability_unavailable`, `tool_failed`, `model_error`, `internal`.

## 12. Rủi ro

| | Rủi ro | Giảm thiểu |
|---|---|---|
| ⚠️ | Agent dùng tool-calling tự do nên khó dự đoán, khó test | Middleware giới hạn + model giả `ScriptedChatModel` cho test tất định + audit log |
| ⚠️ | Prompt injection từ README hoặc nội dung blog | R6, tool export bị ẩn theo mặc định, không có tool nào có tác dụng phụ ngoài export |
| ⚠️ | Repo lớn vượt context hoặc rate limit của GitHub | `github_read_file` giới hạn kích thước, cây thư mục có độ sâu tối đa, cache, hỗ trợ `GITHUB_TOKEN` |
| ⚠️ | Hook Python của skill là code chạy trên server | Chỉ skill trusted (nằm trong repo) được chạy hook; skill người dùng chỉ được dạng khai báo |
| ⚠️ | Cổng 5432 trên máy dev đang bị dự án khác dùng | docker-compose dùng cổng host 5433 |
| ⚠️ | MinIO tạm thời không truy cập được | Tool export trả `tool_failed` và câu trả lời vẫn hiển thị trong chat; `/readyz` báo lỗi |

## 13. Quyết định & câu hỏi còn mở
Đã chốt:
- KPI ở §3 giữ nguyên, không có ngưỡng chi phí theo lượt.
- Artifact lưu trên MinIO tự host.
- Model mặc định và model dự phòng đều qua cùng một endpoint tương thích OpenAI (mặc định Groq).

- Xác thực: dùng cờ `AUTH_ENABLED` (PLAN §4.6).

Còn mở: không còn.

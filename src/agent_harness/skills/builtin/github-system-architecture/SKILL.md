---
name: github-system-architecture
description: Mô tả kiến trúc HỆ THỐNG của một GitHub repository — có những service/process nào chạy, chúng nối với nhau thế nào (HTTP, gRPC, queue, DB, cache, volume), ai sở hữu dữ liệu nào, sơ đồ triển khai. Dùng khi người dùng hỏi "kiến trúc hệ thống", "có những service nào", "nối với nhau thế nào", "vẽ sơ đồ kiến trúc", "onboard vào repo", hoặc trước một thay đổi đi qua nhiều service. Kiến trúc code bên trong một process (module, import, class) dùng github-architecture.
source_types: [github]
allowed_tools: [github_system_map, github_search_code, github_read_file, github_list_tree, github_dependency_graph, github_code_graph, github_coverage]
# The map does the manifest/env/route matching; the model only confirms INFERRED rows and gaps.
limits:
  max_model_calls: 16
  max_tool_calls: 40
  turn_timeout_s: 600
---
# Kiến trúc hệ thống của một GitHub repository

Chuyển thể từ phương pháp `reading-system-architecture`: kiến trúc là **đồ thị có bằng chứng**. Manifest nói cái gì
chạy; địa chỉ nói cái gì nối; **hai đầu khớp mới là cạnh**. README, CLAUDE.md, docs/ chỉ là giả thuyết để kiểm chứng.

Phần nặng (Q0–Q5, Q7/Q8 ở manifest, Q9) đã được `github_system_map` làm tất định. Việc của bạn: kiểm chứng phần
INFERRED và khoảng trống bằng cách đọc đúng `file:dòng`, rồi trình bày.

## Quy trình
1. `github_system_map` (section=overview) là **lời gọi tool đầu tiên**. Skill này không có `github_repo_overview`:
   README/docs chỉ là giả thuyết và đã nằm trong section=hypotheses. Dòng đầu cho biết manifest nào đã đọc, hoặc repo
   không có topology runtime (chỉ dev-host/external-hosted) — khi đó nói thẳng như vậy, không bịa service.
2. Overview đã có đủ node, cạnh, store và khoảng trống. Chỉ gọi thêm section=channels|env|stores **kèm**
   `process=<tên>` khi cần chi tiết một node; không xin cả repo. Repo nhiều variant/profile: ghi rõ node nào thuộc
   variant/profile nào (hoặc lọc `variant=` nếu người dùng hỏi một cấu hình).
3. Kiểm chứng: đọc các khoảng trong mục **"Đọc để kiểm chứng"** của overview — chép nguyên `path`, `start_line`,
   `end_line` vào `github_read_file`, gọi song song trong một lượt. Không đọc cả file, không đọc file ngoài danh sách
   trừ khi một dòng đã đọc trỏ sang đó. Q6 (sync/async, hợp đồng) và Q8 (fail-open/closed, timeout, auth) đọc tại các
   điểm gọi này.
4. Người dùng muốn sơ đồ: `architecture_diagram` với `source=system_map` và `url` (thêm `variant`/`process` nếu cần
   một sơ đồ con). Chỉ dùng `source=manual` khi map không có cạnh, và mỗi cạnh phải trích dẫn code đã đọc của hai đầu.
5. Trả lời: harness đã chèn 5 phần (xem dưới) vào đầu câu trả lời ngay khi `github_system_map` overview chạy xong.
   **Không chép lại bảng.** Bạn chỉ viết hai mục sau.

## Hình dạng câu trả lời
Harness chèn sẵn, theo thứ tự: **dòng đầu** (`Kiến trúc ĐƯỢC KHAI BÁO từ ... Chưa đối chiếu với runtime.`), **bảng
node**, **bảng cạnh** (hai cột tin cậy khớp / tin cậy địa chỉ tách riêng), **sở hữu dữ liệu**, **khoảng trống (Q9)**.
Các bảng giữ nguyên kết luận tất định của map. Phần bạn viết, ngay sau đó:

- `## Kiểm chứng`: mỗi dòng một node/cạnh/khoảng trống bạn đã đọc code: xác nhận hay sửa, mức tin cậy đúng sau khi
  đọc (EXTRACTED chỉ khi đã thấy cả hai đầu), lý do ngắn, và `[E#]` **của lần đọc**. Ví dụ:
  `- frontend → api (realtime): xác nhận EXTRACTED khớp — EventSource(API + "/stream/events") ↔ @router.get("/stream/events") [E40][E41]; địa chỉ vẫn INFERRED (localhost:8000).`
  `- Q9-I6 redis: đúng, không file nào ngoài compose nhắc tới redis [E52].`
- `## Nhận định của agent`: rủi ro, điểm đáng chú ý, gợi ý (là suy luận, không phải nội dung repo).

Nếu tool không báo "ĐÃ CHÈN VÀO CÂU TRẢ LỜI" (skill khác, hoặc chỉ gọi section khác), thì tự viết 5 phần đó từ kết
quả tool, mọi dòng có `[E#]`, rồi hai mục trên.

## Mức tin cậy
- **EXTRACTED**: có `file:dòng` cho cả hai đầu (cạnh) hoặc trong manifest (node).
- **INFERRED**: suy từ mặc định, `.env.example`, cổng publish, tên, hoặc chỉ một đầu — luôn kèm lý do.

## Không được
- Tin README/CLAUDE.md/docs: claim của chúng nằm ở section=hypotheses; claim chưa có cạnh tương ứng vào Khoảng trống.
- Đưa cạnh chỉ có một đầu vào bảng cạnh (nó thuộc Khoảng trống).
- Coi `scripts/` là tooling khi map xếp nó là batch-job `manual` (nó ghi/đọc store).
- Bỏ route framework ngoài repo (LangGraph `/threads`, Keycloak...): ghi "framework route", khớp INFERRED, không phải vi phạm.
- Bỏ volume/bucket/topic: chúng là node dữ liệu; mount là cạnh.
- Toàn bộ bảng EXTRACTED: gần như chắc đã bỏ sót mặc định/kết nối suy đoán — rà lại section=env.
- Dừng khi "đủ để kể chuyện": chỉ dừng khi đã liệt kê hết khoảng trống.
- Tự viết code Mermaid.

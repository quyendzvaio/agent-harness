---
name: github-system-architecture
description: Mô tả kiến trúc HỆ THỐNG của một GitHub repository — có những service/process nào chạy, chúng nối với nhau thế nào (HTTP, gRPC, queue, DB, cache, volume), ai sở hữu dữ liệu nào, sơ đồ triển khai. Dùng khi người dùng hỏi "kiến trúc hệ thống", "có những service nào", "nối với nhau thế nào", "vẽ sơ đồ kiến trúc", "onboard vào repo", hoặc trước một thay đổi đi qua nhiều service. Kiến trúc code bên trong một process (module, import, class) dùng github-architecture.
source_types: [github]
allowed_tools: [github_system_map, github_repo_overview, github_search_code, github_read_file, github_list_tree, github_dependency_graph, github_code_graph, github_coverage]
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
1. `github_system_map` (section=overview) **trước mọi tool khác**. Dòng đầu cho biết manifest nào đã đọc, hoặc repo
   không có topology runtime (chỉ dev-host/external-hosted) — khi đó nói thẳng như vậy, không bịa service.
2. `github_system_map` section=gaps, rồi section=env cho các process có cạnh INFERRED. Repo nhiều variant/profile:
   chọn variant người dùng hỏi (mặc định: toàn bộ, ghi rõ node nào thuộc variant/profile nào).
3. Kiểm chứng có chủ đích, mỗi lần đọc phải trỏ tới một dòng mà map đã nêu:
   - Mỗi cạnh/node INFERRED quan trọng và mỗi khoảng trống: `github_read_file` với `start_line`/`end_line` quanh
     dòng đó (±20 dòng), hoặc `github_search_code` tên biến/route/topic.
   - Q6 (sync/async, hợp đồng) và Q8 (fail-open/closed, timeout, auth) đọc **tại điểm gọi** mà bảng cạnh chỉ ra.
   - Không đọc file theo thứ tự thư mục; không đọc README để "hiểu kiến trúc".
4. Người dùng muốn sơ đồ: `architecture_diagram` với `source=system_map` và `url` (thêm `variant`/`process` nếu cần
   một sơ đồ con). Chỉ dùng `source=manual` khi map không có cạnh, và mỗi cạnh phải trích dẫn code đã đọc của hai đầu.
5. Trả lời đúng **5 phần** dưới đây.

## Hình dạng câu trả lời (bắt buộc, theo thứ tự)
1. **Dòng đầu**: nguyên văn dòng đầu của `github_system_map` (`Kiến trúc ĐƯỢC KHAI BÁO từ ... Chưa đối chiếu với runtime.`).
2. **Bảng node**: `tên | loại | nơi chạy | entrypoint | bằng chứng | tin cậy`.
3. **Bảng cạnh**: `từ → đến | kênh | địa chỉ/topic/volume | đầu client | đầu server | sync/async | thời điểm | khi hỏng | tin cậy khớp | tin cậy địa chỉ` — hai cột tin cậy tách riêng.
4. **Sở hữu dữ liệu**: mỗi store, writer, reader, bằng chứng.
5. **Khoảng trống (Q9)**: mọi vi phạm, mỗi dòng một vi phạm, kể cả claim README chưa kiểm được. Không có thì ghi
   "Q9: 0 vi phạm / N bất biến".

Mọi dòng có `[E#]`. Lấy bảng từ kết quả tool (có thể rút gọn cột dài), sửa mức tin cậy chỉ khi đã đọc code xác nhận —
và khi đó trích dẫn nhãn của lần đọc. Nhận xét của bạn (rủi ro, gợi ý) để sau 5 phần, ghi "Nhận định của agent:".

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

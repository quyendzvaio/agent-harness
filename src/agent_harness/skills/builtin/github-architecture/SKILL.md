---
name: github-architecture
description: Phân tích nhanh kiến trúc, luồng thực thi, module và dependency của một GitHub repository. Dùng khi người dùng đưa link github.com và muốn hiểu tổng quan/thiết kế. Nếu người dùng muốn đọc toàn bộ / chi tiết toàn repo, dùng github-deep-dive. Câu hỏi về service/process đang chạy và cách chúng nối với nhau dùng github-system-architecture.
source_types: [github]
allowed_tools: [github_system_map, github_repo_overview, github_code_graph, github_dependency_graph, github_search_code, github_list_tree, github_read_file, github_coverage]
resources: [templates/report.md]
# Overview + two graphs + reading the key files: models batch reads in parallel, so 24 calls is too tight.
# 600 s: a slow free model needs ~100 s just to emit a diagram's arguments; wrap-up starts at 50% / 35% left.
limits:
  max_model_calls: 16
  max_tool_calls: 40
  turn_timeout_s: 600
---
# Phân tích kiến trúc GitHub repository

## Quy trình
0. Repo có manifest triển khai (docker-compose, k8s, Procfile, skaffold): gọi `github_system_map` trước để biết process nào chạy và nối với nhau qua kênh nào; dùng kết quả làm khung, rồi mới đi vào code.
1. `github_repo_overview` trước tiên: metadata, README, file cấu hình, bản đồ cấu trúc và **entrypoint**.
   Entrypoint ghi "declared in ..." là chắc chắn; entrypoint ghi "guess from filename" phải đọc file để xác nhận.
2. `github_dependency_graph` (level=package) để biết module nào phụ thuộc module nào, module nào được dùng nhiều nhất (fan-in) và có vòng phụ thuộc không. **Không tự suy luận quan hệ import khi đã có đồ thị** — trích dẫn nhãn của đồ thị.
3. `github_code_graph` (op=overview) để thấy các abstraction cốt lõi (class/hàm được nối nhiều nhất), các cụm code liên kết chặt và vòng import. Nếu tool báo không khả dụng thì bỏ qua bước này.
   Lần theo luồng bằng `op=neighbors symbol=<tên>` (direction=in: ai gọi/dùng/kế thừa nó; out: nó gọi/dùng gì) và `op=path symbol=<A> target=<B>`.
4. Chọn 3–6 module cốt lõi: fan-in cao, chứa abstraction cốt lõi, nằm trên đường đi từ entrypoint.
5. Đọc mã bằng `github_read_file`, ưu tiên: entrypoint → module điều phối → module domain → test minh hoạ cách dùng.
6. Dùng `github_search_code` để tìm định nghĩa, nơi gọi một hàm, key cấu hình, route... thay vì đoán.
7. Lần theo **một luồng thực thi chính** từ entrypoint đến kết quả, ghi lại từng bước kèm vị trí file:dòng.
8. Trước khi trả lời, gọi `github_coverage` và ghi kết quả vào mục "Phạm vi đã đọc".
9. Trả lời theo `templates/report.md` (đọc bằng `read_skill_resource`).

## Quy tắc
- Mọi khẳng định về code phải trích dẫn `[E#]` trỏ tới đoạn đã đọc, kết quả tìm kiếm hoặc đồ thị. Không suy đoán nội dung file chưa đọc.
- Quan hệ trong code graph ghi `[INFERRED]` hoặc `[AMBIGUOUS]` chỉ là ứng viên: đọc đoạn code đó để xác nhận rồi mới khẳng định, và trích dẫn đoạn đã đọc.
- Phân biệt rõ: điều code thể hiện (có trích dẫn) và nhận định/đánh giá của bạn (ghi "Nhận định của agent:").
- Đây là phân tích nhanh: đọc khoảng 8–15 file quan trọng nhất, nói rõ phạm vi đã đọc.

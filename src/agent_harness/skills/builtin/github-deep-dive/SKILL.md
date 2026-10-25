---
name: github-deep-dive
description: Đọc và hiểu TOÀN BỘ mã nguồn của một GitHub repository, module theo module, rồi tổng hợp kiến trúc chi tiết. Dùng khi người dùng yêu cầu đọc full / toàn bộ / chi tiết / hiểu cả repo. Chậm và tốn nhiều lượt gọi hơn github-architecture.
source_types: [github]
allowed_tools: [github_system_map, github_repo_overview, github_code_graph, github_dependency_graph, github_search_code, github_list_tree, github_read_file, github_coverage]
resources: [templates/report.md]
limits:
  max_model_calls: 50
  max_tool_calls: 180
  turn_timeout_s: 1200
---
# Đọc toàn bộ repository

Mục tiêu: đọc **toàn bộ mã nguồn** (không tính file sinh tự động, vendored, dữ liệu), hiểu từng module, rồi tổng hợp.
Context của bạn có hạn: kết quả tool cũ sẽ bị xoá khỏi context khi quá dài. Vì vậy **sau mỗi module phải `save_note`** — ghi chú được giữ lại suốt lượt và là nguồn chính để viết báo cáo cuối.

## Quy trình
1. **Bản đồ**: nếu repo có manifest triển khai (docker-compose, k8s, Procfile, skaffold), `github_system_map` trước để biết process/kênh và thư mục code của từng process; sau đó `github_repo_overview`, rồi `github_code_graph` (op=overview: abstraction cốt lõi, các cụm code, vòng import — bỏ qua nếu không khả dụng), `github_dependency_graph` (level=package) và `github_coverage` để có danh sách toàn bộ file nguồn.
2. **Thứ tự đọc**: đi từ lá lên gốc của đồ thị phụ thuộc — đọc module được phụ thuộc nhiều nhất (fan-in cao, thường là domain/model/util) trước, rồi đến module điều phối, cuối cùng là entrypoint/API/CLI. Đọc test sau cùng của mỗi module.
3. **Mỗi module** (một thư mục/package):
   - Đọc hết các file của module bằng `github_read_file` (file dài: đọc nhiều lần với `start_line`).
   - Dùng `github_dependency_graph` level=file, scope=<module> để thấy quan hệ import trong module, và `github_code_graph` op=neighbors cho các class/hàm chính để biết ai gọi/kế thừa/dùng chúng.
   - `save_note(topic=<module>, text=...)`: trách nhiệm, các type/hàm chính, phụ thuộc vào/ra, luồng dữ liệu, điểm đáng chú ý — **kèm [E#]**.
4. **Kiểm tra độ phủ** sau mỗi 2–3 module: `github_coverage`. Tiếp tục với các file "Unread, most depended-on first" cho đến khi đọc ≥ 90% file nguồn hoặc sắp hết ngân sách (xem "Budget" khi nạp skill).
5. **Liên kết**: dùng `github_code_graph` (op=path, op=neighbors direction=in) và `github_search_code` để kiểm tra các điểm nối giữa module (ai gọi hàm nào, cấu hình được đọc ở đâu). Quan hệ `[INFERRED]`/`[AMBIGUOUS]` phải đọc code xác nhận trước khi đưa vào báo cáo.
6. **Tổng hợp** theo `templates/report.md`, dựa trên các ghi chú. Ghi rõ độ phủ cuối cùng.

## Quy tắc
- Mọi khẳng định phải có `[E#]`; nhãn từ các lần đọc trước vẫn hợp lệ dù nội dung đã bị xoá khỏi context.
- Không bỏ qua module chỉ vì nó "có vẻ đơn giản" — mục tiêu là độ phủ.
- Nếu hết ngân sách trước khi đọc xong, vẫn viết báo cáo với những gì đã đọc và liệt kê phần còn thiếu.

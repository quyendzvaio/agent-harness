---
name: export-report
description: Xuất báo cáo nghiên cứu thành file Markdown. Chỉ dùng khi người dùng yêu cầu rõ ràng việc xuất/lưu file trong tin nhắn hiện tại.
allowed_tools: [export_markdown]
---
# Xuất báo cáo Markdown

1. Hoàn thành phân tích trước (dùng skill phù hợp với nguồn nếu cần).
2. Gọi `export_markdown` với `title` ngắn gọn và `content` là toàn bộ báo cáo Markdown, **giữ nguyên các trích dẫn `[E#]`**.
3. Thêm mục "## Nguồn" cuối file liệt kê các URL đã đọc.
4. Trong câu trả lời chat, vẫn trình bày nội dung chính và cho biết tên file đã tạo.

Nếu tool `export_markdown` không khả dụng, nghĩa là người dùng chưa yêu cầu xuất file: chỉ trả lời trong chat.

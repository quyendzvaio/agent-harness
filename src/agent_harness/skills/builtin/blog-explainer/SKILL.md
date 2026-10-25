---
name: blog-explainer
description: Đọc một bài blog kỹ thuật, trích xuất luận điểm, tóm tắt và giải thích kỹ thuật, tách rõ ý của tác giả với suy luận của agent. Dùng khi người dùng đưa link bài viết (không phải GitHub).
source_types: [blog]
allowed_tools: [web_fetch_article]
resources: [templates/report.md]
---
# Giải thích bài blog kỹ thuật

## Quy trình
1. Gọi `web_fetch_article` với URL bài viết.
2. Xác định câu hỏi/vấn đề bài viết giải quyết và các luận điểm chính theo thứ tự tác giả trình bày.
3. Với mỗi khái niệm kỹ thuật khó, giải thích ngắn gọn bằng ngôn ngữ của người dùng.
4. Trả lời theo `templates/report.md` (đọc bằng `read_skill_resource`).

## Quy tắc
- Mục "Tác giả khẳng định" chỉ chứa nội dung có trong bài, mỗi ý có `[E#]`.
- Mục "Diễn giải / suy luận của agent" chứa phần giải thích thêm của bạn, ghi rõ đó là suy luận.
- Nếu bài viết thiếu dẫn chứng cho một khẳng định, nói rõ điều đó.

# Frontend (Next.js) — giao diện thử thủ công

Chat với agent qua SSE, xem từng tool call, báo cáo trích dẫn, usage, và nội dung của mỗi `[E#]`.

```bash
# backend (thư mục gốc): uv run agent-harness serve   -> http://127.0.0.1:8000
cd frontend
cp .env.example .env.local   # BACKEND_URL, BACKEND_API_KEY (chỉ khi AUTH_ENABLED=true)
npm install
npm run dev                  # http://localhost:3100
```

- Trình duyệt chỉ gọi `/api/backend/*`; route handler `app/api/backend/[...path]/route.ts` chuyển tiếp sang
  `BACKEND_URL` (không cần CORS, API key ở phía server, stream SSE không bị buffer, bấm **Dừng** là hủy lượt).
- Chip `[E#]` đỏ gạch ngang = nhãn không có trong evidence của hội thoại (trích dẫn bịa).
- `?c=<conversation_id>` trên URL mở lại một hội thoại. Hội thoại cũ chỉ có nội dung tin nhắn và evidence;
  chi tiết tool call chỉ hiện với lượt chạy trong phiên.

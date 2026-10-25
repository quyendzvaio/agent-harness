from __future__ import annotations

from functools import cache
from pathlib import Path

from agent_harness.domain.skills import SkillManifest

_PROMPTS = Path(__file__).parent / "prompts"


@cache
def _template(name: str) -> str:
    return (_PROMPTS / name).read_text(encoding="utf-8")


def build_system_prompt(
    *, skills: list[SkillManifest], active_skill: SkillManifest | None, language: str, export_allowed: bool,
    wants_diagram: bool = False, notes: list[dict] | None = None, wrap_up: str | None = None,
) -> str:
    catalog = "\n".join(f"- `{s.name}`: {s.description}" for s in skills) or "- (không có skill nào)"
    active = f"Skill đang dùng: `{active_skill.name}`." if active_skill else "Chưa nạp skill nào."
    export = (
        "Người dùng ĐÃ yêu cầu xuất file trong tin nhắn này: dùng tool `export_markdown` sau khi hoàn thành phân tích."
        if export_allowed
        else "Người dùng KHÔNG yêu cầu xuất file: chỉ trả lời trong chat, không tạo file."
    )
    diagram = (
        "Người dùng ĐÃ yêu cầu vẽ sơ đồ: gọi tool `architecture_diagram`. Repo GitHub có manifest triển khai: ưu tiên "
        "`source=system_map` kèm `url` (harness tự dựng node/cạnh từ `github_system_map`; thêm `process=<tên>` cho sơ "
        "đồ một luồng). Chỉ dùng `source=manual` khi map không có cạnh: node là thành phần chạy thực tế (service, "
        "worker, scheduler, DB, queue, cache, API ngoài, client), không phải từng file; đánh số luồng 1, 2.1, 2.2... "
        "theo thứ tự thực thi; mỗi cạnh trích dẫn evidence của code đã đọc (github_read_file/github_search_code) chứng "
        "minh nó, không dùng README. Có thể vẽ một sơ đồ tổng thể và một đến hai sơ đồ luồng chính. Harness tự chèn sơ "
        "đồ và bảng các bước vào câu trả lời: KHÔNG chép lại, KHÔNG lặp lại câu trả lời trước, chỉ giải thích ngắn các "
        "bước kèm [E#]. KHÔNG tự viết code Mermaid."
        if wants_diagram
        else "Người dùng KHÔNG yêu cầu vẽ sơ đồ: không vẽ sơ đồ Mermaid/ASCII."
    )
    prompt = _template("system.md").format(
        skills_catalog=catalog,
        active_skill=active,
        export_rule=export,
        diagram_rule=diagram,
        language="tiếng Việt" if language == "vi" else "English",
    )
    if wrap_up == "answer":
        prompt += (
            "\n\n# NGÂN SÁCH ĐÃ HẾT\nĐây là lần gọi cuối, không còn tool nào. Viết NGAY câu trả lời cuối đầy đủ cho "
            "người dùng từ evidence và ghi chú đã có (kèm trích dẫn [E#]); nói rõ phần nào chưa kịp đọc."
        )
    elif wrap_up == "diagram":
        prompt += (
            "\n\n# NGÂN SÁCH SẮP HẾT\nGọi `architecture_diagram` NGAY (`source=system_map` kèm `url` nếu đã gọi "
            "`github_system_map`; nếu không thì với các thành phần và luồng đã thấy, chỉ dùng nhãn [E#] đã có), rồi "
            "viết câu trả lời cuối."
        )
    if notes:
        prompt += "\n\n# Ghi chú đã lưu (save_note)\n" + "\n".join(f"## {n['topic']}\n{n['text']}" for n in notes)
    return prompt

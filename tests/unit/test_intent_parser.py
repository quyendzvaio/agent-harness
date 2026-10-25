import pytest

from agent_harness.application.intent_parser import parse_intent


@pytest.mark.parametrize(
    "message",
    [
        "phân tích https://github.com/a/b và xuất file md",
        "Xuất ra file markdown giúp mình",
        "lưu thành file nhé",
        "tạo file báo cáo",
        "please export the report",
        "save it as markdown",
        "Phân tích repo rồi lưu md",
    ],
)
def test_detects_explicit_export(message):
    assert parse_intent(message).explicit_export is True


@pytest.mark.parametrize(
    "message",
    [
        "phân tích kiến trúc https://github.com/a/b",
        "không cần xuất file, trả lời trong chat thôi",
        "đừng tạo file",
        "don't export anything",
        "file này làm gì?",
        "https://example.com/export/page",
    ],
)
def test_no_export_without_explicit_request(message):
    assert parse_intent(message).explicit_export is False


def test_extracts_urls_without_trailing_punctuation_and_dedupes():
    intent = parse_intent("xem https://github.com/a/b, và https://github.com/a/b.")
    assert intent.urls == ("https://github.com/a/b",)


def test_language_detection():
    assert parse_intent("phân tích repo này").language == "vi"
    assert parse_intent("analyse this repository please").language == "en"


@pytest.mark.parametrize(
    "message",
    [
        "Vẽ sơ đồ kiến trúc chi tiết https://github.com/quyendzvaio/AI_SOC",
        "vẽ giúp tôi luồng xử lý của repo https://github.com/a/b",
        "phân tích và vẽ kiến trúc https://github.com/a/b",
        "cho tôi sơ đồ kiến trúc hệ thống của https://github.com/a/b",
        "draw the architecture of https://github.com/a/b",
        "give me a diagram of https://github.com/a/b",
        "render it as mermaid",
    ],
)
def test_detects_diagram_request(message):
    assert parse_intent(message).wants_diagram


@pytest.mark.parametrize(
    "message",
    [
        "Phân tích kiến trúc https://github.com/a/b",
        "đọc toàn bộ https://github.com/a/b",
        "phân tích kiến trúc, không cần vẽ sơ đồ https://github.com/a/b",
        "https://github.com/a/diagram-tools",  # words inside URLs do not count
    ],
)
def test_no_diagram_without_request(message):
    assert not parse_intent(message).wants_diagram

import httpx
import pytest
import respx

from agent_harness.domain.errors import PolicyDenied, ToolFailed, TransientError
from agent_harness.domain.policies import SourcePolicy
from agent_harness.research.citation_validator import validate_citations
from agent_harness.sources.blog.article_parser import parse_article
from agent_harness.sources.blog.detector import blog_source
from agent_harness.sources.github.architecture_analyzer import analyze
from agent_harness.sources.github.detector import parse_github_url
from agent_harness.sources.http import SafeHttpClient
from fakes import FakeGithubClient, blog_html


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/acme/tiny-api", ("acme", "tiny-api", None, "")),
        ("https://github.com/acme/tiny-api.git", ("acme", "tiny-api", None, "")),
        ("https://github.com/acme/tiny-api/tree/dev/src/app", ("acme", "tiny-api", "dev", "src/app")),
        ("https://github.com/acme/tiny-api/blob/main/README.md", ("acme", "tiny-api", "main", "README.md")),
    ],
)
def test_github_detector(url, expected):
    target = parse_github_url(url)
    assert (target.owner, target.repo, target.ref, target.path) == expected
    assert target.source.canonical_id == "github:acme/tiny-api"


@pytest.mark.parametrize("url", ["https://gitlab.com/a/b", "https://github.com/acme", "ftp://github.com/a/b"])
def test_github_detector_rejects(url):
    assert parse_github_url(url) is None


def test_blog_canonical_id_drops_tracking_and_fragment():
    a = blog_source("https://Blog.example.com/post/?utm_source=x#top")
    b = blog_source("https://blog.example.com/post")
    assert a.canonical_id == b.canonical_id


def test_article_parser_keeps_article_text_only():
    article = parse_article(blog_html())
    assert article.title == "Why we moved to event sourcing"
    assert article.author == "Jamie Doe"
    headings = [s.heading for s in article.sections]
    assert headings[:3] == ["", "The problem", "What we changed"] or "The problem" in headings
    text = " ".join(p for s in article.sections for p in s.paragraphs)
    assert "immutable event" in text
    assert "newsletter" not in text and "Copyright" not in text and "tracking" not in text


async def test_architecture_summary_from_fixture():
    client = FakeGithubClient()
    tree = await client.get_tree("acme", "tiny-api", "main")
    summary = analyze(tree, {"pyproject.toml": client.fixture["files"]["pyproject.toml"]})
    assert ("Python", 5) in summary.languages
    assert summary.entrypoints[0] == (
        "src/tiny_api/main.py", "declared in pyproject.toml [project.scripts]: `tiny-api = tiny_api.main:app`"
    )
    assert summary.dependencies["pyproject.toml"] == ["fastapi", "sqlalchemy", "pydantic"]
    assert "node_modules/" not in summary.top_level


def test_citation_validator():
    text = (
        "## Tổng quan\n"
        "Service này dùng FastAPI làm web framework chính [E1].\n"
        "NoteStore lưu ghi chú trong một list ở bộ nhớ của tiến trình [E2] [E9].\n"
        "Câu này là một khẳng định dài nhưng hoàn toàn không có trích dẫn nào.\n"
        "## Nhận định của agent\n"
        "Kiến trúc này đơn giản nhưng sẽ mất dữ liệu khi tiến trình khởi động lại.\n"
    )
    report = validate_citations(text, {"E1", "E2"})
    assert report.valid == ("E1", "E2")
    assert report.invalid == ("E9",)
    assert report.uncited_sentences == 1 and report.total_sentences == 3


async def _public(host: str) -> list[str]:
    return ["93.184.216.34"]


async def _internal(host: str) -> list[str]:
    return ["10.0.0.7"] if host == "evil.example" else ["93.184.216.34"]


@respx.mock
async def test_safe_http_blocks_redirect_to_internal_host():
    respx.get("https://public.example/start").mock(return_value=httpx.Response(302, headers={"location": "http://evil.example/x"}))
    client = SafeHttpClient(SourcePolicy(), resolver=_internal)
    with pytest.raises(PolicyDenied):
        await client.get("https://public.example/start")


@respx.mock
async def test_safe_http_blocks_redirect_to_literal_metadata_ip():
    respx.get("https://public.example/a").mock(
        return_value=httpx.Response(301, headers={"location": "http://169.254.169.254/latest"})
    )
    with pytest.raises(PolicyDenied):
        await SafeHttpClient(SourcePolicy(), resolver=_public).get("https://public.example/a")


@respx.mock
async def test_safe_http_size_limit_and_transient_errors():
    respx.get("https://public.example/big").mock(return_value=httpx.Response(200, content=b"x" * 5000))
    respx.get("https://public.example/down").mock(return_value=httpx.Response(503))
    client = SafeHttpClient(SourcePolicy(max_fetch_bytes=1024), resolver=_public)
    with pytest.raises(ToolFailed):
        await client.get("https://public.example/big")
    with pytest.raises(TransientError):
        await client.get("https://public.example/down")


@respx.mock
async def test_safe_http_follows_safe_redirect():
    respx.get("https://public.example/old").mock(return_value=httpx.Response(301, headers={"location": "/new"}))
    respx.get("https://public.example/new").mock(return_value=httpx.Response(200, text="hello"))
    response = await SafeHttpClient(SourcePolicy(), resolver=_public).get("https://public.example/old")
    assert response.text == "hello" and response.url.endswith("/new")

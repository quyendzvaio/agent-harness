"""Real LLM endpoint (BASE_URL) + GitHub, in-memory persistence. Run with: uv run pytest -m live tests/e2e"""

import os

import pytest

from agent_harness.config.settings import Settings
from builders import chat, client, data_of, kinds, make_container

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("LLM_API_KEY") and not os.path.exists(".env"), reason="needs LLM_API_KEY"),
]

REPO = os.environ.get("LIVE_REPO_URL", "https://github.com/pallets/itsdangerous")


async def test_live_github_architecture_turn():
    from agent_harness.sources.http import SafeHttpClient

    from agent_harness.sources.github.client import GithubClient

    settings = Settings()  # the app's default limits; models batch parallel tool calls
    fetcher = SafeHttpClient(_policy(settings))
    token = settings.github_token.get_secret_value() if settings.github_token else None
    # make_container defaults to FakeGithubClient; a live run needs the real one.
    container = await make_container(
        settings, github=GithubClient(fetcher, token=token), fetcher=fetcher, code_graph_builder=None
    )
    async with client(container) as http:
        _, events = await chat(http, f"Phân tích ngắn gọn kiến trúc của {REPO}")
    assert kinds(events)[-1] == "done", data_of(events, "error")
    assert data_of(events, "skill_loaded")
    report = data_of(events, "citation_report")[0]
    assert report["valid"] and not report["invalid"]


def _policy(settings):
    from agent_harness.domain.policies import SourcePolicy

    return SourcePolicy(max_fetch_bytes=settings.limits.max_fetch_bytes)

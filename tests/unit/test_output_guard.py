"""output_guard on real degenerate output (gpt-oss via an OpenAI-compatible gateway, AI_SOC analysis)."""

from agent_harness.adapters.models.output_guard import check

LEAKED = (
    "We have a lot of code. We need to produce a summary of the repository. The user just gave the link again. "
    "We need to produce a concise summary of the repo, focusing on architecture, modules, key functionalities, etc.\n\n"
    "We should include key modules: core (config, security), routers (auth, emails, ingest, internal, logs).\n"
)
LOOP = "## Tổng quan\n\nRepo dùng FastAPI [E3].\n\n" + "".join(
    f"We should also mention that the system uses a Dockerfile for services.\nAlso mention system uses item {i}.\n"
    for i in range(7)
)
VIETNAMESE_ANSWER = """## Tổng quan nhanh về `quyendzvaio/AI_SOC`

| Thành phần | Mô tả | Trích dẫn |
|---|---|---|
| Backend | FastAPI + async SQLAlchemy, PostgreSQL | [E33] |
| Backend | FastAPI + async SQLAlchemy, PostgreSQL | [E33] |
|---|---|---|

- Xác thực OTP qua SMTP và JWT [E31].
- Xác thực OTP qua SMTP và JWT [E31].

```python
return x
return x
return x
return x
return x
return x
```
"""
ENGLISH_ANSWER = (
    "This repository implements an AI-assisted SOC. The backend is a FastAPI app [E33] with async SQLAlchemy. "
    "We need a running Postgres for it, as the README says [E2]. Collectors push logs over HTTP [E40]."
)


def test_leaked_reasoning_at_the_start_is_caught():
    assert check(LEAKED).startswith("leaked reasoning")


def test_leak_needs_enough_text_to_judge():
    assert check("We need to produce") is None


def test_repetition_loop_is_caught_even_after_a_real_start():
    assert check(LOOP).startswith("repetition loop")


def test_normal_answers_pass():
    assert check(VIETNAMESE_ANSWER) is None  # short repeated lines, table rules and code are fine
    assert check(ENGLISH_ANSWER) is None  # "We need ..." later in the text is not a leak

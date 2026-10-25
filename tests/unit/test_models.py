import pytest
from pydantic import SecretStr

from agent_harness.adapters.models.openai_compatible import openai_compatible_model


def test_openai_compatible_model_targets_base_url():
    model = openai_compatible_model("some/model", api_key=SecretStr("k"), base_url="https://llm.example/v1")
    assert model.openai_api_base == "https://llm.example/v1"
    assert model.model_name == "some/model"
    assert model.streaming is True and model.stream_usage is True
    assert model.request_timeout == 120  # seconds for the openai SDK


def test_openai_compatible_model_requires_key():
    with pytest.raises(ValueError, match="LLM_API_KEY"):
        openai_compatible_model("m", api_key=None, base_url="https://llm.example/v1")

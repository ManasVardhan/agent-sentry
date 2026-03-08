"""Tests for framework integrations."""

import pytest
from unittest.mock import MagicMock
from agent_sentry.storage import EventStore
from agent_sentry.capture import EventCapture
from agent_sentry.alerts import AlertManager
from agent_sentry.integrations.langchain import AgentSentryCallbackHandler
from agent_sentry.integrations.openai import SentryOpenAIWrapper, _estimate_cost
from agent_sentry.integrations.anthropic import SentryAnthropicWrapper


@pytest.fixture
def capture(tmp_path):
    store = EventStore(str(tmp_path / "test.db"))
    return EventCapture(store=store, alert_manager=AlertManager(async_send=False))


# LangChain tests

def test_langchain_llm_start_end(capture):
    handler = AgentSentryCallbackHandler(capture=capture)

    handler.on_llm_start(
        serialized={"id": ["langchain", "chat_models", "ChatOpenAI"]},
        prompts=["Hello"],
        run_id="run-1",
    )

    response = MagicMock()
    response.llm_output = {"token_usage": {"prompt_tokens": 10, "completion_tokens": 5}}
    response.generations = [[MagicMock(text="Hi there!")]]

    handler.on_llm_end(response, run_id="run-1")

    events = capture.store.get_events()
    assert len(events) == 1
    assert events[0]["success"] is True
    assert events[0]["event_type"] == "llm_call"


def test_langchain_llm_error(capture):
    handler = AgentSentryCallbackHandler(capture=capture)

    handler.on_llm_start(
        serialized={"id": ["ChatOpenAI"]},
        prompts=["Hello"],
        run_id="run-2",
    )
    handler.on_llm_error(ValueError("API Error"), run_id="run-2")

    events = capture.store.get_events()
    assert len(events) == 1
    assert events[0]["success"] is False
    assert events[0]["error_type"] == "ValueError"


def test_langchain_tool_start_end(capture):
    handler = AgentSentryCallbackHandler(capture=capture)

    handler.on_tool_start(
        serialized={"name": "search"},
        input_str="python tutorials",
        run_id="run-3",
    )
    handler.on_tool_end("Found 10 results", run_id="run-3")

    events = capture.store.get_events()
    assert len(events) == 1
    assert events[0]["event_type"] == "tool_call"
    assert events[0]["function_name"] == "search"


# OpenAI wrapper tests

def test_openai_wrapper_success(capture):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.usage.prompt_tokens = 100
    mock_response.usage.completion_tokens = 50
    mock_response.usage.total_tokens = 150
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Hello!"
    mock_client.chat.completions.create.return_value = mock_response

    wrapper = SentryOpenAIWrapper(mock_client, capture=capture)
    result = wrapper.chat_completions_create(
        model="gpt-4",
        messages=[{"role": "user", "content": "Hi"}],
    )

    assert result == mock_response
    events = capture.store.get_events()
    assert len(events) == 1
    assert events[0]["success"] is True
    assert events[0]["cost"] is not None


def test_openai_wrapper_failure(capture):
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = Exception("Rate limit exceeded")

    wrapper = SentryOpenAIWrapper(mock_client, capture=capture)
    with pytest.raises(Exception, match="Rate limit"):
        wrapper.chat_completions_create(model="gpt-4", messages=[])

    events = capture.store.get_events()
    assert len(events) == 1
    assert events[0]["success"] is False


def test_openai_cost_estimation():
    usage = {"prompt_tokens": 1000, "completion_tokens": 500}
    cost = _estimate_cost("gpt-4", usage)
    assert cost is not None
    assert cost > 0

    # Unknown model
    assert _estimate_cost("unknown-model", usage) is None


# Anthropic wrapper tests

def test_anthropic_wrapper_success(capture):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.usage.input_tokens = 100
    mock_response.usage.output_tokens = 50
    mock_block = MagicMock()
    mock_block.text = "Hello from Claude!"
    mock_response.content = [mock_block]
    mock_client.messages.create.return_value = mock_response

    wrapper = SentryAnthropicWrapper(mock_client, capture=capture)
    result = wrapper.messages_create(
        model="claude-3-sonnet-20240229",
        max_tokens=1024,
        messages=[{"role": "user", "content": "Hi"}],
    )

    assert result == mock_response
    events = capture.store.get_events()
    assert len(events) == 1
    assert events[0]["success"] is True
    assert events[0]["function_name"] == "anthropic.claude-3-sonnet-20240229"


def test_anthropic_wrapper_failure(capture):
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = Exception("Unauthorized")

    wrapper = SentryAnthropicWrapper(mock_client, capture=capture)
    with pytest.raises(Exception, match="Unauthorized"):
        wrapper.messages_create(model="claude-3-sonnet", messages=[])

    events = capture.store.get_events()
    assert len(events) == 1
    assert events[0]["success"] is False

"""Tests for the LlamaIndex integration."""

import pytest
from unittest.mock import MagicMock

from agent_sentry.storage import EventStore
from agent_sentry.capture import EventCapture
from agent_sentry.alerts import AlertManager
from agent_sentry.integrations.llamaindex import AgentSentryLlamaIndexHandler


@pytest.fixture
def capture(tmp_path):
    store = EventStore(str(tmp_path / "test.db"))
    return EventCapture(store=store, alert_manager=AlertManager(async_send=False))


@pytest.fixture
def handler(capture):
    return AgentSentryLlamaIndexHandler(capture=capture)


class FakeEventType:
    """Mimics LlamaIndex's CBEventType str enum members."""

    def __init__(self, value):
        self.value = value


def _llm_response(content="Hi there!", usage=None):
    response = MagicMock()
    response.message.content = content
    response.raw = {"usage": usage} if usage else None
    return response


class TestLLMEvents:
    def test_llm_start_end_success(self, handler, capture):
        handler.on_event_start(
            "llm",
            payload={"serialized": {"model": "gpt-4o"}, "formatted_prompt": "Hello"},
            event_id="e1",
        )
        handler.on_event_end(
            "llm",
            payload={"response": _llm_response("Hi there!")},
            event_id="e1",
        )

        events = capture.store.get_events()
        assert len(events) == 1
        e = events[0]
        assert e["event_type"] == "llm_call"
        assert e["function_name"] == "gpt-4o"
        assert e["success"] is True
        assert "Hi there!" in e["result"]
        assert e["duration_ms"] >= 0
        assert e["tags"] == ["llamaindex"]

    def test_llm_token_usage_from_raw_dict(self, handler, capture):
        handler.on_event_start("llm", payload={"model_name": "gpt-4o"}, event_id="e1")
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        handler.on_event_end(
            "llm", payload={"response": _llm_response(usage=usage)}, event_id="e1"
        )

        events = capture.store.get_events()
        assert events[0]["token_usage"] == usage

    def test_llm_messages_captured(self, handler, capture):
        msg = MagicMock()
        msg.role = "user"
        msg.content = "What is 2+2?"
        handler.on_event_start("llm", payload={"messages": [msg]}, event_id="e1")
        handler.on_event_end("llm", payload={"response": _llm_response("4")}, event_id="e1")

        events = capture.store.get_events()
        args = events[0]["args"]
        assert "What is 2+2?" in str(args)

    def test_llm_failure_classified(self, handler, capture):
        handler.on_event_start("llm", payload={"model_name": "gpt-4o"}, event_id="e1")
        handler.on_event_end(
            "llm",
            payload={"exception": TimeoutError("request timed out after 30s")},
            event_id="e1",
        )

        events = capture.store.get_events()
        assert len(events) == 1
        e = events[0]
        assert e["success"] is False
        assert e["error_type"] == "TimeoutError"
        assert "timed out" in e["error_message"]
        assert e["root_cause"] is not None

    def test_llm_completion_payload(self, handler, capture):
        completion = MagicMock()
        completion.text = "completion text"
        completion.raw = None
        handler.on_event_start("llm", payload={}, event_id="e1")
        handler.on_event_end("llm", payload={"completion": completion}, event_id="e1")

        events = capture.store.get_events()
        assert "completion text" in events[0]["result"]
        assert events[0]["function_name"] == "llamaindex_llm"


class TestToolEvents:
    def test_tool_start_end(self, handler, capture):
        tool = MagicMock()
        tool.name = "search"
        handler.on_event_start(
            "function_call",
            payload={"tool": tool, "function_call": {"query": "weather"}},
            event_id="t1",
        )
        handler.on_event_end(
            "function_call",
            payload={"function_call_response": "sunny, 22C"},
            event_id="t1",
        )

        events = capture.store.get_events()
        assert len(events) == 1
        e = events[0]
        assert e["event_type"] == "tool_call"
        assert e["function_name"] == "search"
        assert "sunny" in e["result"]

    def test_tool_error(self, handler, capture):
        handler.on_event_start("function_call", payload={"tool": "search"}, event_id="t1")
        handler.on_event_end(
            "function_call",
            payload={"exception": ValueError("bad input")},
            event_id="t1",
        )

        events = capture.store.get_events()
        assert events[0]["success"] is False
        assert events[0]["error_type"] == "ValueError"
        assert events[0]["event_type"] == "tool_call"


class TestQueryEvents:
    def test_query_start_end(self, handler, capture):
        handler.on_event_start(
            "query", payload={"query_str": "What is RAG?"}, event_id="q1"
        )
        handler.on_event_end("query", payload={"response": "RAG is..."}, event_id="q1")

        events = capture.store.get_events()
        assert len(events) == 1
        e = events[0]
        assert e["event_type"] == "function_call"
        assert e["function_name"] == "query"
        assert "What is RAG?" in str(e["args"])

    def test_sub_question_and_agent_step(self, handler, capture):
        handler.on_event_start(
            "sub_question", payload={"sub_question": "part 1"}, event_id="s1"
        )
        handler.on_event_end("sub_question", payload={}, event_id="s1")
        handler.on_event_start("agent_step", payload={}, event_id="a1")
        handler.on_event_end("agent_step", payload={}, event_id="a1")

        events = capture.store.get_events()
        names = {e["function_name"] for e in events}
        assert names == {"sub_question", "agent_step"}


class TestExceptionEvents:
    def test_standalone_exception_event(self, handler, capture):
        handler.on_event_start(
            "exception",
            payload={"exception": RuntimeError("pipeline blew up")},
            event_id="x1",
        )

        events = capture.store.get_events()
        assert len(events) == 1
        assert events[0]["success"] is False
        assert events[0]["error_type"] == "RuntimeError"
        assert "pipeline blew up" in events[0]["error_message"]

    def test_exception_end_without_start(self, handler, capture):
        handler.on_event_end(
            "llm",
            payload={"exception": ValueError("orphan failure")},
            event_id="never-started",
        )

        events = capture.store.get_events()
        assert len(events) == 1
        assert events[0]["success"] is False


class TestRobustness:
    def test_unknown_event_types_ignored(self, handler, capture):
        handler.on_event_start("chunking", payload={}, event_id="c1")
        handler.on_event_end("chunking", payload={}, event_id="c1")
        handler.on_event_start("embedding", payload={}, event_id="c2")
        handler.on_event_end("embedding", payload={}, event_id="c2")

        assert capture.store.get_events() == []

    def test_end_without_start_ignored(self, handler, capture):
        handler.on_event_end("llm", payload={"response": "orphan"}, event_id="ghost")
        assert capture.store.get_events() == []

    def test_enum_like_event_types(self, handler, capture):
        handler.on_event_start(
            FakeEventType("llm"), payload={"model_name": "gpt-4o"}, event_id="e1"
        )
        handler.on_event_end(
            FakeEventType("llm"), payload={"response": _llm_response()}, event_id="e1"
        )

        events = capture.store.get_events()
        assert len(events) == 1
        assert events[0]["event_type"] == "llm_call"

    def test_none_payloads(self, handler, capture):
        event_id = handler.on_event_start("llm", payload=None, event_id="")
        assert event_id  # generated
        handler.on_event_end("llm", payload=None, event_id=event_id)

        events = capture.store.get_events()
        assert len(events) == 1
        assert events[0]["success"] is True

    def test_custom_tags(self, capture):
        handler = AgentSentryLlamaIndexHandler(capture=capture, tags=["rag", "prod"])
        handler.on_event_start("llm", payload={}, event_id="e1")
        handler.on_event_end("llm", payload={"response": _llm_response()}, event_id="e1")

        assert capture.store.get_events()[0]["tags"] == ["rag", "prod"]

    def test_callback_manager_attributes_present(self, handler):
        # CallbackManager reads these regardless of llama-index availability.
        assert handler.event_starts_to_ignore == ()
        assert handler.event_ends_to_ignore == ()

    def test_trace_lifecycle_noops(self, handler):
        handler.start_trace("trace-1")
        handler.end_trace("trace-1", trace_map={"root": ["child"]})

    def test_unprintable_payload_value(self, handler, capture):
        class Unprintable:
            def __repr__(self):
                raise RuntimeError("nope")

        handler.on_event_start(
            "function_call",
            payload={"tool": "t", "function_call": Unprintable()},
            event_id="t1",
        )
        handler.on_event_end(
            "function_call",
            payload={"function_call_response": Unprintable()},
            event_id="t1",
        )

        events = capture.store.get_events()
        assert len(events) == 1
        assert "unprintable" in events[0]["result"]

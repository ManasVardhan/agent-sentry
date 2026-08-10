"""Tests for cost tracking analytics (agent_sentry.costs)."""

import json

import pytest

from agent_sentry.costs import (
    CostBucket,
    _event_model,
    _event_tokens,
    aggregate_costs,
    summarize_costs,
)


def make_event(
    cost=None,
    model=None,
    function_name="openai.gpt-4",
    success=True,
    timestamp="2026-08-09T10:00:00+00:00",
    token_usage=None,
):
    event = {
        "function_name": function_name,
        "success": success,
        "timestamp": timestamp,
    }
    if cost is not None:
        event["cost"] = cost
    if model is not None:
        event["args"] = {"model": model}
    if token_usage is not None:
        event["token_usage"] = token_usage
    return event


class TestEventModel:
    def test_model_from_args(self):
        event = make_event(model="gpt-4o", function_name="whatever")
        assert _event_model(event) == "gpt-4o"

    def test_model_from_function_name(self):
        event = make_event(function_name="anthropic.claude-3-5-sonnet")
        assert _event_model(event) == "claude-3-5-sonnet"

    def test_openai_prefix(self):
        event = make_event(function_name="openai.gpt-4o-mini")
        assert _event_model(event) == "gpt-4o-mini"

    def test_non_llm_dotted_name_is_unknown(self):
        event = make_event(function_name="mytool.search")
        assert _event_model(event) == "unknown"

    def test_plain_function_name_is_unknown(self):
        event = make_event(function_name="my_agent")
        assert _event_model(event) == "unknown"

    def test_args_model_wins_over_function_name(self):
        event = make_event(model="gpt-4o", function_name="openai.gpt-4")
        assert _event_model(event) == "gpt-4o"


class TestEventTokens:
    def test_total_tokens_preferred(self):
        event = make_event(token_usage={"total_tokens": 1500, "prompt_tokens": 1000})
        assert _event_tokens(event) == 1500

    def test_openai_style_sum(self):
        event = make_event(token_usage={"prompt_tokens": 1000, "completion_tokens": 500})
        assert _event_tokens(event) == 1500

    def test_anthropic_style_sum(self):
        event = make_event(token_usage={"input_tokens": 200, "output_tokens": 100})
        assert _event_tokens(event) == 300

    def test_missing_usage(self):
        assert _event_tokens(make_event()) == 0

    def test_malformed_usage(self):
        event = make_event(token_usage="not a dict")
        assert _event_tokens(event) == 0


class TestAggregateCosts:
    def test_empty_events(self):
        assert aggregate_costs([]) == []

    def test_events_without_cost_skipped(self):
        events = [make_event(), make_event()]
        assert aggregate_costs(events) == []

    def test_by_model(self):
        events = [
            make_event(cost=0.01, function_name="openai.gpt-4"),
            make_event(cost=0.02, function_name="openai.gpt-4"),
            make_event(cost=0.005, function_name="anthropic.claude-3-haiku"),
        ]
        buckets = aggregate_costs(events, by="model")
        assert len(buckets) == 2
        assert buckets[0].key == "gpt-4"
        assert buckets[0].cost == pytest.approx(0.03)
        assert buckets[0].calls == 2
        assert buckets[1].key == "claude-3-haiku"

    def test_sorted_by_cost_desc(self):
        events = [
            make_event(cost=0.001, function_name="openai.gpt-3.5-turbo"),
            make_event(cost=0.5, function_name="openai.gpt-4"),
        ]
        buckets = aggregate_costs(events, by="model")
        assert [b.key for b in buckets] == ["gpt-4", "gpt-3.5-turbo"]

    def test_by_function(self):
        events = [
            make_event(cost=0.01, function_name="openai.gpt-4"),
            make_event(cost=0.02, function_name="anthropic.claude-3-opus"),
        ]
        buckets = aggregate_costs(events, by="function")
        keys = {b.key for b in buckets}
        assert keys == {"openai.gpt-4", "anthropic.claude-3-opus"}

    def test_by_day(self):
        events = [
            make_event(cost=0.01, timestamp="2026-08-08T23:00:00+00:00"),
            make_event(cost=0.02, timestamp="2026-08-09T01:00:00+00:00"),
            make_event(cost=0.03, timestamp="2026-08-09T02:00:00+00:00"),
        ]
        buckets = aggregate_costs(events, by="day")
        assert len(buckets) == 2
        by_key = {b.key: b for b in buckets}
        assert by_key["2026-08-08"].cost == pytest.approx(0.01)
        assert by_key["2026-08-09"].cost == pytest.approx(0.05)

    def test_by_day_skips_bad_timestamps(self):
        events = [make_event(cost=0.01, timestamp=None)]
        assert aggregate_costs(events, by="day") == []

    def test_wasted_cost_from_failures(self):
        events = [
            make_event(cost=0.01, success=True),
            make_event(cost=0.02, success=False),
        ]
        buckets = aggregate_costs(events, by="model")
        assert len(buckets) == 1
        bucket = buckets[0]
        assert bucket.cost == pytest.approx(0.03)
        assert bucket.failures == 1
        assert bucket.wasted_cost == pytest.approx(0.02)

    def test_tokens_accumulated(self):
        events = [
            make_event(cost=0.01, token_usage={"total_tokens": 100}),
            make_event(cost=0.01, token_usage={"prompt_tokens": 50, "completion_tokens": 25}),
        ]
        buckets = aggregate_costs(events, by="model")
        assert buckets[0].tokens == 175

    def test_invalid_grouping_raises(self):
        with pytest.raises(ValueError):
            aggregate_costs([], by="nope")

    def test_bucket_to_dict(self):
        bucket = CostBucket(key="gpt-4", cost=0.0333333, calls=2, failures=1,
                            wasted_cost=0.0111111, tokens=99)
        d = bucket.to_dict()
        assert d["key"] == "gpt-4"
        assert d["cost"] == 0.033333
        assert d["wasted_cost"] == 0.011111
        assert d["calls"] == 2
        assert d["failures"] == 1
        assert d["tokens"] == 99
        json.dumps(d)  # must be JSON-serializable


class TestSummarizeCosts:
    def test_empty(self):
        summary = summarize_costs([])
        assert summary["total_cost"] == 0.0
        assert summary["tracked_calls"] == 0
        assert summary["wasted_pct"] == 0.0
        assert summary["avg_cost_per_call"] == 0.0
        assert summary["top_model"] is None
        assert summary["top_function"] is None

    def test_basic_summary(self):
        events = [
            make_event(cost=0.03, function_name="openai.gpt-4",
                       token_usage={"total_tokens": 1000}),
            make_event(cost=0.01, function_name="openai.gpt-4", success=False,
                       token_usage={"total_tokens": 500}),
            make_event(function_name="my_tool"),  # untracked
        ]
        summary = summarize_costs(events)
        assert summary["total_cost"] == pytest.approx(0.04)
        assert summary["tracked_calls"] == 2
        assert summary["total_events"] == 3
        assert summary["total_tokens"] == 1500
        assert summary["wasted_cost"] == pytest.approx(0.01)
        assert summary["wasted_pct"] == pytest.approx(25.0)
        assert summary["avg_cost_per_call"] == pytest.approx(0.02)
        assert summary["top_model"] == "gpt-4"
        assert summary["top_function"] == "openai.gpt-4"

    def test_summary_is_json_serializable(self):
        events = [make_event(cost=0.01)]
        json.dumps(summarize_costs(events))


class TestCostsCLI:
    def _store_events(self, db_path, events):
        from agent_sentry.storage import EventStore

        store = EventStore(str(db_path))
        for i, event in enumerate(events):
            event.setdefault("event_id", f"evt-{i}")
            event.setdefault("event_type", "llm_call")
            store.store_event(event)

    def _run(self, argv, capsys):
        import sys as _sys
        from unittest import mock

        from agent_sentry.cli import main

        with mock.patch.object(_sys, "argv", ["agent-sentry"] + argv):
            main()
        return capsys.readouterr().out

    def test_costs_table(self, tmp_path, capsys):
        db = tmp_path / "events.db"
        self._store_events(db, [
            make_event(cost=0.03, function_name="openai.gpt-4",
                       token_usage={"total_tokens": 1000}),
            make_event(cost=0.01, function_name="anthropic.claude-3-haiku",
                       success=False),
        ])
        out = self._run(["--db", str(db), "costs"], capsys)
        assert "Cost Tracking" in out
        assert "gpt-4" in out
        assert "claude-3-haiku" in out
        assert "Total: $0.0400" in out
        assert "Wasted on failures: $0.0100" in out

    def test_costs_json(self, tmp_path, capsys):
        db = tmp_path / "events.db"
        self._store_events(db, [
            make_event(cost=0.02, function_name="openai.gpt-4o"),
        ])
        out = self._run(["--db", str(db), "costs", "--json-output"], capsys)
        payload = json.loads(out)
        assert payload["summary"]["total_cost"] == 0.02
        assert payload["breakdown"][0]["key"] == "gpt-4o"

    def test_costs_by_function(self, tmp_path, capsys):
        db = tmp_path / "events.db"
        self._store_events(db, [
            make_event(cost=0.02, function_name="openai.gpt-4o"),
        ])
        out = self._run(["--db", str(db), "costs", "--by", "function"], capsys)
        assert "openai.gpt-4o" in out
        assert "by function" in out

    def test_costs_empty_db(self, tmp_path, capsys):
        db = tmp_path / "events.db"
        self._store_events(db, [])
        out = self._run(["--db", str(db), "costs"], capsys)
        assert "No cost data recorded" in out

    def test_costs_top_limits_rows(self, tmp_path, capsys):
        db = tmp_path / "events.db"
        self._store_events(db, [
            make_event(cost=0.01 * (i + 1), function_name=f"openai.model-{i}")
            for i in range(5)
        ])
        out = self._run(
            ["--db", str(db), "costs", "--json-output", "--top", "2"], capsys
        )
        payload = json.loads(out)
        assert len(payload["breakdown"]) == 2
        # summary still covers everything
        assert payload["summary"]["tracked_calls"] == 5

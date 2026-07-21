"""Tests for custom root cause classifiers."""

import pytest

from agent_sentry.alerts import AlertManager
from agent_sentry.analysis import (
    CustomClassifier,
    RootCause,
    analyze_event,
    classify_error,
    clear_classifiers,
    list_classifiers,
    register_classifier,
    unregister_classifier,
)
from agent_sentry.capture import EventCapture
from agent_sentry.storage import EventStore


@pytest.fixture(autouse=True)
def clean_registry():
    clear_classifiers()
    yield
    clear_classifiers()


@pytest.fixture
def store(tmp_path):
    return EventStore(str(tmp_path / "test.db"))


@pytest.fixture
def capture(store):
    return EventCapture(store=store, alert_manager=AlertManager(async_send=False))


# ---------------------------------------------------------------------------
# Registration validation
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_register_returns_classifier(self):
        c = register_classifier("billing_error", patterns=[r"payment"])
        assert isinstance(c, CustomClassifier)
        assert c.name == "billing_error"
        assert len(c.patterns) == 1

    def test_name_is_stripped(self):
        c = register_classifier("  billing_error  ", patterns=[r"payment"])
        assert c.name == "billing_error"

    def test_empty_name_rejected(self):
        with pytest.raises(ValueError, match="non-empty"):
            register_classifier("", patterns=[r"x"])

    def test_whitespace_name_rejected(self):
        with pytest.raises(ValueError, match="non-empty"):
            register_classifier("   ", patterns=[r"x"])

    def test_duplicate_name_rejected(self):
        register_classifier("dupe", patterns=[r"x"])
        with pytest.raises(ValueError, match="already registered"):
            register_classifier("dupe", patterns=[r"y"])

    def test_no_patterns_or_predicate_rejected(self):
        with pytest.raises(ValueError, match="at least one pattern or a predicate"):
            register_classifier("empty")

    def test_invalid_regex_rejected(self):
        with pytest.raises(ValueError, match="Invalid regex"):
            register_classifier("bad", patterns=[r"[unclosed"])

    def test_invalid_regex_does_not_register(self):
        with pytest.raises(ValueError):
            register_classifier("bad", patterns=[r"ok", r"[unclosed"])
        assert list_classifiers() == []


# ---------------------------------------------------------------------------
# Registry management
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_list_preserves_order(self):
        register_classifier("first", patterns=[r"a"])
        register_classifier("second", patterns=[r"b"])
        assert [c.name for c in list_classifiers()] == ["first", "second"]

    def test_list_returns_copy(self):
        register_classifier("only", patterns=[r"a"])
        listing = list_classifiers()
        listing.clear()
        assert len(list_classifiers()) == 1

    def test_unregister_found(self):
        register_classifier("gone", patterns=[r"a"])
        assert unregister_classifier("gone") is True
        assert list_classifiers() == []

    def test_unregister_missing(self):
        assert unregister_classifier("never_registered") is False

    def test_unregister_then_reregister(self):
        register_classifier("cycle", patterns=[r"a"])
        unregister_classifier("cycle")
        register_classifier("cycle", patterns=[r"b"])
        assert len(list_classifiers()) == 1

    def test_clear(self):
        register_classifier("a", patterns=[r"a"])
        register_classifier("b", patterns=[r"b"])
        clear_classifiers()
        assert list_classifiers() == []


# ---------------------------------------------------------------------------
# Classification behavior
# ---------------------------------------------------------------------------


class TestClassification:
    def test_pattern_match_returns_custom_label(self):
        register_classifier("billing_error", patterns=[r"card\s+declined", r"payment"])
        assert classify_error(error_message="Card declined by issuer") == "billing_error"

    def test_pattern_is_case_insensitive(self):
        register_classifier("billing_error", patterns=[r"payment failed"])
        assert classify_error(error_message="PAYMENT FAILED") == "billing_error"

    def test_matches_error_type_text(self):
        register_classifier("billing_error", patterns=[r"paymenterror"])
        assert classify_error(error_message="boom", error_type="PaymentError") == "billing_error"

    def test_custom_overrides_builtin(self):
        register_classifier("vendor_outage", patterns=[r"timeout"])
        assert classify_error(error_message="request timeout") == "vendor_outage"

    def test_no_match_falls_through_to_builtin(self):
        register_classifier("billing_error", patterns=[r"payment"])
        assert classify_error(error_message="request timed out") == RootCause.TIMEOUT

    def test_first_registered_wins(self):
        register_classifier("first", patterns=[r"boom"])
        register_classifier("second", patterns=[r"boom"])
        assert classify_error(error_message="boom") == "first"

    def test_predicate_match(self):
        register_classifier(
            "slow_call",
            predicate=lambda msg, typ, res, dur, meta: bool(dur and dur > 10000),
        )
        assert classify_error(error_message="err", duration_ms=20000) == "slow_call"

    def test_predicate_no_match(self):
        register_classifier(
            "slow_call",
            predicate=lambda msg, typ, res, dur, meta: bool(dur and dur > 10000),
        )
        assert classify_error(error_message="request timed out", duration_ms=50) == (
            RootCause.TIMEOUT
        )

    def test_predicate_sees_metadata(self):
        register_classifier(
            "staging_only",
            predicate=lambda msg, typ, res, dur, meta: bool(meta and meta.get("env") == "staging"),
        )
        assert classify_error(error_message="err", metadata={"env": "staging"}) == "staging_only"
        assert classify_error(error_message="err", metadata={"env": "prod"}) == RootCause.UNKNOWN

    def test_predicate_sees_result(self):
        register_classifier(
            "empty_json",
            predicate=lambda msg, typ, res, dur, meta: res == "{}",
        )
        assert classify_error(result="{}") == "empty_json"

    def test_predicate_exception_swallowed(self):
        def broken(msg, typ, res, dur, meta):
            raise RuntimeError("classifier bug")

        register_classifier("buggy", predicate=broken)
        assert classify_error(error_message="request timed out") == RootCause.TIMEOUT

    def test_predicate_truthy_value_coerced(self):
        register_classifier(
            "truthy",
            predicate=lambda msg, typ, res, dur, meta: "yes",  # type: ignore[arg-type, return-value]
        )
        assert classify_error(error_message="anything") == "truthy"

    def test_patterns_and_predicate_combined(self):
        register_classifier(
            "combo",
            patterns=[r"combo"],
            predicate=lambda msg, typ, res, dur, meta: bool(dur and dur > 5000),
        )
        assert classify_error(error_message="combo failure") == "combo"
        assert classify_error(error_message="other", duration_ms=6000) == "combo"

    def test_unregistered_classifier_no_longer_applies(self):
        register_classifier("temp", patterns=[r"timeout"])
        unregister_classifier("temp")
        assert classify_error(error_message="request timeout") == RootCause.TIMEOUT

    def test_analyze_event_uses_custom_classifiers(self):
        register_classifier("billing_error", patterns=[r"payment"])
        event = {"error_message": "payment gateway rejected", "error_type": "GatewayError"}
        assert analyze_event(event) == "billing_error"


# ---------------------------------------------------------------------------
# End-to-end through event capture
# ---------------------------------------------------------------------------


class TestCaptureIntegration:
    def test_captured_failure_gets_custom_root_cause(self, capture, store):
        register_classifier("billing_error", patterns=[r"card\s+declined"])

        def charge():
            raise RuntimeError("Card declined: insufficient funds")

        with pytest.raises(RuntimeError):
            capture.capture_call(charge, (), {})

        events = store.get_events()
        assert len(events) == 1
        assert events[0]["root_cause"] == "billing_error"

    def test_captured_failure_without_match_uses_builtin(self, capture, store):
        register_classifier("billing_error", patterns=[r"card\s+declined"])

        def timeout():
            raise TimeoutError("Connection timed out")

        with pytest.raises(TimeoutError):
            capture.capture_call(timeout, (), {})

        events = store.get_events()
        assert events[0]["root_cause"] == RootCause.TIMEOUT

    def test_public_api_exports(self):
        import agent_sentry

        assert agent_sentry.register_classifier is register_classifier
        assert agent_sentry.unregister_classifier is unregister_classifier
        assert agent_sentry.list_classifiers is list_classifiers
        assert agent_sentry.clear_classifiers is clear_classifiers
        assert agent_sentry.CustomClassifier is CustomClassifier

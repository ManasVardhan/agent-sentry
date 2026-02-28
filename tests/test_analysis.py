"""Tests for root cause classification."""

import pytest
from agent_sentry.analysis import classify_error, analyze_event, RootCause


def test_classify_timeout_by_message():
    assert classify_error(error_message="Request timed out") == RootCause.TIMEOUT


def test_classify_timeout_by_duration():
    assert classify_error(error_message="unknown error", duration_ms=45000) == RootCause.TIMEOUT


def test_classify_rate_limit():
    assert classify_error(error_message="Rate limit exceeded (429)") == RootCause.RATE_LIMIT


def test_classify_rate_limit_too_many():
    assert classify_error(error_message="Too many requests") == RootCause.RATE_LIMIT


def test_classify_auth_error():
    assert classify_error(error_message="Invalid API key") == RootCause.AUTH_ERROR


def test_classify_auth_401():
    assert classify_error(error_message="401 Unauthorized") == RootCause.AUTH_ERROR


def test_classify_context_overflow():
    assert classify_error(error_message="Maximum context length exceeded") == RootCause.CONTEXT_OVERFLOW


def test_classify_context_token_limit():
    assert classify_error(error_message="This model's max token limit is 4096") == RootCause.CONTEXT_OVERFLOW


def test_classify_malformed_args():
    assert classify_error(error_message="Invalid argument: expected string") == RootCause.MALFORMED_ARGS


def test_classify_malformed_type_error():
    assert classify_error(error_type="TypeError") == RootCause.MALFORMED_ARGS


def test_classify_malformed_validation():
    assert classify_error(error_message="Validation error on field 'name'") == RootCause.MALFORMED_ARGS


def test_classify_network_error():
    assert classify_error(error_message="Connection refused") == RootCause.NETWORK_ERROR


def test_classify_hallucination_in_result():
    result = "I apologize, but I can't actually access that website."
    assert classify_error(result=result) == RootCause.HALLUCINATION


def test_classify_hallucination_as_ai():
    result = "As an AI language model, I cannot browse the internet."
    assert classify_error(result=result) == RootCause.HALLUCINATION


def test_classify_silent_failure():
    assert classify_error(result=None) == RootCause.SILENT_FAILURE


def test_classify_silent_failure_empty_string():
    assert classify_error(result="") == RootCause.SILENT_FAILURE


def test_classify_silent_failure_empty_dict():
    assert classify_error(result={}) == RootCause.SILENT_FAILURE


def test_classify_unknown():
    assert classify_error(error_message="Something weird happened") == RootCause.UNKNOWN


def test_analyze_event():
    event = {
        "error_message": "Rate limit exceeded",
        "error_type": "RateLimitError",
        "success": False,
    }
    assert analyze_event(event) == RootCause.RATE_LIMIT


def test_root_cause_all_values():
    assert len(RootCause.ALL) >= 9
    assert RootCause.TIMEOUT in RootCause.ALL
    assert RootCause.UNKNOWN in RootCause.ALL

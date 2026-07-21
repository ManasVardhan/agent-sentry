"""Root cause classification for agent failures."""

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Pattern, Sequence


class RootCause:
    """Root cause categories for agent failures."""
    TIMEOUT = "timeout"
    HALLUCINATION = "hallucination"
    CONTEXT_OVERFLOW = "context_overflow"
    MALFORMED_ARGS = "malformed_args"
    WRONG_TOOL = "wrong_tool"
    RATE_LIMIT = "rate_limit"
    AUTH_ERROR = "auth_error"
    SILENT_FAILURE = "silent_failure"
    NETWORK_ERROR = "network_error"
    UNKNOWN = "unknown"

    ALL = [
        TIMEOUT, HALLUCINATION, CONTEXT_OVERFLOW, MALFORMED_ARGS,
        WRONG_TOOL, RATE_LIMIT, AUTH_ERROR, SILENT_FAILURE,
        NETWORK_ERROR, UNKNOWN,
    ]


# Patterns for classification. Each entry is (root_cause, list_of_patterns).
_ERROR_PATTERNS = [
    (RootCause.TIMEOUT, [
        r"timeout",
        r"timed?\s*out",
        r"deadline\s+exceeded",
        r"request\s+timeout",
        r"read\s+timed?\s*out",
        r"connect\s+timed?\s*out",
    ]),
    (RootCause.RATE_LIMIT, [
        r"rate\s*limit",
        r"429",
        r"too\s+many\s+requests",
        r"quota\s+exceeded",
        r"throttl",
        r"retry.after",
    ]),
    (RootCause.AUTH_ERROR, [
        r"auth",
        r"401",
        r"403",
        r"unauthorized",
        r"forbidden",
        r"invalid.api.key",
        r"permission\s+denied",
        r"access\s+denied",
    ]),
    (RootCause.CONTEXT_OVERFLOW, [
        r"context.*(length|window|limit|overflow|exceed)",
        r"max.*token",
        r"token\s+limit",
        r"too\s+long",
        r"maximum\s+context",
        r"input.*too\s+(long|large)",
        r"content_policy",
    ]),
    (RootCause.MALFORMED_ARGS, [
        r"invalid.*argument",
        r"invalid.*param",
        r"missing.*required",
        r"type\s*error",
        r"validation\s*error",
        r"schema.*valid",
        r"json.*pars",
        r"unexpected.*type",
        r"malformed",
    ]),
    (RootCause.NETWORK_ERROR, [
        r"connection\s*(refused|reset|abort)",
        r"dns.*resolv",
        r"network.*unreachable",
        r"ssl.*error",
        r"socket.*error",
        r"broken\s+pipe",
        r"eof.*error",
    ]),
]

# Patterns that suggest hallucination in results
_HALLUCINATION_INDICATORS = [
    r"i\s+(don.t|cannot|can.t)\s+(actually|really)",
    r"as\s+an?\s+ai",
    r"i.m\s+not\s+able\s+to",
    r"i\s+apologize.*but\s+i\s+(can.t|cannot|don.t)",
    r"fabricat",
    r"made\s+up",
    r"not\s+a\s+real",
    r"doesn.t\s+exist",
    r"no\s+such\s+(function|tool|api|endpoint)",
]


# ---------------------------------------------------------------------------
# Custom classifiers
# ---------------------------------------------------------------------------

# Signature: (error_message, error_type, result, duration_ms, metadata) -> bool
ClassifierPredicate = Callable[
    [Optional[str], Optional[str], Any, Optional[float], Optional[Dict[str, Any]]],
    bool,
]


@dataclass
class CustomClassifier:
    """A user-defined root cause classifier.

    Matches when any compiled pattern is found in the combined error text
    (error message plus exception type), or when the predicate returns a
    truthy value. Custom classifiers run before the built-in patterns, so
    they can override built-in classifications.

    Attributes:
        name: The root cause label returned when this classifier matches.
        patterns: Compiled case-insensitive regex patterns.
        predicate: Optional callable receiving (error_message, error_type,
            result, duration_ms, metadata) and returning a bool.
    """

    name: str
    patterns: List[Pattern[str]] = field(default_factory=list)
    predicate: Optional[ClassifierPredicate] = None

    def matches(
        self,
        error_text: str,
        error_message: Optional[str],
        error_type: Optional[str],
        result: Any,
        duration_ms: Optional[float],
        metadata: Optional[Dict[str, Any]],
    ) -> bool:
        """Check whether this classifier matches the failure.

        Predicate exceptions are swallowed and treated as no-match so a
        buggy classifier can never break event capture.
        """
        for pattern in self.patterns:
            if pattern.search(error_text):
                return True
        if self.predicate is not None:
            try:
                return bool(
                    self.predicate(error_message, error_type, result, duration_ms, metadata)
                )
            except Exception:
                return False
        return False


_custom_classifiers: List[CustomClassifier] = []


def register_classifier(
    name: str,
    patterns: Optional[Sequence[str]] = None,
    predicate: Optional[ClassifierPredicate] = None,
) -> CustomClassifier:
    """Register a custom root cause classifier.

    Custom classifiers are checked in registration order before the
    built-in patterns, so they can both add domain-specific causes and
    override built-in classifications.

        register_classifier("billing_error", patterns=[r"payment", r"card declined"])
        register_classifier("slow_call", predicate=lambda msg, typ, res, dur, meta:
                            bool(dur and dur > 10000))

    Args:
        name: Root cause label to return on match (stripped, non-empty).
        patterns: Regex pattern strings, compiled case-insensitively.
        predicate: Callable receiving (error_message, error_type, result,
            duration_ms, metadata) and returning a bool.

    Returns:
        The registered CustomClassifier.

    Raises:
        ValueError: If the name is empty or already registered, if neither
            patterns nor predicate is provided, or if a pattern is invalid.
    """
    label = (name or "").strip()
    if not label:
        raise ValueError("Classifier name must be a non-empty string")
    if any(c.name == label for c in _custom_classifiers):
        raise ValueError(
            f"Classifier '{label}' is already registered. "
            "Call unregister_classifier first to replace it."
        )
    if not patterns and predicate is None:
        raise ValueError("Provide at least one pattern or a predicate")

    compiled: List[Pattern[str]] = []
    for raw in patterns or []:
        try:
            compiled.append(re.compile(raw, re.IGNORECASE))
        except re.error as exc:
            raise ValueError(f"Invalid regex pattern {raw!r}: {exc}") from exc

    classifier = CustomClassifier(name=label, patterns=compiled, predicate=predicate)
    _custom_classifiers.append(classifier)
    return classifier


def unregister_classifier(name: str) -> bool:
    """Remove a custom classifier by name. Returns True if it was found."""
    for i, classifier in enumerate(_custom_classifiers):
        if classifier.name == name:
            del _custom_classifiers[i]
            return True
    return False


def list_classifiers() -> List[CustomClassifier]:
    """Return registered custom classifiers in evaluation order."""
    return list(_custom_classifiers)


def clear_classifiers() -> None:
    """Remove all registered custom classifiers."""
    _custom_classifiers.clear()


def classify_error(
    error_message: Optional[str] = None,
    error_type: Optional[str] = None,
    result: Any = None,
    duration_ms: Optional[float] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Classify a failure into a root cause category.

    Args:
        error_message: The error message string.
        error_type: The exception type name.
        result: The result of the call (used for hallucination detection).
        duration_ms: How long the call took in milliseconds.
        metadata: Additional metadata about the event.

    Returns:
        A string from RootCause indicating the classified cause.
    """
    combined_text = " ".join(filter(None, [
        str(error_message or ""),
        str(error_type or ""),
    ])).lower()

    # Custom classifiers run first so they can override built-ins
    for classifier in _custom_classifiers:
        if classifier.matches(
            combined_text, error_message, error_type, result, duration_ms, metadata
        ):
            return classifier.name

    # Check error patterns
    for cause, patterns in _ERROR_PATTERNS:
        for pattern in patterns:
            if re.search(pattern, combined_text, re.IGNORECASE):
                return cause

    # Check for timeout by duration (>30s with no other classification)
    if duration_ms and duration_ms > 30000 and error_message:
        return RootCause.TIMEOUT

    # Check result for hallucination indicators
    if result:
        result_text = str(result).lower()
        for pattern in _HALLUCINATION_INDICATORS:
            if re.search(pattern, result_text, re.IGNORECASE):
                return RootCause.HALLUCINATION

    # Check for silent failure (no error, no result, or empty result)
    if not error_message and not error_type:
        if result is None or result == "" or result == {} or result == []:
            return RootCause.SILENT_FAILURE

    if error_message or error_type:
        return RootCause.UNKNOWN

    return RootCause.UNKNOWN


def analyze_event(event: Dict[str, Any]) -> str:
    """Analyze a captured event and return its root cause.

    Args:
        event: An event dictionary from capture.

    Returns:
        Root cause classification string.
    """
    return classify_error(
        error_message=event.get("error_message"),
        error_type=event.get("error_type"),
        result=event.get("result"),
        duration_ms=event.get("duration_ms"),
        metadata=event.get("metadata"),
    )

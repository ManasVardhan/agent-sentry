# AGENTS.md - agent-sentry

## Overview
- Crash reporting for AI agents. Wraps any agent function with a `@watch` decorator to capture every call, classify failures by root cause (timeout, hallucination, context overflow, rate limit, etc.), store events in SQLite, and alert via Slack/webhooks/email in real time. Includes a Streamlit dashboard.
- For developers running AI agents in production who need failure visibility, root cause analysis, and alerting without complex infrastructure.
- Core value: one-decorator instrumentation, automatic root cause classification (9 categories), local SQLite storage (WAL mode, thread-safe), multi-channel alerts, Streamlit dashboard, framework integrations (OpenAI, Anthropic, LangChain), under 2ms overhead per call.

## Architecture

```
@watch decorator
    |
    v
EventCapture (intercepts calls, measures latency, catches errors)
    |
    +--> RootCauseClassifier (timeout? hallucination? rate limit?)
    |        (regex pattern matching on error messages + result analysis)
    |
    +--> EventStore (SQLite, WAL mode, thread-safe, indexed)
    |        events table: event_id, timestamp, event_type, function_name,
    |        args, result, error, duration_ms, success, token_usage, cost,
    |        root_cause, metadata, tags
    |
    +--> AlertManager (async by default, multiple channels)
    |        WebhookAlert (retry with exponential backoff)
    |        SlackAlert (formatted blocks)
    |        EmailAlert (SMTP)
    |        CallbackAlert (custom function)
    |
    +--> Dashboard (Streamlit, reads from EventStore)
             reliability score, failure breakdown, event timeline,
             root cause charts, recent failures
```

**Data flow:**
1. `@watch` wraps a function (sync or async)
2. `EventCapture.capture_call()` records start time, calls the function, catches exceptions
3. On success: stores event, checks for silent failures (empty/null results)
4. On failure: captures error message, type, traceback, classifies root cause
5. `RootCauseClassifier` matches error text against regex patterns for 9 categories
6. Event stored in SQLite with all metadata
7. `AlertManager` sends alerts to all configured channels (async by default)
8. Dashboard/CLI reads from the same SQLite database

## Directory Structure

```
agent-sentry/
  .github/workflows/ci.yml        -- CI: lint + test + coverage on Python 3.9-3.13, then build
  agent_sentry/                    -- NOTE: flat layout (not src/), uses setuptools
    __init__.py                    -- @watch decorator, configure(), public API re-exports, __version__ = "0.1.1"
    __main__.py                    -- python -m agent_sentry entry
    capture.py                     -- EventCapture: sync + async call interception, safe serialization
    analysis.py                    -- RootCause categories, classify_error(), regex pattern matching
    storage.py                     -- EventStore: SQLite backend, WAL mode, thread-safe, queries
    alerts.py                      -- AlertManager, WebhookAlert, SlackAlert, EmailAlert, CallbackAlert
    cli.py                         -- argparse CLI: dashboard, report, clear, status, health, summary
    dashboard/
      __init__.py                  -- Dashboard package
      app.py                       -- Streamlit dashboard: metrics, charts, failure browser
    integrations/
      __init__.py                  -- Integrations package
      openai.py                    -- SentryOpenAIWrapper: wraps chat.completions.create + completions.create
      anthropic.py                 -- SentryAnthropicWrapper: wraps messages.create
      langchain.py                 -- AgentSentryCallbackHandler: LangChain callback for LLM + tool events
  launch/                          -- Launch marketing materials
    tweets.md, linkedin.md, reddit.md, hn-post.md
  tests/                           -- 232 tests across 16 test files
    test_capture.py                -- Event capture tests
    test_analysis.py               -- Root cause classification tests
    test_storage.py                -- SQLite storage tests
    test_storage_extended.py       -- Extended storage tests
    test_alerts.py                 -- Alert channel tests
    test_watch.py                  -- @watch decorator tests
    test_async_watch.py            -- Async @watch tests
    test_async.py                  -- Async capture tests
    test_cli.py                    -- CLI command tests
    test_cli_extended.py           -- Extended CLI tests
    test_integrations.py           -- OpenAI/Anthropic/LangChain integration tests
    test_configure.py              -- configure() function tests
    test_cost_estimation.py        -- Cost estimation tests
    test_edge_cases.py             -- Edge case coverage
    test_new_features.py           -- New feature tests
  pyproject.toml                   -- setuptools build, metadata
  README.md                        -- Full docs
  ROADMAP.md                       -- v0.1-v1.0 plans
  CONTRIBUTING.md                  -- Contribution guidelines
  GETTING_STARTED.md               -- Quick start guide
  LICENSE                          -- MIT
```

## Core Concepts

- **@watch**: Decorator that wraps any function (sync or async). Intercepts calls, captures timing, errors, and results. Supports `event_type`, `tags`, `metadata` params. Works with and without arguments (`@watch` or `@watch(event_type="tool_call")`).
- **EventCapture**: Core engine. `capture_call()` (sync) and `async_capture_call()` (async) handle function wrapping. Also provides `log_event()` for manual event logging. Uses `_safe_repr()` for serialization (truncates long values, handles unserializable objects).
- **RootCause**: Classification categories: timeout, hallucination, context_overflow, malformed_args, wrong_tool, rate_limit, auth_error, silent_failure, network_error, unknown.
- **classify_error()**: Matches error message + error type against regex patterns. Also checks result text for hallucination indicators. Detects silent failures (no error but empty/null result). Uses duration threshold (>30s) for timeout classification.
- **EventStore**: SQLite with WAL mode for concurrent reads. Thread-safe via thread-local connections. Indexed on timestamp, success, event_type, root_cause. Methods: `store_event()`, `get_events()`, `get_failure_count()`, `get_total_count()`, `get_reliability_score()`, `get_failure_breakdown()`, `get_event_type_breakdown()`, `health_check()`, `get_summary()`, `clear()`.
- **AlertManager**: Manages multiple AlertChannel instances. Sends alerts asynchronously (threaded) by default. Only alerts on failures (success=False).
- **WebhookAlert**: Generic webhook with automatic retry (exponential backoff, configurable max_retries, base_delay, timeout).
- **SlackAlert**: Slack-formatted blocks with function name, root cause, error, duration.
- **EmailAlert**: SMTP with TLS support.
- **CallbackAlert**: Custom function callback.
- **Integrations**: SentryOpenAIWrapper (wraps `chat.completions.create`), SentryAnthropicWrapper (wraps `messages.create`), AgentSentryCallbackHandler (LangChain callback for LLM start/end/error, tool start/end/error).
- **Reliability Score**: `(1 - failures/total) * 100`, returned as percentage.

## API Reference

### @watch decorator
```python
@watch                                    # no-args form
@watch(event_type="tool_call", tags=["search"], metadata={"priority": "high"})
def my_function(...): ...

@watch                                    # works with async too
async def my_async_function(...): ...
```

### configure()
```python
def configure(
    db_path: str | None = None,
    alert_channels: list[AlertChannel] | None = None,
    webhook_url: str | None = None,
    slack_webhook: str | None = None,
) -> EventCapture
```

### EventCapture
```python
class EventCapture:
    def __init__(self, store=None, alert_manager=None, auto_classify=True)
    def capture_call(self, func, args, kwargs, event_type="function_call", metadata=None, tags=None) -> Any
    async def async_capture_call(self, func, args, kwargs, event_type="function_call", metadata=None, tags=None) -> Any
    def log_event(self, event: dict) -> None
```

### EventStore
```python
class EventStore:
    def __init__(self, db_path: str | None = None)  # defaults to ~/.agent-sentry/events.db
    def store_event(self, event: dict) -> None
    def get_events(self, limit=100, offset=0, event_type=None, success=None, since=None, root_cause=None) -> list[dict]
    def get_failure_count(self, since=None) -> int
    def get_total_count(self, since=None) -> int
    def get_reliability_score(self, since=None) -> float  # 0-100
    def get_failure_breakdown(self, since=None) -> dict[str, int]
    def get_event_type_breakdown(self, since=None) -> dict[str, dict[str, int]]
    def health_check(self) -> dict
    def get_summary(self, since=None) -> dict
    def clear(self) -> None
```

### Analysis
```python
def classify_error(error_message=None, error_type=None, result=None, duration_ms=None, metadata=None) -> str
def analyze_event(event: dict) -> str
```

### Alert Channels
```python
WebhookAlert(url, headers=None, max_retries=3, base_delay=1.0, timeout=10)
SlackAlert(webhook_url)
EmailAlert(smtp_host, smtp_port, from_addr, to_addrs, username=None, password=None, use_tls=True)
CallbackAlert(callback: Callable[[dict], None])
```

### Integrations
```python
SentryOpenAIWrapper(client, capture=None, tags=None)
  .chat_completions_create(**kwargs)     # wraps chat.completions.create
  .completions_create(**kwargs)          # wraps completions.create

SentryAnthropicWrapper(client, capture=None, tags=None)
  .messages_create(**kwargs)             # wraps messages.create

AgentSentryCallbackHandler(capture=None, tags=None)
  # LangChain callback: on_llm_start/end/error, on_tool_start/end/error
```

## CLI Commands

```bash
# Launch Streamlit dashboard
agent-sentry dashboard
agent-sentry dashboard --db /path/to/events.db

# Terminal report (last 24h by default)
agent-sentry report
agent-sentry report --hours 168    # last 7 days

# Show status and DB info
agent-sentry status

# Health check (exit code 0=healthy, 1=unhealthy)
agent-sentry health

# Compact summary
agent-sentry summary
agent-sentry summary --hours 24

# Clear all events
agent-sentry clear

# Global options
agent-sentry --db /path/to/events.db <command>
agent-sentry --version
```

## Configuration

- **Database path**: `--db` flag or `configure(db_path=...)`. Default: `~/.agent-sentry/events.db`
- **Alerts**: `configure(slack_webhook=..., webhook_url=..., alert_channels=[...])`
- **No config files** needed
- **Install extras**: `[dashboard]` for Streamlit, `[openai]` for OpenAI integration, `[anthropic]` for Anthropic, `[langchain]` for LangChain, `[all]` for everything

## Testing

```bash
pip install -e ".[dev]"
pytest --cov=agent_sentry --cov-report=term-missing -v
```

- **232 tests** across 16 test files
- Tests use temporary SQLite databases
- Includes async tests
- Located in `tests/`

## Dependencies

- **Core**: Zero runtime dependencies (stdlib only: sqlite3, json, threading, etc.)
- **dashboard extra**: `streamlit>=1.28.0`, `pandas>=2.0.0`
- **openai extra**: `openai>=1.0.0`
- **anthropic extra**: `anthropic>=0.20.0`
- **langchain extra**: `langchain-core>=0.1.0`
- **dev extra**: `pytest>=7.0.0`, `pytest-cov>=4.0.0`, `ruff>=0.1.0`
- **Python >=3.9** (broadest compatibility in the suite)

## CI/CD

- **GitHub Actions** (`.github/workflows/ci.yml`)
- Matrix: Python 3.9, 3.10, 3.11, 3.12, 3.13 (widest in the suite)
- Jobs: test (lint + pytest with coverage), build (package build)
- Triggers: push/PR to main

## Current Status

- **Version**: 0.1.1
- **Published on PyPI**: yes (`pip install ai-agent-sentry`)
- **What works**: @watch decorator (sync + async), 9 root cause categories with regex classification, SQLite storage with WAL mode, multi-channel alerts (webhook with retry, Slack, email, callback), Streamlit dashboard, CLI (report, status, health, summary, clear), OpenAI/Anthropic/LangChain integrations, cost estimation per integration, reliability scoring
- **Known limitations**: No PostgreSQL backend. No multi-agent session tracking. No event export to JSON/CSV. Dashboard requires Streamlit install.
- **Roadmap**: v0.2 (async watch, CrewAI/AutoGen/LlamaIndex integrations, cost dashboard, event export, retry pattern detection), v0.3 (PostgreSQL, failure correlation, custom classifiers, Prometheus), v0.4 (real-time streaming dashboard, agent replay, A/B testing, anomaly detection), v1.0 (production hardened, JS/TS SDK, SaaS option)

## Development Guide

```bash
git clone https://github.com/manasvardhan/agent-sentry.git
cd agent-sentry
pip install -e ".[dev]"
pytest
```

- **Build system**: setuptools (note: different from other repos that use Hatchling)
- **Source layout**: flat `agent_sentry/` (not `src/` layout)
- **Adding a new root cause**: Add pattern to `_ERROR_PATTERNS` in `analysis.py`, add constant to `RootCause` class
- **Adding a new alert channel**: Subclass `AlertChannel` in `alerts.py`, implement `send(event) -> bool`
- **Adding a new integration**: Create file in `integrations/`, follow the pattern from `openai.py` or `anthropic.py`
- **Adding a new CLI command**: Add parser and handler function in `cli.py` (uses argparse, not click)
- **Code style**: Ruff, line length 100, target Python 3.9

## Git Conventions

- **Branch**: main
- **Commits**: Imperative style ("Add feature X", "Fix bug Y")
- Never use em dashes in commit messages or docs

## Context

- **Author**: Manas Vardhan (ManasVardhan on GitHub)
- **Part of**: A suite of AI agent tooling
- **Related repos**: llm-cost-guardian (cost tracking), agent-replay (trace debugging), llm-shelter (safety guardrails), promptdiff (prompt versioning), mcp-forge (MCP server scaffolding), bench-my-llm (benchmarking)
- **PyPI package**: `ai-agent-sentry` (note: PyPI name differs from repo name and import name)
- **Import as**: `agent_sentry`
- **CLI command**: `agent-sentry`
- **Notable differences from other repos**: Uses setuptools (not Hatchling), flat source layout (not `src/`), argparse (not click), supports Python 3.9+

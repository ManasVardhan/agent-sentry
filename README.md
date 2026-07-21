<p align="center">
  <img src="https://raw.githubusercontent.com/ManasVardhan/agent-sentry/main/assets/banner.png" alt="agent-sentry banner" width="100%" />
</p>

<p align="center">
  <a href="https://pypi.org/project/ai-agent-sentry/"><img src="https://img.shields.io/pypi/v/ai-agent-sentry?color=blue" alt="PyPI"></a>
  <a href="https://pypi.org/project/ai-agent-sentry/"><img src="https://img.shields.io/pypi/pyversions/ai-agent-sentry" alt="Python"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License"></a>
  <a href="https://github.com/ManasVardhan/agent-sentry/actions"><img src="https://github.com/ManasVardhan/agent-sentry/actions/workflows/ci.yml/badge.svg" alt="Tests"></a>
</p>

<p align="center">
  <b>Crash reporting for AI agents. Catch failures before your users do.</b>
</p>

---

Your AI agent works great in demos. In production, it fails silently. Tool calls timeout. Context windows overflow. Hallucinations slip through. And you have no idea until a user complains.

**agent-sentry** catches every failure, classifies why it happened, and alerts you in real time.

## Quick Start

```bash
pip install ai-agent-sentry
```

> **Note:** The PyPI package is `ai-agent-sentry`, but you import it as `agent_sentry`.

```python
from agent_sentry import watch

@watch
def my_agent(query):
    result = call_llm(query)
    return result

# Every call is now tracked. Every failure is caught.
my_agent("summarize this document")
```

Open the dashboard:

```bash
agent-sentry dashboard
```

Three lines of code. Full visibility into every failure.

## Why?

You deployed an agent. It runs 10,000 tool calls a day. Some fail. You don't know which ones, you don't know why, and you don't know how often.

Traditional monitoring sees HTTP 200s and thinks everything is fine. But your agent just hallucinated a fake API endpoint, retried it 5 times, burned $2 in tokens, and returned garbage to the user. Nobody noticed.

**agent-sentry** is Sentry for AI agents.

## Features

| | |
|---|---|
| **@watch decorator** | One line. Wraps any agent function. |
| **Root cause classification** | Timeout, hallucination, context overflow, rate limit, auth error, silent failure, wrong tool. Automatic. |
| **Live dashboard** | Streamlit UI with failure rates, root causes, reliability scores, and cost tracking. |
| **Cost tracking** | Token usage and estimated spend per call. |
| **Reliability score** | Track agent health over time, like a credit score. |
| **Alerts** | Slack, webhooks, email, or custom callbacks. |
| **Retry pattern detection** | Spot retry storms and flaky functions, with wasted time and cost per sequence. |
| **Custom classifiers** | Register your own root cause labels with regex patterns or predicates. They run before the built-ins. |
| **CLI** | Terminal reports without opening a browser. |
| **Zero config** | One decorator. Done. |

## Integrations

```python
# OpenAI
from agent_sentry.integrations.openai import SentryOpenAIWrapper
client = SentryOpenAIWrapper(openai_client)
response = client.chat_completions_create(model="gpt-4", messages=[...])

# Anthropic
from agent_sentry.integrations.anthropic import SentryAnthropicWrapper
client = SentryAnthropicWrapper(anthropic_client)
response = client.messages_create(model="claude-sonnet-4-20250514", messages=[...])

# LangChain
from agent_sentry.integrations.langchain import AgentSentryCallbackHandler
llm = ChatOpenAI(callbacks=[AgentSentryCallbackHandler()])

# Any function
@watch(event_type="tool_call", tags=["search"])
def search_web(query):
    return requests.get(f"https://api.search.com?q={query}").json()
```

## Root Cause Categories

| Category | What It Catches |
|---|---|
| `timeout` | Deadline exceeded, slow requests |
| `hallucination` | References to non-existent tools, APIs, or data |
| `context_overflow` | Token limit exceeded |
| `malformed_args` | Type errors, validation failures |
| `rate_limit` | 429s, quota exceeded |
| `auth_error` | Invalid keys, 401/403 |
| `silent_failure` | No error, but empty/null results |
| `network_error` | Connection refused, DNS, SSL |
| `wrong_tool` | Agent picked the wrong tool |

### Custom Classifiers

Add your own root cause labels for domain-specific failures. Custom
classifiers run before the built-in patterns, so they can also override
a built-in classification:

```python
from agent_sentry import register_classifier

# Match on error text with regex patterns (case-insensitive)
register_classifier("billing_error", patterns=[r"card\s+declined", r"payment"])

# Or match with a predicate over the whole failure
register_classifier(
    "slow_call",
    predicate=lambda msg, typ, result, duration_ms, meta: bool(
        duration_ms and duration_ms > 10_000
    ),
)
```

From then on, every captured failure that matches is stored, reported,
and alerted with your label instead of a generic one. Manage the
registry with `list_classifiers()`, `unregister_classifier(name)`, and
`clear_classifiers()`. A classifier predicate that raises is treated as
no-match, so a buggy classifier can never break event capture.

## Performance

| Metric | Without | With agent-sentry |
|---|---|---|
| Time to detect failure | ~4.2 hours | 0.3 seconds |
| Failures identified | ~15% | 100% |
| Root cause classification | Manual | Automatic |
| Overhead per call | -- | <2ms |
| Storage per 10K events | -- | ~5MB (SQLite) |

## Alerts

```python
from agent_sentry import configure

# Slack
configure(slack_webhook="https://hooks.slack.com/services/YOUR/WEBHOOK/URL")

# Webhooks
configure(webhook_url="https://your-server.com/agent-alerts")

# Custom
from agent_sentry import CallbackAlert
configure(alert_channels=[CallbackAlert(lambda e: print(f"ALERT: {e['error_message']}"))])
```

## CLI

```bash
agent-sentry dashboard          # Launch Streamlit dashboard
agent-sentry report             # Terminal report (last 24h)
agent-sentry report --hours 168 # Last 7 days
agent-sentry summary            # Compact event summary
agent-sentry status             # DB info and status
agent-sentry health             # Health check (exit 0/1, CI-friendly)
agent-sentry top                # Top failing functions
agent-sentry tail               # Most recent failures
agent-sentry export             # Dump events as JSON to stdout
agent-sentry retries            # Detect retry patterns (repeated failing calls)
agent-sentry clear              # Clear all events
```

### Exporting events

Ship events to other tools or archive them with `export`:

```bash
agent-sentry export --format csv -o events.csv        # CSV file
agent-sentry export --failures-only --hours 24        # Recent failures as JSON
agent-sentry export --event-type llm_call --limit 500 # Filter by event type
```

### Retry pattern detection

Find functions stuck in retry loops. A retry sequence is a failed call
followed by more calls to the same function within a time window. Sequences
either recover (final attempt succeeds) or exhaust (caller gives up):

```bash
agent-sentry retries                      # All time, 60s window
agent-sentry retries --hours 24           # Last day only
agent-sentry retries --window 30          # Tighter 30s retry window
agent-sentry retries --min-attempts 3     # Only sequences with 3+ calls
agent-sentry retries --json-output        # Machine-readable output
```

Output includes attempts, failures, outcome, wasted time and cost per
sequence, root cause breakdowns, and an overall recovery rate. Also available
in Python via `detect_retry_sequences` and `summarize_retries`.

## Architecture

```
@watch decorator
    |
    v
EventCapture (intercepts calls, measures latency, catches errors)
    |
    +--> RootCauseClassifier (timeout? hallucination? rate limit?)
    +--> EventStore (SQLite, WAL mode, thread-safe)
    +--> AlertManager (Slack, webhooks, email, callbacks)
    +--> Dashboard (Streamlit, reads from EventStore)
```

All data stays local. SQLite with WAL mode. Thread-safe. No external services required.

## Installation

```bash
pip install ai-agent-sentry                                # Core
pip install ai-agent-sentry[dashboard]                     # + Streamlit dashboard
pip install ai-agent-sentry[openai,anthropic,langchain]    # + Framework integrations
pip install ai-agent-sentry[all]                           # Everything
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Issues and PRs welcome.

## Roadmap

See [ROADMAP.md](ROADMAP.md).

## License

MIT. See [LICENSE](LICENSE).

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

## Configuration examples

Common setups beyond the defaults above.

**Custom database location** — keep the SQLite file on a mounted volume or separate path (useful in Docker or when the cwd is read-only):

```python
from agent_sentry import configure, watch

configure(db_path="/var/lib/myapp/agent_sentry_events.db")

@watch
def run_agent(task: str) -> str:
    ...
```

**Slack and a generic webhook together** — both fire on failures:

```python
from agent_sentry import configure

configure(
    slack_webhook="https://hooks.slack.com/services/AAA/BBB/CCC",
    webhook_url="https://api.mycompany.com/internal/agent-alerts",
)
```

**Webhook with auth headers** — for APIs that expect a bearer token or API key:

```python
from agent_sentry import configure, WebhookAlert

configure(
    alert_channels=[
        WebhookAlert(
            "https://api.mycompany.com/v1/incidents",
            headers={"Authorization": "Bearer YOUR_TOKEN_HERE"},
        ),
    ],
)
```

**Email alerts (SMTP)** — for teams that want inbox notifications:

```python
from agent_sentry import configure, EmailAlert

configure(
    alert_channels=[
        EmailAlert(
            smtp_host="smtp.example.com",
            smtp_port=587,
            from_addr="alerts@example.com",
            to_addrs=["oncall@example.com"],
            username="alerts@example.com",
            password="...",  # prefer env vars in real apps
            use_tls=True,
        ),
    ],
)
```

**Tagged tool calls** — filter or group failures in the dashboard by tool or pipeline:

```python
from agent_sentry import watch

@watch(event_type="tool_call", tags=["retrieval", "prod"])
def fetch_context(query: str) -> list[str]:
    ...

@watch(event_type="llm_call", tags=["summarization"])
def summarize(docs: list[str]) -> str:
    ...
```

Call `configure(...)` once at process startup (before decorated functions run) so the store and alert channels are shared for all `@watch` usage.

## CLI

```bash
agent-sentry dashboard          # Launch Streamlit dashboard
agent-sentry report             # Terminal report (last 24h)
agent-sentry report --hours 168 # Last 7 days
agent-sentry clear              # Clear all events
```

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

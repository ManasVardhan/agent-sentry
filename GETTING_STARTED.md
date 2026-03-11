# Getting Started with agent-sentry

Crash reporting for AI agents. Catch failures before your users do.

## Installation

```bash
pip install agent-sentry
```

With optional integrations:

```bash
pip install agent-sentry[openai]      # OpenAI wrapper
pip install agent-sentry[anthropic]   # Anthropic wrapper
pip install agent-sentry[langchain]   # LangChain callback
pip install agent-sentry[dashboard]   # Streamlit dashboard
pip install agent-sentry[all]         # Everything
```

## Quick Start

### 1. Wrap your agent functions

```python
from agent_sentry import watch

@watch
def my_agent(query: str) -> str:
    # Your agent logic here
    return call_llm(query)

# Every call is automatically captured with:
# - Duration tracking
# - Error classification
# - Root cause analysis
result = my_agent("What is the weather?")
```

### 2. Add tags and metadata

```python
@watch(event_type="tool_call", tags=["search", "web"])
def search_tool(query: str) -> dict:
    return web_search(query)
```

### 3. Check your agent's health

```bash
# Terminal report
agent-sentry report

# Or with a time window
agent-sentry report --hours 48
```

### 4. Configure alerts

```python
from agent_sentry import configure, SlackAlert, WebhookAlert

configure(
    webhook_url="https://your-webhook.example.com/alerts",
    slack_webhook="https://hooks.slack.com/services/YOUR/WEBHOOK",
)
```

## Root Cause Classification

agent-sentry automatically classifies failures into categories:

| Root Cause | Description |
|---|---|
| `timeout` | Request took too long or timed out |
| `rate_limit` | API rate limit or quota exceeded |
| `auth_error` | Authentication or permission failure |
| `context_overflow` | Token limit exceeded |
| `malformed_args` | Invalid arguments or type errors |
| `network_error` | Connection failures |
| `hallucination` | Model produced unreliable output |
| `silent_failure` | No error but empty/null result |
| `unknown` | Could not classify |

## OpenAI Integration

```python
from agent_sentry.integrations.openai import SentryOpenAI

client = SentryOpenAI()  # Drop-in replacement for OpenAI()

response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello"}],
)
# Automatically tracked with cost estimation
```

## LangChain Integration

```python
from agent_sentry.integrations.langchain import SentryCallbackHandler

handler = SentryCallbackHandler()

# Use with any LangChain chain or agent
chain.invoke({"input": "query"}, config={"callbacks": [handler]})
```

## Dashboard

Launch the Streamlit dashboard for visual monitoring:

```bash
agent-sentry dashboard
```

Requires: `pip install agent-sentry[dashboard]`

## Custom Database Path

```bash
# CLI
agent-sentry --db /path/to/events.db report

# Python
from agent_sentry import configure
configure(db_path="/path/to/events.db")
```

## Next Steps

- Check [README.md](README.md) for full API reference
- See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup
- See [ROADMAP.md](ROADMAP.md) for upcoming features

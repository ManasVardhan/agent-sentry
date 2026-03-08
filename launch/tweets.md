# Launch Tweets

## Main Launch Tweet
I built crash reporting for AI agents.

Your agent fails silently in production. Tool calls timeout, context overflows, hallucinations slip through. You don't know until a user complains.

agent-sentry: one decorator, automatic failure classification, real-time alerts.

pip install agent-sentry

https://github.com/ManasVardhan/agent-sentry

## Thread Follow-up (reply to main tweet)

### 2/
How it works:

```python
from agent_sentry import watch

@watch
def my_agent(query):
    return call_llm(query)
```

Every call is tracked. Every failure is classified: timeout, hallucination, context overflow, malformed args, rate limit, auth error.

Run `agent-sentry dashboard` for the full picture.

### 3/
Why I built this:

I had an agent running 10k tool calls/day. Some failed. I had no idea which ones or why.

Traditional monitoring saw 200s and said "all good." Meanwhile the agent was burning tokens on hallucinated API endpoints and returning garbage.

### 4/
What you get:

- Reliability score (like a credit score for your agent)
- Root cause classification for every failure
- Token cost tracking per call
- Slack/webhook alerts when things break
- Streamlit dashboard

60 tests. MIT licensed. Works with OpenAI, Anthropic, LangChain.

## Shorter Alternative Tweet (if you want something punchier)
Your AI agent fails silently in production. You just don't know it yet.

I built agent-sentry: crash reporting for agents. One decorator. Automatic failure classification. Real-time alerts.

Think Sentry, but for LLM agents.

https://github.com/ManasVardhan/agent-sentry

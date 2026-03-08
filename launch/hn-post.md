# Show HN: Agent-Sentry, crash reporting for AI agents

**URL:** https://github.com/ManasVardhan/agent-sentry

**Text:**

I built agent-sentry because I kept deploying agents that failed silently in production. Traditional monitoring sees HTTP 200s and thinks everything is fine, but the agent just hallucinated a fake API endpoint, retried it 5 times, burned $2 in tokens, and returned garbage.

agent-sentry is a Python library that wraps your agent with a single decorator and catches every failure. It classifies root causes automatically (timeout, hallucination, context overflow, malformed args, rate limit, auth error), tracks token costs, and gives you a reliability score you can monitor over time.

Quick start is three lines:

    from agent_sentry import watch

    @watch
    def my_agent(query):
        result = call_llm(query)
        return result

Then run `agent-sentry dashboard` for a Streamlit UI showing failure rates, root causes, and cost breakdown. Works with OpenAI, Anthropic, and LangChain out of the box.

60 tests, MIT licensed. Feedback welcome.

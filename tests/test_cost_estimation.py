"""Tests for cost estimation in OpenAI and Anthropic wrappers."""

from agent_sentry.integrations.openai import _estimate_cost as openai_cost
from agent_sentry.integrations.anthropic import _estimate_cost as anthropic_cost


class TestOpenAICostEstimation:
    def test_gpt4_cost(self):
        usage = {"prompt_tokens": 1000, "completion_tokens": 500}
        cost = openai_cost("gpt-4", usage)
        assert cost is not None
        # gpt-4: $0.03/1K prompt + $0.06/1K completion
        expected = (1000 / 1000 * 0.03) + (500 / 1000 * 0.06)
        assert cost == round(expected, 6)

    def test_gpt4_turbo_cost(self):
        usage = {"prompt_tokens": 2000, "completion_tokens": 1000}
        cost = openai_cost("gpt-4-turbo-preview", usage)
        assert cost is not None
        expected = (2000 / 1000 * 0.01) + (1000 / 1000 * 0.03)
        assert cost == round(expected, 6)

    def test_gpt4o_cost(self):
        usage = {"prompt_tokens": 500, "completion_tokens": 200}
        cost = openai_cost("gpt-4o", usage)
        assert cost is not None
        # gpt-4o: $0.0025/1K prompt + $0.01/1K completion
        expected = (500 / 1000 * 0.0025) + (200 / 1000 * 0.01)
        assert cost == round(expected, 6)

    def test_gpt4o_mini_cost(self):
        usage = {"prompt_tokens": 10000, "completion_tokens": 5000}
        cost = openai_cost("gpt-4o-mini", usage)
        assert cost is not None
        expected = (10000 / 1000 * 0.00015) + (5000 / 1000 * 0.0006)
        assert cost == round(expected, 6)

    def test_gpt41_cost(self):
        usage = {"prompt_tokens": 1000, "completion_tokens": 500}
        cost = openai_cost("gpt-4.1", usage)
        assert cost is not None
        expected = (1000 / 1000 * 0.002) + (500 / 1000 * 0.008)
        assert cost == round(expected, 6)

    def test_o3_cost(self):
        usage = {"prompt_tokens": 1000, "completion_tokens": 500}
        cost = openai_cost("o3", usage)
        assert cost is not None
        expected = (1000 / 1000 * 0.01) + (500 / 1000 * 0.04)
        assert cost == round(expected, 6)

    def test_gpt35_turbo_cost(self):
        usage = {"prompt_tokens": 1000, "completion_tokens": 500}
        cost = openai_cost("gpt-3.5-turbo", usage)
        assert cost is not None
        expected = (1000 / 1000 * 0.0005) + (500 / 1000 * 0.0015)
        assert cost == round(expected, 6)

    def test_unknown_model_returns_none(self):
        usage = {"prompt_tokens": 1000, "completion_tokens": 500}
        assert openai_cost("llama-3-70b", usage) is None

    def test_zero_tokens(self):
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        cost = openai_cost("gpt-4", usage)
        assert cost == 0.0

    def test_missing_token_keys(self):
        """Missing keys should default to 0."""
        cost = openai_cost("gpt-4", {})
        assert cost == 0.0

    def test_partial_model_match(self):
        """Model name containing a known prefix should still match."""
        usage = {"prompt_tokens": 100, "completion_tokens": 50}
        cost = openai_cost("gpt-4-0613", usage)
        assert cost is not None
        assert cost > 0


class TestAnthropicCostEstimation:
    def test_claude3_opus_cost(self):
        usage = {"input_tokens": 1000, "output_tokens": 500}
        cost = anthropic_cost("claude-3-opus-20240229", usage)
        assert cost is not None
        expected = (1000 / 1000 * 0.015) + (500 / 1000 * 0.075)
        assert cost == round(expected, 6)

    def test_claude3_sonnet_cost(self):
        usage = {"input_tokens": 1000, "output_tokens": 500}
        cost = anthropic_cost("claude-3-sonnet-20240229", usage)
        assert cost is not None
        expected = (1000 / 1000 * 0.003) + (500 / 1000 * 0.015)
        assert cost == round(expected, 6)

    def test_claude3_haiku_cost(self):
        usage = {"input_tokens": 5000, "output_tokens": 2000}
        cost = anthropic_cost("claude-3-haiku-20240307", usage)
        assert cost is not None
        expected = (5000 / 1000 * 0.00025) + (2000 / 1000 * 0.00125)
        assert cost == round(expected, 6)

    def test_claude_sonnet_4_cost(self):
        usage = {"input_tokens": 1000, "output_tokens": 500}
        cost = anthropic_cost("claude-sonnet-4-20250514", usage)
        assert cost is not None
        assert cost > 0

    def test_claude35_sonnet_cost(self):
        usage = {"input_tokens": 1000, "output_tokens": 500}
        cost = anthropic_cost("claude-3-5-sonnet-20241022", usage)
        assert cost is not None
        assert cost > 0

    def test_claude35_haiku_cost(self):
        usage = {"input_tokens": 1000, "output_tokens": 500}
        cost = anthropic_cost("claude-3-5-haiku-20241022", usage)
        assert cost is not None
        # claude-3-5-haiku: $0.0008/1K input + $0.004/1K output
        expected = (1000 / 1000 * 0.0008) + (500 / 1000 * 0.004)
        assert cost == round(expected, 6)

    def test_claude_opus_4_cost(self):
        usage = {"input_tokens": 1000, "output_tokens": 500}
        cost = anthropic_cost("claude-opus-4-20250514", usage)
        assert cost is not None
        expected = (1000 / 1000 * 0.015) + (500 / 1000 * 0.075)
        assert cost == round(expected, 6)

    def test_unknown_model_returns_none(self):
        usage = {"input_tokens": 1000, "output_tokens": 500}
        assert anthropic_cost("gemini-pro", usage) is None

    def test_zero_tokens(self):
        usage = {"input_tokens": 0, "output_tokens": 0}
        cost = anthropic_cost("claude-3-opus-20240229", usage)
        assert cost == 0.0

    def test_missing_token_keys(self):
        cost = anthropic_cost("claude-3-opus-20240229", {})
        assert cost == 0.0

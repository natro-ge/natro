"""The Claude models the agent uses: request options and prices."""

# Per model: USD per million tokens (input, output), and request options.
MODELS = {
    # Low effort keeps spoken replies quick; Sonnet still thinks when a request
    # needs it. If Sonnet declines a request, the API reruns it on a fallback
    # model ("default" picks one by the reason) instead of returning a refusal.
    "claude-sonnet-5-5": {
        "prices": (2.0, 10.0),
        "options": {"output_config": {"effort": "low"}, "betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"},
    },
    # For PC and screen tasks: about 20 times cheaper than Sonnet, and quick. It
    # caches from 512 tokens (Haiku 4.5 needed 4,096, more than Natro had). The
    # prices are for prompts up to 100K tokens, which Natro's stay under. It has
    # no server-side fallback: when it declines, Sonnet takes over (STRONGER).
    "claude-haiku-5-5": {"prices": (0.10, 0.50), "options": {"output_config": {"effort": "low"}}},
}
DEFAULT_MODEL = "claude-sonnet-5-5"
# Requests that use the PC or a screen (commands, settings, clicking in apps) start here.
PC_MODEL = "claude-haiku-5-5"
# Takes over the rest of a request when the model above gets stuck: it declines, its tools fail twice in a
# row, or the request takes many steps. Sonnet 5.5 reads Haiku 5.5's thinking, so nothing is lost.
STRONGER = {"claude-haiku-5-5": "claude-sonnet-5-5"}
# Models with adaptive thinking, whose thinking blocks are tied to the exact conversation.
ADAPTIVE_THINKING = {"claude-sonnet-5-5", "claude-haiku-5-5"}
# Used for the rest of a conversation when the model's API fails (overloaded, down).
FALLBACK_MODEL = {"claude-sonnet-5-5": "claude-haiku-5-5", "claude-haiku-5-5": "claude-sonnet-5-5"}

# Cache writes (5-minute) and reads, relative to the input price.
CACHE_WRITE = 1.25
CACHE_READ = 0.1


def options(model, check_history=False):
    """Request options for a model.

    check_history makes the API reject a request whose earlier conversation was
    changed after Claude thought about it (an edited message, instructions or
    tools), instead of quietly dropping the thinking: for tests, to prove that
    the agent only ever appends to a conversation.
    """
    result = dict(MODELS[model]["options"])
    if check_history and model in ADAPTIVE_THINKING:
        result["thinking"] = {"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "error"}}
        result["betas"] = [*result.get("betas", []), "thinking-binding-controls-2026-08-01"]
    return result


def cost(model, usage):
    """USD for one response, from its token usage."""
    price_in, price_out = MODELS[model]["prices"]
    return (usage.input_tokens * price_in + usage.output_tokens * price_out
            + (usage.cache_creation_input_tokens or 0) * price_in * CACHE_WRITE
            + (usage.cache_read_input_tokens or 0) * price_in * CACHE_READ) / 1_000_000

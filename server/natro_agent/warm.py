"""Keeping Claude's cached instructions from running out while he's about. Warming is a part of Agent (agent.py), whose
models, client, spending and first instructions it reads and whose requests fill in Warmth."""
import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime

import anthropic

from natro_agent import log
from natro_agent.config import timezone
from natro_agent.models import CHECK_HISTORY, TOKEN_KINDS, cost, options

# Both models' cached instructions are kept from running out (an hour after they were last read) for this long
# after his last request, while it's between these hours his time, by reading them again WARM_EVERY minutes after
# the last read. On his requests of 20-26 Oct it would have left 8 of 217 starting cold instead of 42, and the
# cache's cost at $0.18 a day instead of $0.35.
WARM_FOR_HOURS = 6
WARM_FROM, WARM_UNTIL = 8, 1  # 08:00 to 01:00
WARM_EVERY_MINUTES = 55


@dataclass
class Warmth:
    """When each model last read Natro's instructions (time.monotonic() at a call's start: any call reads them, or
    writes them if they weren't cached), and when he last asked something. The workers (jobs) share it."""
    used: dict = field(default_factory=dict)
    asked: float = None


class Warming:
    """Agent's warm-ups, run from the server's clocks by keep_warm."""

    def warm_due(self, now=None, local=None):
        """The models whose cached instructions should be read now, so they don't run out while he's about (see
        WARM_FOR_HOURS): none when he hasn't asked anything for that long, at night, or over the budget."""
        now = time.monotonic() if now is None else now
        local = local or datetime.now(timezone())
        asked = self.warmth.asked
        if asked is None or now - asked > WARM_FOR_HOURS * 3600 or self.ledger.over_budget:
            return []
        if WARM_UNTIL <= local.hour < WARM_FROM:  # the hours run past midnight
            return []
        return [model for model in dict.fromkeys((self.model, self.cheap_model))
                if now - self.warmth.used.get(model, float("-inf")) >= WARM_EVERY_MINUTES * 60]

    async def warm(self, model):
        """Read the instructions and tools a new conversation would start with on model (writing them if they
        weren't cached), so they stay cached another hour. max_tokens 0: nothing is written back, only the prompt
        read; thinking and effort as a real request's, as they're part of what's cached."""
        session = self._claude_session()
        request = dict(model=model, max_tokens=0, system=session.system,
                       messages=[{"role": "user", "content": "warmup"}], **options(model, CHECK_HISTORY))
        if session.tool_definitions:
            request["tools"] = session.tool_definitions
        self.warmth.used[model] = time.monotonic()
        response = await self.client.beta.messages.create(**request)
        spent = cost(model, response.usage)
        self.ledger.add(spent)
        log.write("warm", model=model, cost=round(spent, 6),
                  tokens={kind: getattr(response.usage, kind) or 0 for kind in TOKEN_KINDS})

    async def keep_warm(self):
        """Until cancelled: each minute, warms the models warm_due names."""
        while True:
            await asyncio.sleep(60)
            for model in self.warm_due():
                try:
                    await self.warm(model)
                except anthropic.APIError as e:
                    log.write("model_error", model=model, error=repr(e), warming=True)

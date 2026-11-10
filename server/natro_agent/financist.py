"""The financist: a specialist Natro hands questions about his money to, which reads his real numbers and thinks.

A run of its own, apart from her conversation, for three reasons. She runs at low effort for quick replies, and this is
where depth is worth the seconds. Its instructions (identity/FINANCIST.md) don't sit in the prompt of every request,
for what's a few a week. And its tools only read, so what a merchant's name or a web page says can lead to nothing being
done, whereas she can send messages.

Nothing is kept between questions: for a follow-up, she puts what's been said into the next question. What each run
costs goes on the request that asked (tool_calls.SPEND), and the log keeps only how many steps it took and what it cost.
"""
import asyncio
import json
import time
from datetime import datetime

import anthropic

from natro_agent import identity, log
from natro_agent.config import SERVER_DIR, timezone
from natro_agent.models import CHECK_HISTORY, FINANCIST_MODEL, cost, options
from natro_agent.tool_calls import SPEND
from natro_agent.tools import ToolError, result_text

INSTRUCTIONS_FILE = SERVER_DIR / "identity" / "FINANCIST.md"
# Model calls a question may take: enough to read several things and think, not to wander.
MOST_STEPS = 8
MAX_TOKENS = 16000
READ_SECONDS = 30
RESULT_CHARS = 20000
DECLINED = "I can't help with that one."
RAN_OUT = "I ran out of room before finishing that, so ask me about one part of it."


def text_of(content):
    return "".join(block.text for block in content if block.type == "text").strip()


def echoed(content):
    """The assistant turn to send back: unchanged, thinking blocks included, except when the API switched to a fallback
    model partway through, whose earlier thinking and tool calls the API won't take back."""
    switches = [i for i, block in enumerate(content) if block.type == "fallback"]
    if not switches:
        return list(content)
    boundary = switches[-1]
    return [block for block in content[:boundary] if block.type == "text"] + list(content[boundary + 1:])


class Financist:
    """tools: what it reads with. client: Claude's. memory: what Natro remembers about him."""

    def __init__(self, tools, client, memory=None, model=FINANCIST_MODEL):
        self.tools = {tool.name: tool for tool in tools}
        self.definitions = [tool.definition() for tool in sorted(tools, key=lambda tool: tool.name)]
        self.client = client
        self.memory = memory
        self.model = model

    def _system(self):
        system = [{"type": "text", "text": INSTRUCTIONS_FILE.read_text(encoding="utf-8").strip()}]
        remembered = self.memory.prompt_section() if self.memory else None
        if remembered:
            system.append({"type": "text", "text": identity.memory_prompt(remembered)})
        return system

    async def answer(self, question):
        """Its answer to a question, as text for him. Raises ToolError if Claude can't be reached."""
        now = datetime.now(timezone())
        messages = [{"role": "user", "content": f"[{now:%A %d %B %Y, %H:%M}]\n{question}"}]
        system = self._system()
        started, spent, used, steps = time.monotonic(), 0.0, [], 0
        try:
            while True:
                steps += 1
                response = await self._call(system, messages, last=steps == MOST_STEPS)
                paid = cost(self.model, response.usage)
                spent += paid
                if (spend := SPEND.get()) is not None:
                    spend(paid, response.usage)
                content = echoed(response.content)
                if response.stop_reason == "refusal":
                    return DECLINED
                if response.stop_reason == "max_tokens":
                    return text_of(content) or RAN_OUT
                messages.append({"role": "assistant", "content": content})
                if response.stop_reason == "pause_turn":
                    continue
                uses = [block for block in content if block.type == "tool_use"]
                if not uses:
                    return text_of(content) or RAN_OUT
                used += [block.name for block in uses]
                results = await asyncio.gather(*(self._read(block) for block in uses))
                messages.append({"role": "user", "content": list(results)})
        except anthropic.APIError as e:
            log.write("error", step="financist", error=repr(e))
            raise ToolError(f"The financist couldn't reach Claude: {type(e).__name__}.")
        finally:
            # No amounts here: only what it took. "usd", as "cost" in the log counts toward the month by itself, and the
            # request that asked already carries this.
            log.write("financist", steps=steps, tools=used, usd=round(spent, 6), seconds=round(time.monotonic() - started, 2))

    async def _call(self, system, messages, last=False):
        request = dict(model=self.model, max_tokens=MAX_TOKENS, system=system, messages=messages, tools=self.definitions,
                       # Each step reads the one before, so the whole of it is cached for the next.
                       cache_control={"type": "ephemeral"}, **options(self.model, CHECK_HISTORY))
        if last:  # its last step: it answers with what it has
            request["tool_choice"] = {"type": "none"}
        async with self.client.beta.messages.stream(**request) as stream:
            return await stream.get_final_message()

    async def _read(self, block):
        """One tool call's result."""
        args = block.input if isinstance(block.input, dict) else {}
        tool = self.tools.get(block.name)
        is_error = False
        if tool is None:
            content, is_error = f"There is no tool named {block.name!r}.", True
        else:
            try:
                content = result_text(await asyncio.wait_for(tool.run(args), READ_SECONDS))
            except ToolError as e:
                content, is_error = str(e), True
            except asyncio.TimeoutError:
                content, is_error = f"{block.name} took too long.", True
            except Exception as e:  # one failed read shouldn't end the question
                log.write("error", step="financist", tool=block.name, error=repr(e))
                content, is_error = f"{block.name} failed.", True
        return {"type": "tool_result", "tool_use_id": block.id, "content": content[:RESULT_CHARS], "is_error": is_error}

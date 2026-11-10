"""Running the tools Claude calls: asking first when one needs a yes, the limits on their time and on what comes back,
and what's shown and logged of them. ToolCalls is a part of Agent (agent.py), which sets up what each request keeps of
this: its questions so far, what its tools did, its timing and where its steps show."""
import asyncio
import json
import time
from contextvars import ContextVar
from dataclasses import dataclass

from natro_agent import log, pictures, steps
from natro_agent.models import TOKEN_KINDS
from natro_agent.tools import ToolError, WithPictures, result_text

TOOL_SECONDS = 60
CONFIRM_SECONDS = 60
# More changes than this in one request (events, tasks, notes added or changed) ask the owner once first.
MANY_CHANGES = 5
# Longer tool results are cut, to keep requests small.
RESULT_CHARS = 20000

# How a tool that asks in its own words (Tool.own_question: send_message) asks the owner: await ASK.get()(question,
# details) is his yes, asked as any question of the request (or the job) running it is. Set for each tool call.
ASK = ContextVar("ask", default=None)
# The device the request (or the job) running a tool came from, for a tool that acts on a device itself (spotify.py).
# Set for each tool call.
ASKER = ContextVar("asker", default=None)
# What a tool that runs a model of its own (the financist) spends goes on the request running it:
# SPEND.get()(cost, usage), after each model call. Set for each tool call.
SPEND = ContextVar("spend", default=None)
# In place of a private tool's result, in the log and the details under a reply.
LEFT_OUT = "(private: left out)"


def plain_question(name, args):
    """The question before a risky tool when the model wrote no sentence first: what it will do, in plain words."""
    what = steps.describe(name, args)["text"]
    where = {"pc": " on the PC", "phone": " on the phone"}.get(name.partition("_")[0], "")
    details = [str(value) for value in args.values()
               if isinstance(value, (str, int, float)) and not isinstance(value, bool) and str(value).strip()
               and str(value).casefold() not in what.casefold()]
    details = f": {steps.short(', '.join(details), 60)}" if details else ""
    return f"{what}{where}{details}. Should I go ahead?"


@dataclass
class Changes:
    """The owner's items one request has added or changed so far, and whether he allowed many."""
    made: int = 0
    allowed: bool = False


def call_key(block):
    """A tool call's name and arguments, to find the same call twice."""
    return block.name, json.dumps(block.input, sort_keys=True, default=str)


async def repeated(block):
    """The tool_result for a call made twice in one step: the first one ran."""
    return {"type": "tool_result", "tool_use_id": block.id, "is_error": False,
            "content": "Not done again: the very same call is in this step already, and that one ran."}


def first_line(text):
    return text.strip().splitlines()[0] if text.strip() else text


class ToolCalls:
    """Agent's half that runs tools, for the loop and for quick commands."""

    async def _many_changes(self, changes, tools, tool_uses, device, question):
        """Ask once before a request's changes go past MANY_CHANGES. Returns the ids of the calls he declined."""
        changing = [block for block in tool_uses if getattr(tools.get(block.name), "changes", False)]
        if not changing:
            return set()
        if not changes.allowed and changes.made + len(changing) > MANY_CHANGES:
            counts = {}
            for block in changing:
                counts[block.name] = counts.get(block.name, 0) + 1
            details = ", ".join(f"{name} x{count}" for name, count in counts.items())
            changes.allowed = await self._ask(device, question or f"Should I make {len(changing)} changes?",
                                              f"{len(changing)} changes: {details}")
            if not changes.allowed:
                return {block.id for block in changing}
        changes.made += len(changing)
        return set()

    async def _use_tool(self, session, block, device, question, reply, declined=False):
        """Run one of Claude's tool calls and return its tool_result."""
        args = block.input if isinstance(block.input, dict) else {}
        content, is_error, shown = await self._call_tool(session.tools, block.name, args, device, question, reply,
                                                         declined)
        seen = [image for image in (pictures.for_model(mime, data) for mime, data in shown) if image]
        if seen:  # a screenshot: she sees it with the tool's words
            content = [{"type": "text", "text": content}, *seen]
        return {"type": "tool_result", "tool_use_id": block.id, "content": content, "is_error": is_error}

    async def _call_tool(self, tools, name, args, device, question, reply, declined=False):
        """Run one tool call, asking first if it needs a yes. Returns (text for the model, whether it failed, the
        pictures it gave as (mime, bytes) pairs).

        declined: the owner already said no to it (as one of many changes).
        """
        tool = tools.get(name)
        if tool is None:
            # Models sometimes get the letter case of a name wrong.
            tool = next((t for known, t in tools.items() if known.lower() == name.lower()), None)
        call = {"tool": name, "args": args}
        shown = []
        if tool is None:
            content, is_error = f"There is no tool named {name!r}.", True
        elif declined or tool.confirm and not tool.own_question and not await self._ask(
                device, question or plain_question(tool.name, args),
                f"{tool.name} {json.dumps(args, ensure_ascii=False)}"):
            content, is_error = "Your owner said no, so this was not done.", False
            call["declined"] = True
        else:
            self._show_step(tool.name, args)
            ASK.set(lambda question, details: self._ask(device, question, details))
            ASKER.set(device)
            SPEND.set(lambda spent, usage: self._spent(reply, spent, usage))
            began, asked = time.monotonic(), self._timing.asking
            content, is_error, shown = await self._run(tool, args)
            # Without waiting for his yes, when it asked in its own words.
            self._timing.tool(tool.name, tool.source, time.monotonic() - began - (self._timing.asking - asked),
                              not is_error)
            if not is_error:
                self._done.append(f"{tool.name} {json.dumps(args, ensure_ascii=False)[:160]}: "
                                  f"{first_line(content)[:160]}")
        private = tool is not None and tool.private and not is_error
        call.update(ok=not is_error, result=LEFT_OUT if private else content[:500])
        log.write("tool", device=device.name, **call)
        reply.tool_calls.append(call)
        if len(content) > RESULT_CHARS:
            content = f"{content[:RESULT_CHARS]}\n[cut here: the whole result was {len(content)} characters]"
        return content, is_error, shown

    @staticmethod
    def _spent(reply, spent, usage):
        """A tool's own model call, counted with the request's."""
        reply.cost += spent
        for kind in TOKEN_KINDS:
            reply.tokens[kind] = reply.tokens.get(kind, 0) + (getattr(usage, kind, 0) or 0)

    def _show_step(self, name, args):
        try:
            self._on_step(steps.describe(name, args))
        except Exception as e:  # a step is only for show: never in the way of the tool
            log.write("error", step="describe", tool=name, error=repr(e))

    async def _run(self, tool, args):
        """Returns (text for the model, whether it failed, its pictures)."""
        # A tool that asks in its own words: its question's time too, and its tries on another device (send_message).
        seconds = tool.seconds or TOOL_SECONDS + (CONFIRM_SECONDS + 2 * TOOL_SECONDS if tool.own_question else 0)
        try:
            result = await asyncio.wait_for(tool.run(args), seconds)
        except ToolError as e:
            return str(e), True, []
        except asyncio.TimeoutError:
            return f"{tool.name} took longer than {seconds} seconds and was stopped.", True, []
        except Exception as e:
            return f"{tool.name} failed: {type(e).__name__}: {e}", True, []
        return result_text(result), False, list(result.pictures) if isinstance(result, WithPictures) else []

    async def _ask(self, device, question, details):
        """Ask the owner on the device; no answer within CONFIRM_SECONDS counts as no."""
        self._asked.append(question)
        with self._timing.asked():
            async with self._asking:
                try:
                    return await asyncio.wait_for(device.confirm(question, details), CONFIRM_SECONDS)
                except asyncio.TimeoutError:
                    return False

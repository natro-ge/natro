"""The free Gemini tier, for requests with no personal data (offered when GEMINI_API_KEY is set).

Gemini gets Natro's identity, only the tools that are neither personal nor need
a yes, and the conversation so far, which never holds personal data: once a
conversation needs anything personal it moves to Claude and stays there. It
also gets one extra tool, hand_over, which it calls when a request needs more;
Claude then answers that request. Google may use what the free tier receives to
improve its products: the owner accepted that for non-personal requests.
"""
import asyncio
import os
import re
import time
from dataclasses import dataclass

from google import genai
from google.genai import errors, types

from natro_agent import log

# Free-tier models to try, best first. Each has its own free quota, and the
# newest are often overloaded ("503: high demand"; on 2026-10-05 3.8, 3.7 and 3.5
# Flash were, while 3.6 Flash and 3.5 Flash-Lite answered).
MODELS = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"]
# A model that is overloaded is skipped for this long, twice as long after each failure in a row, up to
# MAX_REST_SECONDS (over 12-17 October 3.8 Flash failed 17 times and answered 5); one over its quota until
# Google says it resets (its 429 has a retry delay: hours for the daily quota), but at least MIN_REST_SECONDS.
REST_SECONDS = 600
MAX_REST_SECONDS = 3 * 3600
MIN_REST_SECONDS = 30
# A model that hasn't answered by then is treated as overloaded (a call once hung for minutes;
# answers normally take 1-5 s). A request's first call, which writes a short answer or picks a tool, gets
# less (its answers took 1-4 s), so a model that hangs costs less of the request.
ANSWER_SECONDS = 10
FIRST_ANSWER_SECONDS = 6
BUSY_CODES = {429, 503}
# Gemini sometimes starts its reply with a copy of the request's first line ("[2026-10-13 15:23] ..."):
# square brackets with a time of day or the line's "·" in them.
HEADER_COPY = re.compile(r"^\s*\[[^\[\]\n]{0,300}?(?:\d:\d\d|·)[^\[\]\n]{0,300}\]\s*")
MAX_STEPS = 8
HAND_OVER = "hand_over"

HAND_OVER_DECLARATION = types.FunctionDeclaration(
    name=HAND_OVER,
    description=("Pass this request to the assistant with all the tools. Call it first, before any other tool and "
                 "without writing anything, when the request needs anything listed under Handing over."),
    parameters_json_schema={"type": "object", "properties": {
        "reason": {"type": "string", "description": "A few words on why."}}, "required": ["reason"]},
)

HAND_OVER_RULES = """## Handing over

You handle quick everyday requests. Another assistant with all of Natro's tools takes over when you call hand_over. Call it, before any other tool and without writing anything, when the request:
- involves your owner's personal data: his emails, calendar, tasks, notes, files, contacts, messages, phone notifications or screen, or what you remember about him and his life;
- asks you to remember or forget something, or refers to earlier conversations you can't see;
- needs a risky action: deleting, sending, calling, sharing, paying, force-closing an app;
- needs a tool you don't have, or asks what you can do.
Your owner doesn't see the hand-over; never mention it."""


@dataclass
class Outcome:
    text: str = ""
    handed_over: bool = False
    reason: str = ""
    model: str = ""


def rest_seconds(error, failures=1):
    """How long a busy model rests: until its quota resets if Google says when, else REST_SECONDS, doubled for
    each failure in a row before this one (failures counts this one), up to MAX_REST_SECONDS."""
    try:
        for detail in error.details["error"]["details"]:
            if detail.get("@type", "").endswith("RetryInfo"):
                return max(MIN_REST_SECONDS, float(detail["retryDelay"].rstrip("s")))
    except (AttributeError, KeyError, TypeError, ValueError):
        pass
    return min(MAX_REST_SECONDS, REST_SECONDS * 2 ** (failures - 1))


def without_header(text):
    """The reply without a copy of the request's first line at its start."""
    return HEADER_COPY.sub("", text, count=1) or text


def declaration(tool):
    return types.FunctionDeclaration(name=tool.name, description=tool.description,
                                     parameters_json_schema=tool.input_schema)


class Gemini:
    def __init__(self, client=None, models=MODELS):
        self.client = client or genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        self.models = list(models)
        self._resting = {}  # model -> when it may be tried again
        self._failures = {}  # model -> how many times in a row it failed

    @property
    def model(self):
        return self.models[0]

    def _config(self, session):
        return types.GenerateContentConfig(
            system_instruction=session.system,
            tools=[types.Tool(function_declarations=[declaration(tool) for tool in session.tool_list]
                              + [HAND_OVER_DECLARATION])],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),
        )

    async def respond(self, session, request, run_tool, shortcut=False):
        """Answer one request in a Gemini session, or hand it over.

        run_tool(name, args) runs a tool and returns (text, failed). The model's
        turns go back into session.contents unchanged: they carry the thought
        signatures Gemini needs to continue. shortcut: a simple request, so if its
        first step is one "final" tool, that tool's result is the reply.
        """
        session.contents.append(types.Content(role="user", parts=[types.Part.from_text(text=request)]))
        for step in range(MAX_STEPS):
            response = await self._generate(session, first=step == 0)
            calls = response.function_calls or []
            for call in calls:
                if call.name == HAND_OVER:
                    return Outcome(handed_over=True, reason=(call.args or {}).get("reason", ""), model=session.model)
            content = response.candidates[0].content if response.candidates else None
            if content is None or not content.parts:
                # Nothing usable came back (blocked or empty): let Claude answer.
                return Outcome(handed_over=True, reason="no answer from Gemini", model=session.model)
            session.contents.append(content)
            if not calls:
                return Outcome(text=without_header((response.text or "").strip()), model=session.model)
            parts = []
            results = []
            for call in calls:
                text, failed = await run_tool(call.name, dict(call.args or {}))
                results.append((text, failed))
                parts.append(types.Part(function_response=types.FunctionResponse(
                    id=call.id, name=call.name, response={"error" if failed else "result": text})))
            session.contents.append(types.Content(role="user", parts=parts))
            tool = session.tools.get(calls[0].name) if len(calls) == 1 else None
            if shortcut and step == 0 and tool and tool.final and not results[0][1] and not (response.text or "").strip():
                # The tool's result says it all ("Paused."): no second model call to word it.
                reply = results[0][0].strip().splitlines()[0]
                session.contents.append(types.Content(role="model", parts=[types.Part.from_text(text=reply)]))
                return Outcome(text=reply, model=session.model)
        return Outcome(text="I stopped there: that was taking too many steps.", model=session.model)

    async def _generate(self, session, first=False):
        """One model call (first: a request's first). The session keeps the model that first answered; a busy
        model is skipped for a while."""
        now = time.monotonic()
        order = [session.model] if session.model else []
        order += [model for model in self.models if model not in order and self._resting.get(model, 0) <= now]
        seconds = min(FIRST_ANSWER_SECONDS, ANSWER_SECONDS) if first else ANSWER_SECONDS
        error = RuntimeError("every free Gemini model is resting")
        for model in order:
            try:
                response = await asyncio.wait_for(self.client.aio.models.generate_content(
                    model=model, contents=session.contents, config=self._config(session)), seconds)
            except (errors.APIError, asyncio.TimeoutError) as e:
                if isinstance(e, errors.APIError) and e.code not in BUSY_CODES:
                    raise
                self._failures[model] = self._failures.get(model, 0) + 1
                rest = rest_seconds(e, self._failures[model])
                self._resting[model] = time.monotonic() + rest
                log.write("model_rest", model=model, seconds=round(rest), failures=self._failures[model],
                          error=repr(e)[:300])
                error = e
                continue
            self._failures.pop(model, None)
            session.model = model
            return response
        raise error

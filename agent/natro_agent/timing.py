"""How long each part of a request took, for the dashboard's breakdown (dashboard.py).

The server starts a Timing when a request comes in and logs it as one "timing" entry once the reply has gone out:
the voice pipeline's parts (recognizing the speech, the Georgian-only retry, translating), waiting for the request
before it (Natro answers one at a time), each model call (until its first token, and in all, with its tokens), each
step's tools (side by side, so a step is as long as its slowest one) and waiting for his yes. Waiting for his yes is
his time, not Natro's: a step's time leaves it out. The device that speaks the reply says how long it took to start
("spoken", logged as its own entry).
"""
import contextlib
import time


class Timing:
    def __init__(self):
        self.started = time.monotonic()
        # The voice pipeline's seconds (Voice.process): recognize, georgian (the retry, when it ran), translate,
        # translate_first (until the translation's first words).
        self.voice = {}
        self.wait = 0.0  # for the request before it
        self.asking = 0.0  # for his yes
        self.calls = []
        self.steps = []  # each step's tools, side by side, without waiting for his yes
        self.tools = []

    def call(self, model, usage, first_token, seconds):
        """A model call: seconds until its first token (None: it wrote nothing) and in all."""
        self.calls.append({
            "model": model, "first_token": None if first_token is None else round(first_token, 3),
            "seconds": round(seconds, 3), "input": usage.input_tokens or 0,
            "cache_read": usage.cache_read_input_tokens or 0, "cache_write": usage.cache_creation_input_tokens or 0,
            "output": usage.output_tokens or 0})

    def tool(self, name, source, seconds, ok):
        self.tools.append({"tool": name, "source": source, "seconds": round(seconds, 3), "ok": ok})

    @contextlib.contextmanager
    def step(self):
        """Around a step's tools: how long they took, less any wait for his yes meanwhile."""
        began, asked = time.monotonic(), self.asking
        try:
            yield
        finally:
            self.steps.append(round(max(0.0, time.monotonic() - began - (self.asking - asked)), 3))

    @contextlib.contextmanager
    def asked(self):
        """Around waiting for his yes."""
        began = time.monotonic()
        try:
            yield
        finally:
            self.asking += time.monotonic() - began

    def record(self):
        """The fields of its log entry; total is until now."""
        return {"total": round(time.monotonic() - self.started, 3),
                "voice": {key: round(value, 3) for key, value in self.voice.items()},
                "wait": round(self.wait, 3), "asking": round(self.asking, 3), "calls": self.calls,
                "steps": self.steps, "tools": self.tools}

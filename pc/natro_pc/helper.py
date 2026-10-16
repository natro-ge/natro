"""This PC's tools and Natro's voice, for the new desktop app (desktop/) until they're ported to Rust.

    python -m natro_pc.helper

The desktop app starts it (no console window) and talks to it in JSON lines:

  helper to app:  {"type": "tools", "tools": [...]}            first: the tools, as the brain should see them
  app to helper:  {"type": "tool", "id": "c1", "name": "...", "args": {...}}
  helper to app:  {"type": "tool_result", "id": "c1", "ok": true, "result": "..."}
  app to helper:  {"type": "say", "text": "..."}               speak it (Kokoro), after anything already queued
  app to helper:  {"type": "app", "id": "a1", "name": "Spotify"}
  helper to app:  {"type": "app", "id": "a1", "ok": true, "result": "<AppID>"}   the Start menu app, for its icon

Anything else it prints goes to stderr (the desktop app logs it). It exits when stdin closes.
"""
import json
import sys
import threading

from natro_pc.apps import StartMenu
from natro_pc.config import load_env
from natro_pc.speak import Speaker
from natro_pc.tools import ToolError, all_tools


def main():
    out = open(sys.stdout.fileno(), "w", encoding="utf-8", buffering=1, closefd=False)
    sys.stdout = sys.stderr  # stray prints (the tools', the voice's) must not break the protocol
    lines = open(sys.stdin.fileno(), encoding="utf-8", closefd=False)
    load_env()
    tools = {tool.name: tool for tool in all_tools()}
    writing = threading.Lock()

    def send(**message):
        with writing:
            out.write(json.dumps(message, ensure_ascii=False) + "\n")

    def run_tool(id, name, args):
        tool = tools.get(name)
        try:
            if tool is None:
                raise ToolError(f"The PC has no tool called {name!r}.")
            result, ok = tool.run(**args), True
        except ToolError as e:
            result, ok = str(e), False
        except Exception as e:
            result, ok = f"{name} failed on the PC: {type(e).__name__}: {e}", False
        send(type="tool_result", id=id, ok=ok, result=result)

    def find_app(id, name):
        try:
            send(type="app", id=id, ok=True, result=menu.find(name).app_id)
        except Exception as e:  # not installed (ToolError), or the Start menu couldn't be read
            send(type="app", id=id, ok=False, result=str(e))

    send(type="tools", tools=[tool.spec() for tool in tools.values()])
    menu = StartMenu()
    threading.Thread(target=menu.warm_up, daemon=True).start()
    speaker = Speaker()
    for line in lines:
        message = json.loads(line)
        if message.get("type") == "tool":
            threading.Thread(target=run_tool, daemon=True,
                             args=(message.get("id"), message.get("name"), message.get("args") or {})).start()
        elif message.get("type") == "app":
            threading.Thread(target=find_app, daemon=True, args=(message.get("id"), message.get("name", ""))).start()
        elif message.get("type") == "say":
            speaker.say(message.get("text", ""))


if __name__ == "__main__":
    main()

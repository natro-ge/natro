import json

import pytest

from conftest import FakeClient, run
from natro_agent import keys as keys_module
from natro_agent.agent import Agent
from natro_agent.keys import (FIELDS, Field, Keys, Refused, check_anthropic, check_budget, check_project,
                              check_tavily, check_timezone, hint, rewrite_env)
from natro_agent.server import Server
from test_server import HELLO, session

ENV = """# Server
ANTHROPIC_API_KEY=sk-ant-old-0000000000001111
# Optional: web search with Tavily's free plan (tavily.com).
# TAVILY_API_KEY=
NATRO_LISTEN=100.1.2.3:8700
NATRO_DEVICE_TOKEN=device-secret
NATRO_MONTHLY_BUDGET=15
"""


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def accept(value):
    return value


def refuse(value):
    raise Refused("Anthropic doesn't accept this key.")


def make(tmp_path, check=accept, clock=None):
    env = tmp_path / ".env"
    env.write_text(ENV, encoding="utf-8")
    fields = [Field(field.key, field.label, check if field.secret else field.check, field.secret, field.note)
              for field in FIELDS]
    return Keys(env, tmp_path / "tokens" / "settings-pin.json", fields, clock or Clock())


def unlock(keys, pin="2468", device="pc"):
    return run(keys.handle(device, {"do": "unlock", "pin": pin}))


def test_lines_are_changed_in_place_and_comments_stay():
    text = rewrite_env(ENV, {"ANTHROPIC_API_KEY": "sk-new", "TAVILY_API_KEY": "tvly-new", "GOOGLE_MAPS_API_KEY": "maps"})
    assert text.splitlines() == [
        "# Server",
        "ANTHROPIC_API_KEY=sk-new",
        "# Optional: web search with Tavily's free plan (tavily.com).",
        "TAVILY_API_KEY=tvly-new",  # it was commented out
        "NATRO_LISTEN=100.1.2.3:8700",
        "NATRO_DEVICE_TOKEN=device-secret",
        "NATRO_MONTHLY_BUDGET=15",
        "GOOGLE_MAPS_API_KEY=maps",  # it wasn't there
    ]


def test_the_first_unlock_chooses_the_pin_and_keys_show_only_their_end(tmp_path):
    keys = make(tmp_path)
    assert run(keys.handle("pc", {"do": "status"})) == {"ok": True, "pin": False}
    answer = unlock(keys)
    assert answer["ok"] and answer["new_pin"] and answer["session"]
    assert run(keys.handle("pc", {"do": "status"}))["pin"]
    saved = json.loads((tmp_path / "tokens" / "settings-pin.json").read_text())
    assert "2468" not in json.dumps(saved) and len(saved["salt"]) == 32
    fields = {field["key"]: field for field in answer["fields"]}
    assert fields["ANTHROPIC_API_KEY"] == {
        "key": "ANTHROPIC_API_KEY", "label": "Anthropic API key", "secret": True, "set": True, "value": "",
        "hint": "••••1111", "note": fields["ANTHROPIC_API_KEY"]["note"]}
    assert fields["TAVILY_API_KEY"]["set"] is False
    assert fields["NATRO_MONTHLY_BUDGET"]["value"] == "15"
    # What the devices connect with isn't there at all.
    assert "NATRO_LISTEN" not in fields and "NATRO_DEVICE_TOKEN" not in fields
    assert "sk-ant-old" not in json.dumps(answer)
    assert unlock(keys, "2468")["new_pin"] is False


def test_wrong_pins_have_to_wait_longer_each_time(tmp_path):
    clock = Clock()
    keys = make(tmp_path, clock=clock)
    unlock(keys, "2468")
    for left in (4, 3, 2, 1):
        assert unlock(keys, "1111")["message"].startswith(f"Wrong PIN ({left} tr")
    answer = unlock(keys, "1111")
    assert not answer["ok"] and answer["wait"] == 30
    # Even the right PIN waits.
    assert unlock(keys, "2468")["message"].startswith("Too many wrong tries: wait 30 seconds")
    clock.now += 31
    assert unlock(keys, "1111")["wait"] == 60
    clock.now += 61
    assert unlock(keys, "2468")["ok"]
    assert unlock(keys, "1111")["message"].startswith("Wrong PIN (4 tries")  # counted afresh
    assert unlock(keys, "12")["message"] == "A PIN is 4 to 12 digits."


def test_saving_checks_writes_backs_up_and_restarts(tmp_path):
    keys = make(tmp_path)
    session = unlock(keys)["session"]
    answer = run(keys.handle("pc", {"do": "save", "session": session, "values": {
        "ANTHROPIC_API_KEY": '  "sk-ant-new-9999999999992222" ', "NATRO_MONTHLY_BUDGET": "20.0",
        "NATRO_TIMEZONE": "Asia/Tbilisi"}}))
    assert answer["ok"] and answer["restarting"]
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY=sk-ant-new-9999999999992222\n" in env
    assert "NATRO_MONTHLY_BUDGET=20\n" in env and env.endswith("NATRO_TIMEZONE=Asia/Tbilisi\n")
    assert (tmp_path / ".env.bak").read_text(encoding="utf-8") == ENV
    fields = {field["key"]: field for field in answer["fields"]}
    assert fields["ANTHROPIC_API_KEY"]["hint"] == "••••2222"
    # The same values again: nothing to save, no restart.
    again = run(keys.handle("pc", {"do": "save", "session": session, "values": {"NATRO_MONTHLY_BUDGET": "20"}}))
    assert again["ok"] and not again["restarting"]


def test_nothing_is_saved_when_a_key_is_refused(tmp_path):
    keys = make(tmp_path, check=refuse)
    session = unlock(keys)["session"]
    answer = run(keys.handle("pc", {"do": "save", "session": session, "values": {
        "ANTHROPIC_API_KEY": "sk-typo", "NATRO_MONTHLY_BUDGET": "20", "NATRO_LISTEN": "0.0.0.0:80",
        "NATRO_TIMEZONE": "Mars/Base", "TAVILY_API_KEY": "tvly a"}}))
    assert not answer["ok"]
    assert answer["errors"] == {"ANTHROPIC_API_KEY": "Anthropic doesn't accept this key.",
                                "NATRO_LISTEN": "This can't be changed from the app.",
                                "NATRO_TIMEZONE": "Not a time zone (like Asia/Tbilisi).",
                                "TAVILY_API_KEY": "No spaces."}
    assert (tmp_path / ".env").read_text(encoding="utf-8") == ENV
    assert not (tmp_path / ".env.bak").exists()


def test_sessions_belong_to_one_device_and_end(tmp_path):
    clock = Clock()
    keys = make(tmp_path, clock=clock)
    session = unlock(keys)["session"]
    save = {"do": "save", "session": session, "values": {"NATRO_MONTHLY_BUDGET": "16"}}
    assert run(keys.handle("phone", save))["locked"]
    assert run(keys.handle("pc", {**save, "session": "guess"}))["locked"]
    clock.now += 299
    assert run(keys.handle("pc", {"do": "pin", "session": session, "pin": "1357"}))["ok"]  # used: 5 more minutes
    clock.now += 299
    assert run(keys.handle("pc", save))["ok"]
    clock.now += 301
    assert run(keys.handle("pc", save))["locked"]
    assert not unlock(keys, "2468")["ok"] and unlock(keys, "1357")["ok"]  # the new PIN
    session = unlock(keys, "1357")["session"]
    run(keys.handle("pc", {"do": "lock", "session": session}))
    assert run(keys.handle("pc", {**save, "session": session}))["locked"]


def test_values_are_checked():
    assert check_budget("15") == "15" and check_budget("7.50") == "7.5"
    for bad in ("free", "0", "-3", "5000"):
        with pytest.raises(Refused):
            check_budget(bad)
    assert check_timezone("Europe/Berlin") == "Europe/Berlin"
    with pytest.raises(Refused):
        check_timezone("../etc/passwd")
    assert check_project("natro-471210") == "natro-471210"
    with pytest.raises(Refused, match="project ID"):
        check_project("Natro Project")
    assert hint("short") == "••••" and hint("") == ""


@pytest.mark.parametrize("status, problem", [
    (200, None), (429, None), (401, "Anthropic doesn't accept this key."),
    (0, "Couldn't reach Anthropic to check the key."), (500, "Anthropic answered 500 to the check: try again.")])
def test_a_providers_answer_to_the_check(monkeypatch, status, problem):
    asked = []
    monkeypatch.setattr(keys_module, "_status", lambda url, headers: asked.append((url, headers)) or status)
    if problem:
        with pytest.raises(Refused, match=problem):
            check_anthropic("sk-ant-x")
    else:
        assert check_anthropic("sk-ant-x") == "sk-ant-x"
    assert asked[0][1]["x-api-key"] == "sk-ant-x"


def test_a_bad_request_means_a_bad_key(monkeypatch):
    monkeypatch.setattr(keys_module, "_status", lambda url, headers: 400)
    with pytest.raises(Refused, match="Tavily doesn't accept"):
        check_tavily("tvly-x")


def test_over_the_connection_natro_restarts_after_answering(tmp_path):
    keys = make(tmp_path)
    restarts = []
    keys.restart = lambda: restarts.append(True)
    server = Server(Agent(client=FakeClient()), token="secret", keys=keys)

    async def device(ws, port):
        await ws.send(json.dumps(HELLO))
        await ws.recv()  # welcome

        async def ask(**message):
            await ws.send(json.dumps({"type": "keys", **message}))
            while True:
                answer = json.loads(await ws.recv())
                if answer["type"] == "keys":
                    return answer

        status = await ask(id="k1", do="status")
        unlocked = await ask(id="k2", do="unlock", pin="2468")
        assert restarts == []
        saved = await ask(id="k3", do="save", session=unlocked["session"], values={"NATRO_MONTHLY_BUDGET": "25"})
        return status, unlocked, saved

    status, unlocked, saved = run(session(server, device))
    assert status == {"type": "keys", "id": "k1", "ok": True, "pin": False}
    assert unlocked["id"] == "k2" and unlocked["ok"]
    assert saved["id"] == "k3" and saved["restarting"]
    assert restarts == [True]
    assert "NATRO_MONTHLY_BUDGET=25\n" in (tmp_path / ".env").read_text(encoding="utf-8")


def test_the_maps_key_is_tried_with_a_short_walk(monkeypatch):
    import io
    import urllib.error

    from natro_agent import travel

    asked = []
    monkeypatch.setattr(travel, "post", lambda url, body, key: asked.append((body["travelMode"], key)) or {})
    assert keys_module.check_maps("AIza-maps") == "AIza-maps"
    assert asked == [("WALK", "AIza-maps")]

    def refused(url, body, key):
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, io.BytesIO(b"{}"))

    monkeypatch.setattr(travel, "post", refused)
    with pytest.raises(Refused, match="Google Maps doesn't accept"):
        keys_module.check_maps("AIza-other")

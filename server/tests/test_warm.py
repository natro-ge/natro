"""The cached instructions: the same whichever devices are online, and kept warm while he's about."""
import json
from datetime import datetime

from conftest import FakeClient, Owner, message, run, text
from natro_agent import agent as agent_module, log
from natro_agent.agent import Agent
from natro_agent.dashboard import commands
from natro_agent.dryrun import PC_TOOLS, DryRunDevice
from natro_agent.memory import Memory
from natro_agent.tools import KnownTools

SONNET, HAIKU = "claude-sonnet-5-5", "claude-haiku-5-5"
DAY = datetime(2026, 10, 26, 14, 0)  # his time
NIGHT = datetime(2026, 10, 27, 3, 0)


def test_a_devices_tools_are_remembered_in_a_file(tmp_path):
    path = tmp_path / "devices.json"
    known = KnownTools(path)
    known.update(DryRunDevice("pc", "PC", PC_TOOLS))
    known.update(DryRunDevice("pc", "PC", []))  # an app still starting up: what was remembered stays
    again = KnownTools(path)
    assert [tool.name for tool in again.tools("pc")] == [tool.name for tool in DryRunDevice("pc", "PC", PC_TOOLS).tools()]
    assert again.tools("phone") == [] and json.loads(path.read_text(encoding="utf-8"))["pc"] == PC_TOOLS


def test_only_his_devices_are_remembered():
    agent = Agent(client=FakeClient())
    agent.connect(DryRunDevice("test", "test", PC_TOOLS))
    assert agent.known_tools.specs == {}


def test_what_she_remembers_is_cached_apart_from_the_instructions(tmp_path):
    memory = Memory(tmp_path / "memory")
    memory.remember("Tuxa's sister is Mariam.", "told")
    client = FakeClient(message(text("Hi.")))
    run(Agent(client=client, memory=memory).respond("hello", Owner()))
    instructions, remembered = client.requests[0]["system"]
    assert instructions["cache_control"] == remembered["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert "Mariam" in remembered["text"] and "Mariam" not in instructions["text"]


def test_both_models_are_warmed_after_a_request_until_read_again():
    client = FakeClient(message(text("Hi.")))
    agent = Agent(client=client)
    assert agent.warm_due(local=DAY) == []  # he hasn't asked anything since the start
    run(agent.respond("hi", Owner()))  # Haiku answers: it has just read them
    now = agent.warmth.used[HAIKU]
    assert agent.warm_due(now, DAY) == [SONNET]  # not read for over an hour
    agent.warmth.used[SONNET] = now
    assert agent.warm_due(now + 54 * 60, DAY) == []
    assert agent.warm_due(now + 55 * 60, DAY) == [SONNET, HAIKU]  # shortly before the hour runs out
    assert agent.warm_due(now + 55 * 60, NIGHT) == []
    assert agent.warm_due(now + 6 * 3600 + 1, DAY) == []  # he hasn't asked anything for 6 hours


def test_nothing_is_warmed_over_the_budget():
    agent = Agent(client=FakeClient(message(text("Hi."))), budget=0.0)
    agent.warmth.asked = 0.0
    assert agent.warm_due(1.0, DAY) == []


def test_a_warm_up_reads_what_the_next_conversation_starts_with(tmp_path, monkeypatch):
    read = message(stop="max_tokens", input_tokens=8, output_tokens=0, cache_read=25000)
    client = FakeClient(read, message(text("Hi.")))
    agent = Agent(client=client)
    agent.connect(DryRunDevice("pc", "PC", PC_TOOLS))
    run(agent.warm(SONNET))
    agent.disconnect(agent.devices["pc"])  # gone meanwhile: the same tools and instructions all the same
    run(agent.respond("what's in my calendar today", Owner()))  # personal: Sonnet
    warm, real = client.requests
    assert warm["max_tokens"] == 0 and "cache_control" not in warm and warm["model"] == real["model"] == SONNET
    assert warm["system"] == real["system"] and warm["tools"] == real["tools"]
    assert warm["output_config"] == real["output_config"] and warm.get("betas") == real.get("betas")
    entry, = [entry for entry in log.entries(f"{datetime.now():%Y-%m}") if entry["kind"] == "warm"]
    assert entry["model"] == SONNET and entry["cost"] == round(25000 * 2.0 * 0.1 / 1e6 + 8 * 2.0 / 1e6, 6)
    assert agent.ledger.spent >= entry["cost"]


def test_keeping_warm_has_its_own_line_on_the_bill():
    entry = {"time": "2026-10-26T10:00:00", "kind": "warm", "model": SONNET, "cost": 0.005}
    assert commands([(None, 0, None, entry)]) == [{"name": "Keeping the cache warm", "times": 1, "cost": 0.005}]


def test_the_hours_run_past_midnight():
    assert agent_module.WARM_UNTIL < agent_module.WARM_FROM  # warm_due's night test relies on it

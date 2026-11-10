"""His money: the connector, the financist's own run, and what stays out of the log."""
import asyncio
import io
import json
import urllib.error

import anthropic
import httpx2
import pytest

from conftest import FakeClient, Owner, message, run, text, thinking, tool_use
from natro_agent import log
from natro_agent.agent import Agent
from natro_agent.finance import Finance, FinanceAPI
from natro_agent.financist import MOST_STEPS, RAN_OUT, Financist
from natro_agent.memory import Memory
from natro_agent.models import FINANCIST_MODEL, cost
from natro_agent.routing import pc_task, personal_or_risky
from natro_agent.tool_calls import LEFT_OUT
from natro_agent.tools import Tool, ToolError

SONNET = "claude-sonnet-5-5"
MONEY = '{"spendable_gel":"4530.00","net_worth_gel":"9000.00"}'
SECRET_AMOUNT = "4530.00"


class Connector:
    """The connector's stand-in: answers with MONEY and records what it was asked."""

    def __init__(self, answer=MONEY):
        self.answer = answer
        self.asked = []

    def __call__(self, url, token):
        self.asked.append((url, token))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def finance(claude, connector=None, memory=None, search=()):
    return Finance(FinanceAPI(url="http://127.0.0.1:8090", token="secret", get=connector or Connector()),
                   memory=memory, search=search, client=claude)


def natro(claude_here, claude_there=None, connector=None):
    """Natro with the two tools, as setup.py builds her. Her replies and the financist's are scripted apart."""
    money = finance(claude_there, connector)
    return Agent(money.tools(), client=claude_here, budget=15.0)


def http_error(status, body):
    return urllib.error.HTTPError("http://x", status, "no", {}, io.BytesIO(json.dumps(body).encode()))


def logged(log_dir):
    return [json.loads(line) for path in log_dir.glob("agent-*.jsonl") for line in path.read_text(encoding="utf-8").splitlines()]


# --- The connector ------------------------------------------------------------------------------

def test_it_asks_with_its_token_and_leaves_out_what_isnt_asked():
    connector = Connector()
    api = FinanceAPI(url="http://127.0.0.1:8090/", token="secret", get=connector)
    assert run(api.read("/spending", period="last-month", category="", tag=None)) == MONEY
    assert connector.asked == [("http://127.0.0.1:8090/connect/v1/spending?period=last-month", "secret")]


def test_it_is_only_offered_with_a_token(monkeypatch):
    monkeypatch.delenv("NATRO_FINANCE_TOKEN", raising=False)
    assert Finance.from_env() is None
    monkeypatch.setenv("NATRO_FINANCE_TOKEN", "secret")
    assert Finance.from_env() is not None


def test_a_question_it_cant_answer_says_what_there_is():
    refusal = http_error(400, {"error": "category", "message": "There's no category called yachts.", "choices": ["Groceries", "Rent"]})
    api = FinanceAPI(token="secret", get=Connector(refusal))
    with pytest.raises(ToolError, match="yachts.*Groceries, Rent"):
        run(api.read("/spending", category="yachts"))


@pytest.mark.parametrize("failure, said", [
    (http_error(401, {"error": "token", "message": "That isn't the connector's token.", "choices": []}), "refused"),
    (urllib.error.HTTPError("http://x", 404, "no", {}, io.BytesIO(b"404 page not found")), "refused"),
    (urllib.error.URLError("connection refused"), "isn't answering"),
    (TimeoutError("timed out"), "isn't answering"),
])
def test_the_app_being_down_is_a_message_not_a_failure(failure, said):
    api = FinanceAPI(token="secret", get=Connector(failure))
    with pytest.raises(ToolError, match=said):
        run(api.read("/position"))


def test_whats_new_in_an_answer_reaches_her_with_nothing_changed_here():
    # Answers go through as they come, so a field added to the contract needs nothing here.
    answer = '{"spendable_gel":"10.00","a_field_from_next_year":{"x":[1,2]}}'
    money = finance(None, Connector(answer))
    now = next(tool for tool in money.tools() if tool.name == "finance_now")
    assert run(now.run({})) == answer


def test_each_reading_tool_asks_the_right_question():
    connector = Connector()
    tools = {tool.name: tool for tool in finance(None, connector).analyst_tools()}
    calls = {
        "position": ({}, "/position"),
        "habits": ({"months": 12}, "/habits?months=12"),
        "afford": ({"price": 2500, "by": "2027-03"}, "/afford?price=2500&by=2027-03"),
        "spending": ({"period": "last-month", "category": "Groceries"}, "/spending?period=last-month&category=Groceries"),
        "transactions": ({"min": 44, "max": 46, "sort": "largest"}, "/transactions?min=44&max=46&sort=largest"),
        "purchase": ({"id": 42}, "/transactions/42"),
        "review": ({"from": "2026-10-01", "to": "2026-10-31"}, "/review?from=2026-10-01&to=2026-10-31"),
        "section": ({"what": "goals"}, "/section/goals"),
    }
    assert set(calls) == set(tools)
    for name, (args, path) in calls.items():
        run(tools[name].run(args))
        assert connector.asked[-1][0] == f"http://127.0.0.1:8090/connect/v1{path}", name


def test_a_strange_id_cant_reach_another_path():
    connector = Connector()
    tools = {tool.name: tool for tool in finance(None, connector).analyst_tools()}
    run(tools["purchase"].run({"id": "../../section/goals"}))
    run(tools["section"].run({"what": "../position"}))
    assert connector.asked[0][0].endswith("/transactions/..%2F..%2Fsection%2Fgoals")
    assert connector.asked[1][0].endswith("/section/..%2Fposition")


def test_nothing_the_financist_holds_can_do_anything():
    # What a merchant's name or a web page says can lead to nothing being done. Only reads, by name.
    search = [Tool("web_search", "Search.", {"type": "object", "properties": {}}, lambda args: "", "natro")]
    tools = finance(None, search=search).analyst_tools()
    assert {tool.name for tool in tools} == {"position", "habits", "afford", "spending", "transactions", "purchase",
                                             "review", "section", "web_search"}
    assert not any(tool.confirm or tool.changes for tool in tools)


# --- The financist's own run --------------------------------------------------------------------

def ask(claude, question="can I afford a laptop?", connector=None, memory=None):
    money = finance(claude, connector, memory)
    return Financist(money.analyst_tools(), claude, memory)


def test_it_reads_then_answers():
    connector = Connector()
    claude = FakeClient(message(thinking(), tool_use("t1", "afford", price=2500), stop="tool_use"),
                        message(thinking(), text("Yes. It leaves you 1,700 lari at your next pay.")))
    answer = run(ask(claude, connector=connector).answer("can I afford a 2500 lari laptop?"))
    assert answer == "Yes. It leaves you 1,700 lari at your next pay."
    assert connector.asked[0][0].endswith("/afford?price=2500")
    # The whole answer of the connector goes to the model, and its thinking goes back unchanged.
    second = claude.requests[1]["messages"]
    assert second[1]["content"][0].type == "thinking"
    assert second[2]["content"][0]["content"] == MONEY
    assert second[2]["content"][0]["is_error"] is False


def test_it_runs_on_its_own_model_at_high_effort_with_its_own_instructions():
    claude = FakeClient(message(text("Fine.")))
    memory_dir_free = ask(claude)
    run(memory_dir_free.answer("how am I doing?"))
    request = claude.requests[0]
    assert request["model"] == FINANCIST_MODEL == "claude-opus-5-5"
    assert request["output_config"]["effort"] == "high"
    assert request["fallbacks"] == "default"
    assert "financial planner" in request["system"][0]["text"]
    assert "licensed" in request["system"][0]["text"]
    assert request["cache_control"] == {"type": "ephemeral"}
    # It's told the day, and his question.
    assert "how am I doing?" in request["messages"][0]["content"]
    assert request["messages"][0]["content"].startswith("[")


def test_it_knows_what_natro_remembers(tmp_path):
    memory = Memory(tmp_path / "memory")
    memory.remember("He's saving for a car.", "told")
    claude = FakeClient(message(text("Fine.")))
    run(ask(claude, memory=memory).answer("how am I doing?"))
    assert "He's saving for a car." in claude.requests[0]["system"][1]["text"]


def test_a_failed_read_goes_back_to_it_and_it_can_still_answer():
    refusal = http_error(400, {"error": "category", "message": "No such category.", "choices": ["Groceries"]})
    claude = FakeClient(message(tool_use("t1", "spending", category="food"), stop="tool_use"), message(text("Here's what I found.")))
    assert run(ask(claude, connector=Connector(refusal)).answer("food?")) == "Here's what I found."
    result = claude.requests[1]["messages"][2]["content"][0]
    assert result["is_error"] is True and "Groceries" in result["content"]


def test_a_tool_it_doesnt_have_is_said_not_run():
    claude = FakeClient(message(tool_use("t1", "send_message", to="Nino"), stop="tool_use"), message(text("Sorry.")))
    run(ask(claude).answer("x"))
    assert claude.requests[1]["messages"][2]["content"][0]["is_error"] is True


def test_it_reads_several_things_at_once():
    connector = Connector()
    claude = FakeClient(message(tool_use("t1", "position"), tool_use("t2", "habits"), stop="tool_use"), message(text("Done.")))
    run(ask(claude, connector=connector).answer("x"))
    assert len(connector.asked) == 2
    assert len(claude.requests[1]["messages"][2]["content"]) == 2


def test_it_has_to_answer_at_its_last_step():
    reads = [message(tool_use(f"t{i}", "position"), stop="tool_use") for i in range(MOST_STEPS - 1)]
    claude = FakeClient(*reads, message(text("With what I have: fine.")))
    assert run(ask(claude).answer("x")) == "With what I have: fine."
    assert len(claude.requests) == MOST_STEPS
    assert "tool_choice" not in claude.requests[-2]
    assert claude.requests[-1]["tool_choice"] == {"type": "none"}


def test_when_it_runs_out_of_room_it_says_what_it_has():
    assert run(ask(FakeClient(message(text("So far: no."), stop="max_tokens"))).answer("x")) == "So far: no."
    assert run(ask(FakeClient(message(thinking(), stop="max_tokens"))).answer("x")) == RAN_OUT


def test_a_refusal_is_a_sentence():
    assert "can't help" in run(ask(FakeClient(message(stop="refusal"))).answer("x"))


def test_claude_being_down_is_a_message():
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    with pytest.raises(ToolError, match="couldn't reach Claude"):
        run(ask(FakeClient(anthropic.APIConnectionError(request=request))).answer("x"))


def test_its_price_counts_a_cache_read_at_a_twentieth():
    usage = message(text("x"), input_tokens=1_000_000, output_tokens=0, cache_read=1_000_000).usage
    assert cost(FINANCIST_MODEL, usage) == pytest.approx(4.0 + 0.20)


# --- With Natro ---------------------------------------------------------------------------------

def test_natro_hands_a_question_over_and_its_answer_is_her_reply(log_dir):
    here = FakeClient(message(tool_use("t1", "financist", question="can I afford a 2500 lari laptop?"), stop="tool_use"))
    there = FakeClient(message(tool_use("t2", "afford", price=2500), stop="tool_use"),
                       message(text(f"Yes, as you have {SECRET_AMOUNT} lari spendable.")))
    reply = run(natro(here, there).respond("can I afford a 2500 lari laptop?", Owner()))
    assert reply.text == f"Yes, as you have {SECRET_AMOUNT} lari spendable."
    # Only one call of hers: nothing rewords the planner's answer.
    assert len(here.requests) == 1
    assert here.requests[0]["model"] == SONNET
    # What it cost is on the request.
    assert reply.cost > cost(SONNET, message(text("x")).usage)
    assert reply.tokens["input_tokens"] == 3000


def test_a_month_of_financist_questions_counts_toward_the_budget(log_dir):
    here = FakeClient(message(tool_use("t1", "financist", question="x"), stop="tool_use"))
    agent = natro(here, FakeClient(message(text("Fine."))))
    reply = run(agent.respond("can I afford it?", Owner()))
    assert agent.ledger.spent == pytest.approx(reply.cost)
    # Counted once when the log is summed again after a restart: its own entry has no "cost".
    entries = logged(log_dir)
    assert sum(entry.get("cost", 0) for entry in entries) == pytest.approx(reply.cost, abs=1e-5)
    assert next(entry for entry in entries if entry["kind"] == "financist")["steps"] == 1


def test_a_follow_up_finds_the_answer_in_the_conversation(log_dir):
    here = FakeClient(message(tool_use("t1", "financist", question="x"), stop="tool_use"),
                      message(tool_use("t2", "financist", question="and in March?"), stop="tool_use"))
    there = FakeClient(message(text("Not yet.")), message(text("By March, yes.")))
    agent = natro(here, there)
    run(agent.respond("can I afford a car?", Owner()))
    assert run(agent.respond("and in March?", Owner())).text == "By March, yes."
    assert here.requests[1]["messages"][3] == {"role": "assistant", "content": "Not yet."}


def test_his_money_stays_out_of_the_log_and_the_details(log_dir):
    here = FakeClient(message(tool_use("t1", "finance_now"), stop="tool_use"), message(text("You have plenty.")))
    reply = run(natro(here).respond("what's my balance", Owner()))
    assert reply.tool_calls[0]["result"] == LEFT_OUT and reply.tool_calls[0]["ok"] is True
    assert SECRET_AMOUNT not in (log_dir / next(p.name for p in log_dir.glob("agent-*.jsonl"))).read_text(encoding="utf-8")
    # And she reads it all.
    assert here.requests[1]["messages"][2]["content"][0]["content"] == MONEY


def test_the_financists_answer_and_workings_stay_out_of_the_tool_log(log_dir):
    here = FakeClient(message(tool_use("t1", "financist", question="x"), stop="tool_use"))
    there = FakeClient(message(tool_use("t2", "position"), stop="tool_use"), message(text("Secret verdict.")))
    reply = run(natro(here, there).respond("how am I doing?", Owner()))
    assert reply.tool_calls[0]["result"] == LEFT_OUT
    tool_entries = [entry for entry in logged(log_dir) if entry["kind"] in ("tool", "financist")]
    assert "Secret verdict." not in json.dumps(tool_entries) and SECRET_AMOUNT not in json.dumps(tool_entries)


class LockedPhone(Owner):
    name, label = "phone", "phone"
    locked = True


@pytest.mark.parametrize("tool, args", [("finance_now", {}), ("financist", {"question": "how am I doing?"})])
def test_a_locked_phone_doesnt_get_his_money(log_dir, tool, args):
    connector = Connector()
    there = FakeClient()  # the financist isn't even started
    here = FakeClient(message(tool_use("t1", tool, **args), stop="tool_use"), message(text("Unlock your phone first.")))
    reply = run(natro(here, there, connector).respond("how am I doing?", LockedPhone()))
    assert reply.text == "Unlock your phone first."
    assert reply.tool_calls[0]["ok"] is False and "locked" in reply.tool_calls[0]["result"]
    assert connector.asked == [] and there.requests == []


def test_a_failure_isnt_private_so_it_can_be_looked_into(log_dir):
    here = FakeClient(message(tool_use("t1", "finance_now"), stop="tool_use"), message(text("It's not answering.")))
    reply = run(natro(here, connector=Connector(urllib.error.URLError("refused"))).respond("what's my balance", Owner()))
    assert reply.text == "It's not answering."
    assert reply.tool_calls[0]["ok"] is False and "isn't answering" in reply.tool_calls[0]["result"]


def test_a_failed_financist_leaves_natro_to_say_so(log_dir):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    here = FakeClient(message(tool_use("t1", "financist", question="x"), stop="tool_use"), message(text("I can't reach it now.")))
    reply = run(natro(here, FakeClient(anthropic.APIConnectionError(request=request))).respond("can I afford it?", Owner()))
    assert reply.text == "I can't reach it now."


def test_it_has_longer_than_other_tools_to_think(log_dir):
    money = {tool.name: tool for tool in finance(None).tools()}
    assert money["financist"].seconds > 60
    assert money["finance_now"].seconds == 0


def test_a_tool_past_its_own_time_is_stopped(log_dir):
    async def slow(args):
        await asyncio.sleep(5)
        return "never"
    tool = Tool("slow", "Slow.", {"type": "object", "properties": {}}, slow, "natro", seconds=0.01)
    here = FakeClient(message(tool_use("t1", "slow"), stop="tool_use"), message(text("Too slow.")))
    reply = run(Agent([tool], client=here, budget=15.0).respond("go", Owner()))
    assert "took longer" in reply.tool_calls[0]["result"]


@pytest.mark.parametrize("words", ["how much money do I have", "can I afford a laptop", "what's my balance", "am I over budget",
                                   "how much did I spend", "should I buy it", "who owes me", "what do I pay for subscriptions",
                                   "how are my finances", "what's my net worth", "is that a good purchase", "save up for a car"])
def test_money_goes_to_sonnet(words):
    assert personal_or_risky(words)


def test_buying_a_computer_is_about_money_not_the_computer():
    assert pc_task("open the settings on my laptop") and not pc_task("can I afford a new laptop")


@pytest.mark.parametrize("words", ["play Yellow", "open Chrome", "what's the capital of France", "gelato near me",
                                   "weather in Tbilisi", "balancing a chair"])
def test_other_words_dont(words):
    assert not personal_or_risky(words)

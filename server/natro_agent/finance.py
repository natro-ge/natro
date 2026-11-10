"""His money, from his Finance Planner, a program of its own on this server (its docs/CONNECT.md).

Finance Planner is the one who knows his money, and stays his to use without Natro. Natro only reads it, through its
connector: a contract that program keeps, on a port only this server can reach, with a token of its own. Answers go to
the model as they come, with nothing here reading them, so what the connector adds reaches her with no change here.

finance_now is a quick look, for her. financist hands a question to a specialist (financist.py), who reads with the
rest of the tools here and thinks. None of it is kept, and all of it is private (Tool.private), so it stays out of the
log and the details under a reply. Offered only with NATRO_FINANCE_TOKEN set.
"""
import asyncio
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from natro_agent.financist import Financist
from natro_agent.tool_calls import ASKER
from natro_agent.tools import Tool, ToolError

DEFAULT_URL = "http://127.0.0.1:8090"
PREFIX = "/connect/v1"
TIMEOUT_SECONDS = 20
# How long the financist may think: longer than any other tool, as it reads and reasons.
FINANCIST_SECONDS = 240

_ANY_PERIOD = ("period: 7d, 30d, this-month, last-month, 3m, 6m, 12m, 24m, ytd, last-year, all, a month (2026-07) or a "
               "year (2026). Or from and to, days, both included.")


def get(url, token):
    """GET a text answer (blocking)."""
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "User-Agent": "Natro"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8")


def refusal_text(body):
    """What a 4xx from the connector says, with what it would have taken, or None if it isn't from the connector."""
    try:
        answer = json.loads(body)
        message, choices = answer["message"], answer.get("choices") or []
    except (ValueError, KeyError, TypeError):
        return None
    return f"{message} {', '.join(choices)}".strip()


class FinanceAPI:
    """The connector. url and token: NATRO_FINANCE_URL and NATRO_FINANCE_TOKEN. get: for tests."""

    def __init__(self, url=None, token=None, get=get):
        self.url = (url or os.environ.get("NATRO_FINANCE_URL") or DEFAULT_URL).rstrip("/")
        self.token = token or os.environ.get("NATRO_FINANCE_TOKEN")
        self._get = get

    @property
    def available(self):
        return bool(self.token)

    async def read(self, path, **params):
        """An answer of the connector, as text. Empty parameters aren't sent."""
        query = urllib.parse.urlencode({key: value for key, value in params.items() if value not in (None, "")})
        url = f"{self.url}{PREFIX}{path}" + (f"?{query}" if query else "")
        try:
            return await asyncio.to_thread(self._get, url, self.token)
        except urllib.error.HTTPError as e:
            said = refusal_text(e.read().decode("utf-8", "replace"))
            if said and e.code != 401:
                raise ToolError(said)
            raise ToolError(f"The finance app refused Natro ({e.code}): its token may have changed.")
        except OSError as e:  # the app is restarting for a deploy, or down
            raise ToolError(f"The finance app isn't answering right now: {e}")


def not_behind_a_lock():
    """His money is behind a password and a fingerprint in Finance Planner. Anyone can say "OK Natro" to his phone on a
    table, so a request from a locked phone doesn't get it."""
    if getattr(ASKER.get(), "locked", False):
        raise ToolError("His phone is locked, so his money stays hidden: he can unlock it and ask again.")


def _text(description):
    return {"type": "string", "description": description}


def _number(description):
    return {"type": "number", "description": description}


_PERIOD_FIELDS = {"period": _text(_ANY_PERIOD), "from": _text("First day, 2026-07-01."), "to": _text("Last day, 2026-07-31.")}
_SCOPE_FIELDS = {"category": _text("A category's name, or a part of it. A wrong one lists them."),
                 "tag": _text("A tag's name."), "merchant": _text("A merchant's name, or a part of it.")}


class Finance:
    """memory: what Natro remembers, for the financist. search: Natro's web_search tools, for prices. client: Claude's
    (set once the agent has one)."""

    def __init__(self, api, memory=None, search=(), client=None):
        self.api = api
        self.memory = memory
        self.search = list(search)
        self.client = client

    @classmethod
    def from_env(cls, memory=None, search=()):
        """Finance, or None with no token set."""
        api = FinanceAPI()
        return cls(api, memory, search) if api.available else None

    def analyst_tools(self):
        """What the financist reads with: everything here only reads, so what a merchant's name or a web page says can
        lead to nothing being done, whatever it says."""
        def reader(path, *names):
            async def run(args):
                return await self.api.read(path, **{name: args.get(name) for name in names})
            return run

        async def purchase(args):
            return await self.api.read(f"/transactions/{urllib.parse.quote(str(args.get('id', '')), safe='')}")

        async def section(args):
            return await self.api.read(f"/section/{urllib.parse.quote(str(args.get('what', '')), safe='')}")

        def tool(name, description, properties, run, required=()):
            schema = {"type": "object", "properties": properties}
            if required:
                schema["required"] = list(required)
            return Tool(name, description, schema, run, "finance")

        return [
            tool("position", "Where he stands today: what's spendable and set aside, debts, net worth, each account; his "
                 "salary and when it's next; the stretch to it (bills due, the typical day's spending, what's left free); "
                 "this month so far; what's typical; budgets near their limit.", {}, reader("/position")),
            tool("habits", "His habits over full months: each month's income, spending and net, what's typical, each "
                 "category (this month, last, its average, its budget), the biggest merchants. For where money could "
                 "come from.", {"months": {"type": "integer", "description": "How many full months, 1 to 36 (default 6)."}},
                 reader("/habits", "months")),
            tool("afford", "A purchase against his money: bought today (what's left spendable, what's left at his next "
                 "pay, months of spending covered), or saved for (how long at his typical net, with his goals kept up "
                 "or not), and by a date.",
                 {"price": _number("What it costs."), "currency": _text("GEL unless named: USD, EUR."),
                  "by": _text("A date he wants it by: 2027-03-31, or a month: 2027-03.")},
                 reader("/afford", "price", "currency", "by"), required=("price",)),
            tool("spending", "Totals for a stretch of time, for all of it or one category, tag or merchant: spent, income, "
                 "net, per month, by category, by month, the merchants, the biggest payments.",
                 {**_PERIOD_FIELDS, **_SCOPE_FIELDS}, reader("/spending", "period", "from", "to", "category", "tag", "merchant")),
            tool("transactions", "The payments themselves, with their count and sum. For finding one (\"that 45 lari\": "
                 "min 44, max 46) or listing some. Each has an id, for purchase. Default: the last 30 days, newest first.",
                 {**_PERIOD_FIELDS, **_SCOPE_FIELDS, "query": _text("Words in the merchant, description, name or note."),
                  "kind": _text("spending or income."), "status": _text("pending, confirmed, missing, declined, not_in_statement."),
                  "min": _number("Smallest amount, in lari, either sign."), "max": _number("Largest amount, in lari, either sign."),
                  "sort": _text("newest or largest."), "limit": {"type": "integer", "description": "How many, up to 100 (default 25)."}},
                 reader("/transactions", "period", "from", "to", "category", "tag", "merchant", "query", "kind", "status",
                        "min", "max", "sort", "limit")),
            tool("purchase", "One payment with what it's judged against: the merchant's history, the category's month and "
                 "budget, its share of that month, the rest of that day. For rating a decision. Find its id with "
                 "transactions.", {"id": {"type": "integer", "description": "The payment's id."}}, purchase, required=("id",)),
            tool("review", "A stretch of time gone over: totals against a typical stretch that long, each category against "
                 "its usual and budget, the biggest payments, those far above the usual at their merchant, merchants new "
                 "in it, new regular charges. For rating how he's done. Default: this month.", _PERIOD_FIELDS,
                 reader("/review", "period", "from", "to")),
            tool("section", "Another page of his: budgets, subscriptions, goals, owed (who owes him), networth (month ends), "
                 "categories, tags. A wrong name lists what there is.", {"what": _text("Which one.")}, section, required=("what",)),
        ] + self.search

    def tools(self):
        """What Natro herself is offered: a quick look, and the specialist."""
        async def now(args):
            not_behind_a_lock()
            return await self.api.read("/position")

        async def financist(args):
            not_behind_a_lock()
            question = " ".join(str(args.get("question") or "").split())
            if not question:
                raise ToolError("Say what he asked.")
            return await Financist(self.analyst_tools(), self.client, self.memory).answer(question)

        return [
            Tool("finance_now",
                 "A quick look at his money, from his Finance Planner (TBC, Bank of Georgia, cash): balances, what's "
                 "spendable and set aside, net worth, this month's spending against a typical month, budgets near their "
                 "limit, his next salary and the bills before it. For when he only wants a number: \"what's my balance?\", "
                 "\"how much have I spent this month?\". Amounts are in lari unless a field names another currency.",
                 {"type": "object", "properties": {}}, now, "natro", private=True),
            Tool("financist",
                 "His financial planner, a specialist who goes through his real spending, income, budgets, goals and debts, "
                 "thinks it over, and answers. For anything that takes judgment about his money: can he afford something, "
                 "how to plan or save for a purchase, rating a purchase or how he's been spending, where the money goes, "
                 "what to cut, whether a subscription is worth it. It takes a minute or so, and its answer reaches him as "
                 "it is, with no more from you.",
                 {"type": "object", "properties": {"question": _text(
                     "What he asked, in his words, with everything that matters from the conversation: the item, its "
                     "price, the date, what he's weighing, and for a follow-up what the planner already told him.")},
                  "required": ["question"]},
                 financist, "natro", private=True, whole=True, seconds=FINANCIST_SECONDS),
        ]

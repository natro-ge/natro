"""His Edge through Playwright (faked): what each action becomes, and what's refused."""
import pytest

from natro_pc import browser
from natro_pc.browser import Browser, read_only, tools
from natro_pc.tools import ToolError

PAGE = """### Page
- Page URL: {url}
- Page Title: Inbox
### Snapshot
```yaml
- generic [active] [ref=e1]:
  - searchbox "Search mail" [ref=e3]
  - textbox "Message" [ref=e4]
  - textbox "Password" [ref=e5]
  - button "Send" [ref=e6] [cursor=pointer]
  - link "Inbox (3)" [ref=e7] [cursor=pointer]:
    - /url: /inbox
  - combobox "Sort by" [ref=e8]
  - button "Say \\"hi\\"" [ref=e9]
```"""


class FakePlaywright:
    """Answers every call with the page at url; records the calls."""

    def __init__(self, url="https://mail.example.com/"):
        self.url = url
        self.calls = []

    def call(self, name, args):
        self.calls.append((name, args))
        if name == "browser_tabs":
            return "### Result\n- 0: (current) [Inbox](https://mail.example.com/)"
        return PAGE.format(url=self.url)


def make(url="https://mail.example.com/"):
    fake = FakePlaywright(url)
    edge = Browser(fake)
    edge.page()
    fake.calls.clear()
    return edge, fake


def test_open_reads_the_page_and_its_elements():
    edge, fake = make()
    said = edge.open("mail.example.com")
    assert fake.calls == [("browser_navigate", {"url": "https://mail.example.com"}), ("browser_snapshot", {})]
    assert said.startswith("### Page\n- Page URL: https://mail.example.com/")
    assert edge.elements["e6"] == ("button", "Send") and edge.elements["e9"] == ("button", 'Say "hi"')
    edge.open("https://x.example/a", new_tab=True)
    assert fake.calls[2] == ("browser_tabs", {"action": "new"})
    with pytest.raises(ToolError, match="Give a web address"):
        edge.open("the news")


def test_actions_become_playwrights_in_order():
    edge, fake = make()
    said = edge.act([{"do": "type", "ref": "e3", "text": "flight", "submit": True},
                     {"do": "click", "ref": "e7"}, {"do": "select", "ref": "e8", "text": "Newest"},
                     {"do": "key", "text": "page down"}, {"do": "back"}])
    assert said.startswith("Done.\n### Page")
    assert [call for call in fake.calls if call[0] != "browser_snapshot"] == [
        ("browser_type", {"element": 'searchbox Search mail', "target": "e3", "text": "flight", "submit": True}),
        ("browser_click", {"element": "link Inbox (3)", "target": "e7"}),
        ("browser_select_option", {"element": "combobox Sort by", "target": "e8", "values": ["Newest"]}),
        ("browser_press_key", {"key": "PageDown"}),
        ("browser_navigate_back", {})]
    assert len(fake.calls) == 10  # each change is followed by reading the page


def test_what_sends_buys_or_deletes_asks_first():
    edge, fake = make()
    with pytest.raises(ToolError, match="e6 \\(button Send\\) looks like it sends"):
        edge.act([{"do": "click", "ref": "e7"}, {"do": "click", "ref": "e6"}])
    assert fake.calls == []  # nothing done: every action is checked first
    with pytest.raises(ToolError, match="Enter in textbox Message would likely send it"):
        edge.act([{"do": "type", "ref": "e4", "text": "hi", "submit": True}])
    with pytest.raises(ToolError, match="Pressing Enter here"):
        edge.act([{"do": "type", "ref": "e4", "text": "hi"}, {"do": "key", "text": "enter"}])
    edge.act([{"do": "type", "ref": "e3", "text": "flight"}, {"do": "key", "text": "Enter"}])  # in a search box
    with pytest.raises(ToolError, match="Pressing Delete"):
        edge.act([{"do": "key", "text": "Delete"}])
    edge.act([{"do": "type", "ref": "e4", "text": "hi"}, {"do": "click", "ref": "e6"}], risky=True)
    assert fake.calls[-2][0] == "browser_click" and fake.calls[-2][1]["target"] == "e6"


def test_never_a_password_and_only_known_elements():
    edge, _ = make()
    with pytest.raises(ToolError, match="never types passwords"):
        edge.act([{"do": "type", "ref": "e5", "text": "hunter2"}], risky=True)
    with pytest.raises(ToolError, match="no element e99"):
        edge.act([{"do": "click", "ref": "e99"}])
    with pytest.raises(ToolError, match="Unknown action 'scroll'"):
        edge.act([{"do": "scroll"}])


def test_bank_and_payment_sites_are_read_only(monkeypatch):
    edge, fake = make("https://ibank.bog.ge/accounts")
    for risky in (False, True):
        with pytest.raises(ToolError, match="only reads here, and never clicks or types, even with his yes"):
            edge.act([{"do": "click", "ref": "e7"}], risky=risky)
    edge.act([{"do": "back"}])  # leaving is fine
    assert "(A bank or payment site: you can only read here.)" in Browser(FakePlaywright("https://www.paypal.com/")).open(
        "paypal.com")
    assert [read_only(url) for url in ("https://tbconline.ge/x", "https://www.libertybank.ge/", "https://wise.com/",
                                       "https://mail.google.com/", "https://www.bookmyshow.com/")] == [
        True, True, True, False, False]
    monkeypatch.setenv("NATRO_READ_ONLY_SITES", "myshop.ge")
    assert read_only("https://pay.myshop.ge/") and not read_only("https://notmyshop.ge/")


def test_tabs():
    edge, fake = make()
    assert "(current) [Inbox]" in edge.tabs("list")
    edge.tabs("select", 2)
    assert fake.calls[-1] == ("browser_tabs", {"action": "select", "index": 2})
    with pytest.raises(ToolError, match="needs the tab's index"):
        edge.tabs("close")


def test_offered_only_with_the_extensions_token(monkeypatch):
    monkeypatch.delenv(browser.TOKEN, raising=False)
    assert tools() == []
    offered = {tool.name: tool for tool in tools(Browser(FakePlaywright()))}
    assert set(offered) == {"browser_open", "browser_page", "browser_act", "browser_act_risky", "browser_tabs"}
    assert all(tool.personal for tool in offered.values())
    assert [name for name, tool in offered.items() if tool.confirm] == ["browser_act_risky"]

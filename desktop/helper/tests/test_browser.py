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
    # Without it only the brain's send_message is there: Natro's own Edge needs no extension.
    assert [tool.name for tool in tools(sender=Browser(FakePlaywright()))] == ["send_message"]
    offered = {tool.name: tool for tool in tools(Browser(FakePlaywright()), Browser(FakePlaywright()))
               if not tool.hidden}
    assert set(offered) == {"browser_open", "browser_page", "browser_act", "browser_act_risky", "browser_tabs"}
    assert all(tool.personal for tool in offered.values())
    assert [name for name, tool in offered.items() if tool.confirm] == ["browser_act_risky"]


CHAT = """### Page
- Page URL: {url}
### Snapshot
```yaml
- main [ref=e1]:
  - link "{who}" [ref=e2] [cursor=pointer]:
    - /url: /nino.k/
  - text: nino.k
{messages}
  - textbox "Message..." [ref=e9]{typed}
{send}```"""


class FakeChat:
    """A chat in Instagram's web page: what's typed in its box goes on Send or Enter, and shows in the chat."""

    def __init__(self, who="Nino Kapanadze"):
        self.who, self.typed, self.sent, self.calls = who, "", [], []

    def call(self, name, args):
        self.calls.append((name, args))
        if name == "browser_type":
            self.typed = args["text"]
        if name in ("browser_click", "browser_press_key") and self.typed:
            self.sent.append(self.typed)
            self.typed = ""
        url = next((a["url"] for n, a in reversed(self.calls) if n == "browser_navigate"), "")
        return CHAT.format(url=url, who=self.who, typed=f": {self.typed}" if self.typed else "",
                           messages="\n".join(f"  - generic [ref=m{i}]: {text}" for i, text in enumerate(self.sent)),
                           send='  - button "Send" [ref=e10]\n' if self.typed else "")


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setattr(browser, "CHAT_SECONDS", 0)
    monkeypatch.setattr(browser, "SEEN_SECONDS", 0)
    monkeypatch.setattr(browser.time, "sleep", lambda seconds: None)


def test_a_message_from_edge_when_the_phone_is_offline(quick):
    chat = FakeChat()
    said = Browser(chat).send_message("instagram", "Running late", name="Nino Kapanadze",
                                      chat="thread_shortcut_1234567890_3402", username="nino.k")
    assert said == "Sent to Nino Kapanadze on Instagram."
    assert [call for call in chat.calls if call[0] != "browser_snapshot"] == [
        ("browser_navigate", {"url": "https://www.instagram.com/direct/t/3402/"}),
        ("browser_type", {"element": "textbox Message...", "target": "e9", "text": "Running late", "submit": False}),
        ("browser_click", {"element": "button Send", "target": "e10"})]
    assert chat.sent == ["Running late"]
    assert browser.chat_urls("messenger", facebook="100012345678901", username="andria.t") == [
        "https://www.messenger.com/t/100012345678901", "https://www.messenger.com/t/andria.t"]
    assert browser.chat_urls("instagram", username="nino.k") == ["https://ig.me/m/nino.k"]
    assert browser.chat_urls("messenger", chat="thread_shortcut_GROUP:42") == ["https://www.messenger.com/t/42"]
    assert browser.chat_urls("messenger", chat="thread_shortcut_ADVANCED_CRYPTO_ONE_TO_ONE:7", facebook="1") == [
        "https://www.messenger.com/t/1", "https://www.messenger.com/e2ee/t/7"]


def test_someone_elses_chat_gets_nothing(quick):
    chat = FakeChat(who="Giorgi")
    with pytest.raises(ToolError, match="didn't open in Edge, or isn't plainly theirs, so nothing was sent"):
        Browser(chat).send_message("instagram", "Hi", name="Nino Kapanadze", chat="3402")
    assert not any(name == "browser_type" for name, _ in chat.calls) and chat.sent == []
    with pytest.raises(ToolError, match="Instagram and Messenger only"):
        Browser(chat).send_message("whatsapp", "Hi", name="Nino")


def test_sending_is_in_natros_own_hidden_edge_closed_after():
    chat = FakeChat()
    send = next(tool for tool in tools(Browser(FakePlaywright()), Browser(chat)) if tool.name == "send_message")
    send.run(app="instagram", text="Hi", name="Nino Kapanadze", chat="thread_shortcut_1_3402")
    assert chat.sent == ["Hi"] and chat.calls[-1] == ("browser_close", {})
    hidden = browser.hidden_edge(browser.SEND_PROFILE).args
    assert hidden == ["--browser", "msedge", "--headless", "--user-data-dir", str(browser.SEND_PROFILE)]
    assert "--extension" in browser.his_edge("token").args and "--headless" not in browser.his_edge("token").args


def test_edges_send_message_is_the_brains_alone():
    offered = {tool.name: tool for tool in tools(Browser(FakePlaywright()), Browser(FakePlaywright()))}
    spec = offered["send_message"].spec()
    assert spec["hidden"] and spec["confirm"] and spec["personal"]


def test_a_pin_page_stops_it_and_is_never_the_box(quick):
    class PinPage(FakeChat):
        def call(self, name, args):
            self.calls.append((name, args))
            return ('### Page\n- Page URL: https://www.messenger.com/e2ee/t/7\n### Snapshot\n```yaml\n'
                    '- heading "Enter your PIN" [ref=e1]\n- textbox "PIN" [ref=e2]\n```')

    page = PinPage()
    with pytest.raises(ToolError, match="isn't signed in to Messenger .* Nothing was sent"):
        Browser(page).send_message("messenger", "Hi", name="Andria Beridze", facebook="100012345678901")
    assert not any(name == "browser_type" for name, _ in page.calls)

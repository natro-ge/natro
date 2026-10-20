"""Whole web pages: their text, reading on, other kinds of files, and never a private address."""
import urllib.error
import urllib.request

import pytest

from conftest import run
from natro_agent import webpage
from natro_agent.tools import ToolError
from natro_agent.webpage import READ_CHARS, Redirects, WebPage, checked, html_text

ARTICLE = ("<html><head><title>Kin-Za &amp; friends</title><style>p {color: red}</style></head><body>"
           "<nav><a href='/'>Home</a> <a href='/menu'>Menu</a></nav>"
           "<article><h1>The best khachapuri in Berlin</h1><p>Kin-Za is in Mitte.</p>"
           "<ul><li>Adjarian</li><li>Imeruli</li></ul>"
           "<table><tr><th>Dish</th><th>Price</th></tr><tr><td>Lobio</td><td>12 €</td></tr></table>"
           f"<p>{'A long paragraph about Georgian food. ' * 20}</p></article>"
           "<script>track()</script><footer>© 2026 Some site</footer></body></html>")


@pytest.fixture
def anywhere(monkeypatch):
    """Every host is public (no real name lookups in tests)."""
    monkeypatch.setattr(webpage, "public", lambda host: True)


def page(content, content_type="text/html", charset="utf-8", final="https://example.com/kinza"):
    return lambda url: (final, content_type, charset, content if isinstance(content, bytes) else content.encode())


def read(reader, url="https://example.com/kinza", start=0):
    return run(reader.read(url, start))


# --- Text from a page ---------------------------------------------------------------------------

def test_a_page_is_its_article_without_menus_scripts_or_footers():
    title, text = html_text(ARTICLE.encode(), "utf-8")
    assert title == "Kin-Za & friends"
    assert text.startswith("# The best khachapuri in Berlin\n\nKin-Za is in Mitte.\n\n- Adjarian\n- Imeruli")
    assert "Dish | Price" in text and "Lobio | 12 €" in text
    assert "Home" not in text and "track()" not in text and "©" not in text and "color" not in text


def test_without_an_article_the_whole_page_and_its_charset():
    markup = ('<html><head><meta charset="windows-1251"><title>Новости</title></head>'
              "<body><div>Привет из Тбилиси</div><p>Second</p></body></html>").encode("windows-1251")
    title, text = html_text(markup, None)
    assert title == "Новости" and text == "Привет из Тбилиси\n\nSecond"


def test_a_page_and_reading_on(anywhere):
    long = "<html><body>" + "".join(f"<p>Line {i} {'x' * 100}</p>" for i in range(300)) + "</body></html>"
    reader = WebPage(get=page(long))
    first = read(reader)
    assert first.startswith("https://example.com/kinza\n\nLine 0 ")
    assert first.endswith(f"[cut at character {READ_CHARS} of {len(html_text(long.encode(), 'utf-8')[1])}: "
                          f"read again with start={READ_CHARS} for more]")
    assert read(reader, start=READ_CHARS).startswith("https://example.com/kinza\n\n")
    assert "There's no more text" in read(reader, start=10**6)
    assert read(WebPage(get=page(ARTICLE))).startswith("Kin-Za & friends (https://example.com/kinza)\n\n# The best")


def test_other_kinds_of_files(anywhere, monkeypatch):
    assert read(WebPage(get=page('{"rate": 2.7}', "application/json"))).endswith('{"rate": 2.7}')
    assert read(WebPage(get=page("plain words", "text/plain"))).endswith("plain words")
    from natro_agent import google_tools
    monkeypatch.setattr(google_tools, "pdf_text", lambda data: "The PDF's text.")
    assert read(WebPage(get=page(b"%PDF-1.7", "application/pdf"))).endswith("The PDF's text.")
    with pytest.raises(ToolError, match="a video/mp4 file, not a page"):
        read(WebPage(get=page(b"....", "video/mp4")))
    with pytest.raises(ToolError, match="no text Natro can read"):
        read(WebPage(get=page("<html><body><script>app()</script></body></html>")))


def test_failures_say_why(anywhere):
    def missing(url):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    def offline(url):
        raise urllib.error.URLError("timed out")

    with pytest.raises(ToolError, match="answered 404"):
        read(WebPage(get=missing))
    with pytest.raises(ToolError, match="Couldn't open the page: timed out"):
        read(WebPage(get=offline))


# --- Only the public internet -------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8384/", "http://localhost/", "http://100.101.107.5:8700/health", "http://10.0.0.1/",
    "http://192.168.1.1/", "http://169.254.169.254/latest/meta-data/", "http://[::1]/"])
def test_private_addresses_are_refused(url):
    with pytest.raises(ToolError, match="isn't on the public internet"):
        checked(url)


def test_only_web_links(anywhere):
    for url in ["file:///etc/passwd", "ftp://example.com/a", "example.com", ""]:
        with pytest.raises(ToolError, match="whole web address"):
            checked(url)
    assert checked("https://example.com/a?b=1") == "https://example.com/a?b=1"


def test_a_name_that_leads_inside_is_refused(monkeypatch):
    monkeypatch.setattr(webpage.socket, "getaddrinfo", lambda host, port: [(0, 0, 0, "", ("10.1.2.3", 0))])
    with pytest.raises(ToolError, match="isn't on the public internet"):
        checked("https://sneaky.example.com/")


def test_a_redirect_inside_is_refused():
    request = urllib.request.Request("https://example.com/")
    with pytest.raises(ToolError, match="isn't on the public internet"):
        Redirects().redirect_request(request, None, 302, "Found", {}, "http://127.0.0.1:8384/rest/config")


def test_the_tool_is_not_personal():
    tool, = WebPage().tools()
    assert tool.name == "web_page" and not tool.personal and not tool.confirm

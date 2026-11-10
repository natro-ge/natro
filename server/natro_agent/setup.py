"""Builds Natro the same way for the service and the typed chat: her own tools and MCP servers."""
from natro_agent.agent import Agent
from natro_agent.briefing import Briefing
from natro_agent.config import MCP_CONFIG, ROOT
from natro_agent.finance import Finance
from natro_agent.google_tools import TOKEN_FILE, Google, GoogleAPI
from natro_agent.history import History
from natro_agent.memory import Memory
from natro_agent.notes import Notes
from natro_agent.places import Places
from natro_agent.planning import Planning
from natro_agent.spotify import TOKEN_FILE as SPOTIFY_TOKEN_FILE, Spotify, SpotifyAPI
from natro_agent.travel import Travel
from natro_agent.weather import Weather
from natro_agent.webpage import WebPage
from natro_agent.websearch import WebSearch


def google_account():
    """Calendar, Tasks, Gmail, Drive and Docs, once the owner has signed in (natro_agent.google_signin); else None."""
    return Google(GoogleAPI.from_token()) if TOKEN_FILE.exists() else None


async def build_agent(servers, model=None, reminders=None, google=None, travel=None, places=None, watchers=None):
    """servers: an MCPServers, started here (close it when done). reminders: the server's, for the briefing.
    google: the server's google_account() (else one is made here). travel: the server's Travel (likewise).
    places: the server's Places (likewise, known by name but with no tools here). watchers: the server's, for when to
    leave in the briefing."""
    places = places or Places(ROOT / "places.json", {})
    memory = Memory(ROOT / "memory", places)
    weather = Weather()
    search = WebSearch().tools()
    tools = (memory.tools() + Notes(ROOT / "notes").tools() + search + WebPage().tools()
             + weather.tools() + History().tools() + (travel or Travel(places=places)).tools())
    google = google or google_account()
    if google:
        tools += google.tools() + Planning(google).tools()
    tools += Briefing(ROOT / "briefing.json", google, weather, reminders,
                      watchers.first_leaving if watchers else None).tools()
    # Spotify once the owner has signed in (natro_agent.spotify_signin).
    spotify = Spotify(SpotifyAPI.from_token()) if SPOTIFY_TOKEN_FILE.exists() else None
    if spotify:
        tools += spotify.tools()
    # His money, once this server's Finance Planner is connected (finance.py).
    finance = Finance.from_env(memory, search)
    if finance:
        tools += finance.tools()
    tools += await servers.start(MCP_CONFIG)
    # A model given here answers everything (for trying one out); otherwise Haiku, with Sonnet for personal or
    # risky requests.
    options = {"model": model, "cheap_model": model} if model else {}
    agent = Agent(tools, memory=memory, **options)
    if spotify:
        spotify.online = agent.devices
    if finance:
        finance.client = agent.client
    return agent

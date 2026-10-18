"""Builds Natro the same way for the service and the typed chat: her own tools, MCP servers, Gemini."""
import os

from natro_agent.agent import Agent
from natro_agent.briefing import Briefing
from natro_agent.config import MCP_CONFIG, ROOT
from natro_agent.gemini import Gemini
from natro_agent.google_tools import TOKEN_FILE, Google, GoogleAPI
from natro_agent.history import History
from natro_agent.memory import Memory
from natro_agent.notes import Notes
from natro_agent.spotify import TOKEN_FILE as SPOTIFY_TOKEN_FILE, Spotify, SpotifyAPI
from natro_agent.travel import Travel
from natro_agent.weather import Weather
from natro_agent.webpage import WebPage
from natro_agent.websearch import WebSearch


def google_account():
    """Calendar, Tasks, Gmail, Drive and Docs, once the owner has signed in (natro_agent.google_signin); else None."""
    return Google(GoogleAPI.from_token()) if TOKEN_FILE.exists() else None


async def build_agent(servers, model=None, reminders=None, google=None, travel=None):
    """servers: an MCPServers, started here (close it when done). reminders: the server's, for the briefing.
    google: the server's google_account() (else one is made here). travel: the server's Travel (likewise)."""
    memory = Memory(ROOT / "memory")
    weather = Weather()
    tools = (memory.tools() + Notes(ROOT / "notes").tools() + WebSearch().tools() + WebPage().tools()
             + weather.tools() + History().tools() + (travel or Travel()).tools())
    google = google or google_account()
    if google:
        tools += google.tools()
    tools += Briefing(ROOT / "briefing.json", google, weather, reminders).tools()
    # Spotify once the owner has signed in (natro_agent.spotify_signin).
    if SPOTIFY_TOKEN_FILE.exists():
        tools += Spotify(SpotifyAPI.from_token()).tools()
    tools += await servers.start(MCP_CONFIG)
    # The free Gemini tier only when a key is set; otherwise everything goes to Claude.
    gemini = Gemini() if os.environ.get("GEMINI_API_KEY") else None
    # A model given here answers everything (for trying one out); otherwise Sonnet, with Haiku for PC tasks.
    options = {"model": model, "pc_model": model} if model else {}
    return Agent(tools, memory=memory, gemini=gemini, **options)

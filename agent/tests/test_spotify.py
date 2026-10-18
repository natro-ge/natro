import asyncio
import json
from datetime import date, timedelta

import pytest

from conftest import run
from natro_agent.spotify import API, MAX_ADD, SIGN_IN_AGAIN, TOKEN_URL, Spotify, SpotifyAPI, spotify_uri, studio_version
from natro_agent.tools import ToolError

PC = {"id": "d-pc", "name": "TUXA-ASUS", "type": "Computer", "is_active": False, "volume_percent": 50}
PHONE = {"id": "d-phone", "name": "Nothing Phone (2)", "type": "Smartphone", "is_active": False}
YELLOW = {"type": "track", "name": "Yellow", "uri": "spotify:track:y1", "artists": [{"name": "Coldplay"}],
          "duration_ms": 266000, "album": {"uri": "spotify:album:parachutes"}}
FIX_YOU = {"type": "track", "name": "Fix You", "uri": "spotify:track:f1", "artists": [{"name": "Coldplay"}]}


class FakeResponse:
    def __init__(self, status_code, data=None, headers=None):
        self.status_code = status_code
        self._data = data
        self.content = b"x" if data is not None else b""
        self.text = str(data)
        self.headers = headers or {}

    def json(self):
        return self._data


class FakeSpotify:
    """Answers (method, path) with scripted responses (a list is used up in order, a function gets the request's
    params); records every request."""

    def __init__(self, answers):
        self.answers = answers
        self.requests = []

    def request(self, method, url, params=None, json=None, data=None, headers=None, timeout=None):
        path = url.removeprefix(API) if url != TOKEN_URL else "token"
        self.requests.append({"method": method, "path": path, "params": params, "json": json, "data": data,
                              "headers": headers})
        answer = self.answers.get((method, path), (204, None))
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if callable(answer):
            answer = answer(params or {})
        return FakeResponse(*answer)

    def sent(self, method, path):
        return [r for r in self.requests if r["method"] == method and r["path"] == path]


def token(**changes):
    return {"client_id": "cid", "access_token": "a1", "refresh_token": "r1", "expires_at": 10_000,
            "signed_in": date.today().isoformat(), **changes}


def spotify(answers, **token_changes):
    fake = FakeSpotify(answers)
    return Spotify(SpotifyAPI(fake, token(**token_changes), clock=lambda: 1_000), start_seconds=0), fake


def no_active_device():
    return 404, {"error": {"status": 404, "message": "Player command failed: No active device found",
                           "reason": "NO_ACTIVE_DEVICE"}}


def test_expired_token_is_refreshed_and_saved(tmp_path):
    path = tmp_path / "spotify.json"
    fake = FakeSpotify({("POST", "token"): (200, {"access_token": "a2", "expires_in": 3600, "refresh_token": "r2"}),
                        ("GET", "/me"): (200, {"id": "tuxa"})})
    api = SpotifyAPI(fake, token(expires_at=900), path, clock=lambda: 1_000)
    assert run(api.call("GET", "/me")) == {"id": "tuxa"}
    assert fake.requests[0]["data"] == {"grant_type": "refresh_token", "refresh_token": "r1", "client_id": "cid"}
    assert fake.requests[1]["headers"] == {"Authorization": "Bearer a2"}
    saved = json.loads(path.read_text())
    assert saved["access_token"] == "a2" and saved["refresh_token"] == "r2" and saved["expires_at"] == 4_600


def test_token_refreshed_by_another_process_is_used(tmp_path):
    path = tmp_path / "spotify.json"
    path.write_text(json.dumps(token(access_token="a9", refresh_token="r9", expires_at=5_000)))
    fake = FakeSpotify({("GET", "/me"): (200, {"id": "tuxa"})})
    api = SpotifyAPI(fake, token(expires_at=900), path, clock=lambda: 1_000)
    run(api.call("GET", "/me"))
    assert [r["path"] for r in fake.requests] == ["/me"] and fake.requests[0]["headers"]["Authorization"] == "Bearer a9"
    assert api.token["refresh_token"] == "r9"


def test_rejected_access_token_is_renewed_once():
    s, fake = spotify({("GET", "/me"): [(401, {"error": {"message": "expired"}}), (200, {"id": "tuxa"})],
                       ("POST", "token"): (200, {"access_token": "a2", "expires_in": 3600})})
    assert run(s.api.call("GET", "/me")) == {"id": "tuxa"}
    assert [r["path"] for r in fake.requests] == ["/me", "token", "/me"]
    assert s.api.token["refresh_token"] == "r1"  # kept when Spotify sends no new one


def test_revoked_sign_in_says_how_to_fix_it():
    s, _ = spotify({("POST", "token"): (400, {"error": "invalid_grant"})}, expires_at=0)
    with pytest.raises(ToolError, match="spotify_signin"):
        run(s.api.call("GET", "/me"))
    assert SIGN_IN_AGAIN.startswith("The Spotify sign-in")


def test_play_a_song_by_search():
    found = {"tracks": {"items": [YELLOW, None, FIX_YOU]}}
    s, fake = spotify({("GET", "/search"): (200, found)})
    text = run(s.play("yellow coldplay"))
    assert text == "Playing Yellow by Coldplay.\nOther matches: Fix You by Coldplay [spotify:track:f1]"
    assert fake.requests[0]["params"] == {"q": "yellow coldplay", "type": "track", "limit": 5}
    play = fake.sent("PUT", "/me/player/play")[0]
    # In its album, so "next" and the rest of the album work.
    assert play["json"] == {"context_uri": "spotify:album:parachutes", "offset": {"uri": "spotify:track:y1"}}
    assert play["params"] == {}
    run(s.play(uri="spotify:track:f1"))
    assert fake.sent("PUT", "/me/player/play")[1]["json"] == {"uris": ["spotify:track:f1"]}


def test_his_own_playlist_comes_before_search():
    playlists = {"items": [{"type": "playlist", "name": "Gym hits 2025", "uri": "spotify:playlist:g0", "id": "g0"},
                           {"type": "playlist", "name": "Gym", "uri": "spotify:playlist:g1", "id": "g1"}], "next": None}
    s, fake = spotify({("GET", "/me/playlists"): (200, playlists)})
    assert run(s.play("gym", kind="playlist")) == "Playing the playlist Gym."
    assert fake.sent("GET", "/search") == []
    assert fake.sent("PUT", "/me/player/play")[0]["json"] == {"context_uri": "spotify:playlist:g1"}


def test_made_for_you_mixes_are_never_searched():
    # Searching "On Repeat" finds strangers' playlists of that name.
    mine = {"items": [{"type": "playlist", "name": "On Repeat", "uri": "spotify:playlist:or", "id": "or"}]}
    s, fake = spotify({("GET", "/me/playlists"): (200, {"items": []})})
    with pytest.raises(ToolError, match="saves it to his library"):
        run(s.play("my On Repeat playlist", kind="track"))
    assert fake.sent("GET", "/search") == []
    fake.answers[("GET", "/me/playlists")] = (200, mine)
    assert run(s.play("my on repeat playlist", kind="playlist")) == "Playing the playlist On Repeat."


def test_liked_songs_and_links():
    s, fake = spotify({("GET", "/me"): (200, {"id": "tuxa"})})
    assert run(s.play(kind="liked_songs")) == "Playing his Liked Songs."
    assert fake.sent("PUT", "/me/player/play")[0]["json"] == {"context_uri": "spotify:user:tuxa:collection"}
    run(s.play(uri="https://open.spotify.com/intl-de/album/abc123?si=xyz"))
    assert fake.sent("PUT", "/me/player/play")[1]["json"] == {"context_uri": "spotify:album:abc123"}
    assert spotify_uri("spotify:track:y1") == "spotify:track:y1"
    with pytest.raises(ToolError):
        spotify_uri("yellow")


def test_nothing_playing_starts_on_the_pc():
    s, fake = spotify({("GET", "/search"): (200, {"tracks": {"items": [YELLOW]}}),
                       ("PUT", "/me/player/play"): [no_active_device(), (204, None)],
                       ("GET", "/me/player/devices"): (200, {"devices": [PHONE, PC]})})
    assert run(s.play("yellow")) == "Playing Yellow by Coldplay on TUXA-ASUS."
    assert fake.sent("PUT", "/me/player/play")[1]["params"] == {"device_id": "d-pc"}


def test_nothing_open_anywhere():
    s, _ = spotify({("PUT", "/me/player/play"): no_active_device(), ("GET", "/me/player/devices"): (200, {"devices": []})})
    with pytest.raises(ToolError, match="isn't open on any"):
        run(s.control("resume"))


def test_pausing_when_nothing_plays_doesnt_start_a_device():
    s, fake = spotify({("PUT", "/me/player/pause"): no_active_device()})
    with pytest.raises(ToolError, match="isn't playing"):
        run(s.control("pause"))
    assert fake.sent("GET", "/me/player/devices") == []


def test_waits_for_spotify_that_just_started(monkeypatch):
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda seconds: real_sleep(0))
    s, fake = spotify({("PUT", "/me/player/play"): [no_active_device(), (204, None)],
                       ("GET", "/me/player/devices"): [(200, {"devices": []}), (200, {"devices": [PC]})]})
    s.start_seconds = 10
    assert run(s.play(uri="spotify:track:y1")) == "Playing that on TUXA-ASUS."
    assert len(fake.sent("GET", "/me/player/devices")) == 2


def test_play_on_a_named_device_and_move_playback():
    s, fake = spotify({("GET", "/me/player/devices"): (200, {"devices": [PC, PHONE]})})
    assert run(s.play(device="phone")) == "Playing on Nothing Phone (2)."
    assert fake.sent("PUT", "/me/player")[0]["json"] == {"device_ids": ["d-phone"], "play": True}
    with pytest.raises(ToolError, match="Open Spotify devices: TUXA-ASUS \\(computer\\)"):
        run(s.play(uri="spotify:track:y1", device="kitchen"))


def test_queue_only_takes_songs():
    s, fake = spotify({})
    assert run(s.play(uri="spotify:track:y1", queue=True)) == "Added that to the queue."
    assert fake.sent("POST", "/me/player/queue")[0]["params"] == {"uri": "spotify:track:y1"}
    with pytest.raises(ToolError, match="Only songs"):
        run(s.play(uri="spotify:album:a1", queue=True))


def test_podcast_plays_its_newest_episode():
    show = {"type": "show", "name": "Lex Fridman Podcast", "id": "s1", "uri": "spotify:show:s1"}
    episode = {"type": "episode", "name": "#480", "uri": "spotify:episode:e1"}
    s, fake = spotify({("GET", "/search"): (200, {"shows": {"items": [show]}}),
                       ("GET", "/shows/s1/episodes"): (200, {"items": [episode]})})
    assert run(s.play("lex fridman", kind="podcast")) == "Playing #480 (Lex Fridman Podcast)."
    assert fake.sent("PUT", "/me/player/play")[0]["json"] == {"uris": ["spotify:episode:e1"]}


def test_controls():
    s, fake = spotify({("GET", "/me/player"): (200, {"device": PC, "progress_ms": 60_000})})
    assert run(s.control("volume", "up")) == "Volume 60%."
    assert fake.sent("PUT", "/me/player/volume")[0]["params"] == {"volume_percent": 60}
    assert run(s.control("volume", "130")) == "Volume 100%."
    assert run(s.control("repeat", "all")) == "Repeating the playlist or album."
    assert fake.sent("PUT", "/me/player/repeat")[0]["params"] == {"state": "context"}
    assert run(s.control("shuffle", "on")) == "Shuffle on."
    assert run(s.control("seek", "+30")) == "Jumped to 1:30."
    assert run(s.control("seek", "15")) == "Jumped to 0:15."
    assert run(s.control("next")) == "Skipped to the next track."
    with pytest.raises(ToolError, match="needs a number"):
        run(s.control("volume", "loud"))


def test_spotify_errors_in_plain_words():
    s, _ = spotify({("PUT", "/me/player/pause"): (403, {"error": {"message": "Player command failed: Restriction "
                                                                            "violated", "reason": "UNKNOWN"}}),
                    ("PUT", "/me/player/volume"): (403, {"error": {"reason": "VOLUME_CONTROL_DISALLOW"}})})
    with pytest.raises(ToolError, match="already paused"):
        run(s.control("pause"))
    with pytest.raises(ToolError, match="doesn't let Spotify change its volume"):
        run(s.control("volume", "40"))


def test_now_playing():
    state = {"is_playing": True, "progress_ms": 83_000, "shuffle_state": False, "repeat_state": "context",
             "device": dict(PC, is_active=True), "item": YELLOW}
    s, _ = spotify({("GET", "/me/player"): (200, state),
                    ("GET", "/me/player/devices"): (200, {"devices": [dict(PC, is_active=True), PHONE]})})
    assert run(s.now_playing()) == (
        "Playing Yellow by Coldplay [spotify:track:y1], 1:23 of 4:26, on TUXA-ASUS, volume 50%. Shuffle off, "
        "repeat on.\nSpotify is open on: TUXA-ASUS (computer, playing), Nothing Phone (2) (smartphone).")
    s, _ = spotify({("GET", "/me/player/devices"): (200, {"devices": []})})
    assert run(s.now_playing()) == "Nothing is playing. Spotify is open on: none."


def test_like_and_add_to_playlist():
    playlists = {"items": [{"type": "playlist", "name": "Gym", "id": "g1", "uri": "spotify:playlist:g1", "owner": {"id": "someone"}},
                           {"type": "playlist", "name": "Gym mine", "id": "g2", "uri": "spotify:playlist:g2", "owner": {"id": "tuxa"}}]}
    s, fake = spotify({("GET", "/me/player/currently-playing"): (200, {"item": YELLOW}),
                       ("GET", "/me/playlists"): (200, playlists), ("GET", "/me"): (200, {"id": "tuxa"})})
    assert run(s.like()) == "Saved Yellow by Coldplay to his Liked Songs."
    assert fake.sent("PUT", "/me/library")[0]["params"] == {"uris": "spotify:track:y1"}
    fake.answers[("GET", "/me/library/contains")] = (200, [True])
    assert run(s.like()) == "Yellow by Coldplay is already in his Liked Songs."
    assert len(fake.sent("PUT", "/me/library")) == 1  # not saved again
    # Only playlists he owns or collaborates on can take songs.
    assert run(s.add_to_playlist("gym")) == "Added Yellow by Coldplay to the playlist Gym mine."
    assert fake.sent("POST", "/playlists/g2/items")[0]["json"] == {"uris": ["spotify:track:y1"]}


def song(name, id, artist, album="An album"):
    return {"type": "track", "name": name, "id": id, "uri": f"spotify:track:{id}", "artists": [{"name": artist}],
            "album": {"name": album}}


NUMB_LIVE = song("Comfortably Numb - Live", "n1", "Pink Floyd", "Pulse (Live)")
NUMB = song("Comfortably Numb", "n2", "Pink Floyd", "The Wall")
KASHMIR = song("Kashmir - Remaster", "k1", "Led Zeppelin", "Physical Graffiti (Remaster)")
GYM_MINE = {"type": "playlist", "name": "Gym mine", "id": "g2", "uri": "spotify:playlist:g2", "owner": {"id": "tuxa"}}


def test_studio_versions_come_first():
    cover = song("Comfortably Numb", "n3", "Scissor Sisters")
    assert studio_version([NUMB_LIVE, cover, NUMB], "comfortably numb pink floyd") is NUMB
    # A remaster over a live take (here only its album says so); a cover by someone else is another song.
    kashmir_live = song("Kashmir", "k2", "Led Zeppelin", "Celebration Day (Live)")
    kashmir_cover = song("Kashmir", "k3", "A Tribute Band")
    assert studio_version([kashmir_live, kashmir_cover, KASHMIR], "kashmir") is KASHMIR
    remaster = song("Wish You Were Here - 2011 Remaster", "w1", "Pink Floyd")
    plain_name = song("Wish You Were Here", "w2", "Pink Floyd")
    assert studio_version([remaster, plain_name], "wish you were here") is plain_name
    assert studio_version([remaster, plain_name], "wish you were here 2011 remaster") is remaster
    # Only live takes found, or a live one asked for: that.
    assert studio_version([NUMB_LIVE], "comfortably numb") is NUMB_LIVE
    assert studio_version([NUMB_LIVE, NUMB], "comfortably numb live") is NUMB_LIVE
    # A title's own words aren't a version.
    wings = song("Live and Let Die", "l1", "Wings", "Live and Let Die")
    wings_live = song("Live and Let Die - Live", "l2", "Wings", "Wings Over America (Live)")
    assert studio_version([wings_live, wings], "live and let die wings") is wings
    assert studio_version([], "nothing") is None


def test_songs_are_found_and_added_without_playing():
    found = {"comfortably numb pink floyd": [NUMB_LIVE, NUMB], "kashmir led zeppelin": [KASHMIR],
             "yellow coldplay": [YELLOW], "zzzz": []}
    s, fake = spotify({("GET", "/search"): lambda params: (200, {"tracks": {"items": found[params["q"]]}}),
                       ("GET", "/me/playlists"): (200, {"items": [GYM_MINE]}), ("GET", "/me"): (200, {"id": "tuxa"}),
                       ("GET", "/playlists/g2/items"): (200, {"items": [{"item": YELLOW}], "next": None})})
    text = run(s.add_to_playlist("gym", queries=["comfortably numb pink floyd", "kashmir led zeppelin",
                                                 "yellow coldplay", "kashmir led zeppelin", "zzzz"]))
    assert text == ("Added 2 songs to the playlist Gym mine: Comfortably Numb by Pink Floyd; Kashmir - Remaster by "
                    "Led Zeppelin. Already in it: Yellow by Coldplay; Kashmir - Remaster by Led Zeppelin. Spotify "
                    "found nothing for: zzzz.")
    # One request adds them all, and nothing was played or queued for it.
    assert [r["json"] for r in fake.sent("POST", "/playlists/g2/items")] == [
        {"uris": ["spotify:track:n2", "spotify:track:k1"]}]
    assert not any(r["path"].startswith("/me/player") for r in fake.requests)
    assert {r["params"]["limit"] for r in fake.sent("GET", "/search")} == {10}  # Development Mode's most
    # A single query, and a song already there.
    assert run(s.add_to_playlist("gym", query="yellow coldplay")) == \
        "Yellow by Coldplay is already in the playlist Gym mine."
    assert len(fake.sent("POST", "/playlists/g2/items")) == 1
    with pytest.raises(ToolError, match="found nothing for: zzzz"):
        run(s.add_to_playlist("gym", queries=["zzzz"]))
    with pytest.raises(ToolError, match="spotify_create_playlist makes one"):
        run(s.add_to_playlist("road trip", queries=["yellow coldplay"]))
    with pytest.raises(ToolError, match=f"At most {MAX_ADD}"):
        run(s.add_to_playlist("gym", queries=["yellow coldplay"] * (MAX_ADD + 1)))


def test_a_new_playlist_is_made_then_filled_by_its_uri():
    made = {"type": "playlist", "name": "Classic Rock", "id": "new1", "uri": "spotify:playlist:new1",
            "owner": {"id": "tuxa"}}
    wall = {"type": "album", "name": "The Wall", "id": "a1", "uri": "spotify:album:a1", "artists": [{"name": "Pink Floyd"}]}
    s, fake = spotify({("GET", "/me/playlists"): (200, {"items": [GYM_MINE]}), ("GET", "/me"): (200, {"id": "tuxa"}),
                       ("POST", "/me/playlists"): (201, made), ("GET", "/playlists/new1"): (200, made),
                       ("GET", "/search"): (200, {"albums": {"items": [wall]}}),
                       ("GET", "/albums/a1/tracks"): (200, {"items": [{"uri": "spotify:track:t1"},
                                                                      {"uri": "spotify:track:t2"}]})})
    assert run(s.create_playlist("Classic Rock", "Like Pink Floyd")) == \
        "Made the private playlist Classic Rock [spotify:playlist:new1]; it's empty."
    assert fake.sent("POST", "/me/playlists")[0]["json"] == {"name": "Classic Rock", "public": False,
                                                            "description": "Like Pink Floyd"}
    # Found by its URI at once, even before Spotify lists it among his playlists; a whole album goes in.
    assert run(s.add_to_playlist("spotify:playlist:new1", query="the wall pink floyd", kind="album")) == \
        "Added the album The Wall by Pink Floyd to the playlist Classic Rock."
    assert fake.sent("GET", "/search")[0]["params"]["type"] == "album"
    assert fake.sent("POST", "/playlists/new1/items")[0]["json"] == {"uris": ["spotify:track:t1", "spotify:track:t2"]}
    # One by the same name isn't made twice.
    fake.answers[("GET", "/me/playlists")] = (200, {"items": [made]})
    assert run(s.create_playlist("classic rock")).startswith(
        "He already has the playlist Classic Rock [spotify:playlist:new1], so no new one was made")
    assert len(fake.sent("POST", "/me/playlists")) == 1
    # Someone else's can't be filled.
    fake.answers[("GET", "/playlists/new1")] = (200, dict(made, owner={"id": "someone"}))
    with pytest.raises(ToolError, match="isn't his"):
        run(s.add_to_playlist("https://open.spotify.com/playlist/new1?si=x", query="yellow"))


def test_library():
    playlists = {"items": [{"name": "Gym", "uri": "spotify:playlist:g1", "owner": {"id": "tuxa"},
                            "items": {"total": 42}},
                           {"name": "Discover Weekly", "uri": "spotify:playlist:dw", "owner": {
                               "id": "spotify", "display_name": "Spotify"}}]}
    played = {"items": [{"played_at": "2026-10-06T05:41:12.000Z", "track": YELLOW}]}
    s, _ = spotify({("GET", "/me/playlists"): (200, playlists), ("GET", "/me"): (200, {"id": "tuxa"}),
                    ("GET", "/me/player/recently-played"): (200, played)})
    assert run(s.library()) == ("- Gym, 42 items [spotify:playlist:g1]\n"
                                "- Discover Weekly, by Spotify [spotify:playlist:dw]")
    assert run(s.library("recently_played")) == "- Tue 09:41: Yellow by Coldplay [spotify:track:y1]"


def test_tools_routing_and_sign_in_reminder():
    s, _ = spotify({}, signed_in=(date.today() - timedelta(days=175)).isoformat())
    tools = {tool.name: tool for tool in s.tools()}
    # Playing and controlling music may go to the free tier; his playlists and history may not.
    assert {name for name, tool in tools.items() if tool.personal} == {"spotify_library"}
    assert not any(tool.confirm for tool in tools.values())
    # Filling a playlist is its own tool, which says it needs no playing; queueing points to it.
    assert "never play" in tools["spotify_add_to_playlist"].description
    assert "not adding to a playlist" in tools["spotify_play"].description
    assert "queries" in tools["spotify_add_to_playlist"].input_schema["properties"]
    assert all(tool.source == "spotify" for tool in tools.values())
    text = run(tools["spotify_control"].run({"action": "next"}))
    assert text.startswith("Skipped to the next track. (Spotify's sign-in ends around")
    s, _ = spotify({})
    assert run(s.tools()[1].run({"action": "next"})) == "Skipped to the next track."

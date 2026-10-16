from natro_agent.steps import describe


def test_opening_an_app_names_it():
    assert describe("pc_open_app", {"app": "Spotify"}) == {"text": "Opening Spotify", "icon": "app", "app": "Spotify"}
    assert describe("phone_open_app", {"app": "WhatsApp"})["text"] == "Opening WhatsApp"


def test_spotify_steps_are_about_spotify():
    play = describe("spotify_play", {"query": "Yellow Coldplay"})
    assert play == {"text": "Playing Yellow Coldplay", "icon": "music", "app": "Spotify"}
    assert describe("spotify_play", {"query": "Fear", "queue": True})["text"] == "Queueing Fear"
    assert describe("spotify_play", {"kind": "liked_songs"})["text"] == "Playing Liked Songs"
    assert describe("spotify_control", {"action": "pause"})["text"] == "Pausing music"
    assert describe("spotify_control", {"action": "shuffle", "value": "on"})["text"] == "Shuffle on"


def test_phone_steps():
    assert describe("phone_set_alarm", {"time": "07:30"}) == {"text": "Alarm for 07:30", "icon": "alarm"}
    assert describe("phone_set_timer", {"seconds": 300})["text"] == "Timer for 5 min"
    assert describe("phone_set_timer", {"seconds": 90})["text"] == "Timer for 1 min 30 s"
    assert describe("phone_media", {"action": "next"})["text"] == "Next song"
    assert describe("phone_flashlight", {"on": True})["text"] == "Flashlight on"


def test_long_details_are_cut_short():
    text = describe("web_search", {"query": "weather in Tbilisi tomorrow afternoon please"})["text"]
    assert text.startswith("Searching weather in") and text.endswith("…")
    assert len(text) <= len("Searching ") + 22


def test_files_show_only_the_name():
    assert describe("pc_read_file", {"path": r"C:\Users\Tuxa\Documents\plan.docx"})["text"] == "Reading plan.docx"


def test_unknown_tools_still_say_something():
    assert describe("github_create_issue", {}) == {"text": "Github create issue", "icon": "work"}
    assert describe("phone_new_tool", {}) == {"text": "New tool", "icon": "work"}

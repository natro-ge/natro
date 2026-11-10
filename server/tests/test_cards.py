"""Cards: pictures he saved, shown full screen on his phone, now or at one of his places."""
import json
from datetime import date, datetime, timedelta

import pytest

from conftest import run
from natro_agent.cards import Cards
from natro_agent.config import timezone
from natro_agent.places import Places
from natro_agent.tools import ToolError

JPEG = b"\xff\xd8\xff\xe0 a voucher"
SHOP = {"id": "pl1", "name": "Carrefour", "names": [], "lat": 41.54, "lng": 45.0, "radius": 150, "address": ""}


class Phone:
    name = "phone"
    label = "phone"
    places = True
    cards = True

    def __init__(self):
        self.posted = []
        self.bytes = []

    def post(self, **message):
        self.posted.append(message)

    def post_bytes(self, data):
        self.bytes.append(bytes(data))

    def sent(self, kind):
        return [message for message in self.posted if message["type"] == kind]


def world(tmp_path, online=True):
    phone = Phone()
    devices = {"phone": phone} if online else {}
    places = Places(tmp_path / "places.json", devices)
    places.items.append(dict(SHOP))
    cards = Cards(tmp_path / "cards", devices, places)
    cards.picture_sent = lambda: ("image/jpeg", JPEG)
    return cards, places, phone


def saved(cards, name="Carrefour", **args):
    return run(cards.save({"name": name, **args}))


def show(cards, name="Carrefour", **args):
    return run(cards.show({"name": name, **args}))


# Saving

def test_the_picture_he_just_sent_is_kept(tmp_path):
    cards, _, _ = world(tmp_path)
    assert saved(cards) == "Saved your Carrefour card."
    card, = cards.cards
    assert (tmp_path / "cards" / f"{card['id']}.jpg").read_bytes() == JPEG
    kept = Cards(tmp_path / "cards", {})
    assert kept.cards == [card]


def test_with_no_picture_she_asks_for_it(tmp_path):
    cards, _, _ = world(tmp_path)
    cards.picture_sent = lambda: None
    with pytest.raises(ToolError, match="ask him to send it"):
        saved(cards)
    assert cards.cards == []


def test_saving_a_name_again_replaces_it(tmp_path):
    cards, _, _ = world(tmp_path)
    saved(cards)
    first = cards.cards[0]["id"]
    cards.picture_sent = lambda: ("image/png", b"png")
    saved(cards, "my carrefour")
    card, = cards.cards
    assert card["id"] == first and card["mime"] == "image/png" and card["name"] == "Carrefour"
    assert [path.name for path in (tmp_path / "cards").glob("cd*")] == [f"{first}.png"]


def test_an_expiry_day(tmp_path):
    cards, _, _ = world(tmp_path)
    assert saved(cards, expires="2030-12-31") == "Saved your Carrefour card. It ends on 31 December 2030."
    with pytest.raises(ToolError, match="a day like"):
        saved(cards, "Other", expires="soon")
    assert len(cards.cards) == 1
    with pytest.raises(ToolError, match="Say what"):
        saved(cards, " ")


# Showing

def test_shown_now_it_goes_to_the_phone_with_its_bytes(tmp_path):
    cards, _, phone = world(tmp_path)
    saved(cards)
    assert show(cards, "the carrefour") == "It's on your phone."
    message, = phone.sent("card")
    assert message["name"] == "Carrefour" and message["show"] is True and message["mime"] == "image/jpeg"
    assert phone.bytes == [JPEG]


def test_an_ended_card_still_shows_saying_so(tmp_path):
    cards, _, phone = world(tmp_path)
    saved(cards, expires=(date.today() - timedelta(days=2)).isoformat())
    said = show(cards)
    assert said.startswith("It's on your phone. It ended on")
    assert phone.sent("card")[0]["ended"]


def test_what_cant_be_shown_says_why(tmp_path):
    cards, _, phone = world(tmp_path)
    with pytest.raises(ToolError, match="no card called"):
        show(cards, "Nowhere")
    saved(cards)
    phone.cards = False
    with pytest.raises(ToolError, match="needs updating"):
        show(cards)
    (tmp_path / "other").mkdir()
    offline, _, _ = world(tmp_path / "other", online=False)
    saved(offline)
    with pytest.raises(ToolError, match="offline"):
        show(offline)


# At a place

def test_set_at_a_place_the_rules_name_it_and_the_phone_is_told(tmp_path):
    cards, places, phone = world(tmp_path)
    saved(cards)
    said = show(cards, place="Carrefour", every_time=True)
    assert said == "OK, I'll show your Carrefour card when you get to Carrefour, every time. Your phone keeps a copy."
    assert phone.sent("card") == []  # the phone asks for its copy
    rule = phone.sent("place_rules")[-1]["places"][0]
    named, = rule["cards"]
    assert (named["card"], named["name"], named["on"], named["every"]) == (cards.cards[0]["id"], "Carrefour", "arrived", True)


def test_the_phone_asks_for_a_copy_and_it_comes(tmp_path):
    cards, _, phone = world(tmp_path)
    saved(cards)
    cards.messages()["card_get"](phone, {"id": cards.cards[0]["id"]})
    message, = phone.sent("card")
    assert message["show"] is False and phone.bytes == [JPEG]
    cards.messages()["card_get"](phone, {"id": cards.cards[0]["id"], "show": True})
    assert phone.sent("card")[-1]["show"] is True
    cards.messages()["card_get"](phone, {"id": "nope"})
    assert len(phone.bytes) == 2


def test_a_place_without_cards_has_rules_as_they_were(tmp_path):
    cards, places, _ = world(tmp_path)
    assert places.rules() == [{"id": "pl1", "name": "Carrefour", "lat": 41.54, "lng": 45.0, "radius": 150}]


def test_once_it_goes_when_the_phone_says_it_got_there_and_every_time_stays(tmp_path):
    cards, places, phone = world(tmp_path)
    saved(cards)
    show(cards, place="Carrefour")
    show(cards, place="Carrefour", on="leave", every_time=True)
    places._event(phone, {"id": "pl1", "event": "arrived"})
    assert [item["place_on"] for item in cards.at_places] == ["left"]
    assert [each["on"] for each in phone.sent("place_rules")[-1]["places"][0]["cards"]] == ["left"]
    places._event(phone, {"id": "pl1", "event": "left"})
    assert len(cards.at_places) == 1


def test_an_ended_card_isnt_named_by_a_place(tmp_path):
    cards, places, _ = world(tmp_path)
    saved(cards, expires=(date.today() - timedelta(days=1)).isoformat())
    show(cards, place="Carrefour")
    assert "cards" not in places.rules()[0]
    assert "ended on" in run(cards.list({}))


def test_a_place_that_isnt_saved_is_for_her_to_ask_about(tmp_path):
    cards, _, _ = world(tmp_path)
    saved(cards)
    with pytest.raises(ToolError, match="no place called"):
        show(cards, place="Mars")


def test_it_shows_among_what_happens_there_and_can_be_stopped(tmp_path):
    cards, places, phone = world(tmp_path)
    saved(cards)
    show(cards, place="Carrefour")
    each, = places.happening("pl1")
    assert (each["kind"], each["on"], each["every"], each["text"]) == ("card", "arrived", False, "show your Carrefour card")
    places.stop_happening(each["id"])
    assert cards.at_places == [] and "cards" not in phone.sent("place_rules")[-1]["places"][0]


def test_the_place_going_takes_it_along_but_keeps_the_card(tmp_path):
    cards, places, _ = world(tmp_path)
    saved(cards)
    show(cards, place="Carrefour")
    assert places.forget("Carrefour") == "Forgot Carrefour, and what was set to happen there."
    assert cards.at_places == [] and len(cards.cards) == 1


def test_forgetting_a_card_takes_its_picture_and_places(tmp_path):
    cards, places, phone = world(tmp_path)
    saved(cards)
    show(cards, place="Carrefour")
    id = cards.cards[0]["id"]
    assert run(cards.forget({"name": "carrefour"})) == "Forgot your Carrefour card."
    assert cards.cards == [] and cards.at_places == [] and not (tmp_path / "cards" / f"{id}.jpg").exists()
    assert "cards" not in phone.sent("place_rules")[-1]["places"][0]
    with pytest.raises(ToolError, match="no card called"):
        run(cards.forget({"name": "carrefour"}))


def test_the_list_says_what_he_has_and_where(tmp_path):
    cards, _, _ = world(tmp_path)
    assert run(cards.list({})) == "No cards."
    saved(cards, expires="2030-01-01")
    show(cards, place="Carrefour")
    listed = run(cards.list({}))
    assert "- Carrefour, ends on 2030-01-01" in listed and "when you get to Carrefour, once (ct" in listed


def test_a_file_that_cant_be_read_or_with_a_bad_line_keeps_what_it_can(tmp_path):
    folder = tmp_path / "cards"
    folder.mkdir()
    (folder / "cards.json").write_text("not json", encoding="utf-8")
    assert Cards(folder, {}).cards == []
    (folder / "cards.json").write_text(json.dumps({"cards": [{"id": "c1", "name": "A", "mime": "image/png"}, {"id": "x"}, 5],
                                                   "at_places": [{"id": "t"}]}), encoding="utf-8")
    kept = Cards(folder, {})
    assert [card["id"] for card in kept.cards] == ["c1"] and kept.at_places == []


def test_a_saved_card_hasnt_ended_on_its_last_day(tmp_path):
    cards, _, _ = world(tmp_path)
    saved(cards, expires=datetime.now(timezone()).date().isoformat())
    assert show(cards) == "It's on your phone."

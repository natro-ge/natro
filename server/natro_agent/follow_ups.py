"""Listening after a reply: for a few seconds after she answers something he said, he can go on by just talking.

The device does the listening, and if he says nothing it ends quietly with nothing sent. This says whether it may
listen after a reply, and for how long, in the reply's "listen": one rule for both devices. A device can still
decide not to (its switch, something playing there, the chat open).
"""

# He reads her replies ("Speak replies" is off), so the time runs from a little more than a short one takes to read,
# up to what a long one gets. At about 20 characters a second, that's 120 characters before it grows.
SECONDS = 6
MOST_SECONDS = 10
READ_PER_SECOND = 20

# Follow-ups in a row before it stops asking for more: a TV in the room can't keep it going.
MOST_IN_A_ROW = 3

# Replies that didn't answer him: "I didn't catch that." and "OK, I won't use Claude for that."
NOT_ANSWERED = ("none", "none (noise)")

# The tools that start sound, and with which action: the microphone would hear it and pay to recognize it. What's
# playing on the device itself the device checks, but this may start after the reply, or on the other device.
STARTS_SOUND = {"spotify_play": None, "spotify_control": {"resume", "next", "previous"},
                "phone_media": {"play", "next", "previous"}}


def seconds_to_read(text):
    """How long to listen after this reply."""
    return min(MOST_SECONDS, max(SECONDS, round(len(text) / READ_PER_SECOND)))


def starts_sound(call):
    """Whether a tool that ran (one of Reply.tool_calls) started something playing."""
    if call["tool"] not in STARTS_SOUND or not call.get("ok"):
        return False
    actions = STARTS_SOUND[call["tool"]]
    return actions is None or (call.get("args") or {}).get("action") in actions


class FollowUps:
    """How many follow-ups in a row each device has sent."""

    def __init__(self):
        self._in_a_row = {}

    def replied(self, device, follow_up, reply):
        """A reply to a spoken request is going to this device (its name); follow_up: the request came from its
        listening after a reply. How long it may listen now, in seconds, or None."""
        in_a_row = self._in_a_row.get(device, 0) + 1 if follow_up else 0
        self._in_a_row[device] = in_a_row
        if in_a_row >= MOST_IN_A_ROW or reply.model in NOT_ANSWERED or any(map(starts_sound, reply.tool_calls)):
            return None
        return seconds_to_read(reply.text)

"""Pictures for Claude to see: one he sends into the chat ("what's this?"), a screenshot a tool took.

Each is made small first: Claude scales anything with a side longer than LONGEST down to it anyway, so sending
more only costs time. A picture that size is about 1,600 input tokens (under a cent on Sonnet). A phone's HEIC
and anything Claude doesn't take become a JPEG; see-through parts go white. Pictures hold his data (his screen,
his photos), so they only ever go to Claude.
"""
import base64
import io

LONGEST = 1568
QUALITY = 85
# What Claude's API takes as is.
TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
# The API's limit for one picture.
MOST_BYTES = 5 * 2**20


def for_model(mime, data):
    """A picture as an image block for Claude, or None if it isn't one that can be read."""
    if not data:
        return None
    mime = (mime or "").lower()
    try:
        mime, data = small(mime, data)
    except ImportError:  # no Pillow: as it is, if Claude takes it
        if mime not in TYPES or len(data) > MOST_BYTES:
            return None
    except Exception:  # not a picture: Claude would refuse the whole request
        return None
    return {"type": "image", "source": {"type": "base64", "media_type": mime,
                                        "data": base64.b64encode(data).decode("ascii")}}


def small(mime, data):
    """(mime, bytes): the picture as it is if Claude takes it and it's small enough, else a smaller JPEG."""
    from PIL import Image

    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except ImportError:
        pass
    with Image.open(io.BytesIO(data)) as picture:
        picture.load()
        if mime in TYPES and max(picture.size) <= LONGEST and len(data) <= MOST_BYTES \
                and getattr(picture, "n_frames", 1) == 1:
            return mime, data
        picture.thumbnail((LONGEST, LONGEST))
        if picture.mode not in ("RGB", "L"):
            rgba = picture.convert("RGBA")
            flat = Image.new("RGB", rgba.size, "white")
            flat.paste(rgba, mask=rgba.getchannel("A"))
            picture = flat
        out = io.BytesIO()
        picture.save(out, "JPEG", quality=QUALITY, optimize=True)
        return "image/jpeg", out.getvalue()

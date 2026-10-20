"""Converting his files: "make the newest PDF in my Downloads a Word file", "this video as an MP3".

convert_file brings the file from his PC or phone to the server (files.Transfers.fetch: nothing passes
it on), converts it in a program of its own, one at a time and with a time limit, and sends the result
to the Downloads folder of the device he wants (the one the file is on, unless he says) like any file
between his devices. No model sees what's in it, only its name. The original and the result are gone
from the server once the result is on its way.

What converts to what:
  documents (LibreOffice):  Word, ODF, RTF, text, HTML -> pdf, docx, odt, rtf, txt, html
                            Excel, ODS, CSV -> pdf, xlsx, ods, csv, html
                            PowerPoint, ODP -> pdf, pptx, odp
                            PDF -> docx, odt, txt (the text, not always the layout)
  Markdown (pandoc):        md -> docx, html, odt, and pdf through LibreOffice
  pictures (Pillow):        jpg, png, webp, gif, bmp, tiff, heic (a phone's photos) -> jpg, png, webp,
                            gif, bmp, tiff, pdf
  sound and video (ffmpeg): mp3, m4a, aac, wav, ogg, opus, flac, amr, wma -> mp3, m4a, wav, ogg, opus, flac
                            mp4, mov, mkv, webm, avi, 3gp, m4v -> mp4, webm, mkv, mov, gif, or its sound alone

A conversion that takes longer than WAIT_SECONDS goes on after Natro's answer; the file arrives when
it's done, and if it fails he's told (tell: an alert, set by the server).
"""
import asyncio
import itertools
import shutil
import sys
import time
from pathlib import Path

from natro_agent import log
from natro_agent.files import human_size, safe_name
from natro_agent.identity import KNOWN_DEVICES
from natro_agent.tools import Tool, ToolError

MOST_BYTES = 300 << 20
# Natro waits this long for the result before answering; after that it comes when it's ready. Most take a few
# seconds; a big presentation half a minute or more.
WAIT_SECONDS = 15
# How long one conversion may take.
DOCUMENT_SECONDS = 180
PICTURE_SECONDS = 60
MEDIA_SECONDS = 20 * 60

WRITER = {"doc", "docx", "odt", "rtf", "txt", "html", "htm"}
CALC = {"xls", "xlsx", "ods", "csv"}
IMPRESS = {"ppt", "pptx", "odp"}
PICTURES = {"jpg", "jpeg", "png", "webp", "gif", "bmp", "tif", "tiff", "heic", "heif"}
SOUNDS = {"mp3", "m4a", "aac", "wav", "ogg", "opus", "flac", "amr", "wma"}
VIDEOS = {"mp4", "mov", "mkv", "webm", "avi", "3gp", "m4v"}

# What each kind of file can become.
TARGETS = {
    "writer": {"pdf", "docx", "odt", "rtf", "txt", "html"},
    "calc": {"pdf", "xlsx", "ods", "csv", "html"},
    "impress": {"pdf", "pptx", "odp"},
    "pdf": {"docx", "odt", "txt"},
    "markdown": {"docx", "html", "odt", "pdf"},
    "picture": {"jpg", "png", "webp", "gif", "bmp", "tiff", "pdf"},
    "sound": {"mp3", "m4a", "wav", "ogg", "opus", "flac"},
    "video": {"mp4", "webm", "mkv", "mov", "gif", "mp3", "m4a", "wav", "ogg", "opus", "flac"},
}
# How people name formats, to file extensions.
ALIASES = {"jpeg": "jpg", "tif": "tiff", "word": "docx", "doc": "docx", "excel": "xlsx", "xls": "xlsx",
           "powerpoint": "pptx", "ppt": "pptx", "text": "txt", "htm": "html", "markdown": "md", "heif": "heic",
           "aiff": "wav", "vorbis": "ogg"}
# LibreOffice's export filters where the bare extension isn't enough.
FILTERS = {"txt": "txt:Text (encoded):UTF8", "csv": "csv:Text - txt - csv (StarCalc):44,34,76,1"}
PILLOW_FORMATS = {"jpg": "JPEG", "png": "PNG", "webp": "WEBP", "gif": "GIF", "bmp": "BMP", "tiff": "TIFF",
                  "pdf": "PDF"}


def extension(name):
    return Path(name).suffix.lower().lstrip(".")


def kind_of(ext):
    """What kind of file an extension is (a key of TARGETS), or None."""
    if ext == "pdf":
        return "pdf"
    if ext == "md":
        return "markdown"
    for kind, extensions in (("writer", WRITER), ("calc", CALC), ("impress", IMPRESS), ("picture", PICTURES),
                             ("sound", SOUNDS), ("video", VIDEOS)):
        if ext in extensions:
            return kind
    return None


def target(wanted):
    """The format he asked for, as a file extension."""
    wanted = str(wanted or "").strip().lower().lstrip(".")
    return ALIASES.get(wanted, wanted)


def first_to_go(command):
    """The command, so that if memory runs out it's the one the kernel stops, not Natro (on Linux: they share the
    service's memory limit, and LibreOffice with a big presentation needs a lot)."""
    if not sys.platform.startswith("linux"):
        return list(command)
    return ["sh", "-c", 'echo 1000 > /proc/self/oom_score_adj; exec "$@"', command[0], *command]


async def run(command, seconds, cwd=None):
    """Run a converter; ToolError with what it said if it fails, runs out of memory or takes too long."""
    process = await asyncio.create_subprocess_exec(*first_to_go(command), cwd=cwd, stdout=asyncio.subprocess.PIPE,
                                                   stderr=asyncio.subprocess.STDOUT)
    try:
        output, _ = await asyncio.wait_for(process.communicate(), seconds)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise ToolError(f"{Path(command[0]).name} took longer than {seconds // 60 or 1} minutes, so it was stopped.")
    if process.returncode == -9:  # SIGKILL: the kernel stopped it for running out of memory
        raise ToolError(f"{Path(command[0]).name} ran out of memory on the server: the file is too big to convert "
                        "there.")
    if process.returncode != 0:
        said = output.decode("utf-8", "replace").strip().splitlines()
        raise ToolError(f"{Path(command[0]).name} couldn't convert it: {said[-1] if said else process.returncode}")
    return output


class Converter:
    """transfers: the server's files.Transfers. tell(text): tells him something later (an alert), set by the
    server. run: runs a converter program (for tests)."""

    def __init__(self, transfers, run=run):
        self.transfers = transfers
        self.tell = lambda text: None
        self._run = run
        self._one_at_a_time = asyncio.Lock()
        self._ids = itertools.count(1)
        self.work = transfers.folder / "converting"
        shutil.rmtree(self.work, ignore_errors=True)  # anything a restart cut off
        self.work.mkdir(parents=True, exist_ok=True)
        self.profile = transfers.folder / "libreoffice"  # LibreOffice's settings, kept: it starts faster

    def tools(self):
        device = {"type": "string", "enum": list(KNOWN_DEVICES)}
        return [Tool(
            "convert_file",
            "Convert a file on his PC or phone to another format: documents (Word, PDF, ODF, RTF, text, HTML, "
            "Markdown), spreadsheets (Excel, ODS, CSV), presentations (PowerPoint, ODP), pictures (JPG, PNG, WebP, "
            "GIF, HEIC, TIFF, BMP, or a PDF of one), sound (MP3, M4A, WAV, OGG, Opus, FLAC) and video (MP4, WebM, "
            "MKV, MOV, a GIF, or its sound alone). Find the file first (pc_search_files, phone_search_files) and "
            "give its full path. The result lands in the Downloads folder of to_device (default: the device the "
            "file is on); the original stays as it is. From a PDF, the text comes over but not always the layout.",
            {"type": "object", "properties": {
                "from_device": device,
                "path": {"type": "string", "description": "The file's full path on that device."},
                "to": {"type": "string", "description": "The format he wants, as a file extension: pdf, docx, "
                                                        "xlsx, jpg, png, mp3, mp4..."},
                "to_device": {**device, "description": "Where the result goes; default the device the file is on."},
            }, "required": ["from_device", "path", "to"]},
            self.convert, "natro", personal=True, final=True)]

    async def convert(self, args):
        source = args.get("from_device")
        if source not in KNOWN_DEVICES:
            raise ToolError(f"from_device is one of: {', '.join(KNOWN_DEVICES)}.")
        to_device = args.get("to_device") or source
        if to_device not in KNOWN_DEVICES:
            raise ToolError(f"to_device is one of: {', '.join(KNOWN_DEVICES)}.")
        path = str(args.get("path") or "").strip()
        name = safe_name(path)
        wanted = target(args.get("to"))
        self.check(name, wanted)
        task = asyncio.create_task(self._convert(source, path, wanted, to_device))
        done, _ = await asyncio.wait([task], timeout=WAIT_SECONDS)
        if done:
            return task.result()  # raises its ToolError, if it failed
        task.add_done_callback(self._later)
        there = KNOWN_DEVICES[to_device]
        return (f"I'm converting {name} to {wanted.upper()}; it's taking a while, so it'll land in your {there}'s "
                "Downloads when it's done.")

    @staticmethod
    def check(name, wanted):
        """Refuses what can't be done, before the file comes."""
        have = extension(name)
        kind = kind_of(have)
        if kind is None:
            raise ToolError(f"Natro can't convert .{have or '?'} files.")
        if wanted == target(have):
            raise ToolError(f"{name} is a {wanted.upper()} already.")
        if wanted not in TARGETS[kind]:
            raise ToolError(f"A .{have} file can become: {', '.join(sorted(TARGETS[kind] - {target(have)}))}.")

    def _later(self, task):
        """A conversion that went on after Natro answered: he's told if it failed."""
        if task.cancelled():
            return
        if isinstance(error := task.exception(), Exception):
            self.tell(f"The conversion didn't work: {error}")

    async def _convert(self, source, path, wanted, to_device):
        started = time.monotonic()
        fetched, name = await self.transfers.fetch(source, path, most=MOST_BYTES)
        folder = self.work / f"c{next(self._ids)}"
        try:
            folder.mkdir()
            original = folder / safe_name(name)
            shutil.move(str(fetched), original)
            async with self._one_at_a_time:
                result = await self._make(original, wanted, folder / "out")
            size = result.stat().st_size
            sent = self.transfers.send(result, f"{Path(name).stem}.{wanted}", to_device)
        finally:
            fetched.unlink(missing_ok=True)
            shutil.rmtree(folder, ignore_errors=True)
        log.write("convert", name=name, to=wanted, size=size, seconds=round(time.monotonic() - started, 1))
        there = KNOWN_DEVICES[to_device]
        return f"Done: {sent['name']} ({human_size(size)}) is on its way to your {there}'s Downloads."

    async def _make(self, original, wanted, out):
        """The converted file, in out (made here)."""
        out.mkdir()
        kind = kind_of(extension(original.name))
        if kind == "picture":
            made = out / f"{original.stem}.{wanted}"
            await asyncio.wait_for(asyncio.to_thread(convert_picture, original, made, wanted), PICTURE_SECONDS)
            return made
        if kind in ("sound", "video"):
            made = out / f"{original.stem}.{wanted}"
            await self._run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-threads", "2",
                             "-i", str(original), *ffmpeg_options(kind, wanted), str(made)], MEDIA_SECONDS)
            return made
        if kind == "markdown":
            if wanted == "pdf":  # through Word, which LibreOffice prints
                middle = out.parent / f"{original.stem}.docx"
                await self._run(["pandoc", str(original), "-o", str(middle)], DOCUMENT_SECONDS)
                return await self._office(middle, "pdf", out)
            made = out / f"{original.stem}.{wanted}"
            await self._run(["pandoc", str(original), "-s", "-o", str(made)], DOCUMENT_SECONDS)
            return made
        return await self._office(original, wanted, out, pdf=kind == "pdf")

    async def _office(self, original, wanted, out, pdf=False):
        command = ["soffice", "--headless", "--norestore", "--nolockcheck", "--nodefault",
                   f"-env:UserInstallation={self.profile.resolve().as_uri()}"]
        if pdf:
            command.append("--infilter=writer_pdf_import")
        command += ["--convert-to", FILTERS.get(wanted, wanted), "--outdir", str(out), str(original)]
        await self._run(command, DOCUMENT_SECONDS)
        made = sorted(out.iterdir())
        if not made:
            raise ToolError("LibreOffice made nothing from it.")
        return made[0]


def ffmpeg_options(kind, wanted):
    """How ffmpeg makes this format: quick to make on the server's two cores, and plays everywhere."""
    if wanted in TARGETS["sound"]:
        audio = {"mp3": ["-c:a", "libmp3lame", "-q:a", "2"], "m4a": ["-c:a", "aac", "-b:a", "192k"],
                 "ogg": ["-c:a", "libvorbis", "-q:a", "5"], "opus": ["-c:a", "libopus", "-b:a", "128k"]}
        return ["-vn", *audio.get(wanted, [])]
    return {
        "mp4": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac",
                "-movflags", "+faststart"],
        "webm": ["-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-crf", "33", "-b:v", "0",
                 "-c:a", "libopus"],
        "gif": ["-an", "-vf", "fps=10,scale=480:-2:flags=lanczos"],
    }.get(wanted, [])


def convert_picture(original, made, wanted):
    """A picture in another format; a phone's HEIC too. The first frame of an animation, except to GIF."""
    from PIL import Image

    if extension(original.name) in ("heic", "heif"):
        from pillow_heif import register_heif_opener
        register_heif_opener()
    with Image.open(original) as picture:
        picture.load()
        frames = getattr(picture, "n_frames", 1)
        if wanted == "gif" and frames > 1:
            picture.save(made, "GIF", save_all=True, loop=0)
            return
        if wanted in ("jpg", "pdf", "bmp") and picture.mode not in ("RGB", "L"):
            # No transparency there: on white.
            rgba = picture.convert("RGBA")
            flat = Image.new("RGB", rgba.size, "white")
            flat.paste(rgba, mask=rgba.getchannel("A"))
            picture = flat
        options = {"quality": 90} if wanted in ("jpg", "webp") else {}
        picture.save(made, PILLOW_FORMATS[wanted], **options)


def available():
    """Which converters this machine has, for the log at the start."""
    return {program: bool(shutil.which(program)) for program in ("soffice", "pandoc", "ffmpeg")} | {
        "pillow": _importable("PIL"), "pillow_heif": _importable("pillow_heif")}


def _importable(module):
    try:
        __import__(module)
        return True
    except ImportError:
        return False

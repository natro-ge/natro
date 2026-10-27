"""Working on his PDFs: "merge these three PDFs", "compress the newest PDF in Downloads", "OCR this scan".

edit_pdf brings the files from his PC or phone to the server as convert_file does (convert.Converter: its folder,
its turn, so one job runs at a time with conversions, and its wait), does the work in a program of its own (this
module, run as python -m natro_agent.pdf job.json, so a huge PDF can't take Natro's memory with it), and sends the
results to the Downloads folder of the device he wants. No model sees what's in them, only their names. Nothing
stays on the server.

What it does:
  merge          two or more PDFs (or pictures, a page each) into one, in the order given
  split          one PDF into several: each part of pages ("1-3, 4-6"), every N pages, or every page alone
  keep_pages     only those pages, in the order given (so it reorders them too)
  delete_pages   all but those pages
  rotate         those pages (default all) by 90, 180 or 270 degrees, clockwise
  compress       smaller, with Ghostscript: light (best quality), recommended (pictures at 150 dpi) or strong (72)
  watermark      a text across the pages, pale and diagonal
  page_numbers   a number at the foot of each page (or of those pages), from start
  lock, unlock   a password to open it (AES-256); no password any more (he gives the one it has)
  to_jpg         each page (or those pages) as a JPG, with Ghostscript
  ocr            a scan's text made searchable and copyable, with OCRmyPDF and Tesseract (English, Georgian, Russian)
More than MOST_FILES results (split, to_jpg) come as one zip. A locked PDF opens with its password, for any of
them; the result isn't locked.
"""
import io
import json
import math
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from natro_agent import convert, log
from natro_agent.convert import PICTURES, extension
from natro_agent.files import human_size, safe_name
from natro_agent.identity import KNOWN_DEVICES
from natro_agent.tools import Tool, ToolError

ACTIONS = ("merge", "split", "keep_pages", "delete_pages", "rotate", "compress", "watermark", "page_numbers", "lock",
           "unlock", "to_jpg", "ocr")
# Ghostscript's settings for each level of compress.
LEVELS = {"light": "/printer", "recommended": "/ebook", "strong": "/screen"}
# Tesseract's languages on the server (apt: tesseract-ocr-eng, -kat, -rus), and what people call them.
LANGUAGES = {"eng": "eng", "english": "eng", "kat": "kat", "georgian": "kat", "rus": "rus", "russian": "rus"}
DEFAULT_LANGUAGES = "eng+kat"
# More results than this come as one zip.
MOST_FILES = 20
JPG_DPI = 150
# How long a job may take; the rest take convert.DOCUMENT_SECONDS.
SECONDS = {"compress": 10 * 60, "to_jpg": 10 * 60, "ocr": 30 * 60}
# For a watermark: DejaVu Sans has Georgian and Cyrillic letters (apt: fonts-dejavu). Without it, Helvetica: Latin
# letters only.
FONTS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
# What the result's name gets after the original's.
SUFFIXES = {"merge": "merged", "keep_pages": "pages", "delete_pages": "pages", "rotate": "rotated",
            "compress": "compressed", "watermark": "watermarked", "page_numbers": "numbered", "lock": "locked",
            "unlock": "unlocked", "ocr": "ocr"}
RANGE = re.compile(r"(?P<a>\d+|first|last|end)?\s*(?P<dash>-|–|to)?\s*(?P<b>\d+|first|last|end)?")


class PdfError(ToolError):
    """Something about his PDF he should hear: the program writes it down, and Natro tells him."""


# --- Natro's side: the tool, its checks, and the job ---------------------------------------------

class PdfTools:
    """converter: the server's convert.Converter, whose folder, turn, soon and tell these share. run: runs a
    program (for tests)."""

    def __init__(self, converter, run=convert.run):
        self.converter = converter
        self._run = run

    def tools(self):
        device = {"type": "string", "enum": list(KNOWN_DEVICES)}
        return [Tool(
            "edit_pdf",
            "Work on PDFs on his PC or phone: merge several (PDFs, or pictures as pages) into one, split one, keep or "
            "delete pages (keep_pages also reorders them), rotate pages, compress, add a text watermark, number the "
            "pages, lock with a password or unlock, turn pages into JPGs, or OCR a scan so its text can be searched "
            "and copied. Find the files first (pc_search_files, phone_search_files) and give their full paths. The "
            "result lands in the Downloads folder of to_device (default: the device the files are on); the "
            "originals stay as they are. To make a PDF of another kind of file, or Word of a PDF, use convert_file.",
            {"type": "object", "properties": {
                "action": {"type": "string", "enum": list(ACTIONS)},
                "from_device": device,
                "paths": {"type": "array", "items": {"type": "string"},
                          "description": "Full paths on from_device. merge: two or more PDFs or pictures, in order; "
                                         "the rest: one PDF."},
                "pages": {"type": "string",
                          "description": "Pages, like '1-3, 5, 8-last'. keep_pages: those, in this order; "
                                         "delete_pages: those; split: each part a file; rotate, watermark, "
                                         "page_numbers, to_jpg: only those (default all)."},
                "every": {"type": "integer", "minimum": 1,
                          "description": "split: a file every this many pages (with neither this nor pages, every "
                                         "page alone)."},
                "degrees": {"type": "integer", "enum": [90, 180, 270],
                            "description": "rotate: clockwise; 270 turns it left."},
                "level": {"type": "string", "enum": list(LEVELS),
                          "description": "compress: light (best quality), recommended (default) or strong "
                                         "(smallest; pictures blur)."},
                "text": {"type": "string", "description": "watermark: its text."},
                "start": {"type": "integer", "description": "page_numbers: the first number (default 1)."},
                "password": {"type": "string",
                             "description": "lock: the password to set. Any other action on a locked PDF: its "
                                            "password."},
                "languages": {"type": "string",
                              "description": "ocr: the document's languages, eng, kat (Georgian) or rus, joined "
                                             f"with + (default {DEFAULT_LANGUAGES})."},
                "to_device": {**device, "description": "Where the result goes; default the device the files are on."},
            }, "required": ["action", "from_device", "paths"]},
            self.edit, "natro", personal=True, final=True)]

    async def edit(self, args):
        action = str(args.get("action") or "").strip().lower()
        source = args.get("from_device")
        if source not in KNOWN_DEVICES:
            raise ToolError(f"from_device is one of: {', '.join(KNOWN_DEVICES)}.")
        to_device = args.get("to_device") or source
        if to_device not in KNOWN_DEVICES:
            raise ToolError(f"to_device is one of: {', '.join(KNOWN_DEVICES)}.")
        paths = args.get("paths")
        paths = [paths] if isinstance(paths, str) else paths if isinstance(paths, list) else []
        paths = [str(path).strip() for path in paths if str(path).strip()]
        options = self.check(action, paths, args)
        name = safe_name(paths[0])
        doing = f"merging {len(paths)} files" if action == "merge" else f"working on {name}"
        there = KNOWN_DEVICES[to_device]
        return await self.converter.soon(
            self._edit(action, source, paths, options, to_device),
            f"I'm {doing}; it's taking a while, so it'll land in your {there}'s Downloads when it's done.",
            f"{doing[0].upper()}{doing[1:]} didn't work")

    @staticmethod
    def check(action, paths, args):
        """Refuses what can't be done, before the files come. The job's options."""
        if action not in ACTIONS:
            raise ToolError(f"action is one of: {', '.join(ACTIONS)}.")
        if action == "merge" and len(paths) < 2:
            raise ToolError("merge takes two or more files, in order.")
        if action != "merge" and len(paths) != 1:
            raise ToolError(f"{action} takes one PDF.")
        for path in paths:
            name = safe_name(path)
            if extension(name) != "pdf" and not (action == "merge" and extension(name) in PICTURES):
                also = " or a picture" if action == "merge" else ""
                raise ToolError(f"{name} isn't a PDF{also}; convert_file makes PDFs of other files.")
        options = {}
        pages = str(args.get("pages") or "").strip()
        if pages:
            list(parts_of(pages))  # raises if they aren't pages
            options["pages"] = pages
        elif action in ("keep_pages", "delete_pages"):
            raise ToolError(f"{action} needs pages, like '1-3, 5'.")
        if action == "split" and args.get("every") is not None and not pages:
            options["every"] = whole(args["every"], "every", least=1)
        if action == "rotate":
            degrees = whole(args.get("degrees"), "degrees") % 360 if args.get("degrees") is not None else None
            if degrees not in (90, 180, 270):
                raise ToolError("degrees is 90, 180 or 270, clockwise.")
            options["degrees"] = degrees
        if action == "compress":
            options["level"] = str(args.get("level") or "recommended").strip().lower()
            if options["level"] not in LEVELS:
                raise ToolError(f"level is one of: {', '.join(LEVELS)}.")
        if action == "watermark":
            options["text"] = " ".join(str(args.get("text") or "").split())
            if not options["text"]:
                raise ToolError("watermark needs its text.")
        if action == "page_numbers":
            options["start"] = whole(args["start"], "start") if args.get("start") is not None else 1
        if args.get("password"):
            options["password"] = str(args["password"])
        elif action == "lock":
            raise ToolError("lock needs the password to set.")
        if action == "ocr":
            options["languages"] = languages(args.get("languages"))
        return options

    async def _edit(self, action, source, paths, options, to_device):
        started = time.monotonic()
        folder = self.converter.new_folder()
        try:
            inputs = []
            for number, path in enumerate(paths, 1):
                place = folder / f"in{number}"  # each its own folder: two files may share a name
                place.mkdir()
                kept, name = await self.converter.bring(source, path, place)
                inputs.append({"file": str(kept), "name": name})
            out = folder / "out"
            out.mkdir()
            job, result = folder / "job.json", folder / "result.json"
            job.write_text(json.dumps({"action": action, "inputs": inputs, "out": str(out), "options": options,
                                       "result": str(result)}, ensure_ascii=False), encoding="utf-8")
            async with self.converter.turn:
                await self._run([sys.executable, "-I", "-m", "natro_agent.pdf", str(job)],
                                SECONDS.get(action, convert.DOCUMENT_SECONDS) + 60, work="work on",
                                program="The PDF tool")
            done = json.loads(result.read_text(encoding="utf-8"))
            if "error" in done:
                raise PdfError(done["error"])
            made = [out / name for name in done["made"]]
            size = sum(file.stat().st_size for file in made)
            sent = [self.converter.transfers.send(file, file.name, to_device) for file in made]
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        log.write("pdf", action=action, files=len(paths), made=len(sent), size=size,
                  seconds=round(time.monotonic() - started, 1))
        there = KNOWN_DEVICES[to_device]
        detail, remark = done.get("detail"), done.get("remark")
        if not sent:
            said = remark
        elif len(sent) == 1:
            said = (f"Done: {sent[0]['name']} ({human_size(size)}{f', {detail}' if detail else ''}) is on its way to "
                    f"your {there}'s Downloads.")
        else:
            said = (f"Done: {len(sent)} files, {sent[0]['name']} to {sent[-1]['name']} ({human_size(size)}), are on "
                    f"their way to your {there}'s Downloads.")
        return f"{said} {remark}" if sent and remark else said


def whole(value, name, least=None):
    """value as a whole number, or ToolError naming it."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ToolError(f"{name} is a whole number.")
    if least is not None and number < least:
        raise ToolError(f"{name} is at least {least}.")
    return number


def languages(value):
    """Tesseract's names for the languages he gave ("Georgian and English" -> "kat+eng")."""
    words = [word for word in re.split(r"[\s+,&]+|\band\b", str(value or "").lower()) if word]
    if not words:
        return DEFAULT_LANGUAGES
    unknown = [word for word in words if word not in LANGUAGES]
    if unknown:
        raise ToolError(f"OCR reads eng, kat (Georgian) and rus, not {', '.join(unknown)}.")
    return "+".join(dict.fromkeys(LANGUAGES[word] for word in words))


# --- Pages --------------------------------------------------------------------------------------

def parts_of(spec):
    """Each part of '1-3, 5, 8-last' as (a, dash, b), the words as written; PdfError if one isn't pages."""
    found = False
    for part in re.split(r"[,;]", str(spec).lower()):
        part = part.strip()
        if not part:
            continue
        match = RANGE.fullmatch(part)
        if not match or not (match["a"] or match["b"]):
            raise PdfError(f"Pages are like '1-3, 5, 8-last'; {part!r} isn't.")
        found = True
        yield match["a"], match["dash"], match["b"]
    if not found:
        raise PdfError("Pages are like '1-3, 5, 8-last'.")


def page_ranges(spec, count, name):
    """'1-3, 5, 8-last' as lists of page indexes (from 0), one per part; one part of every page if there's no spec.
    A range can run backwards ('5-1')."""
    if not str(spec or "").strip() or str(spec).strip().lower() == "all":
        return [list(range(count))]

    def page(word, default):
        if word is None:
            return default
        if word == "first":
            return 1
        if word in ("last", "end"):
            return count
        if not 1 <= int(word) <= count:
            raise PdfError(f"{name} has {count} page{'' if count == 1 else 's'}: there's no page {int(word)}.")
        return int(word)

    ranges = []
    for a, dash, b in parts_of(spec):
        first = page(a, 1)
        last = page(b, count) if dash else first
        step = 1 if last >= first else -1
        ranges.append(list(range(first - 1, last - 1 + step, step)))
    return ranges


def selected(spec, count, name):
    """The pages a spec names, each once, from the first (all of them if there's no spec)."""
    return sorted({index for part in page_ranges(spec, count, name) for index in part})


def page_list(indexes):
    """Page indexes (from 0, in order) as Ghostscript's PageList: '1-3,7'."""
    runs = []
    for index in indexes:
        if runs and index == runs[-1][1] + 1:
            runs[-1][1] = index
        else:
            runs.append([index, index])
    return ",".join(f"{a + 1}" if a == b else f"{a + 1}-{b + 1}" for a, b in runs)


# --- The program's side: python -m natro_agent.pdf job.json -------------------------------------

def run_program(command, seconds):
    """Run Ghostscript or OCRmyPDF; PdfError with what it said if it fails, runs out of memory or takes too long."""
    program = {"gs": "Ghostscript", "ocrmypdf": "OCRmyPDF"}.get(command[0], command[0])
    try:
        done = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=seconds)
    except FileNotFoundError:
        raise PdfError(f"{program} isn't installed on the server.")
    except subprocess.TimeoutExpired:
        raise PdfError(f"{program} took longer than {seconds // 60 or 1} minutes, so it was stopped.")
    if done.returncode == -9:  # SIGKILL: the kernel stopped it for running out of memory
        raise PdfError(f"{program} ran out of memory on the server: the PDF is too big to work on there.")
    if done.returncode != 0:
        said = done.stdout.decode("utf-8", "replace").strip().splitlines()
        raise PdfError(f"{program} couldn't do it: {said[-1] if said else done.returncode}")
    return done.stdout


def read(path, name):
    """A pypdf reader of the file, not yet unlocked."""
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        return PdfReader(path)
    except (PyPdfError, ValueError, OSError) as e:
        raise PdfError(f"{name} can't be read as a PDF ({e}).")


def needs_password(reader):
    """Whether it takes a password to open (not only to change: that's a lock with no password)."""
    return reader.is_encrypted and not reader.decrypt("")


def open_pdf(path, name, password):
    """A pypdf reader of the file, opened with its password if it's locked."""
    reader = read(path, name)
    if needs_password(reader):
        if not password:
            raise PdfError(f"{name} is locked with a password; ask him for it.")
        if not reader.decrypt(password):
            raise PdfError(f"That isn't the password of {name}.")
    return reader


def save(writer, made):
    with open(made, "wb") as file:
        writer.write(file)
    return made


def plain(path, name, password, folder):
    """The PDF itself for Ghostscript or OCRmyPDF, or an unlocked copy if it's locked: (its path, its page count)."""
    from pypdf import PdfWriter

    reader = open_pdf(path, name, password)
    if not reader.is_encrypted:
        return path, len(reader.pages)
    return save(PdfWriter(clone_from=reader), folder / "unlocked.pdf"), len(reader.pages)


def unique(path):
    """path, or with (2), (3)... if there's a file there already."""
    first, number = path, 2
    while path.exists():
        path = first.with_name(f"{first.stem} ({number}){first.suffix}")
        number += 1
    return path


def stamp_font():
    """The font for a watermark, registered with reportlab."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for path in FONTS:
        if Path(path).exists():
            pdfmetrics.registerFont(TTFont("Stamp", path))
            return "Stamp"
    return "Helvetica-Bold"


def stamp_pages(sizes, draw):
    """One page of each size (width, height), drawn on by draw(canvas, index, width, height): a pypdf reader."""
    from pypdf import PdfReader
    from reportlab.pdfgen import canvas

    paper = io.BytesIO()
    drawing = canvas.Canvas(paper, pagesize=sizes[0] if sizes else (595, 842))
    for index, (width, height) in enumerate(sizes):
        drawing.setPageSize((width, height))
        draw(drawing, index, width, height)
        drawing.showPage()
    drawing.save()
    return PdfReader(io.BytesIO(paper.getvalue()))


def stamp(writer, indexes, draw):
    """Draws on those pages of writer, upright as they're seen, wherever their box starts."""
    from pypdf import Transformation

    boxes = []
    for index in indexes:
        page = writer.pages[index]
        if page.rotation:
            page.transfer_rotation_to_content()  # so "the foot" is the foot as it's seen
        boxes.append(page.cropbox)
    stamps = stamp_pages([(float(box.width), float(box.height)) for box in boxes], draw)
    for index, box, overlay in zip(indexes, boxes, stamps.pages):
        writer.pages[index].merge_transformed_page(overlay, Transformation().translate(float(box.left),
                                                                                         float(box.bottom)))


def work(job, program=run_program):
    """Does a job (as PdfTools._edit writes it): {"made": names of the results in out, "detail": for the
    parentheses after the name, "remark": a sentence after}. PdfError for what he should hear."""
    from pypdf import PdfWriter

    action, options, out = job["action"], job.get("options", {}), Path(job["out"])
    inputs = [(Path(item["file"]), item["name"]) for item in job["inputs"]]
    password = options.get("password")
    seconds = SECONDS.get(action, convert.DOCUMENT_SECONDS)
    path, name = inputs[0]
    stem = Path(name).stem
    named = out / f"{stem}-{SUFFIXES.get(action, action)}.pdf"
    detail = remark = ""
    locked = any(needs_password(read(file, title)) for file, title in inputs if extension(title) == "pdf")
    if action == "merge":
        writer = PdfWriter()
        for number, (file, title) in enumerate(inputs, 1):
            if extension(title) != "pdf":  # a picture: a page of its own
                page = file.parent / f"page{number}.pdf"
                convert.convert_picture(file, page, "pdf")
                file = page
            writer.append(open_pdf(file, title, password))
        made = [save(writer, named)]
        detail = f"{len(writer.pages)} pages"
    elif action in ("split", "keep_pages", "delete_pages", "rotate", "watermark", "page_numbers", "lock",
                    "unlock"):
        if action == "lock":
            reader = read(path, name)
            if needs_password(reader):
                raise PdfError(f"{name} is locked with a password already.")
        elif action == "unlock":
            if not read(path, name).is_encrypted:
                raise PdfError(f"{name} isn't locked.")
            reader = open_pdf(path, name, password)
        else:
            reader = open_pdf(path, name, password)
        count = len(reader.pages)
        if action == "split":
            if options.get("pages"):
                parts = page_ranges(options["pages"], count, name)
            else:
                every = options.get("every", 1)
                parts = [list(range(first, min(first + every, count))) for first in range(0, count, every)]
            if len(parts) == 1 and sorted(parts[0]) == list(range(count)):
                raise PdfError(f"That's all of {name} in one file: say where to split it.")
            made = []
            for part in parts:
                piece = PdfWriter()
                for index in part:
                    piece.add_page(reader.pages[index])
                label = f"p{part[0] + 1}" if len(part) == 1 else f"p{part[0] + 1}-{part[-1] + 1}"
                made.append(save(piece, unique(out / f"{stem}-{label}.pdf")))
        elif action in ("keep_pages", "delete_pages"):
            chosen = [index for part in page_ranges(options["pages"], count, name) for index in part]
            if action == "delete_pages":
                chosen = [index for index in range(count) if index not in set(chosen)]
                if not chosen:
                    raise PdfError(f"That's every page of {name}.")
            writer = PdfWriter()
            for index in chosen:
                writer.add_page(reader.pages[index])
            made = [save(writer, named)]
            detail = f"{len(chosen)} of {count} pages"
        else:
            writer = PdfWriter(clone_from=reader)
            indexes = selected(options.get("pages"), count, name)
            if action == "rotate":
                for index in indexes:
                    writer.pages[index].rotate(options["degrees"])
            elif action == "watermark":
                from reportlab.pdfbase.pdfmetrics import stringWidth

                text, font = options["text"], stamp_font()
                wide = max(stringWidth(text, font, 1), 0.01)  # at size 1

                def draw(canvas, index, width, height):
                    size = min(math.hypot(width, height) * 0.6 / wide, height / 5)
                    canvas.setFillColorRGB(0.5, 0.5, 0.5, alpha=0.3)
                    canvas.translate(width / 2, height / 2)
                    canvas.rotate(math.degrees(math.atan2(height, width)))
                    canvas.setFont(font, size)
                    canvas.drawCentredString(0, -size * 0.35, text)
                stamp(writer, indexes, draw)
            elif action == "page_numbers":
                start = options.get("start", 1)

                def draw(canvas, index, width, height):
                    canvas.setFillColorRGB(0.2, 0.2, 0.2)
                    canvas.setFont("Helvetica", 10)
                    canvas.drawCentredString(width / 2, 24, str(start + index))
                stamp(writer, indexes, draw)
            elif action == "lock":
                writer.encrypt(user_password=password, algorithm="AES-256")
            made = [save(writer, named)]
    elif action == "compress":
        source, _ = plain(path, name, password, out.parent)
        program(["gs", "-q", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.6",
                 f"-dPDFSETTINGS={LEVELS[options.get('level', 'recommended')]}", "-dDetectDuplicateImages=true",
                 f"-sOutputFile={named}", str(source)], seconds)
        before, after = path.stat().st_size, named.stat().st_size
        if after > before * 0.95:  # not worth a file
            named.unlink()
            return {"made": [], "remark": f"{name} is about as small as it gets already ({human_size(before)}), so "
                                          "nothing was sent."}
        made = [named]
        detail = f"was {human_size(before)}"
    elif action == "to_jpg":
        source, count = plain(path, name, password, out.parent)
        pages = selected(options.get("pages"), count, name)
        shots = out.parent / "jpg"
        shots.mkdir()
        program(["gs", "-q", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-sDEVICE=jpeg", f"-r{JPG_DPI}", "-dJPEGQ=90",
                 "-dTextAlphaBits=4", "-dGraphicsAlphaBits=4", f"-sPageList={page_list(pages)}",
                 f"-sOutputFile={shots / '%04d.jpg'}", str(source)], seconds)
        made = []
        for number, index in enumerate(pages, 1):
            shot = shots / f"{number:04d}.jpg"
            if not shot.exists():
                raise PdfError(f"Ghostscript made {number - 1} pictures of {len(pages)} pages.")
            made.append(shot.replace(out / f"{stem}-p{index + 1}.jpg"))
    elif action == "ocr":
        source, _ = plain(path, name, password, out.parent)
        program(["ocrmypdf", "--skip-text", "--output-type", "pdf", "--jobs", "2", "-q", "-l",
                 options.get("languages", DEFAULT_LANGUAGES), str(source), str(named)], seconds)
        made = [named]
    else:
        raise PdfError(f"action is one of: {', '.join(ACTIONS)}.")
    if len(made) > MOST_FILES:
        bundle = out / f"{stem}-{'pages' if action == 'split' else 'jpg'}.zip"
        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED if action == "split" else zipfile.ZIP_STORED) as zip:
            for file in made:
                zip.write(file, file.name)
                file.unlink()
        detail = f"{len(made)} files in a zip"
        made = [bundle]
    if locked and action not in ("lock", "unlock"):
        remark = "It isn't locked any more."
    return {"made": [file.name for file in made], "detail": detail, "remark": remark}


def main(argv=None, program=run_program):
    """python -m natro_agent.pdf job.json: does the job, and writes what came of it to the job's result."""
    job = json.loads(Path((argv or sys.argv[1:])[0]).read_text(encoding="utf-8"))
    try:
        done = work(job, program)
    except PdfError as e:
        done = {"error": str(e)}
    Path(job["result"]).write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()

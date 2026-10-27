"""Working on his PDFs: what's refused before the files come, pages, and each action on real PDFs (pypdf and
reportlab here; Ghostscript and OCRmyPDF stood in for), the program of its own for real, and failures."""
import asyncio
import io
import json
import shutil
import sys
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from conftest import run
from natro_agent import convert, pdf
from natro_agent.convert import Converter
from natro_agent.files import human_size, safe_name
from natro_agent.pdf import PdfTools, languages, page_list, page_ranges
from natro_agent.tools import ToolError


def sample(letter, pages, size=(595, 842)):
    """A PDF whose pages say A1, A2... (reportlab)."""
    from reportlab.pdfgen import canvas

    paper = io.BytesIO()
    drawing = canvas.Canvas(paper, pagesize=size)
    for number in range(1, pages + 1):
        drawing.drawString(72, 720, f"{letter}{number}")
        drawing.showPage()
    drawing.save()
    return paper.getvalue()


def texts(content, password=None):
    reader = PdfReader(io.BytesIO(content))
    if password:
        reader.decrypt(password)
    return [page.extract_text().strip() for page in reader.pages]


class FakeTransfers:
    """files: path -> content, as on his device; what's sent down is recorded (name, to, content)."""

    def __init__(self, folder, files):
        self.folder = folder
        self.files = files
        self.fetched = []
        self.sent = []

    async def fetch(self, source, path, most):
        if path not in self.files:
            raise ToolError(f"There's no {path}.")
        self.fetched.append((source, path))
        kept = self.folder / f"fetched-{len(self.fetched)}"
        kept.write_bytes(self.files[path])
        return kept, safe_name(path)

    def send(self, path, name, to):
        self.sent.append((name, to, Path(path).read_bytes()))
        Path(path).unlink()
        return {"name": name, "to": to}


class HereInstead:
    """Runs python -m natro_agent.pdf in this process, with Ghostscript and OCRmyPDF stood in for: each program's
    command is recorded, and it makes its output (gs: a PDF of `made` bytes, or a JPG a page; ocrmypdf: a copy)."""

    def __init__(self, made=b""):
        self.made = made
        self.commands = []

    async def __call__(self, command, seconds, cwd=None, work="convert", program=None):
        assert command[:4] == [sys.executable, "-I", "-m", "natro_agent.pdf"]
        pdf.main(command[4:], program=self.program)
        return b""

    def program(self, command, seconds):
        self.commands.append(command)
        output = next((part.split("=", 1)[1] for part in command if part.startswith("-sOutputFile=")), command[-1])
        if "-sDEVICE=jpeg" in command:
            pages = next(part for part in command if part.startswith("-sPageList=")).split("=", 1)[1]
            count = sum(int(b) - int(a) + 1 if b else 1 for piece in pages.split(",")
                        for a, _, b in [piece.partition("-")])
            for number in range(1, count + 1):
                Path(output.replace("%04d", f"{number:04d}")).write_bytes(b"\xff\xd8 a JPG")
        elif command[0] == "gs":
            Path(output).write_bytes(self.made)
        else:
            shutil.copy(command[-2], output)
        return b""


def make(tmp_path, files, made=b""):
    transfers = FakeTransfers(tmp_path, files)
    converter = Converter(transfers)
    here = HereInstead(made)
    return PdfTools(converter, run=here), transfers, here


def ask(tools, action, *paths, **args):
    return run(tools.edit({"action": action, "from_device": "pc", "paths": list(paths), **args}))


REPORT = r"C:\Users\Tuxa\Downloads\report.pdf"


# --- Refused before the files come ---------------------------------------------------------------

@pytest.mark.parametrize("args, said", [
    ({"action": "shrink", "paths": [REPORT]}, "action is one of: merge, split"),
    ({"action": "merge", "paths": [REPORT]}, "merge takes two or more files"),
    ({"action": "compress", "paths": [REPORT, REPORT]}, "compress takes one PDF"),
    ({"action": "compress", "paths": [r"C:\notes.docx"]}, "notes.docx isn't a PDF; convert_file"),
    ({"action": "merge", "paths": [REPORT, r"C:\song.mp3"]}, "song.mp3 isn't a PDF or a picture"),
    ({"action": "keep_pages", "paths": [REPORT]}, "keep_pages needs pages"),
    ({"action": "delete_pages", "paths": [REPORT], "pages": "the second one"}, "'the second one' isn't"),
    ({"action": "rotate", "paths": [REPORT]}, "degrees is 90, 180 or 270"),
    ({"action": "rotate", "paths": [REPORT], "degrees": 45}, "degrees is 90, 180 or 270"),
    ({"action": "lock", "paths": [REPORT]}, "lock needs the password"),
    ({"action": "watermark", "paths": [REPORT], "text": "  "}, "watermark needs its text"),
    ({"action": "ocr", "paths": [REPORT], "languages": "french"}, "not french"),
    ({"action": "split", "paths": [REPORT], "every": 0}, "every is at least 1"),
])
def test_what_cant_be_done_is_refused_before_the_files_come(tmp_path, args, said):
    tools, transfers, _ = make(tmp_path, {})
    with pytest.raises(ToolError, match=said):
        run(tools.edit({"from_device": "pc", **args}))
    assert transfers.fetched == []


def test_pages():
    assert page_ranges("1-3, 5, 8-last", 9, "a.pdf") == [[0, 1, 2], [4], [7, 8]]
    assert page_ranges("3-1; -2; 4-; first; end", 5, "a.pdf") == [[2, 1, 0], [0, 1], [3, 4], [0], [4]]
    assert page_ranges("2 to 3", 5, "a.pdf") == [[1, 2]]
    assert page_ranges("", 3, "a.pdf") == page_ranges("all", 3, "a.pdf") == [[0, 1, 2]]
    with pytest.raises(ToolError, match="a.pdf has 3 pages: there's no page 4"):
        page_ranges("2-4", 3, "a.pdf")
    with pytest.raises(ToolError, match="there's no page 0"):
        page_ranges("0", 3, "a.pdf")
    assert page_list([0, 1, 2, 6, 8, 9]) == "1-3,7,9-10"
    assert languages(None) == "eng+kat" and languages("Georgian and English") == "kat+eng"
    assert languages("rus+eng, russian") == "rus+eng"


# --- Each action, on real PDFs -----------------------------------------------------------------

def test_merge_pdfs_and_a_picture_in_order(tmp_path):
    from PIL import Image

    picture = io.BytesIO()
    Image.new("RGB", (60, 80), "white").save(picture, "JPEG")
    files = {r"C:\a.pdf": sample("A", 2), r"C:\b.pdf": sample("B", 1), r"C:\photo.jpg": picture.getvalue()}
    tools, transfers, _ = make(tmp_path, files)
    said = ask(tools, "merge", r"C:\b.pdf", r"C:\photo.jpg", r"C:\a.pdf")
    (name, to, content), = transfers.sent
    assert (name, to) == ("b-merged.pdf", "pc") and texts(content) == ["B1", "", "A1", "A2"]
    assert said.startswith("Done: b-merged.pdf (") and said.endswith(", 4 pages) is on its way to your PC's Downloads.")
    assert list(tools.converter.work.iterdir()) == []  # nothing left on the server


def test_split(tmp_path):
    tools, transfers, _ = make(tmp_path, {REPORT: sample("P", 5)})
    said = ask(tools, "split", REPORT)
    assert [name for name, _, _ in transfers.sent] == [f"report-p{n}.pdf" for n in range(1, 6)]
    assert [texts(content) for _, _, content in transfers.sent] == [["P1"], ["P2"], ["P3"], ["P4"], ["P5"]]
    assert said.startswith("Done: 5 files, report-p1.pdf to report-p5.pdf (") and "their way to your PC's" in said
    transfers.sent.clear()
    ask(tools, "split", REPORT, pages="1-2, 3-5")
    assert [(name, texts(content)) for name, _, content in transfers.sent] == [
        ("report-p1-2.pdf", ["P1", "P2"]), ("report-p3-5.pdf", ["P3", "P4", "P5"])]
    transfers.sent.clear()
    ask(tools, "split", REPORT, every=2, to_device="phone")
    assert [(name, to) for name, to, _ in transfers.sent] == [
        ("report-p1-2.pdf", "phone"), ("report-p3-4.pdf", "phone"), ("report-p5.pdf", "phone")]
    with pytest.raises(ToolError, match="That's all of report.pdf in one file"):
        ask(tools, "split", REPORT, pages="1-5")


def test_many_results_come_as_one_zip(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf, "MOST_FILES", 3)
    tools, transfers, _ = make(tmp_path, {REPORT: sample("P", 4)})
    said = ask(tools, "split", REPORT)
    (name, _, content), = transfers.sent
    import zipfile
    with zipfile.ZipFile(io.BytesIO(content)) as bundle:
        assert bundle.namelist() == [f"report-p{n}.pdf" for n in range(1, 5)]
    assert name == "report-pages.zip" and "4 files in a zip" in said


def test_keep_reorder_and_delete_pages(tmp_path):
    tools, transfers, _ = make(tmp_path, {REPORT: sample("P", 4)})
    said = ask(tools, "keep_pages", REPORT, pages="3, 1")
    assert transfers.sent[-1][0] == "report-pages.pdf" and texts(transfers.sent[-1][2]) == ["P3", "P1"]
    assert "2 of 4 pages" in said
    ask(tools, "delete_pages", REPORT, pages="2-3")
    assert texts(transfers.sent[-1][2]) == ["P1", "P4"]
    with pytest.raises(ToolError, match="That's every page of report.pdf"):
        ask(tools, "delete_pages", REPORT, pages="1-last")
    with pytest.raises(ToolError, match="report.pdf has 4 pages: there's no page 9"):
        ask(tools, "keep_pages", REPORT, pages="9")


def test_rotate(tmp_path):
    tools, transfers, _ = make(tmp_path, {REPORT: sample("P", 3)})
    ask(tools, "rotate", REPORT, degrees=90, pages="2")
    reader = PdfReader(io.BytesIO(transfers.sent[-1][2]))
    assert transfers.sent[-1][0] == "report-rotated.pdf" and [page.rotation for page in reader.pages] == [0, 90, 0]
    ask(tools, "rotate", REPORT, degrees=-90)
    assert [page.rotation for page in PdfReader(io.BytesIO(transfers.sent[-1][2])).pages] == [270, 270, 270]


def test_watermark_and_page_numbers(tmp_path):
    turned = PdfWriter(clone_from=PdfReader(io.BytesIO(sample("P", 3))))
    turned.pages[2].rotate(90)  # a page turned on its side: stamped as it's seen
    content = io.BytesIO()
    turned.write(content)
    tools, transfers, _ = make(tmp_path, {REPORT: content.getvalue()})
    ask(tools, "watermark", REPORT, text="DRAFT")
    name, _, made = transfers.sent[-1]
    assert name == "report-watermarked.pdf" and all("DRAFT" in text for text in texts(made))
    ask(tools, "page_numbers", REPORT, pages="2-last")
    name, _, made = transfers.sent[-1]
    pages = texts(made)
    assert name == "report-numbered.pdf" and pages[0] == "P1" and pages[1].endswith("1") and pages[2].endswith("2")
    ask(tools, "page_numbers", REPORT, start=5)
    assert [text[-1] for text in texts(transfers.sent[-1][2])] == ["5", "6", "7"]


def test_lock_and_unlock(tmp_path):
    tools, transfers, _ = make(tmp_path, {REPORT: sample("P", 2)})
    said = ask(tools, "lock", REPORT, password="s3cret")
    name, _, locked = transfers.sent[-1]
    assert name == "report-locked.pdf" and PdfReader(io.BytesIO(locked)).is_encrypted
    assert texts(locked, "s3cret") == ["P1", "P2"] and "isn't locked any more" not in said
    tools.converter.transfers.files[r"C:\locked.pdf"] = locked
    with pytest.raises(ToolError, match="locked.pdf is locked with a password; ask him for it"):
        ask(tools, "compress", r"C:\locked.pdf")
    with pytest.raises(ToolError, match="That isn't the password of locked.pdf"):
        ask(tools, "unlock", r"C:\locked.pdf", password="wrong")
    with pytest.raises(ToolError, match="locked.pdf is locked with a password already"):
        ask(tools, "lock", r"C:\locked.pdf", password="another")
    ask(tools, "unlock", r"C:\locked.pdf", password="s3cret")
    name, _, unlocked = transfers.sent[-1]
    assert name == "locked-unlocked.pdf" and not PdfReader(io.BytesIO(unlocked)).is_encrypted
    assert texts(unlocked) == ["P1", "P2"]
    with pytest.raises(ToolError, match="report.pdf isn't locked"):
        ask(tools, "unlock", REPORT)
    said = ask(tools, "keep_pages", r"C:\locked.pdf", pages="2", password="s3cret")
    assert texts(transfers.sent[-1][2]) == ["P2"] and said.endswith("It isn't locked any more.")


# --- Ghostscript and OCRmyPDF's commands ---------------------------------------------------------

def test_compress(tmp_path):
    big = sample("P", 80)
    tools, transfers, here = make(tmp_path, {REPORT: big}, made=sample("P", 2))
    said = ask(tools, "compress", REPORT)
    command, = here.commands
    assert command[0] == "gs" and "-sDEVICE=pdfwrite" in command and "-dPDFSETTINGS=/ebook" in command
    assert command[-1].endswith("report.pdf") and transfers.sent[-1][0] == "report-compressed.pdf"
    assert said.endswith(f", was {human_size(len(big))}) is on its way to your PC's Downloads.")
    ask(tools, "compress", REPORT, level="strong")
    assert "-dPDFSETTINGS=/screen" in here.commands[-1]
    here.made = big  # no smaller: nothing sent
    transfers.sent.clear()
    said = ask(tools, "compress", REPORT)
    assert said == f"report.pdf is about as small as it gets already ({human_size(len(big))}), so nothing was sent."
    assert transfers.sent == [] and list(tools.converter.work.iterdir()) == []


def test_a_locked_pdf_goes_to_ghostscript_unlocked(tmp_path):
    locked = PdfWriter(clone_from=PdfReader(io.BytesIO(sample("P", 3))))
    locked.encrypt("pw", algorithm="AES-256")
    content = io.BytesIO()
    locked.write(content)
    tools, transfers, here = make(tmp_path, {REPORT: content.getvalue()})
    said = ask(tools, "to_jpg", REPORT, pages="1, 3", password="pw")
    command, = here.commands
    assert command[0] == "gs" and "-sDEVICE=jpeg" in command and "-r150" in command and "-sPageList=1,3" in command
    assert command[-1].endswith("unlocked.pdf")
    assert [name for name, _, _ in transfers.sent] == ["report-p1.jpg", "report-p3.jpg"]
    assert said.endswith("It isn't locked any more.")


def test_ocr(tmp_path):
    tools, transfers, here = make(tmp_path, {REPORT: sample("P", 1)})
    ask(tools, "ocr", REPORT, languages="Georgian, English")
    command, = here.commands
    assert command[:7] == ["ocrmypdf", "--skip-text", "--output-type", "pdf", "--jobs", "2", "-q"]
    assert command[7:9] == ["-l", "kat+eng"] and command[-1].endswith("report-ocr.pdf")
    assert transfers.sent[-1][0] == "report-ocr.pdf" and texts(transfers.sent[-1][2]) == ["P1"]


# --- The program of its own, failures, and the tool ----------------------------------------------

def test_the_program_of_its_own_for_real(tmp_path):
    files = {r"C:\a.pdf": sample("A", 1), r"C:\b.pdf": sample("B", 1)}
    tools, transfers, _ = make(tmp_path, files)
    tools._run = convert.run
    ask(tools, "merge", r"C:\a.pdf", r"C:\b.pdf")
    assert texts(transfers.sent[0][2]) == ["A1", "B1"]
    tools.converter.transfers.files[r"C:\broken.pdf"] = b"not a PDF at all"
    with pytest.raises(ToolError, match="broken.pdf can't be read as a PDF"):
        ask(tools, "compress", r"C:\broken.pdf")
    assert list(tools.converter.work.iterdir()) == []


def test_a_program_that_fails_says_why():
    with pytest.raises(ToolError, match="nowhere-to-be-found isn't installed on the server"):
        pdf.run_program(["nowhere-to-be-found"], 5)
    failing = [sys.executable, "-c", "import sys; print('Error: the PDF is damaged'); sys.exit(1)"]
    with pytest.raises(ToolError, match="couldn't do it: Error: the PDF is damaged"):
        pdf.run_program(failing, 30)


def test_a_slow_one_goes_on_and_says_if_it_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(convert, "WAIT_SECONDS", 0.05)
    tools, transfers, _ = make(tmp_path, {r"C:\a.pdf": sample("A", 1), r"C:\b.pdf": sample("B", 1)})
    told = []
    tools.converter.tell = told.append

    async def slow_failure(command, seconds, cwd=None, work="convert", program=None):
        await asyncio.sleep(0.2)
        raise ToolError("The PDF tool took longer than 10 minutes, so it was stopped.")
    tools._run = slow_failure

    async def go():
        said = await tools.edit({"action": "merge", "from_device": "pc", "paths": [r"C:\a.pdf", r"C:\b.pdf"]})
        await asyncio.sleep(0.4)
        return said

    assert run(go()) == ("I'm merging 2 files; it's taking a while, so it'll land in your PC's Downloads when "
                         "it's done.")
    assert told == ["Merging 2 files didn't work: The PDF tool took longer than 10 minutes, so it was stopped."]


def test_the_tool_is_personal_and_its_result_is_the_reply(tmp_path):
    tools, _, _ = make(tmp_path, {})
    tool, = tools.tools()
    assert tool.name == "edit_pdf" and tool.personal and not tool.confirm and tool.final
    assert tool.input_schema["properties"]["action"]["enum"] == list(pdf.ACTIONS)


@pytest.mark.skipif(not shutil.which("gs"), reason="Ghostscript isn't installed here (it is on the VPS)")
def test_ghostscript_for_real(tmp_path):
    tools, transfers, _ = make(tmp_path, {REPORT: sample("P", 2)})
    tools._run = convert.run
    ask(tools, "to_jpg", REPORT)
    assert [name for name, _, _ in transfers.sent] == ["report-p1.jpg", "report-p2.jpg"]
    assert all(content.startswith(b"\xff\xd8") for _, _, content in transfers.sent)

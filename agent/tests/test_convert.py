"""Converting his files: what converts to what, the converters' commands, pictures for real, and slow ones."""
import asyncio
import shutil
from pathlib import Path

import pytest

from conftest import run
from natro_agent import convert
from natro_agent.convert import Converter, kind_of, target
from natro_agent.tools import ToolError


class FakeTransfers:
    """The file comes from the device as content; what's sent down is recorded (name, to, content)."""

    def __init__(self, folder, content=b"the file", fail=None):
        self.folder = folder
        self.content = content
        self.fail = fail
        self.fetched = []
        self.sent = []

    async def fetch(self, source, path, most):
        if self.fail:
            raise ToolError(self.fail)
        self.fetched.append((source, path, most))
        kept = self.folder / "fetched-file"
        kept.write_bytes(self.content)
        return kept, convert.safe_name(path)

    def send(self, path, name, to):
        self.sent.append((name, to, Path(path).read_bytes()))
        Path(path).unlink()
        return {"name": name, "to": to}


class Programs:
    """Stands in for LibreOffice, pandoc and ffmpeg: records each command and makes its output file."""

    def __init__(self, seconds=0.0):
        self.commands = []
        self.seconds = seconds

    async def __call__(self, command, seconds, cwd=None):
        self.commands.append(command)
        await asyncio.sleep(self.seconds)
        if command[0] == "soffice":
            wanted = command[command.index("--convert-to") + 1].split(":")[0]
            out = Path(command[command.index("--outdir") + 1])
            (out / f"{Path(command[-1]).stem}.{wanted}").write_bytes(b"converted by soffice")
        else:  # pandoc and ffmpeg: the output is named in the command
            Path(command[command.index("-o") + 1] if "-o" in command else command[-1]).write_bytes(b"converted")
        return b""


def make(tmp_path, **fake):
    transfers = FakeTransfers(tmp_path, **fake)
    programs = Programs()
    return Converter(transfers, run=programs), transfers, programs


def ask(converter, **args):
    return run(converter.convert({"from_device": "pc", **args}))


# --- What converts to what ----------------------------------------------------------------------

def test_formats_by_name():
    assert [target(name) for name in ("Word", ".PDF", "jpeg", "excel", "mp3")] == ["docx", "pdf", "jpg", "xlsx", "mp3"]
    assert [kind_of(ext) for ext in ("docx", "csv", "pptx", "pdf", "md", "heic", "m4a", "mov", "exe")] == [
        "writer", "calc", "impress", "pdf", "markdown", "picture", "sound", "video", None]


def test_what_cant_be_done_is_refused_before_the_file_comes(tmp_path):
    converter, transfers, _ = make(tmp_path)
    with pytest.raises(ToolError, match="can't convert .exe"):
        ask(converter, path=r"C:\setup.exe", to="pdf")
    with pytest.raises(ToolError, match="is a PDF already"):
        ask(converter, path=r"C:\a.pdf", to="PDF")
    with pytest.raises(ToolError, match="is a JPG already"):
        ask(converter, path="/sdcard/a.jpeg", from_device="phone", to="jpg")
    with pytest.raises(ToolError, match=r"A .mp3 file can become: flac, m4a, ogg, opus, wav"):
        ask(converter, path=r"C:\song.mp3", to="mp4")
    assert transfers.fetched == []


# --- Converting ---------------------------------------------------------------------------------

def test_a_word_file_becomes_a_pdf_on_the_device_it_was_on(tmp_path):
    converter, transfers, programs = make(tmp_path)
    said = ask(converter, path=r"C:\Users\Tuxa\Downloads\report.docx", to="pdf")
    assert said == "Done: report.pdf (20 bytes) is on its way to your PC's Downloads."
    assert transfers.fetched == [("pc", r"C:\Users\Tuxa\Downloads\report.docx", convert.MOST_BYTES)]
    assert transfers.sent == [("report.pdf", "pc", b"converted by soffice")]
    command, = programs.commands
    assert command[:5] == ["soffice", "--headless", "--norestore", "--nolockcheck", "--nodefault"]
    assert command[5].startswith("-env:UserInstallation=file:") and command[5].endswith("libreoffice")
    assert command[6:8] == ["--convert-to", "pdf"] and command[-1].endswith("report.docx")
    assert list(converter.work.iterdir()) == []  # nothing left on the server


def test_to_another_device(tmp_path):
    converter, transfers, _ = make(tmp_path)
    said = ask(converter, path=r"C:\notes.odt", to="docx", to_device="phone")
    assert said.endswith("on its way to your phone's Downloads.") and transfers.sent[0][:2] == ("notes.docx", "phone")


def test_the_converters_commands(tmp_path):
    converter, transfers, programs = make(tmp_path)
    ask(converter, path=r"C:\scan.pdf", to="word")
    assert "--infilter=writer_pdf_import" in programs.commands[-1] and transfers.sent[-1][0] == "scan.docx"
    ask(converter, path=r"C:\notes.docx", to="txt")
    assert "txt:Text (encoded):UTF8" in programs.commands[-1]
    ask(converter, path=r"C:\costs.xlsx", to="csv")
    assert "csv:Text - txt - csv (StarCalc):44,34,76,1" in programs.commands[-1]
    ask(converter, path=r"C:\talk.mp4", to="mp3")
    assert programs.commands[-1][0] == "ffmpeg" and "-vn" in programs.commands[-1]
    assert "libmp3lame" in programs.commands[-1] and transfers.sent[-1][0] == "talk.mp3"
    ask(converter, path=r"C:\clip.mov", to="mp4")
    assert "libx264" in programs.commands[-1]
    ask(converter, path=r"C:\plan.md", to="pdf")  # through Word
    assert programs.commands[-2][:2] == ["pandoc", programs.commands[-2][1]]
    assert programs.commands[-2][-1].endswith("plan.docx") and programs.commands[-1][0] == "soffice"
    assert transfers.sent[-1][0] == "plan.pdf"


def test_a_picture_for_real(tmp_path):
    from PIL import Image

    picture = tmp_path / "logo.png"
    Image.new("RGBA", (40, 30), (255, 0, 0, 0)).save(picture)  # see-through red
    converter, transfers, programs = make(tmp_path, content=picture.read_bytes())
    ask(converter, path=r"C:\logo.png", to="jpg")
    name, to, made = transfers.sent[0]
    (tmp_path / "out.jpg").write_bytes(made)
    with Image.open(tmp_path / "out.jpg") as jpg:
        assert (name, jpg.format, jpg.size) == ("logo.jpg", "JPEG", (40, 30))
        assert jpg.getpixel((5, 5))[0] > 240 and jpg.getpixel((5, 5))[1] > 240  # on white, not black
    assert programs.commands == []
    ask(converter, path=r"C:\logo.png", to="pdf")
    assert transfers.sent[-1][2].startswith(b"%PDF")


def test_a_failure_says_why_and_leaves_nothing(tmp_path):
    converter, transfers, programs = make(tmp_path)

    async def broken(command, seconds, cwd=None):
        raise ToolError("soffice couldn't convert it: source file could not be loaded")
    converter._run = broken
    with pytest.raises(ToolError, match="could not be loaded"):
        ask(converter, path=r"C:\broken.docx", to="pdf")
    assert transfers.sent == [] and list(converter.work.iterdir()) == []
    (tmp_path / "offline").mkdir()
    gone, _, _ = make(tmp_path / "offline", fail="The PC is offline.")
    with pytest.raises(ToolError, match="offline"):
        ask(gone, path=r"C:\a.docx", to="pdf")


def test_a_slow_one_goes_on_after_her_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(convert, "WAIT_SECONDS", 0.05)
    converter, transfers, programs = make(tmp_path)
    programs.seconds = 0.3
    told = []
    converter.tell = told.append

    async def go():
        said = await converter.convert({"from_device": "pc", "path": r"C:\film.mkv", "to": "mp4"})
        assert transfers.sent == []  # not yet
        await asyncio.sleep(0.5)
        return said

    said = run(go())
    assert said == ("I'm converting film.mkv to MP4; it's taking a while, so it'll land in your PC's Downloads "
                    "when it's done.")
    assert transfers.sent[0][0] == "film.mp4" and told == []

    async def failing(command, seconds, cwd=None):
        await asyncio.sleep(0.2)
        raise ToolError("ffmpeg took longer than 20 minutes, so it was stopped.")
    converter._run = failing

    async def go_failing():
        await converter.convert({"from_device": "pc", "path": r"C:\film.mkv", "to": "webm"})
        await asyncio.sleep(0.4)
    run(go_failing())
    assert told == ["The conversion didn't work: ffmpeg took longer than 20 minutes, so it was stopped."]


def test_the_tool_is_personal_and_its_result_is_the_reply(tmp_path):
    converter, _, _ = make(tmp_path)
    tool, = converter.tools()
    assert tool.name == "convert_file" and tool.personal and not tool.confirm and tool.final


@pytest.mark.skipif(not shutil.which("soffice"), reason="LibreOffice isn't installed here (it is on the VPS)")
def test_libreoffice_for_real(tmp_path):
    source = tmp_path / "hello.txt"
    source.write_text("Hello from Natro.\n", encoding="utf-8")
    converter, transfers, _ = make(tmp_path, content=source.read_bytes())
    converter._run = convert.run
    ask(converter, path="/home/natro/hello.txt", to="pdf")
    assert transfers.sent[0][0] == "hello.pdf" and transfers.sent[0][2].startswith(b"%PDF")

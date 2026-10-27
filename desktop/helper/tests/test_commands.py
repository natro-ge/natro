"""Commands on the PC: which lines run right away, and running them in the PowerShell kept open.

Most of these run PowerShell itself (its parser decides what a line would run): Windows PowerShell on
Windows, pwsh elsewhere. They are skipped where neither is installed.
"""
import os
import shutil

import pytest

from natro_pc import commands
from natro_pc.commands import Commands, PowerShell, harmless, opens_harmlessly, shown
from natro_pc.tools import ToolError

needs_powershell = pytest.mark.skipif(os.name != "nt" and not shutil.which("pwsh"),
                                      reason="PowerShell isn't installed")
THEMES = "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize"

HARMLESS = [
    "Get-Date",
    "Get-ChildItem $HOME | Sort-Object LastWriteTime -Descending | Select-Object -First 5 Name",
    "Get-Process | Where-Object CPU -gt 10 | Format-Table Name, CPU",
    "Get-ChildItem | ForEach-Object { $_.Name.ToUpper() }",
    "(Get-Date).AddDays(3).ToString('dddd')",
    "$free = (Get-PSDrive C).Free; [math]::Round($free / 1GB, 1)",
    "Get-NetIPAddress -AddressFamily IPv4 | Select-Object InterfaceAlias, IPAddress",
    "ipconfig /all",
    "ipconfig",
    "netsh wlan show interfaces",
    "ping -n 1 example.com",
    "Start-Process https://example.com",
    "Start-Process ms-settings:bluetooth",
    "saps 'shell:Downloads'",
    "Invoke-Item $HOME",
    f"Set-ItemProperty -Path {THEMES} -Name AppsUseLightTheme -Value 0",
    f"Set-ItemProperty {THEMES} SystemUsesLightTheme 1",
    "(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods).WmiSetBrightness(1, 60)",
    "Set-Clipboard -Value 'hi'",
    "rundll32.exe user32.dll,LockWorkStation",
    "Get-Process > $null",
    "Test-Connection example.com -Count 1",
    "$env:USERPROFILE",
    "Get-Content $HOME\\notes.txt | Select-String todo",
    "Write-Output 'გამარჯობა'",
]

# (line, a word from why it asks)
ASKS = [
    ("Remove-Item $HOME\\x.txt", "Remove-Item"),
    ("Get-ChildItem | Remove-Item", "Remove-Item"),
    ("Get-Date; Restart-Computer", "Restart-Computer"),
    ("Stop-Process -Name chrome", "Stop-Process"),
    ("Get-Process | ForEach-Object Kill", "method by name"),
    ("Get-Process | % { $_.Kill() }", ".Kill()"),
    ("[IO.File]::Delete('x')", ".Delete()"),
    ("ipconfig /release", "harmless list"),
    ("netsh wlan disconnect", "harmless list"),
    ("Get-Date > $HOME\\out.txt", "writes to"),
    ("Get-Process | Out-File x.txt", "Out-File"),
    ("Start-Process notepad -Verb RunAs", "admin"),
    ("Start-Process $HOME\\Downloads\\setup.exe", "start a program"),
    ("Start-Process file:///C:/x.exe", "start a program"),
    ("Start-Process notepad", "start a program"),
    ("Invoke-Expression 'Get-Date'", "Invoke-Expression"),
    ("iex 'Get-Date'", "Invoke-Expression"),
    ("& $command", "only known when it runs"),
    ("$env:Path = 'C:\\x'", "env:Path"),
    ("$PSDefaultParameterValues['*:Verb'] = 'RunAs'", "other than a variable"),
    ("$ExecutionContext.InvokeCommand.PreCommandLookupAction = { }", "other than a variable"),
    ("Set-ItemProperty HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run -Name x -Value y", "dark mode"),
    (f"Set-ItemProperty {THEMES} AppsUseLightTheme 7", "dark mode"),
    ("Get-ChildItem -Path $(Remove-Item x)", "Remove-Item"),
    ("function Get-Thing { Remove-Item y }; Get-Thing", "Remove-Item"),
    ("Get-Content \\\\server\\share\\x.txt", "another computer"),
    ("C:\\tools\\ipconfig.exe /all", "by its path"),
    ("cmd /c del x", "cmd"),
    ("shutdown /s /t 0", "shutdown"),
    ("Get-Date (", "can't read"),
    ("New-Item x.txt", "New-Item"),
    ("Set-Alias Get-Thing Remove-Item", "Set-Alias"),
    ("${C:\\x.txt} = 'hi'", "C:"),
    ("rundll32.exe shell32.dll,Control_RunDLL", "harmless list"),
    ("winget install Spotify", "harmless list"),
    ("Invoke-WebRequest https://example.com -OutFile x.zip", "Invoke-WebRequest"),
]


@pytest.fixture(scope="module")
def shell():
    shell = PowerShell()
    yield shell
    shell.stop()


@needs_powershell
@pytest.mark.parametrize("line", HARMLESS)
def test_harmless_lines_run_right_away(shell, line):
    assert harmless(shell.analyze(line)) is None


@needs_powershell
@pytest.mark.parametrize("line, why", ASKS)
def test_everything_else_asks(shell, line, why):
    problem = harmless(shell.analyze(line))
    assert problem and why in problem, problem


@needs_powershell
def test_the_brains_quick_pc_commands_are_all_harmless(shell):
    # "Lock the PC", "dark mode on"... run without a model (server/natro_agent/quick.py): never refused.
    quick = pytest.importorskip("natro_agent.quick")
    for _, line in quick.PC_COMMANDS:
        assert harmless(shell.analyze(line)) is None, line


def test_what_opening_may_open(tmp_path):
    (tmp_path / "report.pdf").write_text("x")
    assert opens_harmlessly(str(tmp_path))  # a folder
    assert opens_harmlessly(str(tmp_path / "report.pdf"))  # a document
    assert opens_harmlessly("https://example.com") and opens_harmlessly("ms-settings:display")
    assert opens_harmlessly("$HOME")
    for target in [str(tmp_path / "setup.exe"), str(tmp_path / "x.ps1"), "notepad", "file:///C:/x.exe",
                   "\\\\server\\share", "javascript:alert(1)", None, ""]:
        assert not opens_harmlessly(target), target


def test_an_answer_as_the_brain_reads_it():
    assert shown({"output": "\r\nName  CPU\n----  ---\nfoo   12   \n\n", "errors": []}) == "Name  CPU\n----  ---\nfoo   12"
    assert shown({"output": "", "errors": []}) == "Done."
    assert shown({"output": "a\n", "errors": ["b failed"]}) == "a\nErrors:\nb failed"
    with pytest.raises(ToolError, match="failed: nope"):
        shown({"output": "", "errors": ["nope"]})
    with pytest.raises(ToolError, match="still running"):
        shown({"timed_out": True})
    long = shown({"output": "x" * 10000, "errors": []})
    assert len(long) < 6200 and "[cut here: 10000 characters" in long


@needs_powershell
def test_running_commands(shell, tmp_path):
    pc = Commands(shell)
    assert pc.run("Write-Output 'გამარჯობა héllo'") == "გამარჯობა héllo"
    # Harmless lines share a runspace; a risky one gets its own, so it leaves nothing behind.
    assert pc.run("$x = 41; $x + 1") == "42"
    assert pc.run("$x") == "41"
    assert pc.run_risky("$y = 7") == "Done."
    assert pc.run("$y") == "Done."
    # Each command starts in his home folder.
    assert pc.run("Set-Location ..; (Get-Location).Path") != pc.run("(Get-Location).Path")
    with pytest.raises(ToolError, match="failed"):
        pc.run("Get-Item /no/such/file")
    with pytest.raises(ToolError, match="Say which"):
        pc.run("  ")


@needs_powershell
def test_a_risky_line_is_refused_and_nothing_happens(shell, tmp_path):
    victim = tmp_path / "keep.txt"
    victim.write_text("x")
    with pytest.raises(ToolError, match="run_command_risky"):
        Commands(shell).run(f"Remove-Item '{victim}'")
    assert victim.exists()
    assert Commands(shell).run_risky(f"Remove-Item '{victim}'") == "Done."
    assert not victim.exists()


@needs_powershell
def test_a_command_that_runs_too_long_is_stopped_and_the_next_one_runs(shell):
    with pytest.raises(ToolError, match="still running"):
        shown(shell.run("Start-Sleep 5", seconds=1), seconds=1)
    assert Commands(shell).run("Write-Output ok") == "ok"


@needs_powershell
def test_powershell_starts_again_after_it_stopped(shell):
    shell.stop()
    assert Commands(shell).run("Write-Output again") == "again"


def test_the_tools_as_the_brain_sees_them():
    run, risky = commands.tools(Commands(shell=object()))
    assert (run.name, run.personal, run.confirm) == ("run_command", True, False)
    assert (risky.name, risky.personal, risky.confirm) == ("run_command_risky", True, True)
    assert "run_command_risky" in run.description

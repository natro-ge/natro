# Natro's PowerShell for the PC's commands (pc/natro_pc/commands.py): started once and kept open, so a
# command doesn't wait for PowerShell to start. Written for Windows PowerShell 5.1 (PowerShell 7 runs it too).
#
# One JSON request per line on stdin, one JSON answer per line on stdout:
#   {"op": "check", "command": "..."}
#       what the line would run, from PowerShell's own parser, for commands.py to decide whether it's harmless:
#       {"errors": [...], "commands": [{"name", "command", "type", "path", "invocation", "parameters",
#        "arguments"}], "methods": [...], "redirections": [...], "assignments": [...], "drives": [...],
#        "values": [...]}
#   {"op": "run", "command": "...", "seconds": 30, "fresh": false}
#       {"output": "...", "errors": [...], "timed_out": false}
#       Harmless lines run in one runspace kept open (modules stay loaded); "fresh" ones (after his yes) in a
#       new runspace each, so nothing they define (an alias, a function) is there for the harmless ones later.
# A request that fails answers {"failed": "..."}.

$ErrorActionPreference = 'Stop'
$Lang = 'System.Management.Automation.Language'
$utf8 = New-Object System.Text.UTF8Encoding $false
$reader = New-Object System.IO.StreamReader -ArgumentList ([Console]::OpenStandardInput()), $utf8
$writer = New-Object System.IO.StreamWriter -ArgumentList ([Console]::OpenStandardOutput()), $utf8
$writer.AutoFlush = $true

if ([Environment]::OSVersion.Platform -eq 'Win32NT') {
    # Programs a command starts would otherwise share this PowerShell's stdin and stdout, the requests and
    # answers: one that reads its input (more, findstr) would eat the next request. They get NUL instead,
    # opened with CreateFile: Windows PowerShell's .NET won't open a device as a file.
    Add-Type -Namespace Natro -Name Std -MemberDefinition @'
[DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
static extern System.IntPtr CreateFile(string name, uint access, uint share, System.IntPtr security, uint mode,
    uint flags, System.IntPtr template);
[DllImport("kernel32.dll")] static extern bool SetHandleInformation(System.IntPtr handle, uint mask, uint flags);
[DllImport("kernel32.dll")] static extern bool SetStdHandle(int which, System.IntPtr handle);

public static bool UseNul() {
    // GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, OPEN_EXISTING
    System.IntPtr nul = CreateFile("NUL", 0xC0000000, 3, System.IntPtr.Zero, 3, 0, System.IntPtr.Zero);
    if (nul == new System.IntPtr(-1)) return false;
    SetHandleInformation(nul, 1, 1);  // HANDLE_FLAG_INHERIT: the programs started get it
    return SetStdHandle(-10, nul) && SetStdHandle(-11, nul);
}
'@
    [void][Natro.Std]::UseNul()
}

function New-Runspace {
    $runspace = [runspacefactory]::CreateRunspace()
    $runspace.ApartmentState = 'STA'  # the clipboard needs it
    $runspace.ThreadOptions = 'ReuseThread'
    $runspace.Open()
    return $runspace
}

$kept = New-Runspace
$known = @{}

function Find-Command([string]$name) {
    # What a name runs, as this PowerShell would find it: aliases resolved, programs with their path.
    if (-not $name) { return $null }
    if ($known.ContainsKey($name)) { return $known[$name] }
    $escaped = [System.Management.Automation.WildcardPattern]::Escape($name)
    $info = Get-Command -Name $escaped -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($info -and $info.CommandType -eq 'Alias') { $info = $info.ResolvedCommand }
    $found = $null
    if ($info) {
        $path = $null
        if ($info.CommandType -eq 'Application') { $path = $info.Source }
        $found = @{ name = $info.Name; type = [string]$info.CommandType; path = $path }
    }
    $known[$name] = $found
    return $found
}

function Test-PlainVariable($ast) {
    # $env:NAME or $HOME: known before running, and nothing runs to get them.
    return $ast -is [System.Management.Automation.Language.VariableExpressionAst] -and
        ($ast.VariablePath.DriveName -eq 'env' -or $ast.VariablePath.UserPath -eq 'HOME')
}

function Get-StaticValue($ast) {
    # The text an argument will be, when it's known before running: a plain string or number, a list of them
    # (a,b: programs get it as written), or $env:NAME and $HOME, alone or in a string (left as written:
    # commands.py fills them in). Else $null.
    if ($null -eq $ast) { return $null }
    if ($ast -is [System.Management.Automation.Language.StringConstantExpressionAst]) { return $ast.Value }
    if ($ast -is [System.Management.Automation.Language.ConstantExpressionAst]) { return [string]$ast.Value }
    if (Test-PlainVariable $ast) { return $ast.Extent.Text }
    if ($ast -is [System.Management.Automation.Language.ExpandableStringExpressionAst]) {
        foreach ($nested in $ast.NestedExpressions) {
            if (-not (Test-PlainVariable $nested)) { return $null }
        }
        return $ast.Value
    }
    if ($ast -is [System.Management.Automation.Language.ArrayLiteralAst]) {
        $parts = @()
        foreach ($element in $ast.Elements) {
            $part = Get-StaticValue $element
            if ($null -eq $part) { return $null }
            $parts += $part
        }
        return $parts -join ','
    }
    return $null
}

function Get-Analysis([string]$line) {
    $tokens = $null
    $parseErrors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseInput($line, [ref]$tokens, [ref]$parseErrors)
    $all = $ast.FindAll({ $true }, $true)

    $commands = @()
    foreach ($command in @($all | Where-Object { $_ -is [System.Management.Automation.Language.CommandAst] })) {
        $name = $command.GetCommandName()
        $found = Find-Command $name
        $parameters = @{}
        try {
            $binding = [System.Management.Automation.Language.StaticParameterBinder]::BindCommand($command, $true)
            foreach ($pair in $binding.BoundParameters.GetEnumerator()) {
                $value = $pair.Value.Value
                $text = $null
                if ($value) { $text = $value.Extent.Text }
                $parameters[$pair.Key] = @{
                    value = (Get-StaticValue $value)
                    block = ($value -is [System.Management.Automation.Language.ScriptBlockExpressionAst])
                    text = $text
                }
            }
        } catch { }
        $arguments = @()
        foreach ($element in @($command.CommandElements | Select-Object -Skip 1)) {
            if ($element -is [System.Management.Automation.Language.CommandParameterAst]) {
                $arguments += $element.Extent.Text
            } else {
                $arguments += , (Get-StaticValue $element)
            }
        }
        $commands += @{
            name = $name
            command = $(if ($found) { $found.name } else { $null })
            type = $(if ($found) { $found.type } else { $null })
            path = $(if ($found) { $found.path } else { $null })
            invocation = [string]$command.InvocationOperator
            parameters = $parameters
            arguments = $arguments
        }
    }

    $methods = @()
    foreach ($call in @($all | Where-Object { $_ -is [System.Management.Automation.Language.InvokeMemberExpressionAst] })) {
        if ($call.Member -is [System.Management.Automation.Language.StringConstantExpressionAst]) {
            $methods += $call.Member.Value
        } else {
            $methods += '*'  # a method chosen at run time
        }
    }

    $redirections = @()
    foreach ($redirection in @($all | Where-Object { $_ -is [System.Management.Automation.Language.FileRedirectionAst] })) {
        $redirections += $redirection.Location.Extent.Text
    }

    # What each assignment writes to: a plain variable ("x"), a drive's ("env:Path"), or anything else ("*").
    $assignments = @()
    foreach ($assignment in @($all | Where-Object { $_ -is [System.Management.Automation.Language.AssignmentStatementAst] })) {
        $target = $assignment.Left
        while ($target -is [System.Management.Automation.Language.ConvertExpressionAst]) { $target = $target.Child }
        if ($target -is [System.Management.Automation.Language.VariableExpressionAst]) {
            $assignments += $target.VariablePath.UserPath
        } else {
            $assignments += '*'
        }
    }

    # Variables that name a drive other than env: and variable: (${C:\file.txt} reads or writes a file).
    $drives = @()
    foreach ($variable in @($all | Where-Object { $_ -is [System.Management.Automation.Language.VariableExpressionAst] })) {
        $drive = $variable.VariablePath.DriveName
        if ($drive -and $drive -notin @('env', 'variable')) { $drives += $drive }
    }

    # Every plain value in the line, to catch network paths (\\server\share) wherever they are.
    $values = @()
    foreach ($constant in @($all | Where-Object { $_ -is [System.Management.Automation.Language.StringConstantExpressionAst] -or $_ -is [System.Management.Automation.Language.ExpandableStringExpressionAst] })) {
        $values += $constant.Value
    }

    return @{
        errors = @($parseErrors | ForEach-Object { $_.Message })
        commands = $commands
        methods = $methods
        redirections = $redirections
        assignments = $assignments
        drives = $drives
        values = $values
    }
}

function Invoke-Line([string]$line, [int]$seconds, [bool]$fresh) {
    $runspace = $kept
    if ($fresh) { $runspace = New-Runspace }
    $shell = [PowerShell]::Create()
    $shell.Runspace = $runspace
    try {
        $runspace.SessionStateProxy.Path.SetLocation($HOME) | Out-Null
        [void]$shell.AddScript($line).AddCommand('Out-String').AddParameter('Width', 200)
        $handle = $shell.BeginInvoke()
        if (-not $handle.AsyncWaitHandle.WaitOne($seconds * 1000)) {
            $shell.Stop()
            return @{ output = ''; errors = @(); timed_out = $true }
        }
        $output = (@($shell.EndInvoke($handle)) | ForEach-Object { [string]$_ }) -join ''
        $errors = @($shell.Streams.Error | ForEach-Object { $_.ToString() })
        return @{ output = $output; errors = $errors; timed_out = $false }
    } catch {
        return @{ output = ''; errors = @($_.Exception.Message); timed_out = $false }
    } finally {
        $shell.Dispose()
        if ($fresh) { $runspace.Dispose() }
    }
}

while ($null -ne ($line = $reader.ReadLine())) {
    try {
        $request = $line | ConvertFrom-Json
        if ($request.op -eq 'check') {
            $answer = Get-Analysis $request.command
        } elseif ($request.op -eq 'run') {
            $answer = Invoke-Line $request.command $request.seconds ([bool]$request.fresh)
        } else {
            $answer = @{ failed = "Unknown request: $($request.op)" }
        }
    } catch {
        $answer = @{ failed = $_.Exception.Message }
    }
    $writer.WriteLine((ConvertTo-Json -InputObject $answer -Compress -Depth 8))
}

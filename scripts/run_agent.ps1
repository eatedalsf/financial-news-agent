# Hardened PowerShell wrapper for Windows Task Scheduler.
#
# Activates the venv path, sets UTF-8 + unbuffered stdio, and launches
# the agent. Three log files land in logs/ on every run:
#   - task_scheduler_<stamp>.log         wrapper transcript (Set-Location,
#                                        env, exit code, any PS errors)
#   - task_scheduler_<stamp>_stdout.log  python's stdout
#   - task_scheduler_<stamp>_stderr.log  python's stderr (tracebacks!)
#
# Why three files instead of one Tee-Object'd file?
#   When the task runs under LogonType=Password (session 0, no console
#   host), PowerShell's "*>&1 | Tee-Object" pipeline drops native-exe
#   streams and the file is never created. cmd.exe's redirection
#   operators capture streams at the OS handle level and work reliably
#   in session 0. Start-Transcript catches anything that happens before
#   cmd is invoked.
#
# Task Scheduler action should be:
#   Program/script: powershell.exe
#   Arguments:      -NoProfile -NonInteractive -ExecutionPolicy Bypass `
#                   -File "E:\personal_projects\financial-news-agent\scripts\run_agent.ps1"
#   Start in:       E:\personal_projects\financial-news-agent

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUNBUFFERED = "1"

$LogDir = Join-Path $ProjectRoot "logs"
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

$Stamp      = Get-Date -Format "yyyy-MM-dd_HHmmss"
$Transcript = Join-Path $LogDir "task_scheduler_$Stamp.log"
$StdOut     = Join-Path $LogDir "task_scheduler_${Stamp}_stdout.log"
$StdErr     = Join-Path $LogDir "task_scheduler_${Stamp}_stderr.log"

Start-Transcript -Path $Transcript -Force | Out-Null

try {
    Write-Output "Wrapper started at $(Get-Date -Format 'o')"
    Write-Output "ProjectRoot : $ProjectRoot"
    Write-Output "WorkingDir  : $(Get-Location)"
    Write-Output "User        : $env:USERNAME (DOMAIN=$env:USERDOMAIN SESSION=$env:SESSIONNAME)"
    Write-Output "ComputerName: $env:COMPUTERNAME"

    $Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $Python)) {
        throw "Python not found at $Python"
    }
    Write-Output "PythonExe   : $Python"
    Write-Output "Stdout      : $StdOut"
    Write-Output "Stderr      : $StdErr"
    Write-Output "Launching   : python.exe -u -m src.main"

    # cmd.exe for the redirection — native handle-level capture, immune to
    # PowerShell session-0 pipeline quirks.
    & cmd /c "`"$Python`" -u -m src.main > `"$StdOut`" 2> `"$StdErr`""
    $code = $LASTEXITCODE

    Write-Output "Python exit code: $code"
}
catch {
    Write-Output "Wrapper exception: $_"
    Write-Output "ScriptStackTrace : $($_.ScriptStackTrace)"
    $code = 1
}
finally {
    Stop-Transcript | Out-Null
}

exit $code

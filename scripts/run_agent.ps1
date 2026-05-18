# PowerShell wrapper for Windows Task Scheduler.
# Activates the venv, sets UTF-8 stdio (so emojis in logs don't crash on cp1256),
# changes to the project root, then runs the agent.
#
# Why a wrapper instead of pointing Task Scheduler straight at python.exe?
#   - Sets PYTHONIOENCODING so log emojis don't trip the Windows console codepage.
#   - Logs both stdout and stderr to a dated file you can inspect after the fact.
#   - Sets cwd correctly even if Task Scheduler's "Start in" is misconfigured.
#
# Configure in Task Scheduler -> Action:
#   Program/script: powershell.exe
#   Add arguments:  -ExecutionPolicy Bypass -File "E:\personal_projects\financial-news-agent\scripts\run_agent.ps1"
#   Start in:       E:\personal_projects\financial-news-agent

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$env:PYTHONIOENCODING = "utf-8"

$LogDir = Join-Path $ProjectRoot "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

$Stamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$RunLog = Join-Path $LogDir "task_scheduler_$Stamp.log"

& "$ProjectRoot\.venv\Scripts\python.exe" -m src.main *>&1 | Tee-Object -FilePath $RunLog
exit $LASTEXITCODE

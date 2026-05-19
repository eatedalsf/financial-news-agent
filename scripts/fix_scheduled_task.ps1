# One-shot fix for the "Financial News Agent" Task Scheduler task.
#
# Re-registers the task so the daily 07:00 fire is reliable:
#   - LogonType: Password               (Run whether user is logged on or not)
#   - Action:    powershell.exe -File scripts\run_agent.ps1
#                (the wrapper sets UTF-8 stdio and writes a per-run log
#                 to logs\task_scheduler_<timestamp>.log)
#   - AllowStartIfOnBatteries:    $true
#   - DontStopIfGoingOnBatteries: $true
#   - WakeToRun:                  $true
#   - StartWhenAvailable:         $true   (catch up if 07:00 was missed)
#
# Preserves the existing 07:00 daily trigger and the existing RunLevel
# (so "Run with highest privileges" stays on if you set it).
#
# Usage (in an ELEVATED PowerShell window):
#   cd E:\personal_projects\financial-news-agent
#   .\scripts\fix_scheduled_task.ps1
#
# Prompts once for your Windows password. The password is required so
# Windows can store the credential and run the task when you're logged
# out. It is scrubbed from memory after Register-ScheduledTask returns.

#Requires -RunAsAdministrator

$ErrorActionPreference = "Stop"
$TaskName = "Financial News Agent"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WrapperPath = Join-Path $ProjectRoot "scripts\run_agent.ps1"

if (-not (Test-Path $WrapperPath)) {
    throw "Wrapper not found at: $WrapperPath"
}

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $existing) {
    throw "Task '$TaskName' not found. Create it first via taskschd.msc."
}

function Show-Task {
    param(
        [Parameter(Mandatory)] $Task,
        [string] $Label
    )
    Write-Host $Label -ForegroundColor Cyan
    foreach ($a in $Task.Actions) {
        Write-Host ("  Action        : {0} {1}" -f $a.Execute, $a.Arguments)
    }
    Write-Host ("  LogonType     : {0}" -f $Task.Principal.LogonType)
    Write-Host ("  RunLevel      : {0}" -f $Task.Principal.RunLevel)
    Write-Host ("  Batteries OK  : {0}" -f (-not $Task.Settings.DisallowStartIfOnBatteries))
    Write-Host ("  StopOnBatt    : {0}" -f $Task.Settings.StopIfGoingOnBatteries)
    Write-Host ("  WakeToRun     : {0}" -f $Task.Settings.WakeToRun)
    Write-Host ("  StartIfMissed : {0}" -f $Task.Settings.StartWhenAvailable)
    Write-Host ""
}

Show-Task -Task $existing -Label "BEFORE:"

$cred = Get-Credential -UserName $env:USERNAME -Message `
    "Enter your Windows password to enable 'Run whether user is logged on or not'"
$plainPwd = $cred.GetNetworkCredential().Password

try {
    $newAction = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$WrapperPath`"" `
        -WorkingDirectory $ProjectRoot

    $newSettings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -WakeToRun `
        -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Hours 1)

    Register-ScheduledTask -TaskName $TaskName `
        -Trigger $existing.Triggers `
        -Action $newAction `
        -Settings $newSettings `
        -User $cred.UserName `
        -Password $plainPwd `
        -RunLevel $existing.Principal.RunLevel `
        -Force | Out-Null
}
finally {
    # Scrub the plaintext password from memory even if registration threw.
    $plainPwd = $null
    [System.GC]::Collect()
}

$updated = Get-ScheduledTask -TaskName $TaskName
Show-Task -Task $updated -Label "AFTER:"

Write-Host "Done. To verify, kick off a manual run:" -ForegroundColor Green
Write-Host "  Start-ScheduledTask -TaskName `"$TaskName`""
Write-Host ""
Write-Host "Then check:"
Write-Host "  logs\task_scheduler_<timestamp>.log"
Write-Host ("  logs\reports\{0}.md" -f (Get-Date -Format 'yyyy-MM-dd'))

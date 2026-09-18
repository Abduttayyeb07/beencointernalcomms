<#
.SYNOPSIS
Schedules the Daily Blockchain Intelligence Briefing pipeline to run every morning at 09:00 AM using Windows Task Scheduler.

.DESCRIPTION
Creates or updates a scheduled task named "DailyBlockchainBriefing".
Executes `python run_briefing.py` in the project root daily at 9:00 AM local time.
#>

$ErrorActionPreference = "Stop"

$ProjectDir = "c:\Users\araaf\OneDrive\Desktop\Project\Internal Comms"
$PythonPath = (Get-Command python).Source
$ScriptPath = Join-Path $ProjectDir "run_briefing.py"
$TaskName = "DailyBlockchainBriefing"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "⚡ SCHEDULING DAILY BLOCKCHAIN INTELLIGENCE BRIEFING" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "Target Project: $ProjectDir"
Write-Host "Python Executable: $PythonPath"
Write-Host "Script: $ScriptPath"
Write-Host "Schedule: Daily at 09:00 AM"

try {
    $Action = New-ScheduledTaskAction -Execute $PythonPath -Argument "`"$ScriptPath`"" -WorkingDirectory $ProjectDir
    $Trigger = New-ScheduledTaskTrigger -Daily -At "09:00"
    $Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Force | Out-Null
    Write-Host "`n✅ Scheduled task '$TaskName' registered successfully!" -ForegroundColor Green
    Write-Host "It will trigger automatically every day at 09:00 AM." -ForegroundColor Green
} catch {
    Write-Host "`n⚠️ Failed to register scheduled task: $_" -ForegroundColor Red
    exit 1
}

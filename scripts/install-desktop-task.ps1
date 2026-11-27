# Registers a Windows scheduled task that starts the desktop (moomoo) bot on US trading days.
# Run once in PowerShell from the project folder:   powershell -ExecutionPolicy Bypass -File scripts\install-desktop-task.ps1
# Remove it later with:                              Unregister-ScheduledTask -TaskName "NewsSimDesk-moomoo"
#
# The task fires every 30 min for 10 h starting at 12:00 UTC (7:00 ET in winter / 8:00 ET in summer),
# which covers pre-market analysis through the 4pm ET close all year. It wakes the PC if it is asleep.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$taskName = "NewsSimDesk-moomoo"

# 12:00 UTC on a US weekday, expressed in this PC's local time zone
$utcStart = [DateTime]::SpecifyKind([DateTime]::UtcNow.Date.AddHours(12), [DateTimeKind]::Utc)
$localStart = $utcStart.ToLocalTime()
$dayShift = ($localStart.Date - $utcStart.Date).Days   # +1 for time zones far east of UTC, e.g. NZ
$usDays = @("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
$allDays = @("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")
$localDays = $usDays | ForEach-Object { $allDays[([array]::IndexOf($allDays, $_) + $dayShift + 7) % 7] }

$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $localDays -At $localStart.ToString("HH:mm")
$repeat = New-ScheduledTaskTrigger -Once -At $localStart.ToString("HH:mm") `
    -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Hours 10)
$trigger.Repetition = $repeat.Repetition

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$root\scripts\run-desktop.ps1`"" `
    -WorkingDirectory $root
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 14) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $taskName -Trigger $trigger -Action $action -Settings $settings `
    -Description "News Sim Desk: desktop bot with moomoo prices (paper simulation only)" -Force | Out-Null

Write-Host "Registered '$taskName': $($localDays -join ', ') from $($localStart.ToString('HH:mm')) local time, every 30 min for 10 h."
Write-Host "Keep moomoo OpenD running and logged in during US market hours."

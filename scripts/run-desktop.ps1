# Starts the desktop (moomoo prices) copy of the bot in its own window, titled "News Sim Desk bot".
# The scheduled task calls this every 30 min on US trading days. If the bot is already running, or the
# market is closed, nothing stays open. Closing the bot's window stops the bot for the day.
# Needs: moomoo OpenD running and logged in, and a .env file in the project folder (see .env.example).
#
# .env option DESKTOP_WINDOW = Minimized (default, sits in the taskbar) | Normal (pops up) | Hidden

param([switch]$InWindow)

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
# Fresh PATH from the registry, so git and other newly installed tools are found
$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")

# Load KEY=VALUE lines from .env into this process only
if (Test-Path "$root\.env") {
    foreach ($line in Get-Content "$root\.env") {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$') {
            [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2].Trim('"'), "Process")
        }
    }
}
New-Item -ItemType Directory -Force "$root\logs" | Out-Null
$log = "$root\logs\desktop-$(Get-Date -Format yyyyMMdd).log"
$python = "$root\.venv\Scripts\python.exe"

if (-not $InWindow) {
    # --- launcher (runs hidden from the scheduled task) ---
    $running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like "*bot.run*--prices moomoo*" }
    if ($running) { Add-Content -Encoding UTF8 $log "$(Get-Date -Format s) bot already running (pid $($running[0].ProcessId)), skipping"; exit 0 }
    if (-not (Test-Path "$root\.env")) { Add-Content -Encoding UTF8 $log "$(Get-Date -Format s) .env missing; copy .env.example to .env"; exit 1 }
    if (-not (Test-Path $python)) { Add-Content -Encoding UTF8 $log "$(Get-Date -Format s) .venv missing; see README desktop setup"; exit 1 }
    $style = if ($env:DESKTOP_WINDOW) { $env:DESKTOP_WINDOW } else { "Minimized" }
    Start-Process powershell.exe -WindowStyle $style -WorkingDirectory $root -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"", "-InWindow")
    exit 0
}

# --- the bot's own window ---
$Host.UI.RawUI.WindowTitle = "News Sim Desk bot (moomoo) - close this window to stop it"
Write-Host "News Sim Desk - desktop bot (moomoo prices). Paper simulation only, no real money." -ForegroundColor Cyan
Write-Host "Closing this window stops the bot for today. Log: $log`n" -ForegroundColor Cyan
$publish = if ($env:PUBLISH_DESKTOP -eq "0") { "" } else { "--publish" }
$env:PYTHONUNBUFFERED = "1"
cmd /c "`"$python`" -m bot.run --prices moomoo --max-runtime-min 720 $publish 2>&1" | ForEach-Object {
    Write-Host $_
    Add-Content -Path $log -Value $_ -Encoding UTF8
}
Write-Host "`nBot finished for today. This window closes in 2 minutes." -ForegroundColor Cyan
Start-Sleep -Seconds 120


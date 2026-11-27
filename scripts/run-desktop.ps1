# Runs the desktop (moomoo prices) copy of the bot once. The scheduled task calls this every 30 min on
# US trading days; if a copy is already running or the market is closed, it exits within seconds.
# Needs: moomoo OpenD running and logged in, and a .env file in the project folder (see .env.example).

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# Load KEY=VALUE lines from .env into this process only
if (Test-Path "$root\.env") {
    foreach ($line in Get-Content "$root\.env") {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$') {
            [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2].Trim('"'), "Process")
        }
    }
} else {
    Write-Error ".env not found in $root. Copy .env.example to .env and fill in your keys."
}

$python = "$root\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { Write-Error "Python venv missing. Run: python -m venv .venv; .venv\Scripts\pip install -r requirements-desktop.txt" }

New-Item -ItemType Directory -Force "$root\logs" | Out-Null
$log = "$root\logs\desktop-$(Get-Date -Format yyyyMMdd).log"

# Single-instance guard: skip if another desktop bot is already running
$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*bot.run*--prices moomoo*" }
if ($running) { Add-Content $log "$(Get-Date -Format s) already running (pid $($running.ProcessId)), skipping"; exit 0 }

$publish = @()
if ($env:PUBLISH_DESKTOP -ne "0") { $publish = @("--publish") }

$ErrorActionPreference = "Continue"   # python writes warnings to stderr; don't treat them as fatal
& $python -m bot.run --prices moomoo --max-runtime-min 720 @publish *>> $log
exit $LASTEXITCODE

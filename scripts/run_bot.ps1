# Production runner for Windows Task Scheduler on the ETL VM.
#
#   powershell -NonInteractive -ExecutionPolicy Bypass -File scripts\run_bot.ps1 live
#   powershell -NonInteractive -ExecutionPolicy Bypass -File scripts\run_bot.ps1 daily
#
# GitHub's scheduler ran the hourly job only 4-7 times a day (gaps up to 8h), so the
# schedule lives here instead; GitHub keeps the workflows for manual runs and tests.
# Output goes to logs\bot-YYYY-MM.log next to the repo.
param([Parameter(Mandatory = $true)][ValidateSet('live', 'daily')][string]$Mode)

# Not 'Stop': PowerShell 5.1 turns any stderr line from python (our INFO logging)
# into a terminating error and kills the run.
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $root 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("bot-{0:yyyy-MM}.log" -f (Get-Date))

# .env is also used for local testing and may point at the sandbox channel or say
# DRY_RUN=1; real environment variables win over .env, so pin production here.
# A single space, not '': assigning '' deletes the variable in PowerShell, and the
# sandbox value from .env would come back. config.py strips it to empty.
$env:SLACK_TEST_CHANNEL = ' '
$env:DRY_RUN = '0'
$env:PYTHONIOENCODING = 'utf-8'

Set-Location $root
Add-Content -Path $log -Encoding utf8 -Value ("==== {0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $Mode)
$python = Join-Path $root '.venv\Scripts\python.exe'
# cmd /c merges python's stderr into stdout as plain text, so PowerShell never
# wraps the log lines in error records.
cmd /c "`"$python`" src\main.py $Mode 2>&1" | Add-Content -Path $log -Encoding utf8
$code = $LASTEXITCODE
Add-Content -Path $log -Encoding utf8 -Value ("---- exit {0}" -f $code)
exit $code

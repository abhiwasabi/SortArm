<#
.SYNOPSIS
Run the sorter dashboard online through Tailscale Funnel. See docs/RUN_ONLINE.md.

.DESCRIPTION
Starts the bridge with its local port (no password, never exposed) and a public port (operator
login required), gives ONLY the public port to Tailscale Funnel, and turns Funnel off on exit.
The operator password is read from $env:BRIDGE_OPERATOR_PASSWORD and is never printed.

.EXAMPLE
$env:BRIDGE_OPERATOR_PASSWORD = "<long password>"; .\tools\run-online.ps1 -MockArm
#>
param(
    [int]$LocalPort = 4173,
    [int]$PublicPort = 4174,
    [switch]$Mock,      # simulated camera and arm
    [switch]$MockArm,   # real camera and Gemini, simulated arm
    [string[]]$BridgeArgs = @()
)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)

if ($LocalPort -eq $PublicPort) {
    Write-Error "The public port must differ from the local port: the local port has no password."
    exit 2
}
if (-not $env:BRIDGE_OPERATOR_PASSWORD -or $env:BRIDGE_OPERATOR_PASSWORD.Length -lt 8) {
    Write-Error 'Set $env:BRIDGE_OPERATOR_PASSWORD to a password of at least 8 characters in this window first.'
    exit 2
}

$tailscale = (Get-Command tailscale -ErrorAction SilentlyContinue).Source
if (-not $tailscale -and (Test-Path 'C:\Program Files\Tailscale\tailscale.exe')) {
    $tailscale = 'C:\Program Files\Tailscale\tailscale.exe'
}
if (-not $tailscale) {
    Write-Error 'Tailscale is not installed. Run: winget install Tailscale.Tailscale (then sign in).'
    exit 2
}
$status = & $tailscale status --json | ConvertFrom-Json
$publicHost = "$($status.Self.DNSName)".TrimEnd('.')
if (-not $publicHost) {
    Write-Error 'Tailscale is not signed in or has no DNS name. Sign in from the Tailscale tray icon.'
    exit 2
}

$python = Join-Path (Get-Location) '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }
$bridge = @('-m', 'bridge', '--port', "$LocalPort", '--public-port', "$PublicPort", '--public-host', $publicHost)
if ($Mock) { $bridge += '--mock' } elseif ($MockArm) { $bridge += '--mock-arm' }
$bridge += $BridgeArgs

Write-Host "Local dashboard (this laptop only, no password): http://127.0.0.1:$LocalPort"
Write-Host "Public dashboard (share this):                   https://$publicHost"
Write-Host "Giving ONLY port $PublicPort to Tailscale Funnel. Press Ctrl+C to stop."
& $tailscale funnel --bg "$PublicPort"
# Poll instead of waiting inside Python. Ctrl+C then reaches this finally
# even when the bridge is stuck, and Funnel is turned off either way.
$proc = Start-Process -FilePath $python -ArgumentList $bridge -WorkingDirectory (Get-Location) -NoNewWindow -PassThru
try {
    while (-not $proc.HasExited) {
        Start-Sleep -Milliseconds 200
    }
}
finally {
    if (-not $proc.HasExited) {
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        $proc.WaitForExit()
    }
    Write-Host 'Turning Tailscale Funnel off.'
    & $tailscale funnel reset
    & $tailscale funnel status
}
if ($null -ne $proc.ExitCode -and $proc.ExitCode -ne 0) {
    exit $proc.ExitCode
}

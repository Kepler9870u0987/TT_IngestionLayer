<#
.SYNOPSIS
    Gracefully stop the Email Ingestion producer and worker.
.EXAMPLE
    .\scripts\stop.ps1
    .\scripts\stop.ps1 -Timeout 60
#>
[CmdletBinding()]
param(
    [int]$Timeout = 30
)

$ErrorActionPreference = "Stop"
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$PidDir     = Join-Path $ProjectDir "pids"

function Write-Log { param([string]$Msg); Write-Host "[$(Get-Date -Format 'yyyy-MM-ddTHH:mm:ssZ')] $Msg" }

function Stop-Component {
    param([string]$Name)

    $pidFile = Join-Path $PidDir "$Name.pid"
    if (-not (Test-Path $pidFile)) {
        Write-Log "${Name}: no PID file found"
        return
    }

    $pid = [int](Get-Content $pidFile)
    $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue

    if (-not $proc) {
        Write-Log "${Name}: process $pid not running (stale PID file)"
        Remove-Item $pidFile -Force
        return
    }

    Write-Log "${Name}: stopping PID $pid…"

    # Send Ctrl+C equivalent (graceful)
    try {
        Stop-Process -Id $pid -ErrorAction SilentlyContinue
    } catch {}

    # Wait for exit
    $waited = 0
    while ($waited -lt $Timeout) {
        $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
        if (-not $proc) { break }
        Start-Sleep -Seconds 1
        $waited++
    }

    $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
    if ($proc) {
        Write-Log "${Name}: still running after ${Timeout}s – force killing"
        Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
    } else {
        Write-Log "${Name}: stopped gracefully (${waited}s)"
    }

    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

Stop-Component "producer"
Stop-Component "worker"

Write-Log "All processes stopped."

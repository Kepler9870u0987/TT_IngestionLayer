<#
.SYNOPSIS
    Start the Email Ingestion producer and worker.
.DESCRIPTION
    Launches producer.py and worker.py as background processes,
    storing PIDs in the pids/ directory.
.EXAMPLE
    .\scripts\start.ps1
    .\scripts\start.ps1 -ProducerArgs "--dry-run"
#>
[CmdletBinding()]
param(
    [string]$ProducerArgs = "",
    [string]$WorkerArgs   = "",
    [string]$RedisHost    = $env:REDIS_HOST,
    [int]   $RedisPort    = $(if ($env:REDIS_PORT) { [int]$env:REDIS_PORT } else { 6379 })
)

$ErrorActionPreference = "Stop"
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$PidDir     = Join-Path $ProjectDir "pids"
$LogDir     = Join-Path $ProjectDir "logs"

New-Item -ItemType Directory -Force -Path $PidDir | Out-Null
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-Log { param([string]$Msg); Write-Host "[$(Get-Date -Format 'yyyy-MM-ddTHH:mm:ssZ')] $Msg" }

# ---- Pre-flight checks ----
$Python = Get-Command python -ErrorAction SilentlyContinue
if (-not $Python) { $Python = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $Python) { Write-Error "Python not found. Activate your virtual environment."; exit 1 }
Write-Log "Using Python: $($Python.Source)"

# Activate venv if present
$VenvActivate = Join-Path $ProjectDir "venv\Scripts\Activate.ps1"
if (Test-Path $VenvActivate) {
    & $VenvActivate
    Write-Log "Activated virtualenv"
}

# Check Redis
if (-not $RedisHost) { $RedisHost = "localhost" }
try {
    $tcp = New-Object System.Net.Sockets.TcpClient
    $tcp.Connect($RedisHost, $RedisPort)
    $tcp.Close()
    Write-Log "Redis reachable at ${RedisHost}:${RedisPort}"
} catch {
    Write-Log "WARNING: Redis not reachable at ${RedisHost}:${RedisPort}"
}

# ---- Start Producer ----
$ProducerPidFile = Join-Path $PidDir "producer.pid"
$ProducerRunning = $false
if (Test-Path $ProducerPidFile) {
    $procId = Get-Content $ProducerPidFile
    $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
    if ($proc) { $ProducerRunning = $true }
}

if ($ProducerRunning) {
    Write-Log "Producer already running (PID $procId)"
} else {
    Write-Log "Starting producer…"
    $pArgs = "producer.py $ProducerArgs".Trim()
    $proc = Start-Process -FilePath $Python.Source -ArgumentList $pArgs `
        -WorkingDirectory $ProjectDir `
        -RedirectStandardOutput (Join-Path $LogDir "producer.log") `
        -RedirectStandardError  (Join-Path $LogDir "producer_err.log") `
        -PassThru -WindowStyle Hidden
    $proc.Id | Out-File $ProducerPidFile -Encoding ascii
    Write-Log "Producer started (PID $($proc.Id))"
}

# ---- Start Worker ----
$WorkerPidFile = Join-Path $PidDir "worker.pid"
$WorkerRunning = $false
if (Test-Path $WorkerPidFile) {
    $procId = Get-Content $WorkerPidFile
    $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
    if ($proc) { $WorkerRunning = $true }
}

if ($WorkerRunning) {
    Write-Log "Worker already running (PID $procId)"
} else {
    Write-Log "Starting worker…"
    $wArgs = "worker.py $WorkerArgs".Trim()
    $proc = Start-Process -FilePath $Python.Source -ArgumentList $wArgs `
        -WorkingDirectory $ProjectDir `
        -RedirectStandardOutput (Join-Path $LogDir "worker.log") `
        -RedirectStandardError  (Join-Path $LogDir "worker_err.log") `
        -PassThru -WindowStyle Hidden
    $proc.Id | Out-File $WorkerPidFile -Encoding ascii
    Write-Log "Worker started (PID $($proc.Id))"
}

Write-Log "Done.  Use scripts\stop.ps1 to stop."
exit 0

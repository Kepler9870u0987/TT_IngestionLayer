<#
.SYNOPSIS
    Quick health-check against the running Email Ingestion system.
.EXAMPLE
    .\scripts\health_check.ps1
    .\scripts\health_check.ps1 -Component Worker
    .\scripts\health_check.ps1 -Component Producer -Host 10.0.0.5
#>
[CmdletBinding()]
param(
    [string]$HostName    = "localhost",
    [ValidateSet("Both", "Producer", "Worker")]
    [string]$Component   = "Both",
    [string]$RedisHost   = $(if ($env:REDIS_HOST) { $env:REDIS_HOST } else { "localhost" }),
    [int]   $RedisPort   = $(if ($env:REDIS_PORT) { [int]$env:REDIS_PORT } else { 6379 })
)

$ExitCode = 0
function Write-Log  { param([string]$Msg); Write-Host "[$(Get-Date -Format 'yyyy-MM-ddTHH:mm:ssZ')] $Msg" }
function Write-Ok   { param([string]$Msg); Write-Host "[$(Get-Date -Format 'yyyy-MM-ddTHH:mm:ssZ')] + $Msg" -ForegroundColor Green }
function Write-Fail { param([string]$Msg); Write-Host "[$(Get-Date -Format 'yyyy-MM-ddTHH:mm:ssZ')] x $Msg" -ForegroundColor Red; $script:ExitCode = 1 }

# Component port configuration
$ProducerHealthPort = 8080
$ProducerMetricsPort = 9090
$WorkerHealthPort = 8081
$WorkerMetricsPort = 9091

function Test-ComponentHealth {
    param(
        [string]$Name,
        [int]$HealthPort,
        [int]$MetricsPort
    )
    
    Write-Host ""
    Write-Host "=== $Name ===" -ForegroundColor Cyan
    
    # ---- Liveness ----
    try {
        $r = Invoke-WebRequest -Uri "http://${HostName}:${HealthPort}/health" -UseBasicParsing -TimeoutSec 5
        Write-Ok "Liveness: http://${HostName}:${HealthPort}/health"
    } catch {
        Write-Fail "Liveness: http://${HostName}:${HealthPort}/health"
    }

    # ---- Readiness ----
    try {
        $r = Invoke-WebRequest -Uri "http://${HostName}:${HealthPort}/ready" -UseBasicParsing -TimeoutSec 5
        if ($r.StatusCode -eq 200) {
            Write-Ok "Readiness: http://${HostName}:${HealthPort}/ready"
        } else {
            Write-Fail "Readiness: http://${HostName}:${HealthPort}/ready  (HTTP $($r.StatusCode))"
        }
    } catch {
        Write-Fail "Readiness: http://${HostName}:${HealthPort}/ready"
    }

    # ---- Full Status ----
    try {
        $r = Invoke-WebRequest -Uri "http://${HostName}:${HealthPort}/status" -UseBasicParsing -TimeoutSec 5
        Write-Ok "Status: http://${HostName}:${HealthPort}/status"
    } catch {
        Write-Fail "Status: http://${HostName}:${HealthPort}/status"
    }

    # ---- Prometheus Metrics ----
    try {
        $r = Invoke-WebRequest -Uri "http://${HostName}:${MetricsPort}/metrics" -UseBasicParsing -TimeoutSec 5
        Write-Ok "Metrics: http://${HostName}:${MetricsPort}/metrics"
    } catch {
        Write-Log "  Metrics: http://${HostName}:${MetricsPort}/metrics  (not reachable - may not be running)"
    }
}

# Check components based on parameter
if ($Component -eq "Both" -or $Component -eq "Producer") {
    Test-ComponentHealth -Name "Producer" -HealthPort $ProducerHealthPort -MetricsPort $ProducerMetricsPort
}

if ($Component -eq "Both" -or $Component -eq "Worker") {
    Test-ComponentHealth -Name "Worker" -HealthPort $WorkerHealthPort -MetricsPort $WorkerMetricsPort
}

# ---- Redis ----
Write-Host ""
Write-Host "=== Infrastructure ===" -ForegroundColor Cyan
try {
    $tcp = New-Object System.Net.Sockets.TcpClient
    $tcp.Connect($RedisHost, $RedisPort)
    $tcp.Close()
    Write-Ok "Redis: ${RedisHost}:${RedisPort}"
} catch {
    Write-Fail "Redis: ${RedisHost}:${RedisPort}"
}

exit $ExitCode

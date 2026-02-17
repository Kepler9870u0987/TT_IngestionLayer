<#
.SYNOPSIS
    Quick health-check against the running Email Ingestion system.
.EXAMPLE
    .\scripts\health_check.ps1
    .\scripts\health_check.ps1 -Host 10.0.0.5 -HealthPort 8080
#>
[CmdletBinding()]
param(
    [string]$HostName    = "localhost",
    [int]   $HealthPort  = 8080,
    [int]   $MetricsPort = 9090,
    [string]$RedisHost   = $(if ($env:REDIS_HOST) { $env:REDIS_HOST } else { "localhost" }),
    [int]   $RedisPort   = $(if ($env:REDIS_PORT) { [int]$env:REDIS_PORT } else { 6379 })
)

$ExitCode = 0
function Write-Log  { param([string]$Msg); Write-Host "[$(Get-Date -Format 'yyyy-MM-ddTHH:mm:ssZ')] $Msg" }
function Write-Ok   { param([string]$Msg); Write-Host "[$(Get-Date -Format 'yyyy-MM-ddTHH:mm:ssZ')] + $Msg" -ForegroundColor Green }
function Write-Fail { param([string]$Msg); Write-Host "[$(Get-Date -Format 'yyyy-MM-ddTHH:mm:ssZ')] x $Msg" -ForegroundColor Red; $script:ExitCode = 1 }

# ---- Liveness ----
try {
    $r = Invoke-WebRequest -Uri "http://${HostName}:${HealthPort}/health" -UseBasicParsing -TimeoutSec 5
    Write-Ok "Liveness: http://${HostName}:${HealthPort}/health  (HTTP $($r.StatusCode))"
} catch {
    Write-Fail "Liveness: http://${HostName}:${HealthPort}/health"
}

# ---- Readiness ----
try {
    $r = Invoke-WebRequest -Uri "http://${HostName}:${HealthPort}/ready" -UseBasicParsing -TimeoutSec 5
    if ($r.StatusCode -eq 200) {
        Write-Ok "Readiness: http://${HostName}:${HealthPort}/ready  (HTTP $($r.StatusCode))"
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
    Write-Host "---"
    $r.Content | ConvertFrom-Json | ConvertTo-Json -Depth 10
    Write-Host "---"
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

# ---- Redis ----
try {
    $tcp = New-Object System.Net.Sockets.TcpClient
    $tcp.Connect($RedisHost, $RedisPort)
    $tcp.Close()
    Write-Ok "Redis: ${RedisHost}:${RedisPort}"
} catch {
    Write-Fail "Redis: ${RedisHost}:${RedisPort}"
}

exit $ExitCode

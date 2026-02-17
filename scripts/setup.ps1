<#
.SYNOPSIS
    Setup completo dell'Email Ingestion System su Windows.
.DESCRIPTION
    Configura l'ambiente: crea venv, installa dipendenze, crea .env, verifica Redis.
.EXAMPLE
    .\scripts\setup.ps1
    .\scripts\setup.ps1 -SkipRedisCheck
#>
[CmdletBinding()]
param(
    [switch]$SkipRedisCheck
)

$ErrorActionPreference = "Stop"
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir

function Write-Log { 
    param([string]$Msg, [string]$Color = "White")
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] $Msg" -ForegroundColor $Color
}

function Write-Success { param([string]$Msg); Write-Log "[OK] $Msg" "Green" }
function Write-Error { param([string]$Msg); Write-Log "[ERR] $Msg" "Red" }
function Write-Info { param([string]$Msg); Write-Log ">> $Msg" "Cyan" }

Write-Log "=== Email Ingestion System - Setup Windows ===" "Yellow"
Write-Log ""

# ---- Verifica Python ----
Write-Info "Verifico Python..."
$Python = Get-Command python -ErrorAction SilentlyContinue
if (-not $Python) {
    $Python = Get-Command python3 -ErrorAction SilentlyContinue
}
if (-not $Python) {
    Write-Error "Python non trovato. Installa Python 3.11+ e aggiungi al PATH."
    exit 1
}

$PyVersion = & $Python.Source --version
Write-Success "Python trovato: $PyVersion"

# ---- Crea Virtual Environment ----
$VenvPath = Join-Path $ProjectDir ".venv"
if (Test-Path $VenvPath) {
    Write-Info "Virtual environment già esistente: $VenvPath"
} else {
    Write-Info "Creo virtual environment in .venv..."
    & $Python.Source -m venv $VenvPath
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Fallito creazione venv"
        exit 1
    }
    Write-Success "Virtual environment creato"
}

# ---- Attiva venv ----
$ActivateScript = Join-Path $VenvPath "Scripts\Activate.ps1"
if (Test-Path $ActivateScript) {
    Write-Info "Attivo virtual environment..."
    & $ActivateScript
    Write-Success "Virtual environment attivato"
} else {
    Write-Error "Script di attivazione non trovato"
    exit 1
}

# ---- Installa dipendenze ----
Write-Info "Installo dipendenze..."
$RequirementsFile = Join-Path $ProjectDir "requirements.txt"
if (Test-Path $RequirementsFile) {
    & python -m pip install --upgrade pip -q
    & pip install -r $RequirementsFile -q
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Installazione requirements.txt fallita"
        exit 1
    }
    Write-Success "requirements.txt installato"
} else {
    Write-Error "requirements.txt non trovato"
    exit 1
}

$RequirementsDevFile = Join-Path $ProjectDir "requirements-dev.txt"
if (Test-Path $RequirementsDevFile) {
    & pip install -r $RequirementsDevFile -q
    Write-Success "requirements-dev.txt installato"
}

# ---- Crea .env se non esiste ----
$EnvFile = Join-Path $ProjectDir ".env"
$EnvExampleFile = Join-Path $ProjectDir ".env.example"
if (-not (Test-Path $EnvFile)) {
    if (Test-Path $EnvExampleFile) {
        Write-Info "Creo .env da .env.example..."
        Copy-Item $EnvExampleFile $EnvFile
        Write-Success ".env creato"
        Write-Log "  >> Modifica .env con le tue credenziali OAuth2!" "Yellow"
    } else {
        Write-Error ".env.example non trovato"
    }
} else {
    Write-Success ".env già esistente"
}

# ---- Crea directories logs e pids ----
$LogsDir = Join-Path $ProjectDir "logs"
$PidsDir = Join-Path $ProjectDir "pids"
New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null
New-Item -ItemType Directory -Force -Path $PidsDir | Out-Null
Write-Success "Directory logs/ e pids/ create"

# ---- Verifica Redis ----
if (-not $SkipRedisCheck) {
    Write-Info "Verifico connessione Redis..."
    $RedisHost = "localhost"
    $RedisPort = 6379
    
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $tcp.Connect($RedisHost, $RedisPort)
        $tcp.Close()
        Write-Success "Redis raggiungibile su ${RedisHost}:${RedisPort}"
    } catch {
        Write-Error "Redis non raggiungibile su ${RedisHost}:${RedisPort}"
        Write-Log "  >> Avvia Redis con: redis-server" "Yellow"
        Write-Log "  >> O con Docker: docker run -d -p 6379:6379 redis:7-alpine" "Yellow"
    }
}

# ---- Riepilogo ----
Write-Log ""
Write-Log "=== Setup completato! ===" "Green"
Write-Log ""
Write-Log "Prossimi passi:" "Yellow"
Write-Log "1. Modifica .env con le tue credenziali OAuth2 Google" "White"
Write-Log "2. Configura OAuth2: python producer.py --auth-setup" "White"
Write-Log "3. Avvia sistema: .\scripts\start.ps1" "White"
Write-Log "4. Verifica health: .\scripts\health_check.ps1" "White"
Write-Log "5. Ferma sistema: .\scripts\stop.ps1" "White"
Write-Log ""
Write-Log "Per test: .\scripts\test.ps1" "Cyan"
Write-Log ""
exit 0

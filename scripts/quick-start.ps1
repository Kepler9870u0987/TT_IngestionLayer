<#
.SYNOPSIS
    Quick start guidato per Email Ingestion System su Windows.
.DESCRIPTION
    Script interattivo che guida attraverso setup, configurazione e avvio del sistema.
.EXAMPLE
    .\scripts\quick-start.ps1
#>

$ErrorActionPreference = "Stop"
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir

function Write-Header { 
    param([string]$Text)
    Write-Host ""
    Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Cyan
    Write-Host "  $Text" -ForegroundColor Yellow
    Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Cyan
    Write-Host ""
}

function Write-Step { 
    param([int]$Num, [string]$Text)
    Write-Host ""
    Write-Host "[$Num] $Text" -ForegroundColor Green
    Write-Host ""
}

function Wait-Step {
    Write-Host ""
    Write-Host "Premi INVIO per continuare..." -ForegroundColor Gray
    $null = Read-Host
}

Clear-Host

Write-Header "Email Ingestion System - Quick Start Windows"

Write-Host "Questo script ti guiderà attraverso:" -ForegroundColor White
Write-Host "  1. Setup ambiente (venv, dipendenze)" -ForegroundColor Gray
Write-Host "  2. Configurazione OAuth2 Gmail" -ForegroundColor Gray
Write-Host "  3. Avvio del sistema" -ForegroundColor Gray
Write-Host "  4. Test e monitoraggio" -ForegroundColor Gray

Wait-Step

# ═══════════════════════════════════════════════════════════
# STEP 1: Setup
# ═══════════════════════════════════════════════════════════
Write-Step 1 "Setup ambiente"

Write-Host "Verifico se il setup è già stato fatto..." -ForegroundColor Cyan
$VenvPath = Join-Path $ProjectDir ".venv"
$EnvFile = Join-Path $ProjectDir ".env"

if ((Test-Path $VenvPath) -and (Test-Path $EnvFile)) {
    Write-Host "[OK] Setup già completato" -ForegroundColor Green
    $DoSetup = Read-Host "Vuoi eseguire di nuovo il setup? (si/no)"
} else {
    Write-Host "Setup necessario" -ForegroundColor Yellow
    $DoSetup = "si"
}

if ($DoSetup -eq "si") {
    & "$ScriptDir\setup.ps1"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERR] Setup fallito" -ForegroundColor Red
        exit 1
    }
}

Wait-Step

# ═══════════════════════════════════════════════════════════
# STEP 2: Configurazione OAuth2
# ═══════════════════════════════════════════════════════════
Write-Step 2 "Configurazione OAuth2 Gmail"

Write-Host "Prima di procedere, assicurati di aver configurato:" -ForegroundColor Yellow
Write-Host "  • Google Cloud Project con Gmail API abilitata" -ForegroundColor White
Write-Host "  • OAuth2 credentials (client_id, client_secret)" -ForegroundColor White
Write-Host "  • Credenziali salvate in .env file" -ForegroundColor White
Write-Host ""
Write-Host "Guida completa: docs\OAUTH2_SETUP.md" -ForegroundColor Cyan

$TokenFile = Join-Path $ProjectDir "tokens\gmail_token.json"
if (Test-Path $TokenFile) {
    Write-Host "[OK] Token OAuth2 trovato" -ForegroundColor Green
    $DoAuth = Read-Host "Vuoi riconfigurare OAuth2? (si/no)"
} else {
    Write-Host "Token OAuth2 non trovato" -ForegroundColor Yellow
    $DoAuth = Read-Host "Vuoi configurare OAuth2 ora? (si/no)"
}

if ($DoAuth -eq "si") {
    $Username = Read-Host "Inserisci il tuo indirizzo Gmail (lascia vuoto per usare IMAP_USER dal .env)"
    if ($Username) {
        & "$ScriptDir\oauth-setup.ps1" -Action Setup -Username $Username
    } else {
        & "$ScriptDir\oauth-setup.ps1" -Action Setup
    }
    
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERR] Configurazione OAuth2 fallita" -ForegroundColor Red
        exit 1
    }
}

Wait-Step

# ═══════════════════════════════════════════════════════════
# STEP 3: Verifica Redis
# ═══════════════════════════════════════════════════════════
Write-Step 3 "Verifica Redis"

Write-Host "Verifico che Redis sia in esecuzione..." -ForegroundColor Cyan
try {
    $tcp = New-Object System.Net.Sockets.TcpClient
    $tcp.Connect("localhost", 6379)
    $tcp.Close()
    Write-Host "[OK] Redis raggiungibile su localhost:6379" -ForegroundColor Green
} catch {
    Write-Host "[ERR] Redis non raggiungibile" -ForegroundColor Red
    Write-Host ""
    Write-Host "Avvia Redis con uno di questi comandi:" -ForegroundColor Yellow
    Write-Host "  • redis-server" -ForegroundColor White
    Write-Host "  • docker run -d -p 6379:6379 redis:7-alpine" -ForegroundColor White
    Write-Host "  • wsl redis-server" -ForegroundColor White
    Write-Host ""
    $Continue = Read-Host "Vuoi continuare comunque? (si/no)"
    if ($Continue -ne "si") {
        exit 1
    }
}

Wait-Step

# ═══════════════════════════════════════════════════════════
# STEP 4: Test (opzionale)
# ═══════════════════════════════════════════════════════════
Write-Step 4 "Test sistema (opzionale)"

$DoTest = Read-Host "Vuoi eseguire i test prima di avviare? (si/no)"
if ($DoTest -eq "si") {
    & "$ScriptDir\test.ps1" -Type Unit
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERR] Test falliti" -ForegroundColor Red
        $Continue = Read-Host "Vuoi continuare comunque? (si/no)"
        if ($Continue -ne "si") {
            exit 1
        }
    }
}

Wait-Step

# ═══════════════════════════════════════════════════════════
# STEP 5: Avvio sistema
# ═══════════════════════════════════════════════════════════
Write-Step 5 "Avvio sistema"

Write-Host "Avvio producer e worker..." -ForegroundColor Cyan
& "$ScriptDir\start.ps1"

if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERR] Avvio fallito" -ForegroundColor Red
    exit 1
}

Start-Sleep -Seconds 3

# ═══════════════════════════════════════════════════════════
# STEP 6: Health check
# ═══════════════════════════════════════════════════════════
Write-Step 6 "Verifica health"

& "$ScriptDir\health_check.ps1"

# ═══════════════════════════════════════════════════════════
# Completato
# ═══════════════════════════════════════════════════════════
Write-Header "Sistema avviato con successo!"

Write-Host "[OK] Producer e Worker in esecuzione" -ForegroundColor Green
Write-Host ""
Write-Host "Comandi utili:" -ForegroundColor Yellow
Write-Host "  • Health check:  .\scripts\health_check.ps1" -ForegroundColor White
Write-Host "  • Stop sistema:  .\scripts\stop.ps1" -ForegroundColor White
Write-Host "  • View logs:     Get-Content logs\producer.log -Tail 50 -Wait" -ForegroundColor White
Write-Host "  • Redis CLI:     redis-cli XLEN email_ingestion_stream" -ForegroundColor White
Write-Host ""
Write-Host "Dashboard:" -ForegroundColor Yellow
Write-Host "  • Health:   http://localhost:8080/health" -ForegroundColor White
Write-Host "  • Metrics:  http://localhost:9090/metrics" -ForegroundColor White
Write-Host ""
Write-Host "Logs in tempo reale:" -ForegroundColor Cyan
Write-Host "  Get-Content logs\producer.log -Tail 20 -Wait" -ForegroundColor Gray
Write-Host ""

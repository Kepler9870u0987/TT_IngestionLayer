<#
.SYNOPSIS
    Lancia i test dell'Email Ingestion System.
.DESCRIPTION
    Esegue unit test, integration test e load test con opzioni configurabili.
.EXAMPLE
    .\scripts\test.ps1
    .\scripts\test.ps1 -Coverage
    .\scripts\test.ps1 -Type Unit -Verbose
    .\scripts\test.ps1 -Type Load -LoadEmails 5000
#>
[CmdletBinding()]
param(
    [ValidateSet("All", "Unit", "Integration", "Load")]
    [string]$Type = "Unit",
    
    [switch]$Coverage,
    
    [int]$LoadEmails = 1000,
    
    [int]$LoadWorkers = 2
)

$ErrorActionPreference = "Stop"
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir

function Write-Log { 
    param([string]$Msg, [string]$Color = "White")
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] $Msg" -ForegroundColor $Color
}

Write-Log "=== Email Ingestion System - Test Runner ===" "Yellow"
Write-Log ""

# ---- Attiva venv ----
$VenvActivate = Join-Path $ProjectDir ".venv\Scripts\Activate.ps1"
if (Test-Path $VenvActivate) {
    & $VenvActivate
    Write-Log "Virtual environment attivato" "Green"
} else {
    Write-Log "Virtual environment non trovato. Esegui .\scripts\setup.ps1" "Red"
    exit 1
}

# ---- Costruisci comando pytest ----
$PytestArgs = @()

switch ($Type) {
    "Unit" {
        Write-Log "Eseguo UNIT tests..." "Cyan"
        $PytestArgs += "tests/unit/"
    }
    "Integration" {
        Write-Log "Eseguo INTEGRATION tests..." "Cyan"
        $PytestArgs += "tests/integration/"
    }
    "Load" {
        Write-Log "Eseguo LOAD tests ($LoadEmails emails, $LoadWorkers workers)..." "Cyan"
        & python -m tests.load.load_test --emails $LoadEmails --workers $LoadWorkers
        exit $LASTEXITCODE
    }
    "All" {
        Write-Log "Eseguo TUTTI i test..." "Cyan"
        $PytestArgs += "tests/"
    }
}

if ($PSBoundParameters.ContainsKey('Verbose')) {
    $PytestArgs += "-v"
} else {
    $PytestArgs += "-q"
}

if ($Coverage) {
    $PytestArgs += "--cov=src"
    $PytestArgs += "--cov-report=html"
    $PytestArgs += "--cov-report=term"
    Write-Log "Coverage report attivato (htmlcov/index.html)" "Cyan"
}

$PytestArgs += "--tb=short"

# ---- Esegui pytest ----
Write-Log ""
Write-Log "Comando: pytest $($PytestArgs -join ' ')" "Gray"
Write-Log ""

& pytest @PytestArgs

$ExitCode = $LASTEXITCODE

# ---- Riepilogo ----
Write-Log ""
if ($ExitCode -eq 0) {
    Write-Log "[OK] Test completati con successo!" "Green"
    if ($Coverage -and (Test-Path (Join-Path $ProjectDir "htmlcov\index.html"))) {
        Write-Log "Coverage report: htmlcov\index.html" "Cyan"
    }
} else {
    Write-Log "[ERR] Test falliti (exit code: $ExitCode)" "Red"
}

exit $ExitCode

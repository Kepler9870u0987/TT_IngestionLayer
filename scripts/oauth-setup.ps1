<#
.SYNOPSIS
    Configura OAuth2 per Gmail.
.DESCRIPTION
    Gestisce l'autenticazione OAuth2: setup iniziale, refresh token, revoca, info.
.EXAMPLE
    .\scripts\oauth-setup.ps1
    .\scripts\oauth-setup.ps1 -Action Info
    .\scripts\oauth-setup.ps1 -Action Revoke
    .\scripts\oauth-setup.ps1 -Action Refresh
#>
[CmdletBinding()]
param(
    [ValidateSet("Setup", "Info", "Refresh", "Revoke")]
    [string]$Action = "Setup",
    
    [string]$Username = $env:IMAP_USER
)

$ErrorActionPreference = "Stop"
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir

function Write-Log { 
    param([string]$Msg, [string]$Color = "White")
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] $Msg" -ForegroundColor $Color
}

Write-Log "=== OAuth2 Gmail - $Action ===" "Yellow"
Write-Log ""

# ---- Attiva venv ----
$VenvActivate = Join-Path $ProjectDir ".venv\Scripts\Activate.ps1"
if (Test-Path $VenvActivate) {
    & $VenvActivate
} else {
    Write-Log "Virtual environment non trovato. Esegui .\scripts\setup.ps1" "Red"
    exit 1
}

# ---- Verifica .env ----
$EnvFile = Join-Path $ProjectDir ".env"
if (-not (Test-Path $EnvFile)) {
    Write-Log ".env non trovato. Esegui .\scripts\setup.ps1 prima." "Red"
    exit 1
}

# ---- Esegui azione ----
switch ($Action) {
    "Setup" {
        Write-Log "Avvio setup OAuth2..." "Cyan"
        Write-Log ""
        Write-Log "NOTA: Si aprirà il browser per autenticare con Google." "Yellow"
        Write-Log "      Dopo l'autorizzazione, il token verrà salvato in tokens/" "Yellow"
        Write-Log ""
        Start-Sleep -Seconds 2
        
        if ($Username) {
            & python producer.py --username $Username --auth-setup
        } else {
            & python producer.py --auth-setup
        }
    }
    "Info" {
        Write-Log "Recupero info token..." "Cyan"
        & python -m src.auth.oauth2_gmail --info
    }
    "Refresh" {
        Write-Log "Forzo refresh token..." "Cyan"
        & python -m src.auth.oauth2_gmail --refresh
    }
    "Revoke" {
        Write-Log "Revoco token..." "Cyan"
        Write-Log "ATTENZIONE: Dovrai rifare il setup dopo la revoca!" "Red"
        Write-Log ""
        $Confirm = Read-Host "Confermi la revoca? (si/no)"
        if ($Confirm -eq "si") {
            & python -m src.auth.oauth2_gmail --revoke
            Write-Log "Token revocato. Esegui .\scripts\oauth-setup.ps1 per riautenticare." "Yellow"
        } else {
            Write-Log "Revoca annullata." "Gray"
        }
    }
}

$ExitCode = $LASTEXITCODE

Write-Log ""
if ($ExitCode -eq 0) {
    Write-Log "[OK] Operazione completata" "Green"
} else {
    Write-Log "[ERR] Operazione fallita (exit code: $ExitCode)" "Red"
}

exit $ExitCode

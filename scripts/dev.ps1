<#
.SYNOPSIS
    Script per sviluppatori - utilities per development workflow.
.DESCRIPTION
    Combina operazioni comuni durante lo sviluppo: restart rapido, test watch, logs, ecc.
.EXAMPLE
    .\scripts\dev.ps1 -Action Restart
    .\scripts\dev.ps1 -Action Logs
    .\scripts\dev.ps1 -Action TestWatch
#>
[CmdletBinding()]
param(
    [ValidateSet("Restart", "Logs", "TestWatch", "Clean", "Shell", "Redis")]
    [string]$Action = "Restart",
    
    [switch]$NoTest
)

$ErrorActionPreference = "Stop"
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir

function Write-Log { 
    param([string]$Msg, [string]$Color = "White")
    Write-Host "[DEV] $Msg" -ForegroundColor $Color
}

# ---- Attiva venv ----
$VenvActivate = Join-Path $ProjectDir ".venv\Scripts\Activate.ps1"
if (Test-Path $VenvActivate) {
    & $VenvActivate
}

switch ($Action) {
    # ════════════════════════════════════════════════════════
    "Restart" {
        Write-Log "Restart rapido..." "Yellow"
        
        if (-not $NoTest) {
            Write-Log "Test pre-restart..." "Cyan"
            & "$ScriptDir\test.ps1" -Type Unit -Verbose:$false
            if ($LASTEXITCODE -ne 0) {
                Write-Log "Test falliti - annullo restart" "Red"
                exit 1
            }
        }
        
        Write-Log "Stop..." "Cyan"
        & "$ScriptDir\stop.ps1"
        
        Start-Sleep -Seconds 2
        
        Write-Log "Start..." "Cyan"
        & "$ScriptDir\start.ps1"
        
        Start-Sleep -Seconds 3
        
        Write-Log "Health check..." "Cyan"
        & "$ScriptDir\health_check.ps1"
        
        Write-Log "[OK] Restart completato!" "Green"
    }
    
    # ════════════════════════════════════════════════════════
    "Logs" {
        Write-Log "Streaming logs (Ctrl+C per uscire)..." "Yellow"
        Write-Log ""
        
        $ProducerLog = Join-Path $ProjectDir "logs\producer.log"
        $WorkerLog = Join-Path $ProjectDir "logs\worker.log"
        
        if ((Test-Path $ProducerLog) -and (Test-Path $WorkerLog)) {
            # Mostra entrambi i log in parallelo
            $job1 = Start-Job -ScriptBlock { 
                param($log)
                Get-Content $log -Tail 20 -Wait | ForEach-Object { "[PRODUCER] $_" }
            } -ArgumentList $ProducerLog
            
            $job2 = Start-Job -ScriptBlock { 
                param($log)
                Get-Content $log -Tail 20 -Wait | ForEach-Object { "[WORKER]   $_" }
            } -ArgumentList $WorkerLog
            
            try {
                while ($true) {
                    Receive-Job $job1 -ErrorAction SilentlyContinue
                    Receive-Job $job2 -ErrorAction SilentlyContinue
                    Start-Sleep -Milliseconds 100
                }
            } finally {
                Stop-Job $job1, $job2
                Remove-Job $job1, $job2
            }
        } else {
            Write-Log "Log file non trovati. Sistema non avviato?" "Red"
        }
    }
    
    # ════════════════════════════════════════════════════════
    "TestWatch" {
        Write-Log "Test watch mode - rilancia test ad ogni modifica..." "Yellow"
        Write-Log "Ctrl+C per uscire" "Gray"
        Write-Log ""
        
        # Installa pytest-watch se mancante
        $hasWatch = pip list | Select-String "pytest-watch"
        if (-not $hasWatch) {
            Write-Log "Installo pytest-watch..." "Cyan"
            pip install pytest-watch -q
        }
        
        & ptw tests/unit/ -- -v --tb=short
    }
    
    # ════════════════════════════════════════════════════════
    "Clean" {
        Write-Log "Pulizia file temporanei..." "Yellow"
        
        $ToClean = @(
            ".pytest_cache",
            "htmlcov",
            "__pycache__",
            "*.pyc",
            ".coverage",
            "logs/*.log"
        )
        
        foreach ($pattern in $ToClean) {
            $items = Get-ChildItem -Path $ProjectDir -Filter $pattern -Recurse -ErrorAction SilentlyContinue
            foreach ($item in $items) {
                Write-Log "Rimuovo: $($item.FullName)" "Gray"
                Remove-Item $item.FullName -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
        
        Write-Log "[OK] Pulizia completata" "Green"
    }
    
    # ════════════════════════════════════════════════════════
    "Shell" {
        Write-Log "Avvio shell Python interattiva..." "Yellow"
        Write-Log ""
        
        $StartupScript = @"
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))

# Import comuni
from src.common.redis_client import RedisClient, create_redis_client_from_config
from src.auth.oauth2_gmail import OAuth2Manager, create_oauth2_from_config
from src.imap.imap_client import GmailIMAPClient, create_imap_client_from_config
from src.producer.state_manager import ProducerStateManager
from config.settings import settings

print()
print('═══════════════════════════════════════════════════════')
print('  Email Ingestion System - Debug Shell')
print('═══════════════════════════════════════════════════════')
print()
print('Imports disponibili:')
print('  • RedisClient, create_redis_client_from_config')
print('  • OAuth2Manager, create_oauth2_from_config')
print('  • GmailIMAPClient, create_imap_client_from_config')
print('  • ProducerStateManager')
print('  • settings (config)')
print()
print('Quick start:')
print('  redis = create_redis_client_from_config(settings)')
print('  redis.ping()')
print()
"@
        
        $TempScript = Join-Path $env:TEMP "ingestion_shell.py"
        $StartupScript | Out-File -FilePath $TempScript -Encoding UTF8
        
        & python -i $TempScript
        
        Remove-Item $TempScript -ErrorAction SilentlyContinue
    }
    
    # ════════════════════════════════════════════════════════
    "Redis" {
        Write-Log "Redis utilities..." "Yellow"
        Write-Host ""
        Write-Host "Scegli operazione:" -ForegroundColor Cyan
        Write-Host "  1. Stream length" -ForegroundColor White
        Write-Host "  2. Read messages" -ForegroundColor White
        Write-Host "  3. Consumer groups info" -ForegroundColor White
        Write-Host "  4. Producer state" -ForegroundColor White
        Write-Host "  5. DLQ length" -ForegroundColor White
        Write-Host "  6. Redis CLI" -ForegroundColor White
        Write-Host ""
        $Choice = Read-Host "Scelta (1-6)"
        
        switch ($Choice) {
            "1" {
                Write-Log "Stream length:" "Cyan"
                redis-cli XLEN email_ingestion_stream
            }
            "2" {
                $Count = Read-Host "Quanti messaggi? (default: 10)"
                if (-not $Count) { $Count = 10 }
                redis-cli XREAD COUNT $Count STREAMS email_ingestion_stream 0
            }
            "3" {
                redis-cli XINFO GROUPS email_ingestion_stream
            }
            "4" {
                $User = Read-Host "Username (es: user@gmail.com)"
                if ($User) {
                    Write-Log "Last UID:" "Cyan"
                    redis-cli GET "producer_state:${User}:INBOX:last_uid"
                    Write-Log "UIDVALIDITY:" "Cyan"
                    redis-cli GET "producer_state:${User}:INBOX:uidvalidity"
                }
            }
            "5" {
                Write-Log "DLQ length:" "Cyan"
                redis-cli XLEN email_ingestion_dlq
            }
            "6" {
                Write-Log "Avvio redis-cli..." "Cyan"
                redis-cli
            }
        }
    }
}

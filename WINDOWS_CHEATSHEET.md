# 🚀 Windows Quick Reference - Email Ingestion System

Comandi essenziali per Windows PowerShell.

---

## ⚡ Comandi Rapidi

```powershell
# 🎯 Setup iniziale (prima volta)
.\scripts\quick-start.ps1

# ▶️ Start sistema
.\scripts\start.ps1

# ⏹️ Stop sistema
.\scripts\stop.ps1

# 💚 Health check
.\scripts\health_check.ps1

# 🧪 Test
.\scripts\test.ps1

# 📊 Logs in tempo reale
Get-Content logs\producer.log -Tail 50 -Wait
```

---

## 🔧 Setup & Configurazione

```powershell
# Setup ambiente completo
.\scripts\setup.ps1

# Setup OAuth2 Gmail (apre browser)
.\scripts\oauth-setup.ps1

# Setup OAuth2 con username specifico
.\scripts\oauth-setup.ps1 -Action Setup -Username "tua-email@gmail.com"

# Info token OAuth2
.\scripts\oauth-setup.ps1 -Action Info

# Refresh token
.\scripts\oauth-setup.ps1 -Action Refresh

# Revoca token (reset OAuth2)
.\scripts\oauth-setup.ps1 -Action Revoke
```

---

## ▶️ Avvio & Stop

```powershell
# Start standard
.\scripts\start.ps1

# Start con argomenti custom
.\scripts\start.ps1 -ProducerArgs "--batch-size 100 --poll-interval 30"
.\scripts\start.ps1 -WorkerArgs "--batch-size 50"

# Stop graceful (timeout 30s default)
.\scripts\stop.ps1

# Stop con timeout lungo
.\scripts\stop.ps1 -Timeout 60

# Restart rapido
.\scripts\stop.ps1; .\scripts\start.ps1
```

---

## 🧪 Test

```powershell
# Unit test
.\scripts\test.ps1

# Unit test verbose
.\scripts\test.ps1 -Verbose

# Unit test con coverage
.\scripts\test.ps1 -Coverage

# Integration test
.\scripts\test.ps1 -Type Integration

# Load test
.\scripts\test.ps1 -Type Load -LoadEmails 5000 -LoadWorkers 3

# Tutti i test con coverage
.\scripts\test.ps1 -Type All -Coverage

# Apri coverage report
start htmlcov\index.html
```

---

## 📊 Monitoraggio

### Health Checks
```powershell
# Check completo
.\scripts\health_check.ps1

# Health endpoints diretti
curl http://localhost:8080/health      # Producer
curl http://localhost:8081/health      # Worker
curl http://localhost:8080/status      # Status dettagliato
curl http://localhost:9090/metrics     # Prometheus metrics
```

### Logs
```powershell
# Producer logs (streaming)
Get-Content logs\producer.log -Tail 50 -Wait

# Worker logs (streaming)
Get-Content logs\worker.log -Tail 50 -Wait

# Errori
Get-Content logs\producer_err.log -Tail 20
Get-Content logs\worker_err.log -Tail 20

# Cerca errore specifico
Select-String "ERROR" logs\*.log

# Ultimi 100 log del producer
Get-Content logs\producer.log -Tail 100
```

### Processi
```powershell
# Verifica processi running
Get-Process | Where-Object { $_.Id -eq (Get-Content pids\producer.pid -ErrorAction SilentlyContinue) }
Get-Process | Where-Object { $_.Id -eq (Get-Content pids\worker.pid -ErrorAction SilentlyContinue) }

# Lista tutti i processi Python
Get-Process python

# Kill manuale
Stop-Process -Id (Get-Content pids\producer.pid) -Force
Stop-Process -Id (Get-Content pids\worker.pid) -Force
```

---

## 💾 Redis

### Stream Operations
```powershell
# Stream length
redis-cli XLEN email_ingestion_stream

# Read primi 10 messaggi
redis-cli XREAD COUNT 10 STREAMS email_ingestion_stream 0

# Read ultimi messaggi
redis-cli XREAD COUNT 10 STREAMS email_ingestion_stream $

# Info consumer groups
redis-cli XINFO GROUPS email_ingestion_stream

# Info consumers in un group
redis-cli XINFO CONSUMERS email_ingestion_stream email_processor_group

# Pending messages
redis-cli XPENDING email_ingestion_stream email_processor_group

# DLQ length
redis-cli XLEN email_ingestion_dlq

# Read DLQ
redis-cli XREAD COUNT 10 STREAMS email_ingestion_dlq 0
```

### State Management
```powershell
# Last UID (sostituisci username)
redis-cli GET "producer_state:user@gmail.com:INBOX:last_uid"

# UIDVALIDITY
redis-cli GET "producer_state:user@gmail.com:INBOX:uidvalidity"

# Last poll time
redis-cli GET "producer_state:user@gmail.com:INBOX:last_poll"

# Total emails processed
redis-cli GET "producer_state:user@gmail.com:INBOX:total_emails"

# Lista tutte le chiavi producer
redis-cli KEYS "producer_state:*"
```

### Maintenance
```powershell
# Ping Redis
redis-cli PING

# Info server
redis-cli INFO server

# Memory usage
redis-cli INFO memory

# Cleanup stream (keep last 10000)
redis-cli XTRIM email_ingestion_stream MAXLEN ~ 10000

# Flush database (ATTENZIONE: cancella tutto!)
redis-cli FLUSHDB
```

---

## 🛠️ Development

```powershell
# Restart rapido con test
.\scripts\dev.ps1 -Action Restart

# Restart senza test
.\scripts\dev.ps1 -Action Restart -NoTest

# Logs dual (producer + worker)
.\scripts\dev.ps1 -Action Logs

# Test watch mode (rerun on change)
.\scripts\dev.ps1 -Action TestWatch

# Pulizia file temporanei
.\scripts\dev.ps1 -Action Clean

# Shell Python interattiva
.\scripts\dev.ps1 -Action Shell

# Redis utilities menu
.\scripts\dev.ps1 -Action Redis
```

### Manual Runs
```powershell
# Producer dry-run
python producer.py --dry-run

# Producer custom config
python producer.py --username "user@gmail.com" --batch-size 200 --poll-interval 30

# Worker custom config
python worker.py --consumer worker_02 --batch-size 50

# OAuth2 CLI
python -m src.auth.oauth2_gmail --info
python -m src.auth.oauth2_gmail --refresh
python -m src.auth.oauth2_gmail --revoke

# Backup Redis
python scripts\backup.py --output-dir backups --retention-days 7

# Restore Redis
python scripts\restore.py --file backups\backup.rdb
```

---

## 🐛 Troubleshooting

### Redis non raggiungibile
```powershell
# Start Redis nativo
redis-server

# Start Redis Docker
docker run -d -p 6379:6379 redis:7-alpine

# Start Redis WSL
wsl redis-server

# Check port
Test-NetConnection localhost -Port 6379
```

### Processi bloccati
```powershell
# Stop forzato
.\scripts\stop.ps1 -Timeout 5

# Kill tutti i python
Get-Process python | Stop-Process -Force

# Cleanup PID files
Remove-Item pids\*.pid -Force
```

### OAuth2 non funziona
```powershell
# Reset OAuth2
.\scripts\oauth-setup.ps1 -Action Revoke
.\scripts\oauth-setup.ps1 -Action Setup

# Verifica .env
Get-Content .env | Select-String "GOOGLE"

# Check token file
Test-Path tokens\gmail_token.json
```

### Test falliscono
```powershell
# Reinstalla dipendenze
pip install -r requirements-dev.txt

# Clean cache
Remove-Item -Recurse -Force .pytest_cache, __pycache__

# Test singolo file
pytest tests\unit\test_redis_client.py -v

# Test singola funzione
pytest tests\unit\test_redis_client.py::TestRedisClient::test_ping -v
```

### Venv non attivato
```powershell
# Attiva venv
.\.venv\Scripts\Activate.ps1

# Verifica attivazione
where.exe python
# Output dovrebbe essere: C:\git\TT_IngestionLayer\.venv\Scripts\python.exe
```

---

## 📁 File Importanti

```
.env                               # Configurazione (MODIFICA QUESTO!)
.env.example                       # Template configurazione
tokens/gmail_token.json            # OAuth2 token
logs/producer.log                  # Producer logs
logs/worker.log                    # Worker logs
pids/producer.pid                  # Producer PID
pids/worker.pid                    # Worker PID
htmlcov/index.html                 # Coverage report
```

---

## 🌐 Dashboard URLs

```
http://localhost:8080/health       # Producer health
http://localhost:8080/ready        # Producer readiness
http://localhost:8080/status       # Producer status dettagliato
http://localhost:8081/health       # Worker health
http://localhost:9090/metrics      # Prometheus metrics
```

---

## 🔐 Variabili Ambiente (.env)

```bash
# Redis
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_STREAM_NAME=email_ingestion_stream
REDIS_MAX_STREAM_LENGTH=10000

# IMAP Gmail
IMAP_HOST=imap.gmail.com
IMAP_PORT=993
IMAP_USER=your-email@gmail.com
IMAP_POLL_INTERVAL_SECONDS=60

# OAuth2 (da Google Cloud Console)
GOOGLE_CLIENT_ID=your-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-secret
GOOGLE_TOKEN_FILE=tokens/gmail_token.json

# DLQ
DLQ_STREAM_NAME=email_ingestion_dlq
DLQ_MAX_RETRY_ATTEMPTS=5
```

---

## 🎯 Workflow Completo

### Prima installazione
```powershell
# 1. Setup guidato (fa tutto)
.\scripts\quick-start.ps1

# 2. Modifica .env con credenziali OAuth2

# 3. Setup OAuth2
.\scripts\oauth-setup.ps1

# 4. Start
.\scripts\start.ps1

# 5. Health check
.\scripts\health_check.ps1
```

### Uso quotidiano
```powershell
# Start
.\scripts\start.ps1

# Monitor
Get-Content logs\producer.log -Tail 50 -Wait

# Stop
.\scripts\stop.ps1
```

### Dopo modifiche codice
```powershell
# Test
.\scripts\test.ps1 -Coverage

# Restart
.\scripts\dev.ps1 -Action Restart

# Check
.\scripts\health_check.ps1
```

---

## 💡 Tips

### Alias PowerShell
Aggiungi al tuo `$PROFILE`:
```powershell
# Email Ingestion shortcuts
function ei-start { .\scripts\start.ps1 }
function ei-stop { .\scripts\stop.ps1 }
function ei-health { .\scripts\health_check.ps1 }
function ei-test { .\scripts\test.ps1 }
function ei-logs { Get-Content logs\producer.log -Tail 50 -Wait }
function ei-dev { .\scripts\dev.ps1 @args }
```

Poi usa:
```powershell
ei-start
ei-health
ei-logs
```

### Hotkeys VSCode
Aggiungi a `keybindings.json`:
```json
{
  "key": "ctrl+shift+t",
  "command": "workbench.action.terminal.sendSequence",
  "args": { "text": ".\\scripts\\test.ps1\r" }
}
```

---

## 📚 Documentazione

- **[scripts/README.md](README.md)** - Documentazione completa script
- **[README.md](../README.md)** - Overview sistema
- **[PROGRESS.md](../PROGRESS.md)** - Dettagli implementazione
- **[docs/OAUTH2_SETUP.md](../docs/OAUTH2_SETUP.md)** - Setup OAuth2 Google
- **[docs/runbooks/](../docs/runbooks/)** - Runbooks operativi

---

## ❓ Help

Tutti gli script supportano help:
```powershell
Get-Help .\scripts\start.ps1 -Detailed
Get-Help .\scripts\test.ps1 -Examples
```

---

**Last Updated**: 2026-02-17  
**Version**: Phase 7 Complete (70/70 tasks)

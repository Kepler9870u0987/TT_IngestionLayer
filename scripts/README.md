# Script PowerShell per Windows

Tutti gli script operativi per gestire l'Email Ingestion System su Windows.

## 🚀 Quick Start

```powershell
# Tutto in un colpo - guidato interattivo
.\scripts\quick-start.ps1
```

---

## 📋 Script Disponibili

### 🔧 Setup & Configurazione

#### **`setup.ps1`** - Setup completo ambiente
Configura l'ambiente: crea venv, installa dipendenze, crea .env

```powershell
# Setup base
.\scripts\setup.ps1

# Skip controllo Redis
.\scripts\setup.ps1 -SkipRedisCheck
```

**Cosa fa:**
- ✓ Verifica Python 3.11+
- ✓ Crea virtual environment (.venv)
- ✓ Installa requirements.txt e requirements-dev.txt
- ✓ Crea .env da .env.example
- ✓ Crea directory logs/ e pids/
- ✓ Verifica connessione Redis

---

#### **`oauth-setup.ps1`** - Gestione OAuth2 Gmail
Configura e gestisce l'autenticazione OAuth2

```powershell
# Setup iniziale (apre browser)
.\scripts\oauth-setup.ps1

# Con username specifico
.\scripts\oauth-setup.ps1 -Action Setup -Username "tua-email@gmail.com"

# Mostra info token
.\scripts\oauth-setup.ps1 -Action Info

# Forza refresh token
.\scripts\oauth-setup.ps1 -Action Refresh

# Revoca token (richiede conferma)
.\scripts\oauth-setup.ps1 -Action Revoke
```

**Prerequisiti:**
- Google Cloud Project con Gmail API abilitata
- OAuth2 credentials in .env

---

### ▶️ Avvio & Stop

#### **`start.ps1`** - Avvia producer e worker
Lancia producer e worker come processi background

```powershell
# Avvio standard
.\scripts\start.ps1

# Con argomenti custom per producer
.\scripts\start.ps1 -ProducerArgs "--batch-size 100 --poll-interval 30"

# Con argomenti custom per worker
.\scripts\start.ps1 -WorkerArgs "--batch-size 50"

# Redis custom
.\scripts\start.ps1 -RedisHost "redis.example.com" -RedisPort 6380
```

**Cosa fa:**
- Verifica Python e venv
- Controlla connessione Redis
- Avvia producer.py in background
- Avvia worker.py in background
- Salva PID in pids/producer.pid e pids/worker.pid
- Redirect logs in logs/producer.log e logs/worker.log

**Controlla processi:**
```powershell
Get-Process | Where-Object { $_.Id -eq (Get-Content pids\producer.pid) }
Get-Process | Where-Object { $_.Id -eq (Get-Content pids\worker.pid) }
```

---

#### **`stop.ps1`** - Stop graceful
Ferma producer e worker con shutdown graceful

```powershell
# Stop con timeout 30s (default)
.\scripts\stop.ps1

# Timeout custom
.\scripts\stop.ps1 -Timeout 60
```

**Cosa fa:**
- Legge PID da pids/
- Invia SIGTERM (graceful shutdown)
- Attende fino a Timeout secondi
- Se non si ferma, forza con SIGKILL
- Rimuove file PID

---

### 🔍 Monitoring & Test

#### **`health_check.ps1`** - Verifica health
Controlla stato producer e worker via HTTP endpoints

```powershell
# Health check completo
.\scripts\health_check.ps1
```

**Endpoint verificati:**
- `http://localhost:8080/health` (producer)
- `http://localhost:8081/health` (worker)
- Redis connectivity

**Output esempio:**
```
✓ Producer health: OK
✓ Worker health: OK
✓ Redis: reachable
```

---

#### **`test.ps1`** - Test runner
Esegue unit test, integration test e load test

```powershell
# Unit test (default)
.\scripts\test.ps1

# Con verbose output
.\scripts\test.ps1 -Verbose

# Con coverage report
.\scripts\test.ps1 -Coverage

# Integration test
.\scripts\test.ps1 -Type Integration

# Load test
.\scripts\test.ps1 -Type Load -LoadEmails 5000 -LoadWorkers 3

# Tutti i test
.\scripts\test.ps1 -Type All -Coverage
```

**Output:**
- Test results in console
- Coverage report HTML in `htmlcov/index.html` (se -Coverage)

---

### 🎯 Quick Start Interattivo

#### **`quick-start.ps1`** - Setup guidato step-by-step
Script interattivo che guida attraverso tutti i passi

```powershell
.\scripts\quick-start.ps1
```

**Step guidati:**
1. ✓ Setup ambiente (venv, dipendenze, .env)
2. ✓ Configurazione OAuth2 Gmail
3. ✓ Verifica Redis
4. ✓ Test sistema (opzionale)
5. ✓ Avvio producer + worker
6. ✓ Health check finale

Ideale per prima installazione!

---

## 📊 Monitoraggio in Tempo Reale

### View logs in streaming
```powershell
# Producer logs
Get-Content logs\producer.log -Tail 50 -Wait

# Worker logs
Get-Content logs\worker.log -Tail 50 -Wait

# Errori
Get-Content logs\producer_err.log -Tail 20 -Wait
```

### Dashboard web
- **Health**: http://localhost:8080/health
- **Status**: http://localhost:8080/status
- **Metrics**: http://localhost:9090/metrics

### Redis inspection
```powershell
# Stream length
redis-cli XLEN email_ingestion_stream

# Ultimi 10 messaggi
redis-cli XREAD COUNT 10 STREAMS email_ingestion_stream 0

# Consumer group info
redis-cli XINFO GROUPS email_ingestion_stream

# Last UID state
redis-cli GET "producer_state:user@gmail.com:INBOX:last_uid"
```

---

## 🔥 Workflow Tipico

### Prima volta
```powershell
# 1. Setup completo guidato
.\scripts\quick-start.ps1

# Done! Sistema avviato
```

### Uso quotidiano
```powershell
# Avvia
.\scripts\start.ps1

# Verifica
.\scripts\health_check.ps1

# Logs
Get-Content logs\producer.log -Tail 50 -Wait

# Stop
.\scripts\stop.ps1
```

### Development
```powershell
# Test dopo modifiche
.\scripts\test.ps1 -Coverage

# Riavvio rapido
.\scripts\stop.ps1; .\scripts\start.ps1

# Health check
.\scripts\health_check.ps1
```

### Troubleshooting OAuth2
```powershell
# Info token
.\scripts\oauth-setup.ps1 -Action Info

# Refresh forzato
.\scripts\oauth-setup.ps1 -Action Refresh

# Reset completo
.\scripts\oauth-setup.ps1 -Action Revoke
.\scripts\oauth-setup.ps1 -Action Setup
```

---

## ⚙️ Script Linux/macOS

Gli stessi script esistono anche per Linux/macOS con estensione `.sh`:
- `start.sh`
- `stop.sh`
- `health_check.sh`

Funzionalità identiche, sintassi Bash.

---

## 📁 File Generati

### Logs
```
logs/
  ├── producer.log          # Producer stdout
  ├── producer_err.log      # Producer stderr
  ├── worker.log            # Worker stdout
  └── worker_err.log        # Worker stderr
```

### Process IDs
```
pids/
  ├── producer.pid          # Producer PID
  └── worker.pid            # Worker PID
```

### Configuration
```
.env                        # Environment variables (gitignored)
tokens/
  └── gmail_token.json      # OAuth2 token (gitignored)
```

---

## 🐛 Troubleshooting

### "Python not found"
```powershell
# Installa Python 3.11+ e aggiungi al PATH
# Verifica con:
python --version
```

### "Virtual environment non trovato"
```powershell
# Esegui setup
.\scripts\setup.ps1
```

### "Redis not reachable"
```powershell
# Opzione 1: Redis nativo
redis-server

# Opzione 2: Docker
docker run -d -p 6379:6379 redis:7-alpine

# Opzione 3: WSL
wsl redis-server
```

### "OAuth2AuthenticationError"
```powershell
# Riconfigura OAuth2
.\scripts\oauth-setup.ps1 -Action Setup
```

### "Process già running"
```powershell
# Stop forzato
.\scripts\stop.ps1

# Verifica processi
Get-Content pids\producer.pid | ForEach-Object { Get-Process -Id $_ }

# Kill manuale se necessario
Stop-Process -Id (Get-Content pids\producer.pid) -Force
```

### Test falliscono
```powershell
# Verifica dipendenze
pip install -r requirements-dev.txt

# Clean cache
Remove-Item -Recurse -Force .pytest_cache, __pycache__, **/__pycache__

# Riesegui
.\scripts\test.ps1 -Verbose
```

---

## 💡 Tips & Tricks

### Esegui in modalità dry-run
```powershell
.\scripts\start.ps1 -ProducerArgs "--dry-run"
```

### Aumenta batch size per performance
```powershell
.\scripts\start.ps1 -ProducerArgs "--batch-size 200"
```

### Worker multipli (scaling orizzontale)
```powershell
# Worker 1
.\scripts\start.ps1

# Worker 2 (separata sessione PowerShell)
python worker.py --consumer worker_02

# Worker 3
python worker.py --consumer worker_03
```

### Background metrics update
```powershell
# Monitor stream depth
while ($true) {
    redis-cli XLEN email_ingestion_stream
    Start-Sleep -Seconds 5
}
```

---

## 📚 Documentazione Completa

- [README.md](../README.md) - Overview sistema
- [PROGRESS.md](../PROGRESS.md) - Dettagli implementazione
- [OAUTH2_SETUP.md](../docs/OAUTH2_SETUP.md) - Guida OAuth2 Google Cloud
- [docs/runbooks/](../docs/runbooks/) - Runbook operativi

---

## 🎯 Quick Reference Card

| Azione | Comando |
|--------|---------|
| **Setup iniziale** | `.\scripts\quick-start.ps1` |
| **Start** | `.\scripts\start.ps1` |
| **Stop** | `.\scripts\stop.ps1` |
| **Health** | `.\scripts\health_check.ps1` |
| **Test** | `.\scripts\test.ps1` |
| **OAuth2** | `.\scripts\oauth-setup.ps1` |
| **Logs** | `Get-Content logs\producer.log -Wait` |

---

Tutti gli script supportano `-Help` per vedere le opzioni:
```powershell
Get-Help .\scripts\start.ps1 -Detailed
```

# 🚀 Quick Start - Windows

## ⚡ Esecuzione Script

**Opzione 1: Usando run.bat (consigliato - no problemi execution policy)**
```cmd
run.bat setup
run.bat test
run.bat start
run.bat health_check
```

**Opzione 2: PowerShell diretto**
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1
```

**Opzione 3: Cambia execution policy (una volta sola)**
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```
Poi puoi eseguire direttamente:
```powershell
.\scripts\setup.ps1
.\scripts\start.ps1
```

---

## 🎯 Prima Installazione

```cmd
# Setup guidato completo (consigliato)
run.bat quick-start

# OPPURE setup manuale:
run.bat setup
# Modifica .env con le tue credenziali OAuth2
run.bat oauth-setup
run.bat start
run.bat health_check
```

---

## 🔧 Comandi Principali

```cmd
# Setup ambiente
run.bat setup

# Configurazione OAuth2
run.bat oauth-setup
run.bat oauth-setup -Action Info

# Start/Stop
run.bat start
run.bat stop

# Health check
run.bat health_check

# Test
run.bat test
run.bat test -Coverage
run.bat test -Type Load -LoadEmails 5000

# Dev utilities
run.bat dev -Action Restart
run.bat dev -Action Logs
run.bat dev -Action Clean
```

---

## 📊 Monitoraggio

```cmd
# Health check
run.bat health_check

# Logs in tempo reale (PowerShell)
Get-Content logs\producer.log -Tail 50 -Wait
Get-Content logs\worker.log -Tail 50 -Wait
```

---

## 🐛 Troubleshooting

### "Script disabilitata nel sistema"
Usa `run.bat` invece di chiamare direttamente gli script PowerShell.

### "Accesso negato" su pytest
```cmd
# Reinstalla dipendenze
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt --force-reinstall
```

### Redis non raggiungibile
```cmd
# Start Redis (scegli una opzione)
redis-server
docker run -d -p 6379:6379 redis:7-alpine
wsl redis-server
```

---

## 📚 Documentazione Completa

- **[WINDOWS_CHEATSHEET.md](WINDOWS_CHEATSHEET.md)** - Quick reference completo
- **[scripts/README.md](scripts/README.md)** - Guida dettagliata script
- **[README.md](README.md)** - Documentazione sistema

---

## 💡 Tips

### Alias CMD
Crea `aliases.bat` nella tua home:
```batch
@echo off
doskey ei-setup=run.bat setup $*
doskey ei-start=run.bat start $*
doskey ei-stop=run.bat stop $*
doskey ei-test=run.bat test $*
doskey ei-health=run.bat health_check $*
```

Poi esegui `aliases.bat` in ogni nuova sessione.

---

**Hai problemi?** Controlla [WINDOWS_CHEATSHEET.md](WINDOWS_CHEATSHEET.md) per troubleshooting dettagliato.

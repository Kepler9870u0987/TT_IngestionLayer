# Incident Response Runbook

**Sistema**: Email Ingestion System  
**Ultima revisione**: 2026-02-17  

---

## 1. Classificazione Severità

| Severità | Criteri | SLA Risposta | Esempi |
|----------|---------|--------------|--------|
| **P1 – Critical** | Sistema completamente down, nessuna email processata | 15 min | Redis down, entrambi producer/worker crashati |
| **P2 – High** | Degradazione significativa, processing parziale | 30 min | Circuit breaker aperto, DLQ in crescita rapida |
| **P3 – Medium** | Anomalia non bloccante, sistema operativo | 2 ore | Latenza elevata, duplicati in aumento, token scaduto |
| **P4 – Low** | Informational, nessun impatto utente | 1 giorno | Log warnings, backup mancato |

---

## 2. Procedure per Incidente

### 2.1 Redis Down (P1)

**Sintomi**:
- Health endpoint `/ready` ritorna 503
- Circuit breaker `redis` in stato OPEN
- Log: `RedisConnectionError`

**Diagnosi rapida**:
```bash
# Verifica connettività
redis-cli -h $REDIS_HOST -p $REDIS_PORT ping

# Controlla processo
ps aux | grep redis-server        # Linux
Get-Process redis-server           # Windows

# Controlla logs Redis
tail -50 /var/log/redis/redis-server.log
```

**Azioni**:
1. **Verifica**: `redis-cli ping` – se `PONG`, il problema è di rete
2. **Restart Redis**: `systemctl restart redis` oppure `redis-server /etc/redis/redis.conf`
3. **Verifica dati**: `redis-cli DBSIZE` – confrontare con ultimo backup
4. **Se dati persi**: eseguire restore da backup (vedi [Redis Operations](redis_operations.md))
5. **Verifica recovery**: `curl http://localhost:8080/ready` → 200
6. **Worker recovery**: il worker recupererà automaticamente messaggi orfani via XPENDING/XCLAIM

**Post-incidente**:
- Verificare che circuit breaker torni CLOSED
- Controllare DLQ per messaggi persi durante l'outage
- Aggiornare incident log

---

### 2.2 IMAP Unreachable (P2)

**Sintomi**:
- Circuit breaker `imap` in stato OPEN
- Producer log: `IMAPConnectionError`
- Nessuna nuova email prodotta

**Diagnosi**:
```bash
# Test connettività IMAP
openssl s_client -connect imap.gmail.com:993 -brief

# Controlla status producer
curl -s http://localhost:8080/status | python -m json.tool

# Controlla circuit breaker
curl -s http://localhost:8080/status | python -c "
import json,sys
d=json.load(sys.stdin)
for cb in d.get('circuit_breakers',[]):
    print(f\"{cb['name']}: {cb['state']}  failures={cb['failure_count']}\")
"
```

**Azioni**:
1. Verificare stato Gmail: https://www.google.com/appsstatus
2. Verificare rete/firewall: `telnet imap.gmail.com 993`
3. Se rate-limited: attendere (il circuit breaker gestisce automaticamente)
4. Se persistente: verificare credenziali OAuth2 (`python -m src.auth.oauth2_gmail --info`)
5. Il producer riprenderà automaticamente quando il circuit breaker passa a HALF_OPEN

---

### 2.3 OAuth2 Token Scaduto (P3)

**Sintomi**:
- Producer log: `OAuth2AuthenticationError` o `TokenRefreshError`
- Nessuna email prodotta, ma worker continua a processare il backlog

**Azioni**:
```bash
# Verifica stato token
python -m src.auth.oauth2_gmail --info

# Tentare refresh
python -m src.auth.oauth2_gmail --refresh

# Se refresh fallisce, ri-autenticare
python producer.py --auth-setup

# Verificare token rinnovato
python -m src.auth.oauth2_gmail --info
```

---

### 2.4 Circuit Breaker Aperto (P2)

**Sintomi**:
- Metriche `email_ingestion_circuit_breaker_state` = 1 (OPEN)
- Log: `CircuitBreakerError`

**Diagnosi**:
```bash
# Stato corrente
curl -s http://localhost:8080/status | python -m json.tool

# Metriche Prometheus
curl -s http://localhost:9090/metrics | grep circuit_breaker
```

**Azioni**:
1. Identificare la causa (Redis? IMAP?) dal nome del circuit breaker
2. Risolvere la causa root (vedi sezioni specifiche sopra)
3. Il CB transizionerà automaticamente: OPEN → HALF_OPEN (dopo `recovery_timeout`) → CLOSED (dopo `success_threshold` successi)
4. Non è necessario un restart – il recovery è automatico

---

### 2.5 DLQ in Crescita Rapida (P2)

**Sintomi**:
- Gauge `email_ingestion_dlq_depth` in crescita
- Counter `email_ingestion_dlq_messages_total` rate elevato
- Log: messaggi che superano max_retries

**Azioni**:
1. Ispezionare DLQ: vedi [DLQ Management](dlq_management.md)
2. Identificare pattern negli errori (email malformate? Campo mancante?)
3. Se bug nel processor: fixare e reprocessare dalla DLQ
4. Se email genuinamente non processabili: pulire DLQ con bulk clear

---

### 2.6 Worker Crash / OOM (P1)

**Sintomi**:
- PID file presente ma processo non attivo
- Health endpoint non raggiungibile
- Messaggi pending in crescita (`XPENDING`)

**Azioni**:
```bash
# Verifica processo
cat pids/worker.pid && ps -p $(cat pids/worker.pid)

# Controlla logs per stack trace
tail -100 logs/worker.log

# Restart worker
python worker.py

# Oppure usa script operativo
./scripts/start.sh     # Linux
.\scripts\start.ps1    # Windows

# Il worker recupererà automaticamente messaggi orfani all'avvio
```

---

## 3. Escalation

| Livello | Chi | Quando |
|---------|-----|--------|
| L1 | On-call engineer | Verifica iniziale, risolve P3/P4 |
| L2 | Team lead | P1/P2 non risolti in 30 min |
| L3 | Infra team | Redis cluster issues, Network issues |
| External | Google Support | Gmail API / OAuth2 issues persistenti |

---

## 4. Post-Mortem Template

Dopo ogni P1/P2, documentare:

1. **Timeline**: quando è successo, quando rilevato, quando risolto
2. **Impatto**: email perse/ritardate, durata outage
3. **Root cause**: cause tecniche specifiche
4. **Resolution**: azioni intraprese
5. **Prevention**: azioni per prevenire recurrence
6. **Action items**: task con owner e deadline

---

## 5. Contatti & Risorse

- **Health endpoints**: `http://localhost:8080/health`, `/ready`, `/status`
- **Prometheus metrics**: `http://localhost:9090/metrics`
- **Grafana dashboard**: Import da `config/grafana_dashboard.json`
- **Redis CLI**: `redis-cli -h $REDIS_HOST -p $REDIS_PORT`
- **Logs**: `logs/producer.log`, `logs/worker.log`
- **Backups**: `backups/` directory

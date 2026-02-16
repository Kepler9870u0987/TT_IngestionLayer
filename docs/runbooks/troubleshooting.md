# Troubleshooting Guide

**Sistema**: Email Ingestion System  
**Ultima revisione**: 2026-02-17  

---

## Quick Diagnostics Checklist

```bash
# 1. Processi attivi?
cat pids/producer.pid && ps -p $(cat pids/producer.pid) 2>/dev/null
cat pids/worker.pid   && ps -p $(cat pids/worker.pid) 2>/dev/null

# 2. Redis raggiungibile?
redis-cli ping

# 3. Health endpoints
curl -s http://localhost:8080/health
curl -s http://localhost:8080/ready
curl -s http://localhost:8080/status | python -m json.tool

# 4. Prometheus metrics
curl -s http://localhost:9090/metrics | grep email_ingestion

# 5. Stream status
redis-cli XLEN email_ingestion_stream
redis-cli XLEN email_ingestion_dlq
redis-cli XINFO GROUPS email_ingestion_stream
```

---

## Problema: Producer Non Produce Email

### Sintomi
- `email_ingestion_emails_produced_total` non cresce
- Stream depth stabile o in calo
- Nessun log di polling nel producer

### Diagnosi

**1. Producer è in esecuzione?**
```bash
cat pids/producer.pid
ps -p $(cat pids/producer.pid)   # Linux
Get-Process -Id (Get-Content pids\producer.pid)  # Windows
```

**2. OAuth2 token valido?**
```bash
python -m src.auth.oauth2_gmail --info
```
Se scaduto: `python producer.py --auth-setup`

**3. IMAP raggiungibile?**
```bash
openssl s_client -connect imap.gmail.com:993 -brief
```

**4. Circuit breaker aperto?**
```bash
curl -s http://localhost:8080/status | python -c "
import json,sys
d=json.load(sys.stdin)
for cb in d.get('circuit_breakers',[]):
    print(f\"{cb['name']}: {cb['state']}\")
"
```

**5. Mailbox ha nuove email?**
```bash
# Controlla last UID
redis-cli GET "producer_state:user@gmail.com:INBOX:last_uid"
```

**6. Controllare logs**
```bash
tail -50 logs/producer.log | grep -i "error\|warn\|fail"
```

---

## Problema: Worker Non Consuma Messaggi

### Sintomi
- Stream depth in crescita ma `email_ingestion_emails_processed_total` fermo
- Nessun log di processing nel worker

### Diagnosi

**1. Worker è in esecuzione?**
```bash
cat pids/worker.pid
```

**2. Consumer group esiste?**
```bash
redis-cli XINFO GROUPS email_ingestion_stream
```
Output atteso: almeno un gruppo con consumers > 0

**3. Messaggi pending?**
```bash
redis-cli XPENDING email_ingestion_stream email_processor_group
```
Se pending count alto → messaggi bloccati (mai ACK'd)

**4. Circuit breaker Redis?**
```bash
curl -s http://localhost:8080/status | python -m json.tool
```

**5. Verificare che il worker legga il giusto stream/gruppo**
Controllare variabili d'ambiente e argomenti CLI

---

## Problema: Messaggi Bloccati (Pending)

### Sintomi
- `XPENDING` mostra messaggi con alto delivery count
- Consumer che non li processa

### Diagnosi
```bash
# Mostra dettaglio pending
redis-cli XPENDING email_ingestion_stream email_processor_group - + 10

# Output: message_id, consumer_name, idle_time_ms, delivery_count
```

### Soluzioni

**Se consumer crashato** (messaggi orfani):
- Il worker recupera automaticamente all'avvio e periodicamente (XCLAIM)
- Restart del worker: `python worker.py`

**Se messaggio non processabile**:
```bash
# Forzare ACK (skip il messaggio)
redis-cli XACK email_ingestion_stream email_processor_group <message_id>

# Oppure mandarlo manualmente in DLQ
# (restart worker – il recovery gestirà i messaggi con delivery_count > max)
```

---

## Problema: Latenza Elevata

### Sintomi
- `email_ingestion_processing_latency_seconds` p95/p99 alto
- Grafana mostra spike di latenza

### Diagnosi

**1. Redis latency**
```bash
redis-cli --latency
redis-cli --latency-history
redis-cli INFO memory | grep used_memory_human
```

**2. Stream troppo grande?**
```bash
redis-cli XLEN email_ingestion_stream
redis-cli MEMORY USAGE email_ingestion_stream
```
Se > `max_stream_length`: il trimming potrebbe essere lento

**3. Batch size troppo grande/piccolo?**
- Troppo grande → singolo ciclo lento
- Troppo piccolo → overhead per messaggio alta
- Default consigliato: 10-50

**4. CPU/Memory del processo**
```bash
top -p $(cat pids/worker.pid)     # Linux
Get-Process -Id (Get-Content pids\worker.pid) | Select-Object CPU,WorkingSet  # Windows
```

---

## Problema: Duplicati Processati

### Sintomi
- `email_ingestion_idempotency_duplicates_total` in crescita (è normale un tasso basso)
- Se duplicati NON rilevati: email processate due volte

### Diagnosi
```bash
# Controlla dimensione set idempotenza
redis-cli SCARD "idempotency:processed_ids"

# Controlla TTL del set (se impostato)
redis-cli TTL "idempotency:processed_ids"
```

**Se TTL troppo breve**: messaggi rientrano prima che il set scada.  
Aumentare `IDEMPOTENCY_TTL_SECONDS` (default: 86400 = 24h).

**Se set troppo grande**: memory pressure → potrebbe essere evicted da Redis.  
Monitorare `redis-cli INFO memory`.

---

## Problema: UIDVALIDITY Changed

### Sintomi
- Producer log: `UIDVALIDITY changed for INBOX!`
- Re-fetch completo di tutte le email

### Causa
Evento normale ma raro: Gmail ha ricostruito la mailbox.

### Azioni
- **Nessuna azione necessaria**: il producer resetta automaticamente `last_uid` e riparte
- L'idempotency layer nel worker previene la ri-processazione di email già viste
- Monitorare il volume di messaggi nel post-reset

---

## Comandi Redis Utili

### Stream Operations
```bash
# Info stream
redis-cli XINFO STREAM email_ingestion_stream

# Primi 5 messaggi
redis-cli XRANGE email_ingestion_stream - + COUNT 5

# Ultimi 5 messaggi
redis-cli XREVRANGE email_ingestion_stream + - COUNT 5

# Consumer groups info
redis-cli XINFO GROUPS email_ingestion_stream

# Consumer info
redis-cli XINFO CONSUMERS email_ingestion_stream email_processor_group

# Pending messages (overview)
redis-cli XPENDING email_ingestion_stream email_processor_group

# Pending messages (detail)
redis-cli XPENDING email_ingestion_stream email_processor_group - + 20
```

### DLQ Operations
```bash
redis-cli XLEN email_ingestion_dlq
redis-cli XRANGE email_ingestion_dlq - + COUNT 5
```

### State Operations
```bash
redis-cli GET "producer_state:user@gmail.com:INBOX:last_uid"
redis-cli GET "producer_state:user@gmail.com:INBOX:uidvalidity"
redis-cli GET "producer_state:user@gmail.com:INBOX:total_emails"
```

### Memory & Performance
```bash
redis-cli INFO memory
redis-cli INFO stats
redis-cli MEMORY USAGE email_ingestion_stream
redis-cli SLOWLOG GET 10
```

---

## Log Analysis

### Filtrare errori
```bash
# Errori recenti (JSON logs)
cat logs/worker.log | python -c "
import sys,json
for line in sys.stdin:
    try:
        d=json.loads(line)
        if d.get('level') in ('ERROR','CRITICAL'):
            print(f\"{d['timestamp']} [{d['level']}] {d['message']}\")
    except: pass
" | tail -20
```

### Cercare per correlation ID
```bash
grep "correlation_id.*abc123" logs/worker.log
```

### Performance (processing times)
```bash
grep "processing_time_seconds" logs/worker.log | tail -20
```

---

## Health Endpoint Interpretation

### `/health` (Liveness)
- **200**: processo vivo → tutto ok
- **Non raggiungibile**: processo crashato → restart

### `/ready` (Readiness)
- **200**: tutte le dipendenze connesse
- **503**: almeno una dipendenza critica down → controllare `checks` array nella response

### `/status` (Full Status)
Risposta include:
- `health_checks`: stato di ogni dipendenza
- `circuit_breakers`: stato di tutti i circuit breaker
- `statistics`: contatori dal componente (processed, failed, etc.)
- `uptime_seconds`: tempo dall'avvio
